package remediation

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/sirupsen/logrus"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type RemediationExecutor struct {
	k8sClient *k8s.Client
	dryRun    bool
	logger    *logrus.Logger
}

// NewRemediationExecutor creates a new RemediationExecutor instance.
//
// Parameters:
//   - k8sClient: Kubernetes client for API operations
//
// Returns:
//   - *RemediationExecutor: New executor instance with default settings
func NewRemediationExecutor(k8sClient *k8s.Client) *RemediationExecutor {
	return &RemediationExecutor{
		k8sClient: k8sClient,
		dryRun:    false,
		logger:    utils.Log,
	}
}

// SetDryRun sets the dry-run mode for the executor.
// When dry-run is enabled, actions are logged but not executed.
//
// Parameters:
//   - dryRun: Whether to enable dry-run mode
func (e *RemediationExecutor) SetDryRun(dryRun bool) {
	e.dryRun = dryRun
}

// ExecuteAction executes a remediation action on a pod.
// Creates a timeout context for the action and routes to appropriate handler.
//
// Parameters:
//   - ctx: Context for cancellation and timeout propagation
//   - pod: Target pod for the remediation action
//   - action: Remediation action to execute
//
// Returns:
//   - error: Returns error if action execution fails or context is cancelled
//
// The action timeout is configurable via REMEDIATION_ACTION_TIMEOUT_SECONDS
// environment variable (default: 5 minutes).
func (e *RemediationExecutor) ExecuteAction(ctx context.Context, pod *corev1.Pod, action RemediationAction) error {
	// Check context cancellation before starting action
	if ctx.Err() != nil {
		return fmt.Errorf("context cancelled before action execution: %w", ctx.Err())
	}

	// Create timeout context for action execution (default 5 minutes, configurable)
	actionTimeout := 5 * time.Minute
	if timeoutStr := os.Getenv("REMEDIATION_ACTION_TIMEOUT_SECONDS"); timeoutStr != "" {
		if timeoutSeconds, err := strconv.Atoi(timeoutStr); err == nil && timeoutSeconds > 0 {
			actionTimeout = time.Duration(timeoutSeconds) * time.Second
		}
	}

	actionCtx, cancel := context.WithTimeout(ctx, actionTimeout)
	defer cancel()

	e.logger.Infof("🔧 Executing: %s %s on %s/%s (timeout: %v)", action.Operation, action.Type, pod.Namespace, pod.Name, actionTimeout)

	// Track remediation operation metrics
	startTime := time.Now()
	defer func() {
		duration := time.Since(startTime)
		metrics.RemediationDuration.Observe(duration.Seconds())
		metrics.RemediationActionDuration.WithLabelValues(action.Type, action.Operation).Observe(duration.Seconds())
	}()

	if e.dryRun {
		e.logger.Infof("[DRY-RUN] Would execute: %s", action.Operation)
		return nil
	}

	// Use actionCtx with timeout for all operations
	switch action.Type {
	case "pod":
		return e.executePodAction(actionCtx, pod, action)
	case "deployment":
		return e.executeDeploymentAction(actionCtx, pod, action)
	case "statefulset":
		return e.executeStatefulSetAction(actionCtx, pod, action)
	case "node":
		return e.executeNodeAction(actionCtx, pod, action)
	default:
		return fmt.Errorf("unsupported action type: %s", action.Type)
	}
}

func (e *RemediationExecutor) executePodAction(ctx context.Context, pod *corev1.Pod, action RemediationAction) error {
	// Resolve target if it's a placeholder
	if action.Target == "pod" || action.Target == "" {
		action.Target = pod.Name
		e.logger.Debugf("Resolved target placeholder to actual pod name: %s", action.Target)
	}

	switch action.Operation {
	case "restart":
		gracePeriod := int64(30)
		if gpParam, hasGracePeriod := action.Parameters["grace_period_seconds"]; hasGracePeriod {
			validatedGracePeriod, err := ValidateGracePeriod(gpParam)
			if err != nil {
				return fmt.Errorf("invalid grace_period_seconds: %w", err)
			}
			gracePeriod = validatedGracePeriod
		}

		e.logger.Infof("Restarting pod %s/%s (grace period: %ds)", pod.Namespace, pod.Name, gracePeriod)

		deleteOpts := metav1.DeleteOptions{GracePeriodSeconds: &gracePeriod}
		err := e.k8sClient.Clientset().CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, deleteOpts)
		if err != nil {
			return fmt.Errorf("failed to restart pod: %w", err)
		}

		e.logger.Infof("✅ Pod restarted successfully")
		return nil

	case "delete":
		gracePeriod := int64(0)
		deleteOpts := metav1.DeleteOptions{GracePeriodSeconds: &gracePeriod}
		err := e.k8sClient.Clientset().CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, deleteOpts)
		if err != nil {
			return fmt.Errorf("failed to delete pod: %w", err)
		}

		e.logger.Infof("✅ Pod deleted successfully")
		return nil

	default:
		return fmt.Errorf("unsupported pod operation: %s", action.Operation)
	}
}

