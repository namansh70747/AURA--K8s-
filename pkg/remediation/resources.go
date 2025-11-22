package remediation

import (
	"fmt"

	"github.com/namansh70747/aura-k8s/pkg/config"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"github.com/sirupsen/logrus"
)

// updateContainerResources updates CPU and memory resources for containers.
// This function extracts the common logic used in both deployment and statefulset operations.
//
// Parameters:
//   - containers: List of containers to update resources for
//   - resourceType: Type of resource to update ("memory" or "cpu")
//   - factor: Multiplication factor for resource limits (0.1 to 5.0, will be clamped)
//   - defaultMemory: Default memory value if container has no limit (e.g., "512Mi")
//   - defaultCPU: Default CPU value if container has no limit (e.g., "500m")
//   - logger: Logger instance for logging operations
//
// Returns:
//   - error: Returns error if resourceType is unsupported or resource parsing fails
//
// The function validates factor bounds (0.1 to 5.0) and resource limits:
//   - Memory: Maximum 512Gi
//   - CPU: Maximum 128 cores
func updateContainerResources(
	containers []corev1.Container,
	resourceType string, // "memory" or "cpu"
	factor float64,
	defaultMemory string,
	defaultCPU string,
	logger *logrus.Logger,
) error {
	// Validate factor is reasonable (prevent excessive resource requests)
	const maxFactor = 5.0  // Maximum 5x increase
	const minFactor = 0.1  // Minimum 10% of original
	if factor > maxFactor {
		logger.Warnf("Factor %.2f exceeds maximum %.2f, capping at maximum", factor, maxFactor)
		factor = maxFactor
	}
	if factor < minFactor {
		logger.Warnf("Factor %.2f below minimum %.2f, using minimum", factor, minFactor)
		factor = minFactor
	}
	
	for i := range containers {
		container := &containers[i]

		// Initialize resource limits and requests if nil
		if container.Resources.Limits == nil {
			container.Resources.Limits = corev1.ResourceList{}
		}
		if container.Resources.Requests == nil {
			container.Resources.Requests = corev1.ResourceList{}
		}

		var currentLimit resource.Quantity
		var newLimit resource.Quantity
		var newRequest resource.Quantity

		switch resourceType {
		case "memory":
			currentLimit = container.Resources.Limits[corev1.ResourceMemory]
			if currentLimit.IsZero() {
				currentLimit = resource.MustParse(defaultMemory)
			}
			newLimit = multiplyQuantity(&currentLimit, factor)
			// Validate memory limit is reasonable (max 512Gi)
			maxMemory := resource.MustParse("512Gi")
			if newLimit.Cmp(maxMemory) > 0 {
				logger.Warnf("Calculated memory limit %s exceeds maximum %s, capping", newLimit.String(), maxMemory.String())
				newLimit = maxMemory
			}
			requestRatio := config.GetResourceRequestRatio()
			newRequest = multiplyQuantity(&currentLimit, factor*requestRatio)
			container.Resources.Limits[corev1.ResourceMemory] = newLimit
			container.Resources.Requests[corev1.ResourceMemory] = newRequest

		case "cpu":
			currentLimit = container.Resources.Limits[corev1.ResourceCPU]
			if currentLimit.IsZero() {
				currentLimit = resource.MustParse(defaultCPU)
			}
			newLimit = multiplyQuantity(&currentLimit, factor)
			// Validate CPU limit is reasonable (max 128 cores)
			maxCPU := resource.MustParse("128")
			if newLimit.Cmp(maxCPU) > 0 {
				logger.Warnf("Calculated CPU limit %s exceeds maximum %s, capping", newLimit.String(), maxCPU.String())
				newLimit = maxCPU
			}
			requestRatio := config.GetResourceRequestRatio()
			newRequest = multiplyQuantity(&currentLimit, factor*requestRatio)
			container.Resources.Limits[corev1.ResourceCPU] = newLimit
			container.Resources.Requests[corev1.ResourceCPU] = newRequest

		default:
			return fmt.Errorf("unsupported resource type: %s", resourceType)
		}

		logger.Infof("  Container %s: %s → %s", container.Name, currentLimit.String(), newLimit.String())
	}

	return nil
}

