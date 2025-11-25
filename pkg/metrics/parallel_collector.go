package metrics

import (
	"context"
	"fmt"
	"runtime"
	"sync"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
)

// CollectionStrategy defines the collection strategy
// NOTE: Only StrategyBalanced is used - StrategyFast and StrategyDeep are unused
type CollectionStrategy string

const (
	StrategyBalanced CollectionStrategy = "balanced" // Collect all metrics (only strategy used)
)

// ParallelCollector extends Collector with parallel collection capabilities
type ParallelCollector struct {
	*Collector
	workers        int
	batchSize      int
	cache          *MetricsCache
	cacheTTL       time.Duration
	workerPool     chan struct{} // Semaphore for limiting concurrent workers
	metricsChan    chan *PodMetrics
	batchProcessor *BatchProcessor
}

// NewParallelCollector creates a new parallel collector
func NewParallelCollector(
	k8sClient *k8s.Client,
	db Database,
	mlClient MLClient,
	workers int,
	batchSize int,
	cacheTTL time.Duration,
) *ParallelCollector {
	if workers <= 0 {
		workers = runtime.NumCPU() * 2 // Default: 2x CPU cores
	}
	if batchSize <= 0 {
		batchSize = config.GetBatchSize()
		if batchSize <= 0 {
			batchSize = 100 // Default batch size
		}
	}

	collector := NewCollector(k8sClient, db, mlClient)

	batchProc := NewBatchProcessor(batchSize, 5*time.Second, db)
	batchProc.Start() // Start the batch processor

	return &ParallelCollector{
		Collector:      collector,
		workers:        workers,
		batchSize:      batchSize,
		cache:          NewMetricsCache(cacheTTL, 5*time.Minute),
		cacheTTL:       cacheTTL,
		workerPool:     make(chan struct{}, workers),
		metricsChan:    make(chan *PodMetrics, workers*10), // Buffered channel
		batchProcessor: batchProc,
	}
}

// CollectAll collects metrics from all pods and nodes (implements CollectorInterface)
// Uses comprehensive collector to ensure ALL metrics for ALL containers are collected
func (pc *ParallelCollector) CollectAll(ctx context.Context) error {
	// Use comprehensive collector as primary method - ALWAYS collects ALL metrics
	compCollector := NewComprehensiveCollector(pc.Collector)
	if err := compCollector.CollectAllComprehensiveMetrics(ctx); err != nil {
		utils.Log.WithError(err).Warn("⚠️  Comprehensive collection had errors, falling back to parallel collection")
		// Fallback to parallel collection if comprehensive fails completely
		return pc.CollectMetricsParallel(ctx)
	}
	utils.Log.Info("✅ Comprehensive metrics collection completed via parallel collector")
	return nil
}