func (e *RemediationExecutor) executeDeploymentAction(ctx context.Context, pod *corev1.Pod, action RemediationAction) error {
	// Check if operation is actually pod-level (can fall back)
	isPodLevelOperation := action.Operation == "restart" || action.Operation == "delete"

	deployment, err := e.findDeploymentForPod(ctx, pod)
	if err != nil {
		// If it's a pod-level operation and we can't find deployment, try pod action instead
		if isPodLevelOperation {
			e.logger.Warnf("Failed to find deployment for pod-level operation, falling back to pod action: %v", err)
			return e.executePodAction(ctx, pod, action)
		}
		return fmt.Errorf("failed to find deployment: %w", err)
	}

	// Resolve target if it's a placeholder
	if action.Target == "deployment" || action.Target == "" {
		action.Target = deployment.Name
		e.logger.Debugf("Resolved target placeholder to actual deployment name: %s", action.Target)
	}

	switch action.Operation {
	case "increase_memory":
		factor := 1.5
		if fParam, hasFactor := action.Parameters["factor"]; hasFactor {
			// Use validation helper with reasonable bounds (0.1 to 5.0)
			validatedFactor, err := ValidateFactor(fParam, 0.1, 5.0)
			if err != nil {
				return fmt.Errorf("invalid factor: %w", err)
			}
			factor = validatedFactor
		}

		// Validate deployment exists and has containers
		if len(deployment.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("deployment has no containers")
		}

		// Check resource quotas before increasing (fail-fast on violations)
		if err := e.checkResourceQuota(ctx, deployment.Namespace, "memory", factor); err != nil {
			// If quota check fails due to quota violation, fail the operation immediately
			if strings.Contains(err.Error(), "resource quota would be exceeded") {
				return fmt.Errorf("resource quota check failed: %w", err)
			}
			// For timeout errors, fail the operation to avoid wasted API calls
			if strings.Contains(err.Error(), "timeout") {
				return fmt.Errorf("resource quota check timeout: %w", err)
			}
			// For other errors (quota not found, permission denied), log warning but proceed
			e.logger.WithError(err).Warn("Resource quota check failed (non-critical), proceeding anyway")
			metrics.ResourceQuotaCheckErrors.WithLabelValues(deployment.Namespace, "memory").Inc()
		}

		e.logger.Infof("Increasing memory by %.0f%% for deployment %s/%s", (factor-1)*100, deployment.Namespace, deployment.Name)

		if err := updateContainerResources(
			deployment.Spec.Template.Spec.Containers,
			"memory",
			factor,
			"512Mi",
			"500m",
			e.logger,
		); err != nil {
			return fmt.Errorf("failed to update container resources: %w", err)
		}

		_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
		if err != nil {
			metrics.RemediationActionErrors.WithLabelValues("deployment", "increase_memory", "update_failed").Inc()
			return fmt.Errorf("failed to update deployment memory: %w", err)
		}

		e.logger.Infof("✅ Memory increased successfully")
		return nil

	case "increase_cpu":
		factor := 1.5
		if fParam, hasFactor := action.Parameters["factor"]; hasFactor {
			// Use validation helper with reasonable bounds (0.1 to 5.0)
			validatedFactor, err := ValidateFactor(fParam, 0.1, 5.0)
			if err != nil {
				return fmt.Errorf("invalid factor: %w", err)
			}
			factor = validatedFactor
		}

		// Validate deployment exists and has containers
		if len(deployment.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("deployment has no containers")
		}

		// Check resource quotas before increasing (fail-fast on violations)
		if err := e.checkResourceQuota(ctx, deployment.Namespace, "cpu", factor); err != nil {
			// If quota check fails due to quota violation, fail the operation immediately
			if strings.Contains(err.Error(), "resource quota would be exceeded") {
				return fmt.Errorf("resource quota check failed: %w", err)
			}
			// For timeout errors, fail the operation to avoid wasted API calls
			if strings.Contains(err.Error(), "timeout") {
				return fmt.Errorf("resource quota check timeout: %w", err)
			}
			// For other errors (quota not found, permission denied), log warning but proceed
			e.logger.WithError(err).Warn("Resource quota check failed (non-critical), proceeding anyway")
			metrics.ResourceQuotaCheckErrors.WithLabelValues(deployment.Namespace, "cpu").Inc()
		}

		e.logger.Infof("Increasing CPU by %.0f%% for deployment %s/%s", (factor-1)*100, deployment.Namespace, deployment.Name)

		if err := updateContainerResources(
			deployment.Spec.Template.Spec.Containers,
			"cpu",
			factor,
			"1Gi",  // Default memory for CPU operations
			"500m", // Default CPU
			e.logger,
		); err != nil {
			return fmt.Errorf("failed to update container resources: %w", err)
		}

		_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
		if err != nil {
			metrics.RemediationActionErrors.WithLabelValues("deployment", "increase_cpu", "update_failed").Inc()
			return fmt.Errorf("failed to update deployment CPU: %w", err)
		}

		e.logger.Infof("✅ CPU increased successfully")
		return nil

	case "scale":
		replicas := int32(1)
		if rParam, hasReplicas := action.Parameters["replicas"]; hasReplicas {
			// Use validation helper with reasonable bounds (1 to 100)
			validatedReplicas, err := ValidateReplicas(rParam, 1, 100)
			if err != nil {
				return fmt.Errorf("invalid replicas: %w", err)
			}
			replicas = validatedReplicas
		}

		direction := "up"
		if d, ok := action.Parameters["direction"].(string); ok {
			// Validate direction
			if d != "up" && d != "down" {
				return fmt.Errorf("invalid direction %s: must be 'up' or 'down'", d)
			}
			direction = d
		}

		currentReplicas := *deployment.Spec.Replicas
		var newReplicas int32

		if direction == "up" {
			newReplicas = currentReplicas + replicas
			// Cap at reasonable maximum (100 replicas)
			if newReplicas > 100 {
				newReplicas = 100
			}
		} else {
			newReplicas = currentReplicas - replicas
			if newReplicas < 1 {
				newReplicas = 1
			}
		}

		e.logger.Infof("Scaling deployment %s/%s: %d → %d replicas", deployment.Namespace, deployment.Name, currentReplicas, newReplicas)

		deployment.Spec.Replicas = &newReplicas
		_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
		if err != nil {
			metrics.RemediationActionErrors.WithLabelValues("deployment", "scale", "update_failed").Inc()
			return fmt.Errorf("failed to scale deployment: %w", err)
		}

		e.logger.Infof("✅ Deployment scaled successfully")
		return nil

	case "update_image":
		imageParam, hasImage := action.Parameters["image"]
		if !hasImage {
			return fmt.Errorf("image parameter required")
		}
		image, err := ValidateImage(imageParam)
		if err != nil {
			return fmt.Errorf("invalid image parameter: %w", err)
		}

		containerName := ""
		if cnParam, hasContainer := action.Parameters["container"]; hasContainer {
			validatedContainer, err := ValidateContainer(cnParam)
			if err != nil {
				return fmt.Errorf("invalid container parameter: %w", err)
			}
			containerName = validatedContainer
		}

		e.logger.Infof("Updating image to %s for deployment %s/%s", image, deployment.Namespace, deployment.Name)

		updated := false
		for i := range deployment.Spec.Template.Spec.Containers {
			container := &deployment.Spec.Template.Spec.Containers[i]
			if containerName == "" || container.Name == containerName {
				oldImage := container.Image
				container.Image = image
				e.logger.Infof("  Container %s: %s → %s", container.Name, oldImage, image)
				updated = true
			}
		}

		if !updated {
			return fmt.Errorf("no containers updated")
		}

		_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
		if err != nil {
			metrics.RemediationActionErrors.WithLabelValues("deployment", "update_image", "update_failed").Inc()
			return fmt.Errorf("failed to update deployment image: %w", err)
		}

		e.logger.Infof("✅ Image updated successfully")
		return nil

	case "restart_rollout":
		e.logger.Infof("Restarting rollout for deployment %s/%s", deployment.Namespace, deployment.Name)

		if deployment.Spec.Template.Annotations == nil {
			deployment.Spec.Template.Annotations = make(map[string]string)
		}
		deployment.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = time.Now().Format(time.RFC3339)

		_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
		if err != nil {
			metrics.RemediationActionErrors.WithLabelValues("deployment", "restart_rollout", "update_failed").Inc()
			return fmt.Errorf("failed to restart deployment rollout: %w", err)
		}

		e.logger.Infof("✅ Rollout restarted successfully")
		return nil

	case "rollback_deployment":
		e.logger.Infof("Rolling back deployment %s/%s", deployment.Namespace, deployment.Name)

		// Pre-flight check: Verify deployment has revision history enabled
		if deployment.Spec.RevisionHistoryLimit != nil && *deployment.Spec.RevisionHistoryLimit == 0 {
			return fmt.Errorf("rollback failed: deployment %s/%s has revisionHistoryLimit set to 0, preventing rollback. Set revisionHistoryLimit > 0 to enable rollback", deployment.Namespace, deployment.Name)
		}

		// Health check: Verify we can access deployment history API
		// Try to list replicasets first to check if API is available
		healthCheckStart := time.Now()
		testCtx, testCancel := context.WithTimeout(ctx, 5*time.Second)
		defer testCancel()
		
		// Check context cancellation before health check
		select {
		case <-ctx.Done():
			testCancel()
			return fmt.Errorf("context cancelled before deployment history health check: %w", ctx.Err())
		default:
		}
		
		_, testErr := e.k8sClient.Clientset().AppsV1().ReplicaSets(deployment.Namespace).List(testCtx, metav1.ListOptions{
			Limit: 1,
		})
		healthCheckDuration := time.Since(healthCheckStart)
		metrics.DeploymentHistoryAPICheckDuration.WithLabelValues(deployment.Namespace).Observe(healthCheckDuration.Seconds())
		
		if testErr != nil {
			// Check if error is due to API unavailability
			metrics.DeploymentHistoryAPIHealth.WithLabelValues(deployment.Namespace).Set(0)
			if errors.IsTimeout(testErr) || errors.IsServerTimeout(testErr) {
				return fmt.Errorf("deployment history API unavailable (timeout): %w", testErr)
			}
			if errors.IsServiceUnavailable(testErr) {
				return fmt.Errorf("deployment history API unavailable (service unavailable): %w", testErr)
			}
			// For other errors, log warning but continue
			e.logger.WithError(testErr).Warn("Health check for deployment history API failed, proceeding anyway")
		} else {
			metrics.DeploymentHistoryAPIHealth.WithLabelValues(deployment.Namespace).Set(1)
		}

		// Get deployment rollout history
		replicaSets, err := e.k8sClient.Clientset().AppsV1().ReplicaSets(deployment.Namespace).List(ctx, metav1.ListOptions{
			LabelSelector: metav1.FormatLabelSelector(deployment.Spec.Selector),
		})
		if err != nil {
			// Provide more specific error messages
			if errors.IsNotFound(err) {
				return fmt.Errorf("deployment or namespace not found: %w", err)
			}
			if errors.IsForbidden(err) {
				return fmt.Errorf("permission denied accessing deployment history: %w", err)
			}
			if errors.IsTimeout(err) || errors.IsServerTimeout(err) {
				return fmt.Errorf("timeout accessing deployment history API: %w", err)
			}
			return fmt.Errorf("failed to list replicasets for rollback: %w", err)
		}

		// Find previous revision (not current)
		// Improved logic: find the most recent non-active replicaset
		var previousRS *appsv1.ReplicaSet
		var currentRS *appsv1.ReplicaSet
		
		// First, identify the current active replicaset
		for i := range replicaSets.Items {
			rs := &replicaSets.Items[i]
			if *rs.Spec.Replicas > 0 && rs.Status.ReadyReplicas > 0 {
				currentRS = rs
				break
			}
		}
		
		// Then find the most recent previous replicaset
		for i := range replicaSets.Items {
			rs := &replicaSets.Items[i]
			// Skip current active replicaset
			if currentRS != nil && rs.Name == currentRS.Name {
				continue
			}
			// Check if this replicaset has revision annotation
			if revision, hasRevision := rs.Annotations["deployment.kubernetes.io/revision"]; hasRevision && revision != "" {
				// Find the most recent previous replicaset
				if previousRS == nil || rs.CreationTimestamp.Time.After(previousRS.CreationTimestamp.Time) {
					previousRS = rs
				}
			}
		}

		if previousRS == nil {
			// Fallback strategy: Try to use deployment annotations or labels to find previous version
			e.logger.Warn("Could not find previous replicaset via revision history")
			
			// Validate revision history limit
			if deployment.Spec.RevisionHistoryLimit != nil && *deployment.Spec.RevisionHistoryLimit == 0 {
				return fmt.Errorf("rollback failed: deployment %s/%s has revisionHistoryLimit set to 0, preventing rollback. Set revisionHistoryLimit > 0 to enable rollback", deployment.Namespace, deployment.Name)
			}
			
			// Check if deployment has any annotations that might indicate previous version
			if deployment.Annotations != nil {
				if prevImage, hasPrevImage := deployment.Annotations["deployment.kubernetes.io/previous-image"]; hasPrevImage {
					e.logger.Infof("Found previous image in annotations: %s", prevImage)
					// Try to rollback by updating image to previous version
					for i := range deployment.Spec.Template.Spec.Containers {
						if len(deployment.Spec.Template.Spec.Containers[i].Image) > 0 {
							deployment.Spec.Template.Spec.Containers[i].Image = prevImage
							e.logger.Infof("Rolling back container %s to image: %s", deployment.Spec.Template.Spec.Containers[i].Name, prevImage)
						}
					}
					_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
					if err != nil {
						metrics.RemediationActionErrors.WithLabelValues("deployment", "rollback_deployment", "update_failed").Inc()
						return fmt.Errorf("failed to rollback deployment using previous image annotation: %w", err)
					}
					e.logger.Infof("✅ Deployment rolled back successfully using previous image annotation")
					return nil
				}
			}
			
			// If no fallback strategy works, return error with helpful message
			return fmt.Errorf("rollback failed: no previous revision found for deployment %s/%s. Ensure deployment has revisionHistoryLimit > 0 (current: %v) and deployment history is available. Deployment may be new or history was cleaned up", deployment.Namespace, deployment.Name, deployment.Spec.RevisionHistoryLimit)
		}

		// Restore previous template
		if len(previousRS.Spec.Template.Spec.Containers) > 0 {
			deployment.Spec.Template = previousRS.Spec.Template
			_, err = e.k8sClient.Clientset().AppsV1().Deployments(deployment.Namespace).Update(ctx, deployment, metav1.UpdateOptions{})
			if err != nil {
				metrics.RemediationActionErrors.WithLabelValues("deployment", "rollback_deployment", "update_failed").Inc()
				return fmt.Errorf("failed to rollback deployment: %w", err)
			}
			e.logger.Infof("✅ Deployment rolled back successfully to revision %s", previousRS.Annotations["deployment.kubernetes.io/revision"])
			return nil
		}

		return fmt.Errorf("previous replicaset %s has no container template to restore", previousRS.Name)

	default:
		return fmt.Errorf("unsupported deployment operation: %s", action.Operation)
	}
}

