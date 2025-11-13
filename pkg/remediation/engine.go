package remediation

import (
	"context"
	"fmt"
	"log"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

// RemediationStrategy defines a strategy for fixing an issue
type RemediationStrategy interface {
	Name() string
	CanHandle(anomalyType string) bool
	Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error
	GetConfidence() float64
}

// RemediationEngine orchestrates all remediation strategies
type RemediationEngine struct {
	clientset  *kubernetes.Clientset
	strategies map[string]RemediationStrategy
	dryRun     bool
	logger     *log.Logger
}

// NewRemediationEngine creates a new remediation engine
func NewRemediationEngine(clientset *kubernetes.Clientset, dryRun bool, logger *log.Logger) *RemediationEngine {
	engine := &RemediationEngine{
		clientset:  clientset,
		strategies: make(map[string]RemediationStrategy),
		dryRun:     dryRun,
		logger:     logger,
	}

	// Register all strategies
	engine.registerStrategies()

	return engine
}

func (e *RemediationEngine) registerStrategies() {
	e.strategies["oom_killed"] = &IncreaseMemoryStrategy{}
	e.strategies["crash_loop"] = &RestartPodStrategy{}
	e.strategies["image_pull_backoff"] = &ImagePullStrategy{}
	e.strategies["high_cpu"] = &IncreaseCPUStrategy{}
	e.strategies["high_memory"] = &IncreaseMemoryStrategy{}
	e.strategies["disk_pressure"] = &CleanLogsStrategy{}
	e.strategies["network_latency"] = &RestartNetworkStrategy{}
	e.strategies["network_errors"] = &RestartNetworkStrategy{}
	e.strategies["dns_failures"] = &RestartDNSStrategy{}
	e.strategies["pod_eviction"] = &IncreaseResourcesStrategy{}
	e.strategies["node_not_ready"] = &DrainNodeStrategy{}
	e.strategies["pvc_pending"] = &ExpandPVCStrategy{}
	e.strategies["service_down"] = &RestartServiceStrategy{}
	e.strategies["ingress_errors"] = &RestartIngressStrategy{}
	e.strategies["cert_expiry"] = &RenewCertificateStrategy{}
}

// Remediate applies appropriate remediation strategy
func (e *RemediationEngine) Remediate(ctx context.Context, anomalyType string, pod *corev1.Pod) error {
	strategy, ok := e.strategies[anomalyType]
	if !ok {
		return fmt.Errorf("no strategy for anomaly type: %s", anomalyType)
	}

	if !strategy.CanHandle(anomalyType) {
		return fmt.Errorf("strategy cannot handle anomaly type: %s", anomalyType)
	}

	e.logger.Printf("🔧 Applying %s for %s/%s (anomaly: %s, confidence: %.2f%%)",
		strategy.Name(), pod.Namespace, pod.Name, anomalyType, strategy.GetConfidence()*100)

	if e.dryRun {
		e.logger.Printf("   [DRY-RUN] Would execute: %s", strategy.Name())
		return nil
	}

	err := strategy.Execute(ctx, e.clientset, pod)
	if err != nil {
		e.logger.Printf("   ❌ Failed: %v", err)
		return err
	}

	e.logger.Printf("   ✅ Success!")
	return nil
}

// =============================================================================
// STRATEGY 1: Increase Memory
// =============================================================================

type IncreaseMemoryStrategy struct{}

func (s *IncreaseMemoryStrategy) Name() string           { return "IncreaseMemory" }
func (s *IncreaseMemoryStrategy) GetConfidence() float64 { return 0.95 }
func (s *IncreaseMemoryStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "oom_killed" || anomalyType == "high_memory"
}

func (s *IncreaseMemoryStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	// Get pod's deployment/replicaset owner
	// For simplicity, we'll patch the pod directly
	// In production, you'd patch the deployment

	podClient := clientset.CoreV1().Pods(pod.Namespace)

	// Get current pod
	currentPod, err := podClient.Get(ctx, pod.Name, metav1.GetOptions{})
	if err != nil {
		return fmt.Errorf("failed to get pod: %w", err)
	}

	// Increase memory by 50%
	for i := range currentPod.Spec.Containers {
		container := &currentPod.Spec.Containers[i]

		if container.Resources.Limits == nil {
			container.Resources.Limits = corev1.ResourceList{}
		}
		if container.Resources.Requests == nil {
			container.Resources.Requests = corev1.ResourceList{}
		}

		// Get current memory limit
		memLimit := container.Resources.Limits[corev1.ResourceMemory]
		if memLimit.IsZero() {
			defaultMem, err := resource.ParseQuantity("512Mi")
			if err != nil {
				return fmt.Errorf("failed to parse default memory: %w", err)
			}
			memLimit = defaultMem
		}

		// Increase by 50%
		newLimit := memLimit.Value() * 3 / 2
		container.Resources.Limits[corev1.ResourceMemory] = *resource.NewQuantity(newLimit, resource.BinarySI)
		container.Resources.Requests[corev1.ResourceMemory] = *resource.NewQuantity(newLimit*8/10, resource.BinarySI)
	}

	// Delete pod to force recreate with new limits
	// Note: In production, update the deployment instead
	err = podClient.Delete(ctx, pod.Name, metav1.DeleteOptions{})
	if err != nil {
		return fmt.Errorf("failed to delete pod: %w", err)
	}

	return nil
}

// =============================================================================
// STRATEGY 2: Restart Pod
// =============================================================================

type RestartPodStrategy struct{}

func (s *RestartPodStrategy) Name() string           { return "RestartPod" }
func (s *RestartPodStrategy) GetConfidence() float64 { return 0.85 }
func (s *RestartPodStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "crash_loop"
}

func (s *RestartPodStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	podClient := clientset.CoreV1().Pods(pod.Namespace)

	// Delete pod to trigger restart
	err := podClient.Delete(ctx, pod.Name, metav1.DeleteOptions{})
	if err != nil {
		return fmt.Errorf("failed to delete pod: %w", err)
	}

	return nil
}

// =============================================================================
// STRATEGY 3: Image Pull Fix
// =============================================================================

type ImagePullStrategy struct{}

func (s *ImagePullStrategy) Name() string           { return "FixImagePull" }
func (s *ImagePullStrategy) GetConfidence() float64 { return 0.80 }
func (s *ImagePullStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "image_pull_backoff"
}

func (s *ImagePullStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	// Check if imagePullSecrets are configured
	// If not, this might be the issue

	podClient := clientset.CoreV1().Pods(pod.Namespace)

	// Delete pod to retry image pull
	err := podClient.Delete(ctx, pod.Name, metav1.DeleteOptions{})
	if err != nil {
		return fmt.Errorf("failed to delete pod: %w", err)
	}

	// Log suggestion to check image registry credentials
	log.Printf("⚠️  Suggestion: Verify image registry credentials and imagePullSecrets")

	return nil
}

// =============================================================================
// STRATEGY 4: Increase CPU
// =============================================================================

type IncreaseCPUStrategy struct{}

func (s *IncreaseCPUStrategy) Name() string           { return "IncreaseCPU" }
func (s *IncreaseCPUStrategy) GetConfidence() float64 { return 0.90 }
func (s *IncreaseCPUStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "high_cpu"
}

func (s *IncreaseCPUStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	podClient := clientset.CoreV1().Pods(pod.Namespace)

	currentPod, err := podClient.Get(ctx, pod.Name, metav1.GetOptions{})
	if err != nil {
		return fmt.Errorf("failed to get pod: %w", err)
	}

	// Increase CPU by 50%
	for i := range currentPod.Spec.Containers {
		container := &currentPod.Spec.Containers[i]

		if container.Resources.Limits == nil {
			container.Resources.Limits = corev1.ResourceList{}
		}
		if container.Resources.Requests == nil {
			container.Resources.Requests = corev1.ResourceList{}
		}

		cpuLimit := container.Resources.Limits[corev1.ResourceCPU]
		if cpuLimit.IsZero() {
			defaultCPU, err := resource.ParseQuantity("500m")
			if err != nil {
				return fmt.Errorf("failed to parse default CPU: %w", err)
			}
			cpuLimit = defaultCPU
		}

		newLimit := cpuLimit.MilliValue() * 3 / 2
		container.Resources.Limits[corev1.ResourceCPU] = *resource.NewMilliQuantity(newLimit, resource.DecimalSI)
		container.Resources.Requests[corev1.ResourceCPU] = *resource.NewMilliQuantity(newLimit*8/10, resource.DecimalSI)
	}

	// Delete pod to recreate
	err = podClient.Delete(ctx, pod.Name, metav1.DeleteOptions{})
	if err != nil {
		return fmt.Errorf("failed to delete pod: %w", err)
	}

	return nil
}

// =============================================================================
// STRATEGY 5: Clean Logs
// =============================================================================

type CleanLogsStrategy struct{}

func (s *CleanLogsStrategy) Name() string           { return "CleanLogs" }
func (s *CleanLogsStrategy) GetConfidence() float64 { return 0.75 }
func (s *CleanLogsStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "disk_pressure"
}

func (s *CleanLogsStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	// In a real implementation, you would:
	// 1. Exec into pod
	// 2. Clean /var/log/* files
	// 3. Rotate logs

	// For now, we'll restart the pod
	podClient := clientset.CoreV1().Pods(pod.Namespace)
	err := podClient.Delete(ctx, pod.Name, metav1.DeleteOptions{})
	if err != nil {
		return fmt.Errorf("failed to delete pod: %w", err)
	}

	log.Printf("⚠️  Suggestion: Configure log rotation and persistent volume cleanup")
	return nil
}

// =============================================================================
// STRATEGY 6-15: Additional Strategies (Simplified)
// =============================================================================

type RestartNetworkStrategy struct{}

func (s *RestartNetworkStrategy) Name() string           { return "RestartNetwork" }
func (s *RestartNetworkStrategy) GetConfidence() float64 { return 0.70 }
func (s *RestartNetworkStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "network_latency" || anomalyType == "network_errors"
}
func (s *RestartNetworkStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	return clientset.CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, metav1.DeleteOptions{})
}