// CollectMetricsParallel collects metrics from all pods in parallel
func (pc *ParallelCollector) CollectMetricsParallel(ctx context.Context) error {
	utils.Log.Infof("Starting parallel metrics collection with %d workers", pc.workers)

	// Start batch processor
	pc.batchProcessor.Start()
	defer pc.batchProcessor.Stop()

	// List all pods
	pods, err := pc.k8sClient.ListPods(ctx, "")
	if err != nil {
		return fmt.Errorf("failed to list pods: %w", err)
	}

	utils.Log.Infof("Collecting metrics for %d pods in parallel", len(pods.Items))

	// Filter out system pods
	var filteredPods []corev1.Pod
	for _, pod := range pods.Items {
		if !isSystemPod(&pod) {
			filteredPods = append(filteredPods, pod)
		}
	}

	// Collect metrics in parallel using worker pool
	var wg sync.WaitGroup
	var mu sync.Mutex
	var metricsList []*PodMetrics
	errors := make(chan error, len(filteredPods))

	for _, pod := range filteredPods {
		wg.Add(1)

		// Acquire worker slot
		pc.workerPool <- struct{}{}

		go func(p corev1.Pod) {
			defer wg.Done()
			defer func() { <-pc.workerPool }() // Release worker slot

			// Check cache first
			podKey := GeneratePodKey(p.Namespace, p.Name)
			if cached, found := pc.cache.GetCachedMetrics(podKey); found {
				mu.Lock()
				metricsList = append(metricsList, cached)
				mu.Unlock()
				PodsCollected.WithLabelValues(p.Namespace).Inc()
				return
			}

			// Collect metrics
			metrics, err := pc.collectPodMetricsWithStrategy(ctx, &p, StrategyBalanced)
			if err != nil {
				utils.Log.WithError(err).WithField("pod", p.Name).Debug("Failed to collect metrics")
				CollectionErrors.WithLabelValues("pod").Inc()
				errors <- err
				return
			}

			// Cache metrics
			pc.cache.SetCachedMetrics(podKey, metrics, pc.cacheTTL)

			// Add to batch
			mu.Lock()
			metricsList = append(metricsList, metrics)
			batchReady := len(metricsList) >= pc.batchSize
			currentBatch := make([]*PodMetrics, len(metricsList))
			copy(currentBatch, metricsList)
			if batchReady {
				metricsList = metricsList[:0] // Clear for next batch
			}
			mu.Unlock()

			// Send batch if ready
			if batchReady {
				if err := pc.batchProcessor.AddMetrics(currentBatch); err != nil {
					utils.Log.WithError(err).Error("Failed to add metrics to batch")
				}
			}

			PodsCollected.WithLabelValues(p.Namespace).Inc()

			// Get ML prediction if available (async)
			if pc.mlClient != nil {
				go func(m *PodMetrics) {
					predStart := time.Now()
					prediction, err := pc.mlClient.Predict(ctx, m)
					if err != nil {
						utils.Log.WithError(err).WithField("pod", m.PodName).Debug("ML prediction failed")
						return
					}
					RecordMLPredictionDuration(time.Since(predStart))
					MLPredictionsTotal.Inc()
					if err := pc.db.SaveMLPrediction(ctx, prediction); err != nil {
						utils.Log.WithError(err).WithField("pod", m.PodName).Warn("Failed to save ML prediction")
					}
				}(metrics)
			}
		}(pod)
	}

	// Wait for all workers to complete
	wg.Wait()

	// Flush remaining metrics with a small delay to ensure batch processor is ready
	time.Sleep(100 * time.Millisecond)
	mu.Lock()
	remainingMetrics := make([]*PodMetrics, len(metricsList))
	copy(remainingMetrics, metricsList)
	metricsList = metricsList[:0] // Clear to prevent double processing
	mu.Unlock()

	if len(remainingMetrics) > 0 {
		// Use background context to avoid cancellation
		bgCtx := context.Background()
		if err := pc.batchProcessor.AddMetrics(remainingMetrics); err != nil {
			// If batch processor fails, save directly
			utils.Log.WithError(err).Warn("Batch processor failed, saving metrics directly")
			for _, m := range remainingMetrics {
				if err := pc.db.SavePodMetrics(bgCtx, m); err != nil {
					utils.Log.WithError(err).WithField("pod", m.PodName).Warn("Failed to save metric directly")
				}
			}
		} else {
			// Give batch processor time to process
			time.Sleep(1 * time.Second)
		}
	}

	// Log cache stats
	stats := pc.cache.GetStats()
	utils.Log.Infof("Cache stats: hit_rate=%.2f, hits=%d, misses=%d",
		stats["hit_rate"], stats["hit_count"], stats["miss_count"])

	close(errors)
	var collectionErrors []error
	for err := range errors {
		if err != nil {
			collectionErrors = append(collectionErrors, err)
		}
	}

	if len(collectionErrors) > 0 {
		utils.Log.Warnf("Encountered %d errors during collection", len(collectionErrors))
	}

	utils.Log.Info("Parallel metrics collection completed")
	return nil
}

// collectPodMetricsWithStrategy collects metrics based on strategy
// NOTE: Only StrategyBalanced is used - this is a fallback when comprehensive collector fails
func (pc *ParallelCollector) collectPodMetricsWithStrategy(
	ctx context.Context,
	pod *corev1.Pod,
	strategy CollectionStrategy,
) (*PodMetrics, error) {
	// Always use balanced strategy (comprehensive collector is primary method)
	// This is only called as fallback when comprehensive collector fails
	return pc.Collector.buildPodMetrics(ctx, pod)
}

// isSystemPod checks if pod is a system pod
func isSystemPod(pod *corev1.Pod) bool {
	systemNamespaces := []string{
		"kube-system",
		"kube-public",
		"kube-node-lease",
		"local-path-storage",
	}

	for _, ns := range systemNamespaces {
		if pod.Namespace == ns {
			return true
		}
	}

	return false
}

// getRestartCount gets the total restart count for a pod
// GetBufferMetrics returns recent metrics from the circular buffer
// ParallelCollector embeds *Collector, so it has access to the buffer
func (pc *ParallelCollector) GetBufferMetrics(limit int) []*PodMetrics {
	return pc.Collector.GetBufferMetrics(limit)
}

// GetBufferStats returns statistics about the circular buffer
func (pc *ParallelCollector) GetBufferStats() map[string]interface{} {
	return pc.Collector.GetBufferStats()
}

func getRestartCount(pod *corev1.Pod) int32 {
	var restarts int32
	for _, status := range pod.Status.ContainerStatuses {
		restarts += status.RestartCount
	}
	return restarts
}