func (e *RemediationExecutor) executeStatefulSetAction(ctx context.Context, pod *corev1.Pod, action RemediationAction) error {
	statefulSet, err := e.findStatefulSetForPod(ctx, pod)
	if err != nil {
		return fmt.Errorf("failed to find statefulset: %w", err)
	}

	// Resolve target if it's a placeholder
	if action.Target == "statefulset" || action.Target == "" {
		action.Target = statefulSet.Name
		e.logger.Debugf("Resolved target placeholder to actual statefulset name: %s", action.Target)
	}

	switch action.Operation {
	case "increase_memory":
		factor := 1.5
		if fParam, hasFactor := action.Parameters["factor"]; hasFactor {
			// Use validation helper with reasonable bounds (0.1 to 5.0)
			validatedFactor, err := ValidateFactor(fParam, 0.1, 5.0)
			if err != nil {
				return fmt.Errorf("invalid factor: %w", err)
			}
			factor = validatedFactor
		}

		// Validate statefulset has containers
		if len(statefulSet.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("statefulset has no containers")
		}

		e.logger.Infof("Increasing memory by %.0f%% for statefulset %s/%s", (factor-1)*100, statefulSet.Namespace, statefulSet.Name)

		if err := updateContainerResources(
			statefulSet.Spec.Template.Spec.Containers,
			"memory",
			factor,
			"512Mi",
			"500m",
			e.logger,
		); err != nil {
			return fmt.Errorf("failed to update container resources: %w", err)
		}

		_, err = e.k8sClient.Clientset().AppsV1().StatefulSets(statefulSet.Namespace).Update(ctx, statefulSet, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to update statefulset memory: %w", err)
		}

		e.logger.Infof("✅ Memory increased successfully")
		return nil

	case "increase_cpu":
		factor := 1.5
		if fParam, hasFactor := action.Parameters["factor"]; hasFactor {
			// Use validation helper with reasonable bounds (0.1 to 5.0)
			validatedFactor, err := ValidateFactor(fParam, 0.1, 5.0)
			if err != nil {
				return fmt.Errorf("invalid factor: %w", err)
			}
			factor = validatedFactor
		}

		// Validate statefulset has containers
		if len(statefulSet.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("statefulset has no containers")
		}

		e.logger.Infof("Increasing CPU by %.0f%% for statefulset %s/%s", (factor-1)*100, statefulSet.Namespace, statefulSet.Name)

		if err := updateContainerResources(
			statefulSet.Spec.Template.Spec.Containers,
			"cpu",
			factor,
			"1Gi",  // Default memory for CPU operations
			"500m", // Default CPU
			e.logger,
		); err != nil {
			return fmt.Errorf("failed to update container resources: %w", err)
		}

		_, err = e.k8sClient.Clientset().AppsV1().StatefulSets(statefulSet.Namespace).Update(ctx, statefulSet, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to update statefulset CPU: %w", err)
		}

		e.logger.Infof("✅ CPU increased successfully")
		return nil

	case "rollback_statefulset":
		e.logger.Infof("Rolling back statefulset %s/%s", statefulSet.Namespace, statefulSet.Name)

		// Pre-flight check: Verify statefulset has revision history enabled
		if statefulSet.Spec.RevisionHistoryLimit != nil && *statefulSet.Spec.RevisionHistoryLimit == 0 {
			return fmt.Errorf("rollback failed: statefulset %s/%s has revisionHistoryLimit set to 0, preventing rollback. Set revisionHistoryLimit > 0 to enable rollback", statefulSet.Namespace, statefulSet.Name)
		}

		// Get statefulset rollout history via controller revisions
		controllerRevisions, err := e.k8sClient.Clientset().AppsV1().ControllerRevisions(statefulSet.Namespace).List(ctx, metav1.ListOptions{
			LabelSelector: metav1.FormatLabelSelector(statefulSet.Spec.Selector),
		})
		if err != nil {
			if errors.IsNotFound(err) {
				return fmt.Errorf("statefulset or namespace not found: %w", err)
			}
			if errors.IsForbidden(err) {
				return fmt.Errorf("permission denied accessing statefulset history: %w", err)
			}
			if errors.IsTimeout(err) || errors.IsServerTimeout(err) {
				return fmt.Errorf("timeout accessing statefulset history API: %w", err)
			}
			return fmt.Errorf("failed to list controller revisions for rollback: %w", err)
		}

		// Find previous revision (not current)
		var previousRevision *appsv1.ControllerRevision
		var currentRevision *appsv1.ControllerRevision

		// Identify current active revision
		for i := range controllerRevisions.Items {
			rev := &controllerRevisions.Items[i]
			if rev.Name == statefulSet.Status.CurrentRevision {
				currentRevision = rev
				break
			}
		}

		// Find the most recent previous revision
		for i := range controllerRevisions.Items {
			rev := &controllerRevisions.Items[i]
			// Skip current active revision
			if currentRevision != nil && rev.Name == currentRevision.Name {
				continue
			}
			// Find the most recent previous revision
			if previousRevision == nil || rev.Revision > previousRevision.Revision {
				previousRevision = rev
			}
		}

		if previousRevision == nil {
			// Validate revision history limit
			if statefulSet.Spec.RevisionHistoryLimit != nil && *statefulSet.Spec.RevisionHistoryLimit == 0 {
				return fmt.Errorf("rollback failed: statefulset %s/%s has revisionHistoryLimit set to 0, preventing rollback. Set revisionHistoryLimit > 0 to enable rollback", statefulSet.Namespace, statefulSet.Name)
			}
			return fmt.Errorf("rollback failed: no previous revision found for statefulset %s/%s. Ensure statefulset has revisionHistoryLimit > 0 (current: %v) and revision history is available", statefulSet.Namespace, statefulSet.Name, statefulSet.Spec.RevisionHistoryLimit)
		}

		// Restore previous template
		var previousTemplate appsv1.StatefulSet
		if err := json.Unmarshal(previousRevision.Data.Raw, &previousTemplate); err != nil {
			return fmt.Errorf("failed to unmarshal previous revision template: %w", err)
		}

		if len(previousTemplate.Spec.Template.Spec.Containers) > 0 {
			statefulSet.Spec.Template = previousTemplate.Spec.Template
			_, err = e.k8sClient.Clientset().AppsV1().StatefulSets(statefulSet.Namespace).Update(ctx, statefulSet, metav1.UpdateOptions{})
			if err != nil {
				metrics.RemediationActionErrors.WithLabelValues("statefulset", "rollback_statefulset", "update_failed").Inc()
				return fmt.Errorf("failed to rollback statefulset: %w", err)
			}
			e.logger.Infof("✅ StatefulSet rolled back successfully to revision %d", previousRevision.Revision)
			return nil
		}

		return fmt.Errorf("previous revision %d has no container template to restore", previousRevision.Revision)

	default:
		return fmt.Errorf("unsupported statefulset operation: %s", action.Operation)
	}
}