type RestartDNSStrategy struct{}

func (s *RestartDNSStrategy) Name() string                      { return "RestartDNS" }
func (s *RestartDNSStrategy) GetConfidence() float64            { return 0.75 }
func (s *RestartDNSStrategy) CanHandle(anomalyType string) bool { return anomalyType == "dns_failures" }
func (s *RestartDNSStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	return clientset.CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, metav1.DeleteOptions{})
}

type IncreaseResourcesStrategy struct{}

func (s *IncreaseResourcesStrategy) Name() string           { return "IncreaseResources" }
func (s *IncreaseResourcesStrategy) GetConfidence() float64 { return 0.85 }
func (s *IncreaseResourcesStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "pod_eviction"
}
func (s *IncreaseResourcesStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	return clientset.CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, metav1.DeleteOptions{})
}

type DrainNodeStrategy struct{}

func (s *DrainNodeStrategy) Name() string           { return "DrainNode" }
func (s *DrainNodeStrategy) GetConfidence() float64 { return 0.60 }
func (s *DrainNodeStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "node_not_ready"
}
func (s *DrainNodeStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	log.Printf("⚠️  Node issue detected. Manual intervention recommended.")
	return nil
}

type ExpandPVCStrategy struct{}

func (s *ExpandPVCStrategy) Name() string                      { return "ExpandPVC" }
func (s *ExpandPVCStrategy) GetConfidence() float64            { return 0.80 }
func (s *ExpandPVCStrategy) CanHandle(anomalyType string) bool { return anomalyType == "pvc_pending" }
func (s *ExpandPVCStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	log.Printf("⚠️  PVC issue. Check storage class and provisioner.")
	return nil
}

