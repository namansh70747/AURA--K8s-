package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/remediation"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	utils.Log.Info("Starting AURA K8s Remediator")
	
	// Setup file logging with rotation if configured
	if logDir := os.Getenv("LOG_DIR"); logDir != "" {
		maxSizeMB := 100
		if val := os.Getenv("LOG_MAX_SIZE_MB"); val != "" {
			if size, err := strconv.Atoi(val); err == nil && size > 0 {
				maxSizeMB = size
			}
		}
		maxAgeDays := 7
		if val := os.Getenv("LOG_MAX_AGE_DAYS"); val != "" {
			if age, err := strconv.Atoi(val); err == nil && age > 0 {
				maxAgeDays = age
			}
		}
		maxBackups := 5
		if val := os.Getenv("LOG_MAX_BACKUPS"); val != "" {
			if backups, err := strconv.Atoi(val); err == nil && backups > 0 {
				maxBackups = backups
			}
		}
		compress := os.Getenv("LOG_COMPRESS") != "false"
		cleanup := utils.SetupFileLogging(logDir, "remediator", maxSizeMB, maxAgeDays, maxBackups, compress)
		defer cleanup()
	}

	// Get configuration from environment - fail-fast in production if not set
	env := getEnv("ENVIRONMENT", "development")
	dbURL := getEnv("DATABASE_URL", "")
	if dbURL == "" {
		if env == "production" {
			utils.Log.Fatal("DATABASE_URL environment variable is required in production")
		}
		// Use default only in development - standardize connection string format
		// Use 127.0.0.1 instead of localhost to avoid IPv6 resolution issues
		dbURL = "postgresql://aura:aura_password@127.0.0.1:5432/aura_metrics?sslmode=disable"
		utils.Log.Warn("Using default DATABASE_URL (development only)")
	}
	mcpURL := getEnv("MCP_SERVER_URL", "http://localhost:8000")
	interval := getEnvDuration("REMEDIATION_INTERVAL", 30*time.Second)
	metricsPort := getEnv("METRICS_PORT", "9091")

	// Initialize Kubernetes client
	k8sClient, err := k8s.NewClient()
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to initialize Kubernetes client")
	}
	utils.Log.Info("Kubernetes client initialized")
	
	// Initialize database connection
	postgresDB, err := storage.NewPostgresDB(dbURL)
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to connect to database")
	}
	defer postgresDB.Close()

	// Initialize schema
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := postgresDB.InitSchema(ctx); err != nil {
		utils.Log.WithError(err).Fatal("Failed to initialize database schema")
	}

	// Initialize remediator
	remediator := remediation.NewRemediator(k8sClient, postgresDB, mcpURL)
	
	// Enable dry-run mode if configured
	if getEnv("DRY_RUN", "false") == "true" {
		remediator.SetDryRun(true)
		utils.Log.Info("⚠️  DRY-RUN MODE ENABLED - No actual changes will be made")
	}

	// Start health and metrics server
	go func() {
		mux := http.NewServeMux()
		
		// Health check endpoint with dependency validation
		mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
			// Deep health check
			healthy := true
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			
			// Check database connection
			if err := postgresDB.Ping(ctx); err != nil {
				healthy = false
			}
			
			// Test K8s client
			if _, err := k8sClient.ListPods(ctx, ""); err != nil {
				healthy = false
			}
			
			// Check MCP server (if configured)
			if mcpURL != "" {
				mcpClient := &http.Client{Timeout: 5 * time.Second}
				resp, err := mcpClient.Get(mcpURL + "/health")
				if err != nil || (resp != nil && resp.StatusCode != http.StatusOK) {
					healthy = false
				}
				if resp != nil {
					resp.Body.Close()
				}
			}
			
			if healthy {
				metrics.SetServiceHealth("remediator", true)
				w.WriteHeader(http.StatusOK)
				w.Write([]byte("OK"))
			} else {
				metrics.SetServiceHealth("remediator", false)
				w.WriteHeader(http.StatusServiceUnavailable)
				w.Write([]byte("UNHEALTHY"))
			}
		})
		
		// Prometheus metrics endpoint
		mux.Handle("/metrics", promhttp.Handler())
		
		utils.Log.Infof("Starting health and metrics server on :%s", metricsPort)
		if err := http.ListenAndServe(":"+metricsPort, mux); err != nil {
			utils.Log.WithError(err).Error("Health server failed")
		}
	}()

	// Setup graceful shutdown
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	// Start remediation loop
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	utils.Log.Infof("Remediation started with interval: %s", interval)

	// Process immediately on startup
	if err := remediator.ProcessRemediations(ctx); err != nil {
		utils.Log.WithError(err).Error("Initial remediation failed")
	}

	// Main loop
	for {
		select {
		case <-ticker.C:
			start := time.Now()

			if err := remediator.ProcessRemediations(ctx); err != nil {
				utils.Log.WithError(err).Error("Remediation failed")
				metrics.RemediationsTotal.WithLabelValues("failed").Inc()
				metrics.SetServiceHealth("remediator", false)
			} else {
				duration := time.Since(start)
				metrics.RecordRemediationDuration(duration)
				metrics.RemediationsTotal.WithLabelValues("success").Inc()
				utils.Log.Infof("Remediation completed in %.2fs", duration.Seconds())
				metrics.SetServiceHealth("remediator", true)
			}

		case <-stop:
			utils.Log.Info("Shutting down remediator gracefully...")
			cancel() // Cancel context to stop ongoing operations
			
			// Create shutdown timeout context
			shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer shutdownCancel()
			
			// Close database with timeout
			done := make(chan error, 1)
			go func() {
				done <- postgresDB.Close()
			}()
			
			select {
			case err := <-done:
				if err != nil {
					utils.Log.WithError(err).Error("Error closing database")
				}
			case <-shutdownCtx.Done():
				utils.Log.Warn("Shutdown timeout reached, forcing exit")
			}
			
			utils.Log.Info("Remediator stopped")
			return
		}
	}
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func getEnvDuration(key string, defaultValue time.Duration) time.Duration {
	if value := os.Getenv(key); value != "" {
		if d, err := time.ParseDuration(value); err == nil {
			return d
		}
	}
	return defaultValue
}