func (e *RemediationExecutor) executeNodeAction(ctx context.Context, pod *corev1.Pod, action RemediationAction) error {
	nodeName := pod.Spec.NodeName
	if nodeName == "" {
		return fmt.Errorf("pod has no assigned node")
	}

	// Resolve target if it's a placeholder
	if action.Target == "node" || action.Target == "" {
		action.Target = nodeName
		e.logger.Debugf("Resolved target placeholder to actual node name: %s", action.Target)
	}

	switch action.Operation {
	case "drain":
		e.logger.Infof("Draining node %s (marking unschedulable and evicting pods)", nodeName)

		node, err := e.k8sClient.Clientset().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("failed to get node: %w", err)
		}

		// Mark node as unschedulable
		node.Spec.Unschedulable = true
		_, err = e.k8sClient.Clientset().CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to mark node unschedulable: %w", err)
		}

		// Evict pods from the node with verification
		// Get all pods on this node
		pods, err := e.k8sClient.Clientset().CoreV1().Pods(metav1.NamespaceAll).List(ctx, metav1.ListOptions{
			FieldSelector: fmt.Sprintf("spec.nodeName=%s", nodeName),
		})
		if err != nil {
			return fmt.Errorf("failed to list pods on node: %w", err)
		}

		// Track pods to evict
		podsToEvict := make([]corev1.Pod, 0)
		for _, pod := range pods.Items {
			// Skip daemonset pods and mirror pods
			skip := false
			if pod.OwnerReferences != nil {
				for _, owner := range pod.OwnerReferences {
					if owner.Kind == "DaemonSet" {
						skip = true
						break
					}
				}
			}
			// Also skip mirror pods (static pods)
			if pod.Annotations != nil {
				if _, isMirror := pod.Annotations[corev1.MirrorPodAnnotationKey]; isMirror {
					skip = true
				}
			}
			if !skip {
				podsToEvict = append(podsToEvict, pod)
			}
		}

		// Evict pods with grace period and verify eviction
		gracePeriod := int64(30)
		evictionTimeout := 2 * time.Minute
		maxVerificationAttempts := 10
		verificationInterval := 3 * time.Second

		for _, pod := range podsToEvict {
			// Check context cancellation before each eviction
			select {
			case <-ctx.Done():
				return fmt.Errorf("context cancelled during pod eviction: %w", ctx.Err())
			default:
			}

			eviction := &policyv1.Eviction{
				ObjectMeta: metav1.ObjectMeta{
					Name:      pod.Name,
					Namespace: pod.Namespace,
				},
				DeleteOptions: &metav1.DeleteOptions{
					GracePeriodSeconds: &gracePeriod,
				},
			}

			// Attempt eviction
			podsClient := e.k8sClient.Clientset().CoreV1().Pods(pod.Namespace)
			err := podsClient.EvictV1(ctx, eviction)
			if err != nil {
				// If EvictV1 fails, try regular delete as fallback
				e.logger.WithError(err).Debugf("EvictV1 failed for pod %s/%s, trying regular delete", pod.Namespace, pod.Name)
				deleteOpts := metav1.DeleteOptions{
					GracePeriodSeconds: &gracePeriod,
				}
				if deleteErr := podsClient.Delete(ctx, pod.Name, deleteOpts); deleteErr != nil {
					e.logger.WithError(deleteErr).Warnf("Failed to delete pod %s/%s", pod.Namespace, pod.Name)
					continue
				}
			}

			// Verify pod eviction with retries
			evictionStart := time.Now()
			evicted := false
			for attempt := 0; attempt < maxVerificationAttempts; attempt++ {
				// Check if context is cancelled
				select {
				case <-ctx.Done():
					return fmt.Errorf("context cancelled during pod eviction verification: %w", ctx.Err())
				default:
				}

				// Check if timeout exceeded
				if time.Since(evictionStart) > evictionTimeout {
					e.logger.Warnf("Pod eviction verification timeout for %s/%s", pod.Namespace, pod.Name)
					break
				}

				// Check if pod still exists
				_, err := podsClient.Get(ctx, pod.Name, metav1.GetOptions{})
				if errors.IsNotFound(err) {
					evicted = true
					e.logger.Debugf("Pod %s/%s successfully evicted", pod.Namespace, pod.Name)
					break
				}

				// Wait before next verification attempt
				select {
				case <-ctx.Done():
					return fmt.Errorf("context cancelled during pod eviction verification: %w", ctx.Err())
				case <-time.After(verificationInterval):
				}
			}

			if !evicted {
				e.logger.Warnf("Pod %s/%s may not have been fully evicted (verification incomplete)", pod.Namespace, pod.Name)
			}
		}

		e.logger.Infof("✅ Node drained successfully")
		return nil

	case "uncordon":
		e.logger.Infof("Marking node %s as schedulable", nodeName)

		node, err := e.k8sClient.Clientset().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("failed to get node: %w", err)
		}

		node.Spec.Unschedulable = false
		_, err = e.k8sClient.Clientset().CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to uncordon node: %w", err)
		}

		e.logger.Infof("✅ Node marked as schedulable")
		return nil

	case "taint":
		keyParam, hasKey := action.Parameters["key"]
		valueParam, hasValue := action.Parameters["value"]
		effectParam, hasEffect := action.Parameters["effect"]

		if !hasKey {
			return fmt.Errorf("key parameter is required for taint operation")
		}
		if !hasEffect {
			return fmt.Errorf("effect parameter is required for taint operation (NoSchedule, PreferNoSchedule, NoExecute)")
		}

		key, ok := keyParam.(string)
		if !ok {
			return fmt.Errorf("key parameter must be a string")
		}
		effect, ok := effectParam.(string)
		if !ok {
			return fmt.Errorf("effect parameter must be a string")
		}

		// Validate effect
		validEffects := map[string]bool{
			"NoSchedule":       true,
			"PreferNoSchedule": true,
			"NoExecute":        true,
		}
		if !validEffects[effect] {
			return fmt.Errorf("invalid effect %s: must be NoSchedule, PreferNoSchedule, or NoExecute", effect)
		}

		var value string
		if hasValue {
			valueStr, ok := valueParam.(string)
			if !ok {
				return fmt.Errorf("value parameter must be a string")
			}
			value = valueStr
		}

		e.logger.Infof("Adding taint to node %s: %s=%s:%s", nodeName, key, value, effect)

		node, err := e.k8sClient.Clientset().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("failed to get node: %w", err)
		}

		// Add taint
		taint := corev1.Taint{
			Key:    key,
			Value:  value,
			Effect: corev1.TaintEffect(effect),
		}

		// Check if taint already exists
		taintExists := false
		for i, existingTaint := range node.Spec.Taints {
			if existingTaint.Key == taint.Key {
				node.Spec.Taints[i] = taint
				taintExists = true
				break
			}
		}

		if !taintExists {
			node.Spec.Taints = append(node.Spec.Taints, taint)
		}

		_, err = e.k8sClient.Clientset().CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to taint node: %w", err)
		}

		e.logger.Infof("✅ Node tainted successfully")
		return nil

	case "untaint":
		keyParam, hasKey := action.Parameters["key"]

		if !hasKey {
			return fmt.Errorf("key parameter is required for untaint operation")
		}

		key, ok := keyParam.(string)
		if !ok {
			return fmt.Errorf("key parameter must be a string")
		}

		e.logger.Infof("Removing taint from node %s: %s", nodeName, key)

		node, err := e.k8sClient.Clientset().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("failed to get node: %w", err)
		}

		// Remove taint
		newTaints := []corev1.Taint{}
		for _, taint := range node.Spec.Taints {
			if taint.Key != key {
				newTaints = append(newTaints, taint)
			}
		}

		node.Spec.Taints = newTaints

		_, err = e.k8sClient.Clientset().CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to untaint node: %w", err)
		}

		e.logger.Infof("✅ Node untainted successfully")
		return nil

	case "label":
		keyParam, hasKey := action.Parameters["key"]
		valueParam, hasValue := action.Parameters["value"]

		if !hasKey {
			return fmt.Errorf("key parameter is required for label operation")
		}
		if !hasValue {
			return fmt.Errorf("value parameter is required for label operation")
		}

		key, ok := keyParam.(string)
		if !ok {
			return fmt.Errorf("key parameter must be a string")
		}
		value, ok := valueParam.(string)
		if !ok {
			return fmt.Errorf("value parameter must be a string")
		}

		e.logger.Infof("Adding label to node %s: %s=%s", nodeName, key, value)

		node, err := e.k8sClient.Clientset().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("failed to get node: %w", err)
		}

		// Add or update label
		if node.Labels == nil {
			node.Labels = make(map[string]string)
		}
		node.Labels[key] = value

		_, err = e.k8sClient.Clientset().CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		if err != nil {
			return fmt.Errorf("failed to label node: %w", err)
		}

		e.logger.Infof("✅ Node labeled successfully")
		return nil

	default:
		return fmt.Errorf("unsupported node operation: %s", action.Operation)
	}
}

