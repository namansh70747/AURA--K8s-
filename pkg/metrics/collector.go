package metrics

import (
	"context"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metricsapi "k8s.io/metrics/pkg/apis/metrics/v1beta1"
)

// getMaxRetries returns max retries from environment or default
func getMaxRetries() int {
	return config.GetMaxRetries()
}

// Database interface to avoid circular dependency with storage package
type Database interface {
	SavePodMetrics(ctx context.Context, m *PodMetrics) error
	SaveNodeMetrics(ctx context.Context, m *NodeMetrics) error
	GetRecentPodMetrics(ctx context.Context, podName, namespace string, limit int) ([]*PodMetrics, error)
	SaveMLPrediction(ctx context.Context, pred *MLPrediction) error
}

// MLClient interface for ML predictions
type MLClient interface {
	Predict(ctx context.Context, podMetrics *PodMetrics) (*MLPrediction, error)
}

// Collector collects metrics from Kubernetes pods and nodes
type Collector struct {
	k8sClient *k8s.Client
	db        Database
	mlClient  MLClient
}

// NewCollector creates a new Collector instance.
//
// Parameters:
//   - k8sClient: Kubernetes client for API operations
//   - db: Database interface for storing metrics
//   - mlClient: ML client interface for predictions (can be nil)
//
// Returns:
//   - *Collector: New collector instance
func NewCollector(k8sClient *k8s.Client, db Database, mlClient MLClient) *Collector {
	return &Collector{
		k8sClient: k8sClient,
		db:        db,
		mlClient:  mlClient,
	}
}

// CollectAll collects metrics from all pods and nodes.
//
// Parameters:
//   - ctx: Context for cancellation and timeout propagation
//
// Returns:
//   - error: Returns error if collection fails for pods or nodes
//
// This method collects metrics from all pods first, then all nodes.
func (c *Collector) CollectAll(ctx context.Context) error {
	utils.Log.Info("Starting metrics collection")

	// Collect pod metrics
	if err := c.CollectPodMetrics(ctx); err != nil {
		utils.Log.WithError(err).Error("Failed to collect pod metrics")
		return err
	}

	// Collect node metrics
	if err := c.CollectNodeMetrics(ctx); err != nil {
		utils.Log.WithError(err).Error("Failed to collect node metrics")
		return err
	}

	utils.Log.Info("Metrics collection completed")
	return nil
}

