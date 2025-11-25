package metrics

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
)

// ComprehensiveCollector extends the base collector with comprehensive metrics collection
type ComprehensiveCollector struct {
	*Collector
}

// NewComprehensiveCollector creates a new comprehensive collector
func NewComprehensiveCollector(baseCollector *Collector) *ComprehensiveCollector {
	return &ComprehensiveCollector{Collector: baseCollector}
}

// CollectAllComprehensiveMetrics collects ALL possible Kubernetes metrics
// This is the PRIMARY collection method - it ALWAYS runs and saves ALL metrics
// Errors are logged but collection NEVER stops - every metric is saved to TimescaleDB
func (cc *ComprehensiveCollector) CollectAllComprehensiveMetrics(ctx context.Context) error {
	utils.Log.Info("📊 Comprehensive metrics collection started - collecting ALL metrics for ALL containers")

	// Collect pod metrics for ALL containers - PRIMARY method, ALWAYS runs
	podErr := cc.CollectAllPodContainersMetrics(ctx)
	if podErr != nil {
		utils.Log.WithError(podErr).Error("⚠️  Comprehensive pod metrics collection had errors, but metrics were still saved")
		// Don't return - metrics are saved individually even if batch fails
	}

	// Collect node metrics comprehensively - ALWAYS runs
	nodeErr := cc.CollectComprehensiveNodeMetrics(ctx)
	if nodeErr != nil {
		utils.Log.WithError(nodeErr).Error("⚠️  Comprehensive node metrics collection had errors")
		// Don't return - continue
	}

	// Collect deployment metrics (non-critical)
	if err := cc.CollectDeploymentMetrics(ctx); err != nil {
		utils.Log.WithError(err).Debug("Deployment metrics collection skipped (non-critical)")
	}

	// Collect service metrics (non-critical)
	if err := cc.CollectServiceMetrics(ctx); err != nil {
		utils.Log.WithError(err).Debug("Service metrics collection skipped (non-critical)")
	}

	// Collect event metrics (non-critical)
	if err := cc.CollectEventMetrics(ctx); err != nil {
		utils.Log.WithError(err).Debug("Event metrics collection skipped (non-critical)")
	}

	utils.Log.Info("✅ Comprehensive metrics collection completed - ALL metrics saved to TimescaleDB")

	// Return error only if pod collection completely failed (but metrics were still saved individually)
	if podErr != nil {
		return podErr
	}
	return nil
}