func (e *RemediationExecutor) findDeploymentForPod(ctx context.Context, pod *corev1.Pod) (*appsv1.Deployment, error) {
	if len(pod.OwnerReferences) == 0 {
		return nil, fmt.Errorf("pod has no owner references")
	}

	var foundDeployments []*appsv1.Deployment
	var lastErr error

	for _, owner := range pod.OwnerReferences {
		if owner.Kind == "ReplicaSet" {
			rs, err := e.k8sClient.Clientset().AppsV1().ReplicaSets(pod.Namespace).Get(ctx, owner.Name, metav1.GetOptions{})
			if err != nil {
				e.logger.WithError(err).WithField("replicaset", owner.Name).Warn("Failed to get ReplicaSet")
				lastErr = fmt.Errorf("failed to get ReplicaSet %s: %w", owner.Name, err)
				continue
			}

			if len(rs.OwnerReferences) == 0 {
				e.logger.WithField("replicaset", rs.Name).Warn("ReplicaSet has no owner references - may be standalone or orphaned")
				// Try to find deployment by label selector as fallback
				if rs.Spec.Selector != nil && len(rs.Spec.Selector.MatchLabels) > 0 {
					// This is a best-effort fallback - may not find the exact deployment
					e.logger.WithField("replicaset", rs.Name).Debug("Attempting to find deployment by label selector")
				}
				lastErr = fmt.Errorf("replicaset %s has no owner references", rs.Name)
				continue
			}

			// Check all owner references, not just first
			for _, rsOwner := range rs.OwnerReferences {
				if rsOwner.Kind == "Deployment" {
					deployment, err := e.k8sClient.Clientset().AppsV1().Deployments(pod.Namespace).Get(ctx, rsOwner.Name, metav1.GetOptions{})
					if err != nil {
						e.logger.WithError(err).WithField("deployment", rsOwner.Name).Warn("Failed to get Deployment")
						lastErr = fmt.Errorf("failed to get Deployment %s: %w", rsOwner.Name, err)
						continue
					}
					foundDeployments = append(foundDeployments, deployment)
				}
			}
		}
	}

	// Return first deployment found, or error if none found
	if len(foundDeployments) > 0 {
		if len(foundDeployments) > 1 {
			e.logger.WithField("count", len(foundDeployments)).Warn("Multiple deployments found for pod, using first")
		}
		return foundDeployments[0], nil
	}

	if lastErr != nil {
		return nil, lastErr
	}

	return nil, fmt.Errorf("no deployment found for pod")
}