// CollectPodMetrics collects metrics for all pods in all namespaces.
//
// Parameters:
//   - ctx: Context for cancellation and timeout propagation
//
// Returns:
//   - error: Returns error if pod listing fails
//
// For each pod, this method:
//   - Builds pod metrics
//   - Saves metrics to database
//   - Optionally gets ML prediction (if mlClient is configured)
//   - Tracks collection errors in Prometheus metrics
func (c *Collector) CollectPodMetrics(ctx context.Context) error {
	pods, err := c.k8sClient.ListPods(ctx, "")
	if err != nil {
		return fmt.Errorf("failed to list pods: %w", err)
	}

	utils.Log.Infof("Collecting metrics for %d pods", len(pods.Items))

	// Collect all metrics first, then save in batches for better performance
	var metricsList []*PodMetrics
	batchSize := config.GetBatchSize()
	if batchSize <= 0 {
		batchSize = config.DefaultBatchSize
	}

	for _, pod := range pods.Items {
		metrics, err := c.buildPodMetrics(ctx, &pod)
		if err != nil {
			utils.Log.WithError(err).WithField("pod", pod.Name).Warn("Failed to build metrics for pod")
			CollectionErrors.WithLabelValues("pod").Inc()
			continue
		}

		metricsList = append(metricsList, metrics)
		PodsCollected.WithLabelValues(pod.Namespace).Inc()

		if c.mlClient != nil {
			// Get ML prediction for this pod (still done individually due to ML service API)
			predStart := time.Now()
			prediction, err := c.mlClient.Predict(ctx, metrics)
			if err != nil {
				// Log at Warning level if circuit breaker is open, Debug for transient errors
				if err.Error() == "circuit breaker is OPEN - ML service unavailable" {
					utils.Log.WithError(err).WithField("pod", pod.Name).Warn("ML service unavailable (circuit breaker open)")
					SetServiceHealth("ml_service", false)
				} else {
					utils.Log.WithError(err).WithField("pod", pod.Name).Warn("Failed to get ML prediction")
				}
				CollectionErrors.WithLabelValues("ml").Inc()
				SetServiceHealth("ml_service", false)
			} else {
				RecordMLPredictionDuration(time.Since(predStart))
				MLPredictionsTotal.Inc()
				SetServiceHealth("ml_service", true)
				if err := c.db.SaveMLPrediction(ctx, prediction); err != nil {
					utils.Log.WithError(err).WithField("pod", pod.Name).Warn("Failed to save ML prediction")
				}
			}
		}

		// Save metrics in batches
		if len(metricsList) >= batchSize {
			if batchDB, ok := c.db.(interface {
				SavePodMetricsBatch(ctx context.Context, metricsList []*PodMetrics) error
			}); ok {
				if err := batchDB.SavePodMetricsBatch(ctx, metricsList); err != nil {
					utils.Log.WithError(err).Warnf("Failed to save batch of %d metrics", len(metricsList))
					CollectionErrors.WithLabelValues("pod").Add(float64(len(metricsList)))
				}
			} else {
				// Fallback to individual saves if batch method not available
				for _, m := range metricsList {
					if err := c.db.SavePodMetrics(ctx, m); err != nil {
						utils.Log.WithError(err).WithField("pod", m.PodName).Warn("Failed to save metrics for pod")
						CollectionErrors.WithLabelValues("pod").Inc()
					}
				}
			}
			metricsList = metricsList[:0] // Clear slice
		}
	}

	// Save remaining metrics
	if len(metricsList) > 0 {
		if batchDB, ok := c.db.(interface {
			SavePodMetricsBatch(ctx context.Context, metricsList []*PodMetrics) error
		}); ok {
			if err := batchDB.SavePodMetricsBatch(ctx, metricsList); err != nil {
				utils.Log.WithError(err).Warnf("Failed to save final batch of %d metrics", len(metricsList))
				CollectionErrors.WithLabelValues("pod").Add(float64(len(metricsList)))
			}
		} else {
			// Fallback to individual saves
			for _, m := range metricsList {
				if err := c.db.SavePodMetrics(ctx, m); err != nil {
					utils.Log.WithError(err).WithField("pod", m.PodName).Warn("Failed to save metrics for pod")
					CollectionErrors.WithLabelValues("pod").Inc()
				}
			}
		}
	}

	return nil
}

// CollectNodeMetrics collects metrics for all nodes in the cluster.
//
// Parameters:
//   - ctx: Context for cancellation and timeout propagation
//
// Returns:
//   - error: Returns error if node listing fails
//
// For each node, this method:
//   - Builds node metrics
//   - Saves metrics to database
//   - Tracks collection errors in Prometheus metrics
func (c *Collector) CollectNodeMetrics(ctx context.Context) error {
	nodes, err := c.k8sClient.ListNodes(ctx)
	if err != nil {
		return fmt.Errorf("failed to list nodes: %w", err)
	}

	utils.Log.Infof("Collecting metrics for %d nodes", len(nodes.Items))

	for _, node := range nodes.Items {
		metrics, err := c.buildNodeMetrics(ctx, &node)
		if err != nil {
			utils.Log.WithError(err).WithField("node", node.Name).Warn("Failed to build metrics for node")
			CollectionErrors.WithLabelValues("node").Inc()
			continue
		}

		if err := c.db.SaveNodeMetrics(ctx, metrics); err != nil {
			utils.Log.WithError(err).WithField("node", node.Name).Warn("Failed to save metrics for node")
			CollectionErrors.WithLabelValues("node").Inc()
		} else {
			NodesCollected.Inc()
		}
	}

	return nil
}

