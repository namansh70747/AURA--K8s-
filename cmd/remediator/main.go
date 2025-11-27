package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"strconv"
	"syscall"
	"time"

	_ "net/http/pprof" // Enable pprof for profiling

	"github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/remediation"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	utils.Log.Info("Starting AURA K8s Remediator")

	// Enable pprof for profiling (disabled by default, enable explicitly)
	pprofEnabled := os.Getenv("ENABLE_PPROF") == "true"
	if pprofEnabled {
		pprofAddr := getEnv("PPROF_ADDR", "localhost:6060")
		go func() {
			utils.Log.Infof("Starting pprof server on %s", pprofAddr)
			log.Println(http.ListenAndServe(pprofAddr, nil))
		}()
	}

	// Set GOMAXPROCS to number of CPUs for optimal parallelism
	numCPU := runtime.NumCPU()
	runtime.GOMAXPROCS(numCPU)
	utils.Log.Infof("Set GOMAXPROCS to %d", numCPU)

	// Enable GC tuning for better performance
	// More aggressive GC (lower value = more frequent GC, higher memory usage but lower latency)
	gcPercent := 100 // Default: 100%
	if val := os.Getenv("GOGC"); val != "" {
		if percent, err := strconv.Atoi(val); err == nil && percent > 0 {
			gcPercent = percent
		}
	}
	debug.SetGCPercent(gcPercent)
	utils.Log.Infof("Set GC percent to %d", gcPercent)

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
	dbURL := config.GetDatabaseURL()
	if dbURL == "" {
		if env == "production" {
			utils.Log.Fatal("DATABASE_URL environment variable is required in production")
		}
		utils.Log.Warn("Using default DATABASE_URL (development only)")
	}

	// Validate MCP server URL
	mcpURL := config.GetServiceURL("MCP_SERVER", "8000")
	if mcpURL == "" {
		utils.Log.Warn("MCP_SERVER_URL not set, AI-powered remediation will be disabled")
	}

	// Validate remediation interval (must be >= 5s for performance)
	interval := getEnvDuration("REMEDIATION_INTERVAL", 30*time.Second)
	if interval < 5*time.Second {
		utils.Log.Warnf("Remediation interval too low (%v), setting to minimum 5s", interval)
		interval = 5 * time.Second
	}

	// Preventive remediation runs faster (every 10s by default) for quick response
	preventiveInterval := getEnvDuration("PREVENTIVE_REMEDIATION_INTERVAL", 10*time.Second)
	if preventiveInterval < 2*time.Second {
		utils.Log.Warnf("Preventive remediation interval too low (%v), setting to minimum 2s", preventiveInterval)
		preventiveInterval = 2 * time.Second
	}

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

			// Check MCP server (if configured) - non-critical, only degrades if unavailable
			if mcpURL != "" {
				mcpClient := &http.Client{Timeout: 5 * time.Second}
				resp, err := mcpClient.Get(mcpURL + "/health")
				if err != nil || (resp != nil && resp.StatusCode != http.StatusOK) {
					// MCP server is optional - don't mark as unhealthy, just log
					utils.Log.WithError(err).Debug("MCP server health check failed (non-critical)")
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

		// API endpoint for immediate preventive action triggering
		mux.HandleFunc("/api/v1/trigger-preventive", func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
				return
			}

			// Trigger preventive remediation immediately
			triggerCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()

			if err := remediator.ProcessPreventiveRemediations(triggerCtx); err != nil {
				utils.Log.WithError(err).Error("Failed to trigger preventive remediation via API")
				http.Error(w, fmt.Sprintf("Failed to trigger preventive remediation: %v", err), http.StatusInternalServerError)
				return
			}

			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":  "success",
				"message": "Preventive remediation triggered",
				"time":    time.Now().Format(time.RFC3339),
			})
		})

		utils.Log.Infof("Starting health and metrics server on :%s", metricsPort)
		if err := http.ListenAndServe(":"+metricsPort, mux); err != nil {
			utils.Log.WithError(err).Error("Health server failed")
		}
	}()

	// Setup graceful shutdown
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	// Start remediation loops
	reactiveTicker := time.NewTicker(interval)
	preventiveTicker := time.NewTicker(preventiveInterval)
	cleanupTicker := time.NewTicker(1 * time.Hour) // Cleanup expired warnings every hour
	defer reactiveTicker.Stop()
	defer preventiveTicker.Stop()
	defer cleanupTicker.Stop()

	utils.Log.Infof("Remediation started - Reactive: %s, Preventive: %s", interval, preventiveInterval)

	// Process immediately on startup
	if err := remediator.ProcessRemediations(ctx); err != nil {
		utils.Log.WithError(err).Error("Initial remediation failed")
	}

	// Process preventive remediations immediately on startup
	if err := remediator.ProcessPreventiveRemediations(ctx); err != nil {
		utils.Log.WithError(err).Error("Initial preventive remediation failed")
	}

	// Main loop - run reactive and preventive in parallel
	for {
		select {
		case <-reactiveTicker.C:
			start := time.Now()

			// Process reactive remediations
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

		case <-preventiveTicker.C:
			// Process preventive remediations (faster interval for proactive actions)
			preventiveStart := time.Now()
			if err := remediator.ProcessPreventiveRemediations(ctx); err != nil {
				utils.Log.WithError(err).Error("Preventive remediation failed")
				metrics.RemediationsTotal.WithLabelValues("preventive_failed").Inc()
			} else {
				preventiveDuration := time.Since(preventiveStart)
				utils.Log.Infof("Preventive remediation completed in %.2fs", preventiveDuration.Seconds())
				metrics.RemediationsTotal.WithLabelValues("preventive_success").Inc()
			}

		case <-cleanupTicker.C:
			// Cleanup expired warnings
			cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
			cleaned, err := postgresDB.CleanupExpiredWarnings(cleanupCtx)
			cleanupCancel()
			if err != nil {
				utils.Log.WithError(err).Error("Failed to cleanup expired warnings")
			} else if cleaned > 0 {
				utils.Log.Infof("Cleaned up %d expired warnings", cleaned)
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