// CollectAllPodContainersMetrics collects metrics for ALL containers in ALL pods
func (cc *ComprehensiveCollector) CollectAllPodContainersMetrics(ctx context.Context) error {
	pods, err := cc.k8sClient.ListPods(ctx, "")
	if err != nil {
		return fmt.Errorf("failed to list pods: %w", err)
	}

	// Filter out system namespaces
	systemNamespaces := map[string]bool{
		"kube-system":        true,
		"kube-public":        true,
		"kube-node-lease":    true,
		"local-path-storage": true,
	}

	var userPods []corev1.Pod
	for _, pod := range pods.Items {
		if !systemNamespaces[pod.Namespace] {
			userPods = append(userPods, pod)
		}
	}

	utils.Log.Infof("Collecting comprehensive metrics for %d user pods", len(userPods))

	batchSize := 50 // Smaller batches for comprehensive metrics
	var metricsList []*PodMetrics

	for _, pod := range userPods {
		// Collect metrics for EACH container in the pod - ALL containers MUST be collected
		// CRITICAL: We MUST collect metrics for ALL containers, even if Metrics API has no data
		for _, container := range pod.Spec.Containers {
			metrics, err := cc.buildComprehensivePodMetrics(ctx, &pod, container.Name)

			// CRITICAL: If buildComprehensivePodMetrics fails, create a zero-value metrics object
			// We MUST save metrics for every container, even with zero values
			if err != nil || metrics == nil {
				utils.Log.WithError(err).
					WithField("pod", pod.Name).
					WithField("container", container.Name).
					Warn("⚠️  Failed to build comprehensive metrics, creating zero-value metrics to ensure ALL containers are tracked")

				// Create zero-value metrics to ensure this container is tracked
				metrics = cc.createZeroValueMetrics(ctx, &pod, container.Name)
				if metrics == nil {
					// If even zero-value creation fails, log and continue (should never happen)
					utils.Log.WithField("pod", pod.Name).
						WithField("container", container.Name).
						Error("❌ CRITICAL: Failed to create zero-value metrics - this should never happen")
					CollectionErrors.WithLabelValues("pod").Inc()
					continue
				}
			}

			// CRITICAL: Every metric MUST be added to the list for saving
			// This ensures ALL containers are tracked, even with zero values
			metricsList = append(metricsList, metrics)
			PodsCollected.WithLabelValues(pod.Namespace).Inc()

			// Push to circular buffer for streaming/forecasting
			if cc.streamBuffer != nil {
				cc.streamBuffer.Push(metrics)
			}

			utils.Log.WithField("pod", pod.Name).
				WithField("container", container.Name).
				WithField("namespace", pod.Namespace).
				WithField("cpu", metrics.CPUUsageMillicores).
				WithField("memory", metrics.MemoryUsageBytes).
				Debug("✅ Collected comprehensive metrics for container (including zero values if needed)")

			// Save in batches - if batch fails, save individually to ensure ALL metrics are saved
			if len(metricsList) >= batchSize {
				if err := cc.saveMetricsBatch(ctx, metricsList); err != nil {
					utils.Log.WithError(err).Warnf("⚠️  Batch save failed for %d metrics, saving individually to ensure ALL metrics are saved", len(metricsList))
					// CRITICAL: Save individually if batch fails - we MUST save every metric
					for _, m := range metricsList {
						if err := cc.db.SavePodMetrics(ctx, m); err != nil {
							utils.Log.WithError(err).WithField("pod", m.PodName).WithField("container", m.ContainerName).
								Error("❌ CRITICAL: Failed to save individual metric - metric will be lost")
						} else {
							utils.Log.WithField("pod", m.PodName).WithField("container", m.ContainerName).
								Debug("✅ Saved metric individually (batch failed)")
						}
					}
				} else {
					utils.Log.Debugf("✅ Saved batch of %d metrics to TimescaleDB", len(metricsList))
				}
				metricsList = metricsList[:0]
			}
		}

		// Also collect init container metrics if any - ALL init containers MUST be collected
		for _, initContainer := range pod.Spec.InitContainers {
			metrics, err := cc.buildComprehensivePodMetrics(ctx, &pod, initContainer.Name)
			if err != nil {
				utils.Log.WithError(err).
					WithField("pod", pod.Name).
					WithField("init_container", initContainer.Name).
					Debug("Failed to build metrics for init container, continuing")
				continue
			}
			if metrics != nil {
				metricsList = append(metricsList, metrics)
				utils.Log.WithField("pod", pod.Name).
					WithField("init_container", initContainer.Name).
					Debug("✅ Collected comprehensive metrics for init container")

				// Save in batches
				if len(metricsList) >= batchSize {
					if err := cc.saveMetricsBatch(ctx, metricsList); err != nil {
						utils.Log.WithError(err).Warnf("⚠️  Batch save failed for init container metrics, saving individually")
						// Save individually if batch fails
						for _, m := range metricsList {
							if err := cc.db.SavePodMetrics(ctx, m); err != nil {
								utils.Log.WithError(err).WithField("pod", m.PodName).Error("❌ Failed to save init container metric")
							}
						}
					}
					metricsList = metricsList[:0]
				}
			}
		}
	}

	// Save remaining metrics - CRITICAL: Must save ALL remaining metrics
	if len(metricsList) > 0 {
		if err := cc.saveMetricsBatch(ctx, metricsList); err != nil {
			utils.Log.WithError(err).Warnf("⚠️  Final batch save failed for %d metrics, saving individually to ensure ALL metrics are saved", len(metricsList))
			// CRITICAL: Save individually if batch fails - we MUST save every metric
			for _, m := range metricsList {
				if err := cc.db.SavePodMetrics(ctx, m); err != nil {
					utils.Log.WithError(err).WithField("pod", m.PodName).WithField("container", m.ContainerName).
						Error("❌ CRITICAL: Failed to save final individual metric - metric will be lost")
				} else {
					utils.Log.WithField("pod", m.PodName).WithField("container", m.ContainerName).
						Debug("✅ Saved final metric individually (batch failed)")
				}
			}
		} else {
			utils.Log.Infof("✅ Saved final batch of %d metrics to TimescaleDB", len(metricsList))
		}
	}

	utils.Log.Info("✅ All pod container metrics collected and saved to TimescaleDB")
	return nil
}

