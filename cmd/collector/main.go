package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/config"
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
	dbURL := config.GetDatabaseURL()
	if dbURL == "" {
		if env == "production" {
			utils.Log.Fatal("DATABASE_URL environment variable is required in production")
		}
		utils.Log.Warn("Using default DATABASE_URL (development only)")
	}
	mlURL := config.GetServiceURL("ML_SERVICE", "8001")
	if mlURL == "" {
		utils.Log.Fatal("ML_SERVICE_URL cannot be empty")
	}

	// Validate collection interval (must be >= 100ms for performance)
	interval := getEnvDuration("COLLECTION_INTERVAL", 500*time.Millisecond)
	if interval < 100*time.Millisecond {
		utils.Log.Warnf("Collection interval too low (%v), setting to minimum 100ms", interval)
		interval = 100 * time.Millisecond
	}

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

	// Determine collection mode (parallel or standard)
	useParallel := getEnv("USE_PARALLEL_COLLECTION", "true") == "true"

	var collector interface {
		CollectAll(ctx context.Context) error
		CollectMetricsParallel(ctx context.Context) error
	}

	// Store collector instance for API access
	var standardCollector *metrics.Collector
	var parallelCollector *metrics.ParallelCollector

	if useParallel {
		// Get parallel collection configuration
		workers := 20
		if val := os.Getenv("METRICS_COLLECTOR_WORKERS"); val != "" {
			if w, err := strconv.Atoi(val); err == nil && w > 0 {
				workers = w
			}
		}

		batchSize := 100
		if val := os.Getenv("METRICS_COLLECTOR_BATCH_SIZE"); val != "" {
			if bs, err := strconv.Atoi(val); err == nil && bs > 0 {
				batchSize = bs
			}
		}

		cacheTTL := 10 * time.Second
		if val := os.Getenv("METRICS_CACHE_TTL_SECONDS"); val != "" {
			if ttl, err := strconv.Atoi(val); err == nil && ttl > 0 {
				cacheTTL = time.Duration(ttl) * time.Second
			}
		}

		// Create parallel collector
		parallelCollector = metrics.NewParallelCollector(k8sClient, postgresDB, mlClient, workers, batchSize, cacheTTL)
		collector = parallelCollector
		utils.Log.Infof("Using parallel collection mode with %d workers, batch size %d, cache TTL %v", workers, batchSize, cacheTTL)
	} else {
		// Create standard collector
		standardCollector = metrics.NewCollector(k8sClient, postgresDB, mlClient)
		collector = &standardCollectorWrapper{collector: standardCollector}
		utils.Log.Info("Using standard collection mode")
	}

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
			// Reuse mlClient from main function scope via closure
			healthClient := &http.Client{Timeout: 2 * time.Second}
			resp, err := healthClient.Get(mlURL + "/health")
			if err != nil || (resp != nil && resp.StatusCode != http.StatusOK) {
				degraded = true
				issues = append(issues, "ml_service")
			}
			if resp != nil {
				resp.Body.Close()
			}

			// Non-critical: MCP server (degraded if unavailable)
			mcpURL := config.GetServiceURL("MCP_SERVER", "8000")
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

		// API endpoint to expose circular buffer for predictive orchestrator
		// Works with both standard and parallel collectors
		mux.HandleFunc("/api/v1/buffer/metrics", func(w http.ResponseWriter, r *http.Request) {
			// Get limit from query parameter (default: 1000)
			limit := 1000
			if limitStr := r.URL.Query().Get("limit"); limitStr != "" {
				if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
					limit = l
				}
			}

			// Get buffer metrics from either collector
			var bufferMetrics []*metrics.PodMetrics
			var stats map[string]interface{}

			if parallelCollector != nil {
				// Parallel collector embeds *Collector, so it has access to buffer methods
				bufferMetrics = parallelCollector.GetBufferMetrics(limit)
				stats = parallelCollector.GetBufferStats()
			} else if standardCollector != nil {
				bufferMetrics = standardCollector.GetBufferMetrics(limit)
				stats = standardCollector.GetBufferStats()
			} else {
				http.Error(w, "No collector available", http.StatusServiceUnavailable)
				return
			}

			// Convert to JSON
			type MetricResponse struct {
				PodName           string    `json:"pod_name"`
				Namespace         string    `json:"namespace"`
				Timestamp         time.Time `json:"timestamp"`
				CPUUtilization    float64   `json:"cpu_utilization"`
				MemoryUtilization float64   `json:"memory_utilization"`
				NetworkRxBytes    int64     `json:"network_rx_bytes"`
				NetworkTxBytes    int64     `json:"network_tx_bytes"`
				Restarts          int       `json:"restarts"`
			}

			response := make([]MetricResponse, 0, len(bufferMetrics))
			for _, m := range bufferMetrics {
				if m != nil {
					response = append(response, MetricResponse{
						PodName:           m.PodName,
						Namespace:         m.Namespace,
						Timestamp:         m.Timestamp,
						CPUUtilization:    m.CPUUtilization,
						MemoryUtilization: m.MemoryUtilization,
						NetworkRxBytes:    m.NetworkRxBytes,
						NetworkTxBytes:    m.NetworkTxBytes,
						Restarts:          int(m.Restarts),
					})
				}
			}

			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"metrics": response,
				"count":   len(response),
				"stats":   stats,
			})
		})

		// Buffer stats endpoint - works with both collectors
		mux.HandleFunc("/api/v1/buffer/stats", func(w http.ResponseWriter, r *http.Request) {
			var stats map[string]interface{}

			if parallelCollector != nil {
				stats = parallelCollector.GetBufferStats()
			} else if standardCollector != nil {
				stats = standardCollector.GetBufferStats()
			} else {
				http.Error(w, "No collector available", http.StatusServiceUnavailable)
				return
			}

			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(stats)
		})

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
	if useParallel {
		if err := collector.CollectMetricsParallel(ctx); err != nil {
			utils.Log.WithError(err).Error("Initial parallel collection failed")
		}
	} else {
		if err := collector.CollectAll(ctx); err != nil {
			utils.Log.WithError(err).Error("Initial collection failed")
		}
	}

	// Main loop
	for {
		select {
		case <-ticker.C:
			start := time.Now()

			var err error
			if useParallel {
				err = collector.CollectMetricsParallel(ctx)
			} else {
				err = collector.CollectAll(ctx)
			}

			if err != nil {
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

// standardCollectorWrapper wraps the standard collector to implement the interface
type standardCollectorWrapper struct {
	collector *metrics.Collector
}

func (w *standardCollectorWrapper) CollectAll(ctx context.Context) error {
	return w.collector.CollectAll(ctx)
}

func (w *standardCollectorWrapper) CollectMetricsParallel(ctx context.Context) error {
	// Fallback to standard collection
	return w.collector.CollectAll(ctx)
}

func (w *standardCollectorWrapper) GetBufferMetrics(limit int) []*metrics.PodMetrics {
	return w.collector.GetBufferMetrics(limit)
}

func (w *standardCollectorWrapper) GetBufferStats() map[string]interface{} {
	return w.collector.GetBufferStats()
}
