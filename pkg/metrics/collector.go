package metrics

import (
	"context"
	"fmt"
	"math"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
)

// Database interface to avoid circular dependency with storage package
type Database interface {
	SavePodMetrics(ctx context.Context, m *PodMetrics) error
	SaveNodeMetrics(ctx context.Context, m *NodeMetrics) error
	GetRecentPodMetrics(ctx context.Context, podName, namespace string, limit int) ([]*PodMetrics, error)
}

type Collector struct {
	k8sClient *k8s.Client
	db        Database
}

func NewCollector(k8sClient *k8s.Client, db Database) *Collector {
	return &Collector{
		k8sClient: k8sClient,
		db:        db,
	}
}

// CollectAll collects metrics from all pods and nodes
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

// CollectPodMetrics collects metrics for all pods
func (c *Collector) CollectPodMetrics(ctx context.Context) error {
	pods, err := c.k8sClient.ListPods(ctx, "")
	if err != nil {
		return fmt.Errorf("failed to list pods: %w", err)
	}

	utils.Log.Infof("Collecting metrics for %d pods", len(pods.Items))

	for _, pod := range pods.Items {
		metrics, err := c.buildPodMetrics(ctx, &pod)
		if err != nil {
			utils.Log.WithError(err).WithField("pod", pod.Name).Warn("Failed to build metrics for pod")
			continue
		}

		if err := c.db.SavePodMetrics(ctx, metrics); err != nil {
			utils.Log.WithError(err).WithField("pod", pod.Name).Warn("Failed to save metrics for pod")
		}
	}

	return nil
}

// CollectNodeMetrics collects metrics for all nodes
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
			continue
		}

		if err := c.db.SaveNodeMetrics(ctx, metrics); err != nil {
			utils.Log.WithError(err).WithField("node", node.Name).Warn("Failed to save metrics for node")
		}
	}

	return nil
}

// buildPodMetrics constructs PodMetrics from a pod object
func (c *Collector) buildPodMetrics(ctx context.Context, pod *corev1.Pod) (*PodMetrics, error) {
	if len(pod.Spec.Containers) == 0 {
		return nil, fmt.Errorf("pod has no containers")
	}

	container := pod.Spec.Containers[0]
	containerStatus := getContainerStatus(pod, container.Name)

	// Get resource metrics from metrics server
	podMetrics, err := c.k8sClient.GetPodMetrics(ctx, pod.Namespace, pod.Name)
	if err != nil {
		utils.Log.WithError(err).Warn("Metrics server unavailable, using zero values")
	}
	var cpuUsage, memoryUsage float64
	var memoryBytes int64

	if err == nil && podMetrics != nil && len(podMetrics.Containers) > 0 {
		containerMetrics := podMetrics.Containers[0]
		cpuUsage = float64(containerMetrics.Usage.Cpu().MilliValue())
		memoryBytes = containerMetrics.Usage.Memory().Value()
		memoryUsage = float64(memoryBytes)
	}

	// Get resource limits
	cpuLimit := float64(container.Resources.Limits.Cpu().MilliValue())
	memoryLimit := container.Resources.Limits.Memory().Value()

	// Calculate utilizations
	cpuUtilization := 0.0
	if cpuLimit > 0 {
		cpuUtilization = (cpuUsage / cpuLimit) * 100
	}

	memoryUtilization := 0.0
	if memoryLimit > 0 {
		memoryUtilization = (memoryUsage / float64(memoryLimit)) * 100
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
	hasCrashLoop := lastStateReason == "CrashLoopBackOff"
	hasHighCPU := cpuUtilization > 80

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

		HasOOMKill:   hasOOMKill,
		HasCrashLoop: hasCrashLoop,
		HasHighCPU:   hasHighCPU,
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