// buildPodMetrics constructs PodMetrics from a pod object.
//
// Parameters:
//   - ctx: Context for cancellation and timeout propagation
//   - pod: Kubernetes pod object to extract metrics from
//
// Returns:
//   - *PodMetrics: Pod metrics object with all collected data
//   - error: Returns error if metrics collection fails
//
// This method:
//   - Retrieves resource metrics from metrics server with retry logic
//   - Calculates CPU and memory utilization
//   - Detects anomalies (OOM, crash loops, high CPU, network issues)
//   - Calculates trends from historical data
//   - Note: Network/disk metrics require cAdvisor integration (currently zero)
func (c *Collector) buildPodMetrics(ctx context.Context, pod *corev1.Pod) (*PodMetrics, error) {
	if len(pod.Spec.Containers) == 0 {
		return nil, fmt.Errorf("pod has no containers")
	}

	container := pod.Spec.Containers[0]
	containerStatus := getContainerStatus(pod, container.Name)

	// Get resource limits and requests FIRST (needed for fallback estimation)
	cpuLimit := float64(container.Resources.Limits.Cpu().MilliValue())
	memoryLimit := container.Resources.Limits.Memory().Value()
	cpuRequest := float64(container.Resources.Requests.Cpu().MilliValue())
	memoryRequest := container.Resources.Requests.Memory().Value()

	// Get resource metrics from metrics server with retry
	var cpuUsage, memoryUsage float64
	var memoryBytes int64

	// Retry with configurable backoff, maximum duration, and context cancellation checks
	var podMetrics *metricsapi.PodMetrics
	maxRetries := getMaxRetries()
	maxRetryDuration := 30 * time.Second // Maximum total retry duration
	if maxDurStr := os.Getenv("METRICS_COLLECTOR_MAX_RETRY_DURATION_SECONDS"); maxDurStr != "" {
		if maxDur, err := strconv.Atoi(maxDurStr); err == nil && maxDur > 0 {
			maxRetryDuration = time.Duration(maxDur) * time.Second
		}
	}

	retryStartTime := time.Now()

	for attempt := 0; attempt < maxRetries; attempt++ {
		// Check if context is cancelled
		select {
		case <-ctx.Done():
			utils.Log.Warn("Context cancelled during metrics collection")
			return nil, ctx.Err()
		default:
		}

		// Check if maximum retry duration has been exceeded
		if time.Since(retryStartTime) >= maxRetryDuration {
			utils.Log.Warnf("Maximum retry duration (%v) exceeded, stopping retries", maxRetryDuration)
			break
		}

		var err error
		podMetrics, err = c.k8sClient.GetPodMetrics(ctx, pod.Namespace, pod.Name)
		if err == nil {
			break
		}

		// Classify error as retryable vs non-retryable
		if !isRetryableMetricsError(err) {
			utils.Log.WithError(err).Warn("Non-retryable error encountered, stopping retries")
			podMetrics = nil
			break
		}

		if attempt < maxRetries-1 {
			backoff := config.GetRetryBackoff(attempt)
			// Ensure backoff doesn't exceed remaining retry duration
			elapsed := time.Since(retryStartTime)
			remaining := maxRetryDuration - elapsed
			if backoff > remaining {
				backoff = remaining
			}
			if backoff <= 0 {
				utils.Log.Warn("No time remaining for retry, stopping")
				break
			}

			utils.Log.WithError(err).Debugf("Metrics server retry %d/%d, backoff: %v", attempt+1, maxRetries, backoff)

			// Use context-aware sleep
			select {
			case <-ctx.Done():
				utils.Log.Warn("Context cancelled during retry backoff")
				return nil, ctx.Err()
			case <-time.After(backoff):
				// Continue retry
			}
		} else {
			utils.Log.WithError(err).Warn("Metrics server unavailable after retries, trying historical data fallback")
			// Try to get metrics from historical data as fallback (only use REAL historical metrics)
			historicalMetrics, histErr := c.db.GetRecentPodMetrics(ctx, pod.Name, pod.Namespace, 1)
			if histErr == nil && len(historicalMetrics) > 0 {
				latest := historicalMetrics[0]
				// Only use historical data if it's from a recent collection (within last 5 minutes)
				// This ensures we're using real metrics, not stale data
				age := time.Since(latest.Timestamp)
				if age < 5*time.Minute && latest.CPUUsageMillicores > 0 || latest.MemoryUsageBytes > 0 {
					cpuUsage = latest.CPUUsageMillicores
					memoryBytes = latest.MemoryUsageBytes
					memoryUsage = float64(memoryBytes)
					utils.Log.WithField("pod", pod.Name).Info("Using recent historical metrics as fallback")
				} else {
					// Historical data is too old or zero - use zero (real value, not estimate)
					utils.Log.WithField("pod", pod.Name).Info("Historical metrics too old or zero, using zero values (real, not estimated)")
					cpuUsage = 0
					memoryBytes = 0
					memoryUsage = 0
				}
			} else {
				// No historical data available - use zero (real value, not estimate)
				utils.Log.WithError(err).WithField("pod", pod.Name).Info("No historical metrics available, using zero values (real, not estimated)")
				cpuUsage = 0
				memoryBytes = 0
				memoryUsage = 0
			}
		}
	}

	if podMetrics != nil && len(podMetrics.Containers) > 0 {
		containerMetrics := podMetrics.Containers[0]
		cpuUsage = float64(containerMetrics.Usage.Cpu().MilliValue())
		memoryBytes = containerMetrics.Usage.Memory().Value()
		memoryUsage = float64(memoryBytes)
	}

	// Calculate utilizations with fallback logic
	// Priority: 1) Limits, 2) Requests, 3) Node capacity (if available), 4) Default estimates
	cpuUtilization := 0.0
	if cpuLimit > 0 {
		cpuUtilization = (cpuUsage / cpuLimit) * 100
	} else if cpuRequest > 0 {
		// Fallback to requests if limits not set
		cpuUtilization = (cpuUsage / cpuRequest) * 100
	} else if cpuUsage > 0 {
		// If we have usage but no limits/requests, try to get node capacity as fallback
		if pod.Spec.NodeName != "" {
			node, err := c.k8sClient.GetNode(ctx, pod.Spec.NodeName)
			if err == nil {
				nodeCPUCapacity := float64(node.Status.Capacity.Cpu().MilliValue())
				if nodeCPUCapacity > 0 {
					// Estimate: assume pod can use up to 10% of node capacity if no limits set
					estimatedLimit := nodeCPUCapacity * 0.1
					cpuUtilization = (cpuUsage / estimatedLimit) * 100
				}
			}
		}
		// If still 0, we'll keep it at 0 (no way to calculate without limits/requests/node info)
	}

	memoryUtilization := 0.0
	if memoryLimit > 0 {
		memoryUtilization = (memoryUsage / float64(memoryLimit)) * 100
	} else if memoryRequest > 0 {
		// Fallback to requests if limits not set
		memoryUtilization = (memoryUsage / float64(memoryRequest)) * 100
	} else if memoryUsage > 0 {
		// If we have usage but no limits/requests, try to get node capacity as fallback
		if pod.Spec.NodeName != "" {
			node, err := c.k8sClient.GetNode(ctx, pod.Spec.NodeName)
			if err == nil {
				nodeMemoryCapacity := node.Status.Capacity.Memory().Value()
				if nodeMemoryCapacity > 0 {
					// Estimate: assume pod can use up to 10% of node capacity if no limits set
					estimatedLimit := float64(nodeMemoryCapacity) * 0.1
					memoryUtilization = (memoryUsage / estimatedLimit) * 100
				}
			}
		}
		// If still 0, we'll keep it at 0 (no way to calculate without limits/requests/node info)
	}

	// Additional safety check: ensure utilizations are valid numbers
	if cpuUtilization < 0 {
		cpuUtilization = 0
	}
	if memoryUtilization < 0 {
		memoryUtilization = 0
	}

	// Get pod age
	age := int64(time.Since(pod.CreationTimestamp.Time).Seconds())

	// Check readiness
	ready := false
	for _, cond := range pod.Status.Conditions {
		if cond.Type == corev1.PodReady {
			ready = cond.Status == corev1.ConditionTrue
			break
		}
	}

	// Count restarts
	restarts := int32(0)
	if containerStatus != nil {
		restarts = containerStatus.RestartCount
	}

	// Get container state
	containerState := "Unknown"
	lastStateReason := ""
	containerReady := false

	if containerStatus != nil {
		containerReady = containerStatus.Ready
		if containerStatus.State.Running != nil {
			containerState = "Running"
		} else if containerStatus.State.Waiting != nil {
			containerState = "Waiting"
			lastStateReason = containerStatus.State.Waiting.Reason
		} else if containerStatus.State.Terminated != nil {
			containerState = "Terminated"
			lastStateReason = containerStatus.State.Terminated.Reason
		}

		if containerStatus.LastTerminationState.Terminated != nil {
			lastStateReason = containerStatus.LastTerminationState.Terminated.Reason
		}
	}

	// Calculate trends from historical data
	cpuTrend, memoryTrend, restartTrend := c.calculateTrends(ctx, pod.Name, pod.Namespace)

	// Detect issues
	hasOOMKill := lastStateReason == "OOMKilled"
	// Check for crash loop in multiple ways:
	// 1. Last state reason is CrashLoopBackOff
	// 2. Container is in waiting state with CrashLoopBackOff reason
	// 3. High restart count (> 3) indicates crash loop
	hasCrashLoop := lastStateReason == "CrashLoopBackOff"
	if !hasCrashLoop && containerStatus != nil {
		if containerStatus.State.Waiting != nil {
			hasCrashLoop = containerStatus.State.Waiting.Reason == "CrashLoopBackOff"
		}
	}
	if !hasCrashLoop && restarts > 3 {
		hasCrashLoop = true
	}
	hasHighCPU := cpuUtilization > config.GetHighCPUThreshold()
	hasNetworkIssues := false

	// Network and disk metrics collection
	// NOTE: Network and disk metrics require cAdvisor or CNI integration which is not available in Kind.
	// We only store REAL metrics from historical data (if recent) or zero (real value, not estimate).
	// No fake/estimated values are used - only real data or zero to indicate no data available.
	networkRxBytes := int64(0)
	networkTxBytes := int64(0)
	networkRxErrors := int64(0)
	networkTxErrors := int64(0)
	diskUsageBytes := int64(0)
	diskLimitBytes := int64(0)

	// Try to get network/disk metrics from historical data (only use REAL historical values)
	// Network and disk metrics require cAdvisor/CNI integration which is not available in Kind
	// We only use historical data if it's recent (within 5 minutes) and non-zero
	historicalMetrics, err := c.db.GetRecentPodMetrics(ctx, pod.Name, pod.Namespace, 5)
	if err == nil && len(historicalMetrics) > 0 {
		latest := historicalMetrics[0]
		age := time.Since(latest.Timestamp)
		// Only use historical network/disk data if it's recent (real data, not stale)
		if age < 5*time.Minute {
			networkRxBytes = latest.NetworkRxBytes
			networkTxBytes = latest.NetworkTxBytes
			networkRxErrors = latest.NetworkRxErrors
			networkTxErrors = latest.NetworkTxErrors
			diskUsageBytes = latest.DiskUsageBytes
			diskLimitBytes = latest.DiskLimitBytes
		} else {
			// Historical data too old - use zero (real value, not estimate)
			networkRxBytes = 0
			networkTxBytes = 0
			networkRxErrors = 0
			networkTxErrors = 0
			diskUsageBytes = 0
			diskLimitBytes = 0
		}
	} else {
		// No historical data - use zero (real value, not estimate)
		// Network/disk metrics require cAdvisor or CNI integration which is not available in Kind
		networkRxBytes = 0
		networkTxBytes = 0
		networkRxErrors = 0
		networkTxErrors = 0
		diskUsageBytes = 0
		diskLimitBytes = 0
	}

	// Try to get network/disk info from pod status if available
	// Check for network issues in pod conditions
	for _, cond := range pod.Status.Conditions {
		// Check for network-related conditions
		if cond.Type == corev1.PodScheduled && cond.Status == corev1.ConditionFalse {
			if strings.Contains(strings.ToLower(cond.Reason), "network") ||
				strings.Contains(strings.ToLower(cond.Message), "network") {
				hasNetworkIssues = true
			}
		}
		// Also check for any condition indicating network problems
		if strings.Contains(strings.ToLower(string(cond.Type)), "network") &&
			cond.Status == corev1.ConditionFalse {
			hasNetworkIssues = true
		}
	}

	// Check for disk pressure in node conditions (if we have node info)
	if pod.Spec.NodeName != "" {
		// Try to get node info to check for disk pressure
		node, err := c.k8sClient.GetNode(ctx, pod.Spec.NodeName)
		if err == nil {
			for _, cond := range node.Status.Conditions {
				if cond.Type == corev1.NodeDiskPressure && cond.Status == corev1.ConditionTrue {
					// Node has disk pressure - but we can't measure actual disk usage without cAdvisor
					// Store zero (real value, not estimate) - disk pressure is detected but usage is unknown
					hasNetworkIssues = false // This is disk, not network
				}
			}
		}

		// Disk metrics require cAdvisor integration which is not available in Kind
		// We store zero to indicate no data, not an estimate
		// diskUsageBytes and diskLimitBytes remain 0 (real value, not estimate)
	}

	metrics := &PodMetrics{
		PodName:       pod.Name,
		Namespace:     pod.Namespace,
		NodeName:      pod.Spec.NodeName,
		ContainerName: container.Name,
		Timestamp:     time.Now(),

		CPUUsageMillicores: cpuUsage,
		MemoryUsageBytes:   memoryBytes,
		MemoryLimitBytes:   memoryLimit,
		CPULimitMillicores: cpuLimit,

		CPUUtilization:    cpuUtilization,
		MemoryUtilization: memoryUtilization,

		NetworkRxBytes:  networkRxBytes,
		NetworkTxBytes:  networkTxBytes,
		NetworkRxErrors: networkRxErrors,
		NetworkTxErrors: networkTxErrors,

		DiskUsageBytes: diskUsageBytes,
		DiskLimitBytes: diskLimitBytes,

		Phase:    string(pod.Status.Phase),
		Ready:    ready,
		Restarts: restarts,
		Age:      age,

		ContainerReady:  containerReady,
		ContainerState:  containerState,
		LastStateReason: lastStateReason,

		CPUTrend:     cpuTrend,
		MemoryTrend:  memoryTrend,
		RestartTrend: restartTrend,

		HasOOMKill:       hasOOMKill,
		HasCrashLoop:     hasCrashLoop,
		HasHighCPU:       hasHighCPU,
		HasNetworkIssues: hasNetworkIssues,
	}

	return metrics, nil
}

