package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/ml"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	utils.Log.Info("Starting AURA K8s Collector")

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
		cleanup := utils.SetupFileLogging(logDir, "collector", maxSizeMB, maxAgeDays, maxBackups, compress)
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
	mlURL := getEnv("ML_SERVICE_URL", "http://localhost:8001")
	interval := getEnvDuration("COLLECTION_INTERVAL", 15*time.Second)
	metricsPort := getEnv("METRICS_PORT", "9090")

	// Initialize Kubernetes client
	k8sClient, err := k8s.NewClient()
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to create Kubernetes client")
	}
	utils.Log.Info("Kubernetes client initialized")

	// Validate metrics-server availability
	utils.Log.Info("Validating metrics-server availability...")
	metricsCheckCtx, metricsCheckCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer metricsCheckCancel()

	// Try to get metrics for any pod to validate metrics-server
	pods, err := k8sClient.ListPods(metricsCheckCtx, "")
	if err == nil && len(pods.Items) > 0 {
		// Try to get metrics for the first pod
		testPod := pods.Items[0]
		_, err = k8sClient.GetPodMetrics(metricsCheckCtx, testPod.Namespace, testPod.Name)
		if err != nil {
			utils.Log.WithError(err).Warn("⚠️  Metrics-server may not be available. Collector will continue but metrics collection may fail.")
			utils.Log.Warn("   To install metrics-server in Kind cluster: kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml")
		} else {
			utils.Log.Info("✓ Metrics-server is available")
		}
	} else {
		utils.Log.Info("ℹ️  No pods found yet - metrics-server validation skipped")
	}

	// Initialize database
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

	// Create ML client
	mlClient := ml.NewMLClient(mlURL)
	utils.Log.Infof("ML client initialized with URL: %s", mlURL)

	// Create collector with ML integration (pass PostgresDB directly, it implements Database interface via methods)
	collector := metrics.NewCollector(k8sClient, postgresDB, mlClient)

	// Start health and metrics server
	go func() {
		mux := http.NewServeMux()

		// Health check endpoint with tiered dependency validation
		mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()

			// Tiered health checks: critical vs degraded
			criticalHealthy := true
			degraded := false
			issues := []string{}

			// Critical: Database connection (required for operation)
			if err := postgresDB.Ping(ctx); err != nil {
				criticalHealthy = false
				issues = append(issues, "database")
			}

			// Critical: K8s client (required for operation)
			if _, err := k8sClient.ListPods(ctx, ""); err != nil {
				criticalHealthy = false
				issues = append(issues, "kubernetes")
			}

			// Non-critical: ML service (degraded if unavailable)
			mlURL := getEnv("ML_SERVICE_URL", "http://localhost:8001")
			mlClient := &http.Client{Timeout: 2 * time.Second}
			resp, err := mlClient.Get(mlURL + "/health")
			if err != nil || (resp != nil && resp.StatusCode != http.StatusOK) {
				degraded = true
				issues = append(issues, "ml_service")
			}
			if resp != nil {
				resp.Body.Close()
			}

			// Non-critical: MCP server (degraded if unavailable)
			mcpURL := getEnv("MCP_SERVER_URL", "http://localhost:8000")
			mcpClient := &http.Client{Timeout: 2 * time.Second}
			mcpResp, mcpErr := mcpClient.Get(mcpURL + "/health")
			if mcpErr != nil || (mcpResp != nil && mcpResp.StatusCode != http.StatusOK) {
				degraded = true
				issues = append(issues, "mcp_server")
			}
			if mcpResp != nil {
				mcpResp.Body.Close()
			}

			if criticalHealthy {
				if degraded {
					// Degraded but operational
					metrics.SetServiceHealth("collector", true)
					w.WriteHeader(http.StatusOK)
					w.Write([]byte(fmt.Sprintf("DEGRADED: %s", strings.Join(issues, ", "))))
				} else {
					// Fully healthy
					metrics.SetServiceHealth("collector", true)
					w.WriteHeader(http.StatusOK)
					w.Write([]byte("OK"))
				}
			} else {
				// Unhealthy - critical dependencies down
				metrics.SetServiceHealth("collector", false)
				w.WriteHeader(http.StatusServiceUnavailable)
				w.Write([]byte(fmt.Sprintf("UNHEALTHY: %s", strings.Join(issues, ", "))))
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

	// Start collection loop
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	utils.Log.Infof("Collection started with interval: %s", interval)

	// Collect immediately on startup
	if err := collector.CollectAll(ctx); err != nil {
		utils.Log.WithError(err).Error("Initial collection failed")
	}

	// Main loop
	for {
		select {
		case <-ticker.C:
			start := time.Now()

			if err := collector.CollectAll(ctx); err != nil {
				utils.Log.WithError(err).Error("Collection failed")
				metrics.CollectionErrors.WithLabelValues("all").Inc()
				metrics.SetServiceHealth("collector", false)
			} else {
				duration := time.Since(start)
				metrics.RecordCollectionDuration(duration)
				utils.Log.Infof("Collection completed in %.2fs", duration.Seconds())
				metrics.SetServiceHealth("collector", true)
			}

		case <-stop:
			utils.Log.Info("Shutting down collector gracefully...")
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

			utils.Log.Info("Collector stopped")
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