func (e *RemediationExecutor) findStatefulSetForPod(ctx context.Context, pod *corev1.Pod) (*appsv1.StatefulSet, error) {
	if len(pod.OwnerReferences) == 0 {
		return nil, fmt.Errorf("pod has no owner references")
	}

	var foundStatefulSets []*appsv1.StatefulSet
	var lastErr error

	for _, owner := range pod.OwnerReferences {
		if owner.Kind == "StatefulSet" {
			statefulSet, err := e.k8sClient.Clientset().AppsV1().StatefulSets(pod.Namespace).Get(ctx, owner.Name, metav1.GetOptions{})
			if err != nil {
				e.logger.WithError(err).WithField("statefulset", owner.Name).Warn("Failed to get StatefulSet")
				lastErr = fmt.Errorf("failed to get StatefulSet %s: %w", owner.Name, err)
				continue
			}
			foundStatefulSets = append(foundStatefulSets, statefulSet)
		}
	}

	// Return first StatefulSet found, or error if none found
	if len(foundStatefulSets) > 0 {
		if len(foundStatefulSets) > 1 {
			e.logger.WithField("count", len(foundStatefulSets)).Warn("Multiple StatefulSets found for pod, using first")
		}
		return foundStatefulSets[0], nil
	}

	if lastErr != nil {
		return nil, lastErr
	}

	return nil, fmt.Errorf("no StatefulSet found for pod")
}