// buildNodeMetrics constructs NodeMetrics from a node object
func (c *Collector) buildNodeMetrics(ctx context.Context, node *corev1.Node) (*NodeMetrics, error) {
	// Get resource metrics from metrics server
	nodeMetrics, err := c.k8sClient.GetNodeMetrics(ctx, node.Name)

	cpuUsage := 0.0
	memoryUsage := int64(0)

	if err == nil {
		cpuUsage = float64(nodeMetrics.Usage.Cpu().MilliValue())
		memoryUsage = nodeMetrics.Usage.Memory().Value()
	}

	// Get capacity
	cpuCapacity := float64(node.Status.Capacity.Cpu().MilliValue())
	memoryCapacity := node.Status.Capacity.Memory().Value()
	podCapacity := int(node.Status.Capacity.Pods().Value())

	// Calculate utilizations
	cpuUtilization := 0.0
	if cpuCapacity > 0 {
		cpuUtilization = (cpuUsage / cpuCapacity) * 100
	}

	memoryUtilization := 0.0
	if memoryCapacity > 0 {
		memoryUtilization = (float64(memoryUsage) / float64(memoryCapacity)) * 100
	}

	// Get pod count
	pods, _ := c.k8sClient.ListPods(ctx, "")
	podCount := 0
	for _, pod := range pods.Items {
		if pod.Spec.NodeName == node.Name {
			podCount++
		}
	}

	// Check conditions
	diskPressure := false
	memoryPressure := false
	networkUnavailable := false
	ready := false

	for _, cond := range node.Status.Conditions {
		switch cond.Type {
		case corev1.NodeDiskPressure:
			diskPressure = cond.Status == corev1.ConditionTrue
		case corev1.NodeMemoryPressure:
			memoryPressure = cond.Status == corev1.ConditionTrue
		case corev1.NodeNetworkUnavailable:
			networkUnavailable = cond.Status == corev1.ConditionTrue
		case corev1.NodeReady:
			ready = cond.Status == corev1.ConditionTrue
		}
	}

	metrics := &NodeMetrics{
		NodeName:              node.Name,
		Timestamp:             time.Now(),
		CPUUsageMillicores:    cpuUsage,
		CPUCapacityMillicores: cpuCapacity,
		MemoryUsageBytes:      memoryUsage,
		MemoryCapacityBytes:   memoryCapacity,
		CPUUtilization:        cpuUtilization,
		MemoryUtilization:     memoryUtilization,
		PodCount:              podCount,
		PodCapacity:           podCapacity,
		DiskPressure:          diskPressure,
		MemoryPressure:        memoryPressure,
		NetworkUnavailable:    networkUnavailable,
		Ready:                 ready,
	}

	return metrics, nil
}

