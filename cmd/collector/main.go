package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	// Prometheus metrics
	collectionsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "aura_collector_collections_total",
		Help: "Total number of metric collections performed",
	})
	collectionErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "aura_collector_collection_errors_total",
		Help: "Total number of collection errors",
	})
	collectionDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "aura_collector_collection_duration_seconds",
		Help:    "Duration of metric collection in seconds",
		Buckets: prometheus.DefBuckets,
	})
	podsCollected = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "aura_collector_pods_collected",
		Help: "Number of pods collected in last run",
	})
)

func main() {
	utils.Log.Info("Starting AURA K8s Collector")

	// Get configuration from environment
	dbURL := getEnv("DATABASE_URL", "postgres://aura:aura_password@localhost:5432/aura_metrics?sslmode=disable")
	interval := getEnvDuration("COLLECTION_INTERVAL", 15*time.Second)
	metricsPort := getEnv("METRICS_PORT", "9090")

	// Initialize Kubernetes client
	k8sClient, err := k8s.NewClient()
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to create Kubernetes client")
	}
	utils.Log.Info("Kubernetes client initialized")

	// Initialize database
	db, err := storage.NewPostgresDB(dbURL)
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to connect to database")
	}
	defer db.Close()

	// Initialize schema
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := db.InitSchema(ctx); err != nil {
		utils.Log.WithError(err).Fatal("Failed to initialize database schema")
	}

	// Create collector
	collector := metrics.NewCollector(k8sClient, db)

	// Start Prometheus metrics server
	go func() {
		http.Handle("/metrics", promhttp.Handler())
		http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("OK"))
		})
		utils.Log.Infof("Starting metrics server on :%s", metricsPort)
		if err := http.ListenAndServe(":"+metricsPort, nil); err != nil {
			utils.Log.WithError(err).Error("Metrics server failed")
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
			collectionsTotal.Inc()

			if err := collector.CollectAll(ctx); err != nil {
				utils.Log.WithError(err).Error("Collection failed")
				collectionErrors.Inc()
			} else {
				duration := time.Since(start).Seconds()
				collectionDuration.Observe(duration)
				utils.Log.Infof("Collection completed in %.2fs", duration)
			}

		case <-stop:
			utils.Log.Info("Shutting down collector gracefully...")
			cancel()   // Cancel context to stop ongoing operations
			db.Close() // Explicitly close database connection
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