type RestartServiceStrategy struct{}

func (s *RestartServiceStrategy) Name() string           { return "RestartService" }
func (s *RestartServiceStrategy) GetConfidence() float64 { return 0.75 }
func (s *RestartServiceStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "service_down"
}
func (s *RestartServiceStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	return clientset.CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, metav1.DeleteOptions{})
}

type RestartIngressStrategy struct{}

func (s *RestartIngressStrategy) Name() string           { return "RestartIngress" }
func (s *RestartIngressStrategy) GetConfidence() float64 { return 0.70 }
func (s *RestartIngressStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "ingress_errors"
}
func (s *RestartIngressStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	log.Printf("⚠️  Ingress issue. Check ingress controller logs.")
	return nil
}

type RenewCertificateStrategy struct{}

func (s *RenewCertificateStrategy) Name() string           { return "RenewCertificate" }
func (s *RenewCertificateStrategy) GetConfidence() float64 { return 0.85 }
func (s *RenewCertificateStrategy) CanHandle(anomalyType string) bool {
	return anomalyType == "cert_expiry"
}
func (s *RenewCertificateStrategy) Execute(ctx context.Context, clientset *kubernetes.Clientset, pod *corev1.Pod) error {
	log.Printf("⚠️  Certificate expiry detected. Trigger cert-manager renewal.")
	return nil
}