// calculateTrends calculates linear regression trends from historical data
func (c *Collector) calculateTrends(ctx context.Context, podName, namespace string) (cpuTrend, memoryTrend, restartTrend float64) {
	// Get last 10 data points
	metrics, err := c.db.GetRecentPodMetrics(ctx, podName, namespace, 10)
	if err != nil || len(metrics) < 2 {
		return 0, 0, 0
	}

	// Calculate trends using simple linear regression
	cpuTrend = calculateLinearTrend(metrics, func(m *PodMetrics) float64 { return m.CPUUtilization })
	memoryTrend = calculateLinearTrend(metrics, func(m *PodMetrics) float64 { return m.MemoryUtilization })
	restartTrend = calculateLinearTrend(metrics, func(m *PodMetrics) float64 { return float64(m.Restarts) })

	return
}

// calculateLinearTrend calculates the slope of linear regression
func calculateLinearTrend(metrics []*PodMetrics, getValue func(*PodMetrics) float64) float64 {
	n := float64(len(metrics))
	if n < 2 {
		return 0
	}

	var sumX, sumY, sumXY, sumX2 float64

	for i, m := range metrics {
		x := float64(i)
		y := getValue(m)
		sumX += x
		sumY += y
		sumXY += x * y
		sumX2 += x * x
	}

	// slope = (n*sumXY - sumX*sumY) / (n*sumX2 - sumX*sumX)
	denominator := n*sumX2 - sumX*sumX
	if math.Abs(denominator) < 0.0001 {
		return 0
	}

	slope := (n*sumXY - sumX*sumY) / denominator
	return slope
}