func multiplyQuantity(q *resource.Quantity, factor float64) resource.Quantity {
	value := float64(q.MilliValue()) * factor
	return *resource.NewMilliQuantity(int64(value), q.Format)
}

// checkResourceQuota checks if resource quota allows the requested increase
// Accounts for all pods in namespace to calculate accurate projected usage
func (e *RemediationExecutor) checkResourceQuota(ctx context.Context, namespace, resourceType string, factor float64) error {
	startTime := time.Now()
	defer func() {
		duration := time.Since(startTime)
		metrics.ResourceQuotaCheckDuration.WithLabelValues(namespace, resourceType).Observe(duration.Seconds())
	}()

	// Try to get resource quota for namespace
	quotas, err := e.k8sClient.Clientset().CoreV1().ResourceQuotas(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		// Classify error: if it's a permission/not found error, log and proceed
		// If it's a timeout or server error, fail fast to avoid wasted API calls
		if errors.IsTimeout(err) || errors.IsServerTimeout(err) {
			e.logger.WithError(err).Warn("Resource quota check timed out - failing operation to avoid quota violation")
			metrics.ResourceQuotaCheckErrors.WithLabelValues(namespace, resourceType).Inc()
			return fmt.Errorf("resource quota check timeout: %w", err)
		}
		if errors.IsNotFound(err) || errors.IsForbidden(err) {
			// Resource quotas may not exist or may not be accessible - this is acceptable
			e.logger.WithError(err).Debug("Could not check resource quotas (may not exist or be accessible)")
			metrics.ResourceQuotaCheckErrors.WithLabelValues(namespace, resourceType).Inc()
			return nil // Don't fail the operation if quota doesn't exist
		}
		// For other errors, log warning but proceed (best-effort)
		e.logger.WithError(err).Warn("Resource quota check failed with unknown error")
		metrics.ResourceQuotaCheckErrors.WithLabelValues(namespace, resourceType).Inc()
		return nil
	}

	if len(quotas.Items) == 0 {
		// No quotas defined - allow operation
		return nil
	}

	// Get all pods in namespace to calculate accurate resource usage
	allPods, err := e.k8sClient.Clientset().CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		e.logger.WithError(err).Warn("Failed to list pods for quota calculation, using quota status only")
		allPods = &corev1.PodList{Items: []corev1.Pod{}}
	}

	// Check if quota would be exceeded (enhanced check)
	// Calculate current usage + projected increase accounting for all pods
	violationDetected := false
	for _, quota := range quotas.Items {
		hard := quota.Spec.Hard
		used := quota.Status.Used

		var hardLimit, usedAmount resource.Quantity
		var found bool

		switch resourceType {
		case "memory":
			hardLimit, found = hard[corev1.ResourceLimitsMemory]
			if !found {
				hardLimit, found = hard[corev1.ResourceRequestsMemory]
			}
			if found {
				usedAmount = used[corev1.ResourceLimitsMemory]
				if usedAmount.IsZero() {
					usedAmount = used[corev1.ResourceRequestsMemory]
				}
			}
		case "cpu":
			hardLimit, found = hard[corev1.ResourceLimitsCPU]
			if !found {
				hardLimit, found = hard[corev1.ResourceRequestsCPU]
			}
			if found {
				usedAmount = used[corev1.ResourceLimitsCPU]
				if usedAmount.IsZero() {
					usedAmount = used[corev1.ResourceRequestsCPU]
				}
			}
		}

		if found && !hardLimit.IsZero() {
			// Calculate projected usage more accurately by accounting for all pods
			// Sum up resource requests/limits from all pods in namespace
			var totalPodResources int64
			for _, pod := range allPods.Items {
				// Check context cancellation in loop
				select {
				case <-ctx.Done():
					return fmt.Errorf("context cancelled during quota calculation: %w", ctx.Err())
				default:
				}
				
				for _, container := range pod.Spec.Containers {
					var containerResource resource.Quantity
					switch resourceType {
					case "memory":
						if container.Resources.Requests != nil {
							containerResource = container.Resources.Requests[corev1.ResourceMemory]
						} else if container.Resources.Limits != nil {
							containerResource = container.Resources.Limits[corev1.ResourceMemory]
						}
					case "cpu":
						if container.Resources.Requests != nil {
							containerResource = container.Resources.Requests[corev1.ResourceCPU]
						} else if container.Resources.Limits != nil {
							containerResource = container.Resources.Limits[corev1.ResourceCPU]
						}
					}
					if !containerResource.IsZero() {
						totalPodResources += containerResource.MilliValue()
					}
				}
			}

			// Calculate projected usage: current quota usage + (change in pod resources)
			// For increase: new_usage = current_quota_usage + (total_pod_resources * (factor - 1))
			// For decrease: new_usage = current_quota_usage - (total_pod_resources * (1 - factor))
			var projectedUsage float64
			currentQuotaUsage := float64(usedAmount.MilliValue())
			if factor > 1.0 {
				// Increase operation: add the increase amount
				increaseAmount := float64(totalPodResources) * (factor - 1.0)
				projectedUsage = currentQuotaUsage + increaseAmount
			} else if factor < 1.0 {
				// Decrease operation: subtract the decrease amount
				decreaseAmount := float64(totalPodResources) * (1.0 - factor)
				projectedUsage = currentQuotaUsage - decreaseAmount
				if projectedUsage < 0 {
					projectedUsage = 0
				}
			} else {
				// No change
				projectedUsage = currentQuotaUsage
			}
			hardLimitValue := float64(hardLimit.MilliValue())

			// Calculate utilization percentage
			currentUtilization := (float64(usedAmount.MilliValue()) / hardLimitValue) * 100
			projectedUtilization := (projectedUsage / hardLimitValue) * 100

			if projectedUsage > hardLimitValue {
				violationDetected = true
				metrics.ResourceQuotaViolations.WithLabelValues(namespace, resourceType).Inc()
				e.logger.Warnf("Resource quota would be exceeded: projected %.0f > limit %.0f (current: %.1f%%, projected: %.1f%%)",
					projectedUsage, hardLimitValue, currentUtilization, projectedUtilization)
				// Don't fail - let Kubernetes API reject if quota is actually exceeded
				// This is just a warning to help with planning
			} else if projectedUtilization > 90 {
				// Warn if projected utilization is above 90%
				e.logger.Warnf("Resource quota utilization would be high: projected %.1f%% (current: %.1f%%)",
					projectedUtilization, currentUtilization)
			} else {
				e.logger.Debugf("Resource quota check passed: current %.1f%%, projected %.1f%%",
					currentUtilization, projectedUtilization)
			}
		}
	}

	if violationDetected {
		// Fail fast to prevent wasted API calls
		// Kubernetes API will reject the operation anyway, so we save time and resources
		e.logger.Warn("Resource quota violation detected - failing operation to prevent wasted API calls")
		metrics.ResourceQuotaViolations.WithLabelValues(namespace, resourceType).Inc()
		return fmt.Errorf("resource quota would be exceeded: projected usage exceeds limit for %s in namespace %s", resourceType, namespace)
	}

	return nil
}