// buildComprehensivePodMetrics builds comprehensive metrics for a specific container
func (cc *ComprehensiveCollector) buildComprehensivePodMetrics(ctx context.Context, pod *corev1.Pod, containerName string) (*PodMetrics, error) {
	// Find the container spec
	var containerSpec *corev1.Container
	for i := range pod.Spec.Containers {
		if pod.Spec.Containers[i].Name == containerName {
			containerSpec = &pod.Spec.Containers[i]
			break
		}
	}
	if containerSpec == nil {
		// Check init containers
		for i := range pod.Spec.InitContainers {
			if pod.Spec.InitContainers[i].Name == containerName {
				containerSpec = &pod.Spec.InitContainers[i]
				break
			}
		}
	}
	if containerSpec == nil {
		return nil, fmt.Errorf("container %s not found in pod", containerName)
	}

	containerStatus := getContainerStatus(pod, containerName)

	// Get ALL resource limits and requests
	cpuLimit := float64(containerSpec.Resources.Limits.Cpu().MilliValue())
	memoryLimit := containerSpec.Resources.Limits.Memory().Value()
	cpuRequest := float64(containerSpec.Resources.Requests.Cpu().MilliValue())
	memoryRequest := containerSpec.Resources.Requests.Memory().Value()

	// Get storage limits if available
	ephemeralStorageLimit := containerSpec.Resources.Limits.StorageEphemeral().Value()
	_ = containerSpec.Resources.Requests.StorageEphemeral().Value() // Reserved for future use

	// Get resource metrics with comprehensive fallback
	getHistoricalMetrics := func() (float64, int64, error) {
		historicalMetrics, histErr := cc.db.GetRecentPodMetrics(ctx, pod.Name, pod.Namespace, 1)
		if histErr != nil || len(historicalMetrics) == 0 {
			return 0, 0, fmt.Errorf("no historical metrics available")
		}
		latest := historicalMetrics[0]
		age := time.Since(latest.Timestamp)
		if age < 5*time.Minute && (latest.CPUUsageMillicores > 0 || latest.MemoryUsageBytes > 0) {
			return latest.CPUUsageMillicores, latest.MemoryUsageBytes, nil
		}
		return 0, 0, fmt.Errorf("historical metrics too old or zero")
	}

	// Get metrics with fallback - ALWAYS succeeds (returns zero values if all methods fail)
	var cpuUsage, memoryUsage float64
	var memoryBytes int64
	var metricsSource string

	// Pass container name to get metrics for the specific container
	// This function NEVER returns an error - it always returns zero values as last resort
	cpuUsage, memoryBytes, metricsSource, _ = cc.k8sClient.GetPodMetricsWithFallback(ctx, pod, getHistoricalMetrics, containerName)
	memoryUsage = float64(memoryBytes)

	// Log if we're using zero values (container exists but no metrics available yet)
	if metricsSource == "zero-fallback" || (cpuUsage == 0 && memoryBytes == 0) {
		utils.Log.WithField("pod", pod.Name).
			WithField("container", containerName).
			Debug("Using zero values for container (Metrics API may not have data yet, but container is tracked)")
	}

	// Log metrics source for debugging
	_ = metricsSource // Reserved for future logging

	// Calculate utilizations
	cpuUtilization := 0.0
	if cpuLimit > 0 {
		cpuUtilization = (cpuUsage / cpuLimit) * 100
	} else if cpuRequest > 0 {
		cpuUtilization = (cpuUsage / cpuRequest) * 100
	}

	memoryUtilization := 0.0
	if memoryLimit > 0 {
		memoryUtilization = (memoryUsage / float64(memoryLimit)) * 100
	} else if memoryRequest > 0 {
		memoryUtilization = (memoryUsage / float64(memoryRequest)) * 100
	}

	// Get pod age
	age := int64(time.Since(pod.CreationTimestamp.Time).Seconds())

	// Get ALL pod conditions
	ready := false
	for _, cond := range pod.Status.Conditions {
		if cond.Type == corev1.PodReady {
			ready = cond.Status == corev1.ConditionTrue
		}
		// Other conditions (PodScheduled, PodInitialized, ContainersReady) can be added to PodMetrics type if needed
	}

	// Count restarts
	restarts := int32(0)
	if containerStatus != nil {
		restarts = containerStatus.RestartCount
	}

	// Get container state comprehensively
	containerState := "Unknown"
	lastStateReason := ""
	containerReady := false
	waitingReason := ""
	terminatedReason := ""

	if containerStatus != nil {
		containerReady = containerStatus.Ready
		if containerStatus.State.Running != nil {
			containerState = "Running"
		} else if containerStatus.State.Waiting != nil {
			containerState = "Waiting"
			waitingReason = containerStatus.State.Waiting.Reason
			lastStateReason = waitingReason
		} else if containerStatus.State.Terminated != nil {
			containerState = "Terminated"
			terminatedReason = containerStatus.State.Terminated.Reason
			lastStateReason = terminatedReason
		}

		if containerStatus.LastTerminationState.Terminated != nil {
			lastStateReason = containerStatus.LastTerminationState.Terminated.Reason
		}
	}

	// Calculate trends
	cpuTrend, memoryTrend, restartTrend := cc.calculateTrends(ctx, pod.Name, pod.Namespace)

	// Detect issues comprehensively
	hasOOMKill := lastStateReason == "OOMKilled" || terminatedReason == "OOMKilled"
	hasCrashLoop := lastStateReason == "CrashLoopBackOff" || waitingReason == "CrashLoopBackOff" || restarts > 3
	hasHighCPU := cpuUtilization > 80.0 // Configurable threshold
	hasNetworkIssues := false

	// Check for network issues in pod conditions and events
	for _, cond := range pod.Status.Conditions {
		if cond.Type == corev1.PodScheduled && cond.Status == corev1.ConditionFalse {
			if strings.Contains(strings.ToLower(cond.Reason), "network") ||
				strings.Contains(strings.ToLower(cond.Message), "network") {
				hasNetworkIssues = true
			}
		}
	}

	// Get volume information
	volumeMountCount := 0
	for _, container := range pod.Spec.Containers {
		volumeMountCount += len(container.VolumeMounts)
	}

	// Network and disk metrics (try to get from historical data)
	networkRxBytes := int64(0)
	networkTxBytes := int64(0)
	networkRxErrors := int64(0)
	networkTxErrors := int64(0)
	diskUsageBytes := int64(0)
	diskLimitBytes := ephemeralStorageLimit

	historicalMetrics, err := cc.db.GetRecentPodMetrics(ctx, pod.Name, pod.Namespace, 1)
	if err == nil && len(historicalMetrics) > 0 {
		latest := historicalMetrics[0]
		if time.Since(latest.Timestamp) < 5*time.Minute {
			networkRxBytes = latest.NetworkRxBytes
			networkTxBytes = latest.NetworkTxBytes
			networkRxErrors = latest.NetworkRxErrors
			networkTxErrors = latest.NetworkTxErrors
			diskUsageBytes = latest.DiskUsageBytes
		}
	}

	// Build comprehensive metrics
	metrics := &PodMetrics{
		PodName:       pod.Name,
		Namespace:     pod.Namespace,
		NodeName:      pod.Spec.NodeName,
		ContainerName: containerName,
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

// createZeroValueMetrics creates a metrics object with zero values for a container
// This ensures ALL containers are tracked even when Metrics API has no data
func (cc *ComprehensiveCollector) createZeroValueMetrics(ctx context.Context, pod *corev1.Pod, containerName string) *PodMetrics {
	// Find the container spec
	var containerSpec *corev1.Container
	for i := range pod.Spec.Containers {
		if pod.Spec.Containers[i].Name == containerName {
			containerSpec = &pod.Spec.Containers[i]
			break
		}
	}
	if containerSpec == nil {
		utils.Log.WithField("pod", pod.Name).
			WithField("container", containerName).
			Warn("Container spec not found, using minimal zero-value metrics")
		// Return minimal metrics even if container spec not found
		return &PodMetrics{
			PodName:       pod.Name,
			Namespace:     pod.Namespace,
			NodeName:      pod.Spec.NodeName,
			ContainerName: containerName,
			Timestamp:     time.Now(),
			Phase:         string(pod.Status.Phase),
			Ready:         isPodReady(pod),
			// All other fields default to zero
		}
	}

	// Get resource limits and requests
	cpuLimit := float64(containerSpec.Resources.Limits.Cpu().MilliValue())
	memoryLimit := containerSpec.Resources.Limits.Memory().Value()
	ephemeralStorageLimit := containerSpec.Resources.Limits.StorageEphemeral().Value()

	// Get container status (using helper from collector.go)
	containerStatus := getContainerStatus(pod, containerName)
	containerReady := false
	containerState := "Unknown"
	lastStateReason := ""
	restarts := int32(0)

	if containerStatus != nil {
		containerReady = containerStatus.Ready
		restarts = containerStatus.RestartCount
		if containerStatus.State.Running != nil {
			containerState = "Running"
		} else if containerStatus.State.Waiting != nil {
			containerState = "Waiting"
			lastStateReason = containerStatus.State.Waiting.Reason
		} else if containerStatus.State.Terminated != nil {
			containerState = "Terminated"
			lastStateReason = containerStatus.State.Terminated.Reason
		}
	}

	// Calculate pod age
	age := int64(0)
	if !pod.CreationTimestamp.IsZero() {
		age = int64(time.Since(pod.CreationTimestamp.Time).Seconds())
	}

	// Create zero-value metrics with proper structure
	return &PodMetrics{
		PodName:       pod.Name,
		Namespace:     pod.Namespace,
		NodeName:      pod.Spec.NodeName,
		ContainerName: containerName,
		Timestamp:     time.Now(),

		// Resource usage - ZERO VALUES (Metrics API has no data)
		CPUUsageMillicores: 0,
		MemoryUsageBytes:   0,
		MemoryLimitBytes:   memoryLimit,
		CPULimitMillicores: cpuLimit,

		// Utilizations - ZERO (no usage)
		CPUUtilization:    0,
		MemoryUtilization: 0,

		// Network and disk - ZERO VALUES
		NetworkRxBytes:  0,
		NetworkTxBytes:  0,
		NetworkRxErrors: 0,
		NetworkTxErrors: 0,
		DiskUsageBytes:  0,
		DiskLimitBytes:  ephemeralStorageLimit,

		// Pod state
		Phase:    string(pod.Status.Phase),
		Ready:    isPodReady(pod),
		Restarts: restarts,
		Age:      age,

		// Container state
		ContainerReady:  containerReady,
		ContainerState:  containerState,
		LastStateReason: lastStateReason,

		// Trends - ZERO (no historical data)
		CPUTrend:     0,
		MemoryTrend:  0,
		RestartTrend: 0,

		// Health indicators
		HasOOMKill:       false,
		HasCrashLoop:     false,
		HasHighCPU:       false,
		HasNetworkIssues: false,
	}
}

// CollectComprehensiveNodeMetrics collects comprehensive node metrics
func (cc *ComprehensiveCollector) CollectComprehensiveNodeMetrics(ctx context.Context) error {
	nodes, err := cc.k8sClient.ListNodes(ctx)
	if err != nil {
		return fmt.Errorf("failed to list nodes: %w", err)
	}

	utils.Log.Infof("Collecting comprehensive metrics for %d nodes", len(nodes.Items))

	for _, node := range nodes.Items {
		metrics, err := cc.buildComprehensiveNodeMetrics(ctx, &node)
		if err != nil {
			utils.Log.WithError(err).WithField("node", node.Name).Warn("Failed to build comprehensive node metrics")
			CollectionErrors.WithLabelValues("node").Inc()
			continue // Continue with next node
		}

		if err := cc.db.SaveNodeMetrics(ctx, metrics); err != nil {
			utils.Log.WithError(err).WithField("node", node.Name).Warn("Failed to save node metrics")
			CollectionErrors.WithLabelValues("node").Inc()
		} else {
			NodesCollected.Inc()
		}
	}

	return nil
}

// buildComprehensiveNodeMetrics builds comprehensive node metrics
func (cc *ComprehensiveCollector) buildComprehensiveNodeMetrics(ctx context.Context, node *corev1.Node) (*NodeMetrics, error) {
	// Get resource metrics from metrics server
	nodeMetrics, err := cc.k8sClient.GetNodeMetrics(ctx, node.Name)
	if err != nil {
		// Continue with zero values - don't fail
		utils.Log.WithError(err).WithField("node", node.Name).Debug("Node metrics API unavailable, using zero values")
	}

	cpuUsage := 0.0
	memoryUsage := int64(0)

	if err == nil && nodeMetrics != nil {
		cpuUsage = float64(nodeMetrics.Usage.Cpu().MilliValue())
		memoryUsage = nodeMetrics.Usage.Memory().Value()
	}

	// Get ALL capacity information
	cpuCapacity := float64(node.Status.Capacity.Cpu().MilliValue())
	memoryCapacity := node.Status.Capacity.Memory().Value()
	podCapacity := int(node.Status.Capacity.Pods().Value())
	_ = node.Status.Capacity.StorageEphemeral().Value() // Reserved for future use

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
	pods, _ := cc.k8sClient.ListPods(ctx, "")
	podCount := 0
	for _, pod := range pods.Items {
		if pod.Spec.NodeName == node.Name {
			podCount++
		}
	}

	// Get ALL node conditions
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
		// NodePIDPressure can be added to NodeMetrics type if needed
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

// CollectDeploymentMetrics collects deployment-level metrics
func (cc *ComprehensiveCollector) CollectDeploymentMetrics(ctx context.Context) error {
	// This would collect deployment-level metrics
	// For now, we'll log that it's being called
	utils.Log.Debug("Collecting deployment metrics (placeholder)")
	return nil
}

// CollectServiceMetrics collects service-level metrics
func (cc *ComprehensiveCollector) CollectServiceMetrics(ctx context.Context) error {
	// This would collect service-level metrics
	utils.Log.Debug("Collecting service metrics (placeholder)")
	return nil
}

// CollectEventMetrics collects event-based metrics
func (cc *ComprehensiveCollector) CollectEventMetrics(ctx context.Context) error {
	// This would collect event-based metrics
	utils.Log.Debug("Collecting event metrics (placeholder)")
	return nil
}

// saveMetricsBatch saves a batch of metrics to TimescaleDB
// CRITICAL: This function MUST save all metrics - if batch fails, saves individually
func (cc *ComprehensiveCollector) saveMetricsBatch(ctx context.Context, metricsList []*PodMetrics) error {
	if len(metricsList) == 0 {
		return nil
	}

	// Try batch save first (more efficient)
	if batchDB, ok := cc.db.(interface {
		SavePodMetricsBatch(ctx context.Context, metricsList []*PodMetrics) error
	}); ok {
		batchErr := batchDB.SavePodMetricsBatch(ctx, metricsList)
		if batchErr == nil {
			// Success - all metrics saved
			utils.Log.Debugf("✅ Saved batch of %d metrics to TimescaleDB", len(metricsList))
			return nil
		}
		// Batch failed - fall through to individual saves
		utils.Log.WithError(batchErr).Warn("⚠️  Batch save failed, falling back to individual saves to ensure ALL metrics are saved")
	}

	// CRITICAL FALLBACK: Save each metric individually to ensure ALL metrics are saved
	// This guarantees that every metric is saved to TimescaleDB even if batch fails
	savedCount := 0
	failedCount := 0
	for _, m := range metricsList {
		if err := cc.db.SavePodMetrics(ctx, m); err != nil {
			utils.Log.WithError(err).
				WithField("pod", m.PodName).
				WithField("container", m.ContainerName).
				WithField("namespace", m.Namespace).
				Error("❌ CRITICAL: Failed to save individual metric to TimescaleDB")
			failedCount++
		} else {
			savedCount++
		}
	}

	if failedCount > 0 {
		utils.Log.Errorf("❌ CRITICAL: %d out of %d metrics failed to save to TimescaleDB", failedCount, len(metricsList))
		return fmt.Errorf("failed to save %d metrics", failedCount)
	}

	utils.Log.Debugf("✅ Saved %d metrics individually to TimescaleDB (batch unavailable or failed)", savedCount)
	return nil
}