// getContainerStatus returns the status for a specific container
func getContainerStatus(pod *corev1.Pod, containerName string) *corev1.ContainerStatus {
	for i := range pod.Status.ContainerStatuses {
		if pod.Status.ContainerStatuses[i].Name == containerName {
			return &pod.Status.ContainerStatuses[i]
		}
	}
	return nil
}

// isRetryableMetricsError classifies errors as retryable vs non-retryable
func isRetryableMetricsError(err error) bool {
	if err == nil {
		return false
	}

	// Check for Kubernetes API errors
	if k8sErr, ok := err.(*errors.StatusError); ok {
		status := k8sErr.Status()
		code := status.Code

		// Retryable: server errors (5xx), timeouts, too many requests
		if code >= 500 && code < 600 {
			return true
		}
		if code == 408 || code == 429 { // Request Timeout, Too Many Requests
			return true
		}
		// Also retry on 503 Service Unavailable
		if code == 503 {
			return true
		}

		// Non-retryable: client errors (4xx) except 408, 429
		if code >= 400 && code < 500 {
			return false
		}
	}

	// Check for context errors
	if err == context.DeadlineExceeded || err == context.Canceled {
		return false // Context errors are not retryable
	}

	// Check error message for common patterns
	errStr := strings.ToLower(err.Error())

	// Non-retryable errors - expanded list
	nonRetryablePatterns := []string{
		"not found",
		"forbidden",
		"unauthorized",
		"bad request",
		"invalid",
		"malformed",
		"already exists",
		"conflict",
		"permission denied",
		"access denied",
		"method not allowed",
		"unsupported media type",
		"no such host",
		"name resolution",
		"certificate",
		"tls",
		"authentication failed",
		"authorization failed",
		"resource already exists",
		"duplicate",
		"immutable field",
		"field is immutable",
		"invalid name",
		"invalid namespace",
		"invalid label",
		"invalid annotation",
		"invalid selector",
		"invalid resource",
		"invalid value",
		"invalid format",
		"parse error",
		"json unmarshal",
		"yaml unmarshal",
		"decode error",
		"validation error",
		"validation failed",
	}
	for _, pattern := range nonRetryablePatterns {
		if strings.Contains(errStr, pattern) {
			return false
		}
	}

	// Retryable errors - expanded list
	retryablePatterns := []string{
		"timeout",
		"connection refused",
		"connection reset",
		"connection closed",
		"connection aborted",
		"broken pipe",
		"temporary",
		"temporary failure",
		"unavailable",
		"server error",
		"internal error",
		"network",
		"network error",
		"network unreachable",
		"context deadline exceeded",
		"too many requests",
		"rate limit",
		"rate limited",
		"throttled",
		"service unavailable",
		"gateway timeout",
		"bad gateway",
		"502 bad gateway",
		"503 service unavailable",
		"504 gateway timeout",
		"connection timeout",
		"read timeout",
		"write timeout",
		"i/o timeout",
		"no route to host",
		"connection timed out",
		"dial tcp",
		"tls handshake timeout",
		"transport connection closing",
		"connection pool",
		"pool exhausted",
		"max retries exceeded",
		"circuit breaker open",
		"circuit breaker half-open",
		"retry",
		"backoff",
		"exponential backoff",
		"transient",
		"transient error",
		"temporary network",
		"network partition",
		"leader election",
		"etcd",
		"apiserver",
		"metrics server",
	}
	for _, pattern := range retryablePatterns {
		if strings.Contains(errStr, pattern) {
			return true
		}
	}

	// Check for specific Kubernetes error types
	if strings.Contains(errStr, "server could not find the requested resource") {
		// This might be a version mismatch or API not available - not retryable
		return false
	}

	if strings.Contains(errStr, "metrics.k8s.io") || strings.Contains(errStr, "metrics-server") {
		// Metrics server errors are often transient
		return true
	}

	// Default: classify unknown errors as non-retryable (fail-fast approach)
	// This prevents wasting resources on errors that are unlikely to succeed on retry
	// Only retry if we have strong evidence the error is transient
	utils.Log.WithError(err).Debug("Unknown error type - classifying as non-retryable (fail-fast)")
	return false
}
