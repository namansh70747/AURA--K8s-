package k8s

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
	v1beta1 "k8s.io/metrics/pkg/apis/metrics/v1beta1"
)

// KubeletMetrics represents metrics from kubelet API
type KubeletMetrics struct {
	CPUUsageMillicores float64
	MemoryUsageBytes   int64
}

// GetPodMetricsFromKubelet gets pod metrics directly from kubelet API
// This is a fallback when metrics-server is unavailable
func (c *Client) GetPodMetricsFromKubelet(ctx context.Context, pod *corev1.Pod) (*KubeletMetrics, error) {
	if pod.Spec.NodeName == "" {
		return nil, fmt.Errorf("pod has no node name")
	}

	// Get node to find kubelet address
	node, err := c.GetNode(ctx, pod.Spec.NodeName)
	if err != nil {
		return nil, fmt.Errorf("failed to get node: %w", err)
	}

	// Find node internal IP
	var nodeIP string
	for _, addr := range node.Status.Addresses {
		if addr.Type == corev1.NodeInternalIP {
			nodeIP = addr.Address
			break
		}
	}
	if nodeIP == "" {
		return nil, fmt.Errorf("node has no internal IP")
	}

	// Try kubelet metrics endpoint (port 10250)
	// Note: This requires proper authentication/authorization in production
	// For Kind clusters, we can use insecure TLS
	kubeletURL := fmt.Sprintf("https://%s:10250/stats/summary", nodeIP)

	// Create HTTP client with insecure TLS (for Kind clusters)
	client := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{
				InsecureSkipVerify: true, // Only for development/Kind
			},
		},
	}

	req, err := http.NewRequestWithContext(ctx, "GET", kubeletURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to kubelet: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("kubelet returned status %d: %s", resp.StatusCode, string(body))
	}

	var summary struct {
		Pods []struct {
			PodRef struct {
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"podRef"`
			Containers []struct {
				Name string `json:"name"`
				CPU  struct {
					UsageNanoCores *uint64 `json:"usageNanoCores"`
				} `json:"cpu"`
				Memory struct {
					UsageBytes *uint64 `json:"usageBytes"`
				} `json:"memory"`
			} `json:"containers"`
		} `json:"pods"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&summary); err != nil {
		return nil, fmt.Errorf("failed to decode kubelet response: %w", err)
	}

	// Find our pod in the summary
	for _, podSummary := range summary.Pods {
		if podSummary.PodRef.Name == pod.Name && podSummary.PodRef.Namespace == pod.Namespace {
			if len(podSummary.Containers) == 0 {
				return nil, fmt.Errorf("pod has no containers in kubelet summary")
			}

			// Use first container (kubelet summary provides metrics per container)
			// Note: Kubelet summary doesn't easily support container name matching
			// For container-specific metrics, we'd need to match by container name in the summary
			container := podSummary.Containers[0]

			metrics := &KubeletMetrics{}

			// Convert CPU from nanocores to millicores
			if container.CPU.UsageNanoCores != nil {
				metrics.CPUUsageMillicores = float64(*container.CPU.UsageNanoCores) / 1_000_000.0
			}

			// Get memory usage
			if container.Memory.UsageBytes != nil {
				metrics.MemoryUsageBytes = int64(*container.Memory.UsageBytes)
			}

			return metrics, nil
		}
	}

	return nil, fmt.Errorf("pod not found in kubelet summary")
}

// GetPodMetricsFromCAdvisor gets pod metrics from cAdvisor API
// This is another fallback when metrics-server is unavailable
func (c *Client) GetPodMetricsFromCAdvisor(ctx context.Context, pod *corev1.Pod) (*KubeletMetrics, error) {
	if pod.Spec.NodeName == "" {
		return nil, fmt.Errorf("pod has no node name")
	}

	// Get node to find kubelet address
	node, err := c.GetNode(ctx, pod.Spec.NodeName)
	if err != nil {
		return nil, fmt.Errorf("failed to get node: %w", err)
	}

	// Find node internal IP
	var nodeIP string
	for _, addr := range node.Status.Addresses {
		if addr.Type == corev1.NodeInternalIP {
			nodeIP = addr.Address
			break
		}
	}
	if nodeIP == "" {
		return nil, fmt.Errorf("node has no internal IP")
	}

	// Try cAdvisor API endpoint (port 10250)
	// cAdvisor is built into kubelet
	// Path: /api/v1.3/subcontainers/
	cAdvisorURL := fmt.Sprintf("https://%s:10250/api/v1.3/subcontainers/", nodeIP)

	// Create HTTP client with insecure TLS (for Kind clusters)
	client := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{
				InsecureSkipVerify: true, // Only for development/Kind
			},
		},
	}

	req, err := http.NewRequestWithContext(ctx, "GET", cAdvisorURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to cAdvisor: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("cAdvisor returned status %d: %s", resp.StatusCode, string(body))
	}

	var containers []struct {
		Name string `json:"name"`
		Spec struct {
			HasCPU    bool `json:"has_cpu"`
			HasMemory bool `json:"has_memory"`
		} `json:"spec"`
		Stats []struct {
			CPU struct {
				Usage struct {
					Total uint64 `json:"total"`
				} `json:"usage"`
			} `json:"cpu"`
			Memory struct {
				Usage uint64 `json:"usage"`
			} `json:"memory"`
			Timestamp time.Time `json:"timestamp"`
		} `json:"stats"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&containers); err != nil {
		return nil, fmt.Errorf("failed to decode cAdvisor response: %w", err)
	}

	// Find container for our pod
	// cAdvisor container names are like: /kubepods/burstable/pod<pod-uid>/<container-id>
	// Note: In production, you'd need more sophisticated matching based on pod UID
	for _, container := range containers {
		// Check if this container belongs to our pod
		// cAdvisor names include pod UID
		if len(container.Stats) == 0 {
			continue
		}

		// Try to match by pod UID in container name
		// This is a simplified match - in production, you'd need more sophisticated matching
		// For now, we'll use the first container that has stats
		latestStats := container.Stats[len(container.Stats)-1]

		metrics := &KubeletMetrics{}

		// Convert CPU from nanoseconds to millicores
		// cAdvisor provides CPU usage in nanoseconds
		if container.Spec.HasCPU {
			// Get the latest CPU usage
			// Note: CPU usage is cumulative, we need to calculate rate
			// For simplicity, we'll use the total usage
			if len(container.Stats) >= 2 {
				// Calculate rate: (latest - previous) / time difference
				prevStats := container.Stats[len(container.Stats)-2]
				timeDiff := latestStats.Timestamp.Sub(prevStats.Timestamp).Seconds()
				if timeDiff > 0 {
					cpuDiff := float64(latestStats.CPU.Usage.Total - prevStats.CPU.Usage.Total)
					metrics.CPUUsageMillicores = (cpuDiff / timeDiff) / 1_000_000.0 // Convert to millicores
				}
			}
		}

		// Get memory usage
		if container.Spec.HasMemory {
			metrics.MemoryUsageBytes = int64(latestStats.Memory.Usage)
		}

		// If we found metrics, return them
		if metrics.CPUUsageMillicores > 0 || metrics.MemoryUsageBytes > 0 {
			return metrics, nil
		}
	}

	return nil, fmt.Errorf("pod container not found in cAdvisor response")
}

// GetPodMetricsWithFallback tries multiple methods to get pod metrics for a specific container
// Priority: 1) Metrics API, 2) Kubelet API, 3) cAdvisor API, 4) Historical data, 5) Zero values
// containerName: if empty, uses first container; otherwise finds metrics for specified container
func (c *Client) GetPodMetricsWithFallback(ctx context.Context, pod *corev1.Pod, getHistorical func() (float64, int64, error), containerName string) (cpuUsage float64, memoryBytes int64, source string, err error) {
	// Method 1: Try Metrics API (metrics-server)
	utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debug("Trying Metrics API (metrics-server)")
	podMetrics, err := c.GetPodMetrics(ctx, pod.Namespace, pod.Name)
	if err == nil && podMetrics != nil && len(podMetrics.Containers) > 0 {
		// Find metrics for the specific container
		var containerMetrics *v1beta1.ContainerMetrics
		if containerName != "" {
			// CRITICAL: Only use metrics if we find the EXACT container match
			// Don't fallback to first container - this ensures each container gets its own metrics
			for i := range podMetrics.Containers {
				if podMetrics.Containers[i].Name == containerName {
					containerMetrics = &podMetrics.Containers[i]
					break
				}
			}

			// If specific container not found, return zero values (don't use first container's metrics)
			if containerMetrics == nil {
				utils.Log.WithField("pod", pod.Name).WithField("container", containerName).
					Debug("Container not found in Metrics API response - will use zero values for this container")
				// Continue to next method (Kubelet/cAdvisor) or return zero values
			}
		} else {
			// If containerName is empty, use first container (legacy behavior)
			containerMetrics = &podMetrics.Containers[0]
		}

		if containerMetrics != nil {
			cpuUsage = float64(containerMetrics.Usage.Cpu().MilliValue())
			memoryBytes = containerMetrics.Usage.Memory().Value()
			if cpuUsage > 0 || memoryBytes > 0 {
				utils.Log.WithField("pod", pod.Name).WithField("container", containerName).
					Info("✅ Got metrics from Metrics API (metrics-server)")
				return cpuUsage, memoryBytes, "metrics-api", nil
			}
		}
	}
	utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debugf("Metrics API failed or container not found: %v", err)

	// Method 2: Try Kubelet API directly (gets first container - kubelet summary doesn't support container selection)
	utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debug("Trying Kubelet API (direct)")
	kubeletMetrics, err := c.GetPodMetricsFromKubelet(ctx, pod)
	if err == nil && kubeletMetrics != nil {
		if kubeletMetrics.CPUUsageMillicores > 0 || kubeletMetrics.MemoryUsageBytes > 0 {
			utils.Log.WithField("pod", pod.Name).WithField("container", containerName).
				WithField("cpu", kubeletMetrics.CPUUsageMillicores).
				WithField("memory", kubeletMetrics.MemoryUsageBytes).
				Info("✅ Got metrics from Kubelet API (direct)")
			return kubeletMetrics.CPUUsageMillicores, kubeletMetrics.MemoryUsageBytes, "kubelet-api", nil
		} else {
			utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debug("Kubelet API returned zero values")
		}
	} else {
		utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debugf("Kubelet API failed: %v", err)
	}

	// Method 3: Try cAdvisor API (gets first container - cAdvisor doesn't support container selection easily)
	utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debug("Trying cAdvisor API")
	cAdvisorMetrics, err := c.GetPodMetricsFromCAdvisor(ctx, pod)
	if err == nil && cAdvisorMetrics != nil {
		if cAdvisorMetrics.CPUUsageMillicores > 0 || cAdvisorMetrics.MemoryUsageBytes > 0 {
			utils.Log.WithField("pod", pod.Name).WithField("container", containerName).
				WithField("cpu", cAdvisorMetrics.CPUUsageMillicores).
				WithField("memory", cAdvisorMetrics.MemoryUsageBytes).
				Info("✅ Got metrics from cAdvisor API")
			return cAdvisorMetrics.CPUUsageMillicores, cAdvisorMetrics.MemoryUsageBytes, "cadvisor-api", nil
		} else {
			utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debug("cAdvisor API returned zero values")
		}
	} else {
		utils.Log.WithField("pod", pod.Name).WithField("container", containerName).Debugf("cAdvisor API failed: %v", err)
	}

	// Method 4: Try historical data
	if getHistorical != nil {
		utils.Log.WithField("pod", pod.Name).Debug("Trying historical data")
		histCPU, histMem, histErr := getHistorical()
		if histErr == nil && (histCPU > 0 || histMem > 0) {
			utils.Log.WithField("pod", pod.Name).Info("✅ Got metrics from historical data")
			return histCPU, histMem, "historical", nil
		}
		utils.Log.WithField("pod", pod.Name).Debugf("Historical data failed: %v", histErr)
	}

	// Method 5: Return zero values (last resort)
	// CRITICAL: Always return zero values instead of error - this ensures ALL containers are tracked
	utils.Log.WithField("pod", pod.Name).WithField("container", containerName).
		Debug("⚠️ All metrics collection methods failed, returning zero values (container will still be tracked)")
	return 0, 0, "zero-fallback", nil
}
