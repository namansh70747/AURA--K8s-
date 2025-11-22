package remediation

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// getRemediationDelay returns delay between remediations from environment or default
func getRemediationDelay() time.Duration {
	if val := os.Getenv("REMEDIATION_DELAY_SECONDS"); val != "" {
		if delay, err := strconv.Atoi(val); err == nil && delay > 0 {
			return time.Duration(delay) * time.Second
		}
	}
	return 2 * time.Second
}

// getActionDelay returns delay between actions in a plan from environment or default
func getActionDelay() time.Duration {
	if val := os.Getenv("REMEDIATION_ACTION_DELAY_SECONDS"); val != "" {
		if delay, err := strconv.Atoi(val); err == nil && delay > 0 {
			return time.Duration(delay) * time.Second
		}
	}
	return 3 * time.Second
}

type RemediationPlan struct {
	Actions    []RemediationAction `json:"actions"`
	Reasoning  string              `json:"reasoning"`
	Confidence float64             `json:"confidence"`
	RiskLevel  string              `json:"risk_level"`
}

type RemediationAction struct {
	Type       string                 `json:"type"`
	Target     string                 `json:"target"`
	Operation  string                 `json:"operation"`
	Parameters map[string]interface{} `json:"parameters"`
	Order      int                    `json:"order"`
}

// ActionState tracks the execution state of a remediation action for rollback purposes
type ActionState struct {
	Action    RemediationAction
	Executed  bool
	Timestamp time.Time
}

type Remediator struct {
	k8sClient  *k8s.Client
	db         *storage.PostgresDB
	mcpURL     string
	executor   *RemediationExecutor
	dryRun     bool
	httpClient *http.Client // Reusable HTTP client for MCP calls
}

func NewRemediator(k8sClient *k8s.Client, db *storage.PostgresDB, mcpURL string) *Remediator {
	return &Remediator{
		k8sClient: k8sClient,
		db:        db,
		mcpURL:    mcpURL,
		executor:  NewRemediationExecutor(k8sClient),
		dryRun:    false,
		httpClient: &http.Client{
			Timeout: config.GetMCPTimeout(),
			Transport: &http.Transport{
				MaxIdleConns:        100,
				MaxIdleConnsPerHost: 10,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}
}

func (r *Remediator) SetDryRun(dryRun bool) {
	r.dryRun = dryRun
	r.executor.SetDryRun(dryRun)
}

func (r *Remediator) ProcessRemediations(ctx context.Context) error {
	start := time.Now()
	defer func() {
		duration := time.Since(start)
		metrics.RecordRemediationDuration(duration)
	}()

	utils.Log.Info("🔍 Processing remediations with AI assistance")

	issues, err := r.db.GetOpenIssues(ctx)
	if err != nil {
		return fmt.Errorf("failed to get open issues: %w", err)
	}

	if len(issues) == 0 {
		utils.Log.Info("📋 No open issues to process")
		return nil
	}

	utils.Log.Infof("📋 Found %d open issues to process", len(issues))

	successCount := 0
	failureCount := 0

	for _, issue := range issues {
		// Check if remediation was already applied (prevent duplicate remediations)
		hasRemediation, err := r.db.HasSuccessfulRemediation(ctx, issue.ID)
		if err == nil && hasRemediation {
			utils.Log.WithField("issue_id", issue.ID).Info("Remediation already applied, skipping")
			successCount++
			metrics.RemediationsTotal.WithLabelValues("skipped").Inc()
			continue
		}

		// Retry logic for failed remediations (max 2 retries for transient failures)
		maxRetries := 2
		var lastErr error
		success := false

		for attempt := 0; attempt <= maxRetries; attempt++ {
			// Double-check if remediation was applied between attempts
			if attempt > 0 {
				hasRemediation, err := r.db.HasSuccessfulRemediation(ctx, issue.ID)
				if err == nil && hasRemediation {
					utils.Log.WithField("issue_id", issue.ID).Info("Remediation applied during retry, skipping")
					success = true
					break
				}
			}

			if attempt > 0 {
				backoff := time.Duration(attempt) * 5 * time.Second
				utils.Log.WithField("issue_id", issue.ID).Infof("Retrying remediation (attempt %d/%d) after %v", attempt+1, maxRetries+1, backoff)

				// Check context cancellation
				select {
				case <-ctx.Done():
					return ctx.Err()
				case <-time.After(backoff):
				}
			}

			if err := r.processIssue(ctx, issue); err != nil {
				lastErr = err
				// Check if error is retryable (transient failures)
				if attempt < maxRetries && isRetryableError(err) {
					continue // Retry
				}
				// Non-retryable or max retries reached
				break
			} else {
				success = true
				break
			}
		}

		if success {
			successCount++
			metrics.RemediationsTotal.WithLabelValues("success").Inc()
		} else {
			utils.Log.WithError(lastErr).WithField("issue_id", issue.ID).Error("❌ Failed to process issue after retries")
			failureCount++
			metrics.RemediationsTotal.WithLabelValues("failed").Inc()
		}

		// Configurable sleep between remediations
		remediationDelay := getRemediationDelay()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(remediationDelay):
		}
	}

	// Update success rate metric
	if successCount+failureCount > 0 {
		successRate := float64(successCount) / float64(successCount+failureCount)
		metrics.RemediationSuccessRate.Set(successRate)
	}

	utils.Log.Infof("✅ Remediation complete: %d succeeded, %d failed", successCount, failureCount)
	return nil
}

func (r *Remediator) processIssue(ctx context.Context, issue *metrics.Issue) error {
	utils.Log.WithField("issue_id", issue.ID).Infof("🔧 Processing issue: %s (%s)", issue.Description, issue.IssueType)

	pod, err := r.k8sClient.GetPod(ctx, issue.Namespace, issue.PodName)
	if err != nil {
		utils.Log.WithError(err).Warn("⚠️  Pod not found, marking issue as resolved")
		return r.markIssueResolved(ctx, issue, "pod_not_found", "Pod no longer exists", true, 0)
	}

	issueContext := r.gatherContext(ctx, issue, pod)

	plan, err := r.getAIRemediationPlan(ctx, issue, issueContext)
	if err != nil {
		utils.Log.WithError(err).Error("❌ Failed to get AI remediation plan")
		fallbackPlan := r.getFallbackPlan(issue, pod)
		plan = &fallbackPlan
	}

	utils.Log.Infof("🤖 AI Plan: %s (confidence: %.2f, risk: %s)", plan.Reasoning, plan.Confidence, plan.RiskLevel)

	// Validate plan before execution - resolve placeholder targets when possible
	if err := r.validatePlanWithPod(plan, pod); err != nil {
		utils.Log.WithError(err).WithFields(map[string]interface{}{
			"issue_id":           issue.ID,
			"pod_name":           issue.PodName,
			"namespace":          issue.Namespace,
			"issue_type":         issue.IssueType,
			"plan_actions_count": len(plan.Actions),
			"plan_confidence":    plan.Confidence,
			"plan_risk_level":    plan.RiskLevel,
		}).Error("Invalid remediation plan: validation failed")
		metrics.RemediationPlanValidationErrors.Inc()
		return r.recordFailedRemediation(ctx, issue, "invalid_plan",
			fmt.Sprintf("Plan validation failed for issue %s (pod: %s/%s, type: %s): %v. Plan had %d actions, confidence: %.2f, risk: %s",
				issue.ID, issue.Namespace, issue.PodName, issue.IssueType, err, len(plan.Actions), plan.Confidence, plan.RiskLevel), 0)
	}

	minConfidence := config.GetMinConfidenceForRemediation()
	if plan.Confidence < minConfidence {
		utils.Log.Warnf("⚠️  Low confidence (%.2f < %.2f), skipping remediation", plan.Confidence, minConfidence)
		return r.recordFailedRemediation(ctx, issue, "low_confidence",
			fmt.Sprintf("AI confidence too low: %.2f < %.2f", plan.Confidence, minConfidence), 0)
	}

	highConfidenceThreshold := config.GetHighConfidenceThreshold()
	if plan.RiskLevel == "high" && plan.Confidence < highConfidenceThreshold {
		utils.Log.Warn("⚠️  High risk with insufficient confidence, requires manual approval")
		return r.recordFailedRemediation(ctx, issue, "high_risk",
			"High-risk remediation requires manual approval or higher confidence", 0)
	}

	// Track execution start time for duration calculation
	executionStart := time.Now()
	if err := r.executePlan(ctx, issue, pod, plan); err != nil {
		utils.Log.WithError(err).Error("❌ Failed to execute remediation plan")
		metrics.RemediationsTotal.WithLabelValues("failed").Inc()
		executionDuration := time.Since(executionStart)
		return r.recordFailedRemediation(ctx, issue, "execution_failed", err.Error(), executionDuration)
	}

	executionDuration := time.Since(executionStart)
	utils.Log.Infof("✅ Remediation executed successfully in %v", executionDuration)
	metrics.RemediationsTotal.WithLabelValues("success").Inc()
	return r.markIssueResolved(ctx, issue, "remediation_applied", plan.Reasoning, true, executionDuration)
}

func (r *Remediator) gatherContext(ctx context.Context, issue *metrics.Issue, pod *corev1.Pod) map[string]interface{} {
	context := make(map[string]interface{})

	context["pod_status"] = pod.Status.Phase
	context["restart_count"] = r.getRestartCount(pod)
	context["node_name"] = pod.Spec.NodeName
	context["age_seconds"] = time.Since(pod.CreationTimestamp.Time).Seconds()

	containerStates := make([]map[string]interface{}, 0)
	for _, cs := range pod.Status.ContainerStatuses {
		state := map[string]interface{}{
			"name":          cs.Name,
			"ready":         cs.Ready,
			"restart_count": cs.RestartCount,
			"image":         cs.Image,
		}

		if cs.State.Waiting != nil {
			state["state"] = "waiting"
			state["reason"] = cs.State.Waiting.Reason
			state["message"] = cs.State.Waiting.Message
		} else if cs.State.Running != nil {
			state["state"] = "running"
			state["started_at"] = cs.State.Running.StartedAt.Time
		} else if cs.State.Terminated != nil {
			state["state"] = "terminated"
			state["reason"] = cs.State.Terminated.Reason
			state["exit_code"] = cs.State.Terminated.ExitCode
			state["message"] = cs.State.Terminated.Message
		}

		containerStates = append(containerStates, state)
	}
	context["containers"] = containerStates

	resources := make([]map[string]interface{}, 0)
	for _, container := range pod.Spec.Containers {
		res := map[string]interface{}{"name": container.Name}

		if container.Resources.Requests != nil {
			res["requests"] = map[string]string{
				"cpu":    container.Resources.Requests.Cpu().String(),
				"memory": container.Resources.Requests.Memory().String(),
			}
		}

		if container.Resources.Limits != nil {
			res["limits"] = map[string]string{
				"cpu":    container.Resources.Limits.Cpu().String(),
				"memory": container.Resources.Limits.Memory().String(),
			}
		}

		resources = append(resources, res)
	}
	context["resources"] = resources

	conditions := make([]map[string]interface{}, 0)
	for _, cond := range pod.Status.Conditions {
		conditions = append(conditions, map[string]interface{}{
			"type":    cond.Type,
			"status":  cond.Status,
			"reason":  cond.Reason,
			"message": cond.Message,
		})
	}
	context["conditions"] = conditions

	if len(pod.OwnerReferences) > 0 {
		owner := pod.OwnerReferences[0]
		context["owner"] = map[string]string{
			"kind": owner.Kind,
			"name": owner.Name,
		}
	}

	events, err := r.k8sClient.Clientset().CoreV1().Events(issue.Namespace).List(ctx,
		metav1.ListOptions{
			FieldSelector: fmt.Sprintf("involvedObject.name=%s", issue.PodName),
			Limit:         5,
		})
	if err == nil && len(events.Items) > 0 {
		eventList := make([]map[string]interface{}, 0)
		for _, event := range events.Items {
			eventList = append(eventList, map[string]interface{}{
				"reason":  event.Reason,
				"message": event.Message,
				"count":   event.Count,
				"type":    event.Type,
			})
		}
		context["recent_events"] = eventList
	}

	return context
}

func (r *Remediator) getAIRemediationPlan(ctx context.Context, issue *metrics.Issue, context map[string]interface{}) (*RemediationPlan, error) {
	if issue == nil {
		return nil, fmt.Errorf("issue parameter cannot be nil")
	}

	payload := map[string]interface{}{
		"issue_id":    issue.ID,
		"pod_name":    issue.PodName,
		"namespace":   issue.Namespace,
		"issue_type":  issue.IssueType,
		"severity":    issue.Severity,
		"description": issue.Description,
		"context":     context,
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Log request for debugging (redact sensitive data if any)
	utils.Log.WithFields(map[string]interface{}{
		"issue_id":   issue.ID,
		"pod_name":   issue.PodName,
		"namespace":  issue.Namespace,
		"issue_type": issue.IssueType,
		"severity":   issue.Severity,
		"url":        r.mcpURL + "/analyze-with-plan",
	}).Debug("Calling MCP server for remediation plan")

	// Retry logic with exponential backoff
	maxRetries := 3
	var plan RemediationPlan
	var lastErr error
	startTime := time.Now()

	for attempt := 0; attempt < maxRetries; attempt++ {
		// Use reusable HTTP client instead of creating new one each time
		req, err := http.NewRequestWithContext(ctx, "POST", r.mcpURL+"/analyze-with-plan", bytes.NewBuffer(jsonData))
		if err != nil {
			lastErr = fmt.Errorf("failed to create request: %w", err)
			metrics.MCPRequestsTotal.WithLabelValues("analyze-with-plan", "error").Inc()
			if attempt < maxRetries-1 {
				backoff := time.Duration(attempt+1) * 2 * time.Second
				utils.Log.WithError(err).Warnf("MCP server call failed (attempt %d/%d), retrying in %v", attempt+1, maxRetries, backoff)
				time.Sleep(backoff)
				continue
			}
			metrics.MCPRequestDuration.WithLabelValues("analyze-with-plan").Observe(time.Since(startTime).Seconds())
			return nil, lastErr
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := r.httpClient.Do(req)

		if err != nil {
			lastErr = fmt.Errorf("failed to call MCP server: %w", err)
			metrics.MCPRequestsTotal.WithLabelValues("analyze-with-plan", "error").Inc()
			if attempt < maxRetries-1 {
				backoff := time.Duration(attempt+1) * 2 * time.Second
				utils.Log.WithError(err).Warnf("MCP server call failed (attempt %d/%d), retrying in %v", attempt+1, maxRetries, backoff)
				time.Sleep(backoff)
				continue
			}
			metrics.MCPRequestDuration.WithLabelValues("analyze-with-plan").Observe(time.Since(startTime).Seconds())
			return nil, lastErr
		}

		if resp.StatusCode != http.StatusOK {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			// Limit body length for logging
			bodyStr := string(body)
			if len(bodyStr) > 500 {
				bodyStr = bodyStr[:500] + "..."
			}
			lastErr = fmt.Errorf("MCP server returned status %d: %s", resp.StatusCode, bodyStr)
			utils.Log.WithFields(map[string]interface{}{
				"status_code": resp.StatusCode,
				"response":    bodyStr,
				"attempt":     attempt + 1,
			}).Error("MCP server returned error")
			metrics.MCPRequestsTotal.WithLabelValues("analyze-with-plan", "error").Inc()
			if attempt < maxRetries-1 {
				backoff := time.Duration(attempt+1) * 2 * time.Second
				utils.Log.Warnf("MCP server returned error (attempt %d/%d), retrying in %v", attempt+1, maxRetries, backoff)
				time.Sleep(backoff)
				continue
			}
			metrics.MCPRequestDuration.WithLabelValues("analyze-with-plan").Observe(time.Since(startTime).Seconds())
			return nil, lastErr
		}

		if err := json.NewDecoder(resp.Body).Decode(&plan); err != nil {
			resp.Body.Close()
			lastErr = fmt.Errorf("failed to decode response: %w", err)
			utils.Log.WithError(err).Error("Failed to decode MCP response")
			metrics.MCPRequestsTotal.WithLabelValues("analyze-with-plan", "error").Inc()
			if attempt < maxRetries-1 {
				backoff := time.Duration(attempt+1) * 2 * time.Second
				utils.Log.Warnf("Failed to decode MCP response (attempt %d/%d), retrying in %v", attempt+1, maxRetries, backoff)
				time.Sleep(backoff)
				continue
			}
			metrics.MCPRequestDuration.WithLabelValues("analyze-with-plan").Observe(time.Since(startTime).Seconds())
			return nil, lastErr
		}
		resp.Body.Close()

		// Log successful response (summary only)
		utils.Log.WithFields(map[string]interface{}{
			"actions_count": len(plan.Actions),
			"confidence":    plan.Confidence,
			"risk_level":    plan.RiskLevel,
		}).Debug("MCP server returned remediation plan")
		metrics.MCPRequestsTotal.WithLabelValues("analyze-with-plan", "success").Inc()
		metrics.MCPRequestDuration.WithLabelValues("analyze-with-plan").Observe(time.Since(startTime).Seconds())

		if len(plan.Actions) > 0 {
			return &plan, nil
		}

		lastErr = fmt.Errorf("AI returned empty action plan")
		if attempt < maxRetries-1 {
			backoff := time.Duration(attempt+1) * 2 * time.Second
			utils.Log.Warnf("Empty action plan (attempt %d/%d), retrying in %v", attempt+1, maxRetries, backoff)
			time.Sleep(backoff)
		}
	}

	return nil, fmt.Errorf("MCP server unavailable after %d attempts: %w", maxRetries, lastErr)
}

func (r *Remediator) getFallbackPlan(issue *metrics.Issue, pod *corev1.Pod) RemediationPlan {
	var actions []RemediationAction
	reasoning := "Fallback remediation based on issue type"
	confidence := 0.6
	riskLevel := "medium"

	// Resolve actual resource names from pod metadata
	podName := pod.Name
	var deploymentName string
	var statefulSetName string

	// Try to find deployment or statefulset from owner references
	if len(pod.OwnerReferences) > 0 {
		for _, owner := range pod.OwnerReferences {
			if owner.Kind == "ReplicaSet" {
				// Try to get deployment from replicaset
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				rs, err := r.k8sClient.Clientset().AppsV1().ReplicaSets(pod.Namespace).Get(ctx, owner.Name, metav1.GetOptions{})
				if err == nil && len(rs.OwnerReferences) > 0 {
					for _, rsOwner := range rs.OwnerReferences {
						if rsOwner.Kind == "Deployment" {
							deploymentName = rsOwner.Name
							break
						}
					}
				}
			} else if owner.Kind == "StatefulSet" {
				statefulSetName = owner.Name
			}
		}
	}

	switch issue.IssueType {
	case "OOMKilled", "oom_killed", "high_memory":
		if deploymentName != "" {
			actions = []RemediationAction{
				{
					Type:       "deployment",
					Target:     deploymentName,
					Operation:  "increase_memory",
					Parameters: map[string]interface{}{"factor": 1.5},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("OOM detected - increasing memory by 50%% for deployment %s", deploymentName)
		} else if statefulSetName != "" {
			actions = []RemediationAction{
				{
					Type:       "statefulset",
					Target:     statefulSetName,
					Operation:  "increase_memory",
					Parameters: map[string]interface{}{"factor": 1.5},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("OOM detected - increasing memory by 50%% for statefulset %s", statefulSetName)
		} else {
			actions = []RemediationAction{
				{
					Type:       "pod",
					Target:     podName,
					Operation:  "restart",
					Parameters: map[string]interface{}{"grace_period_seconds": 30},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("OOM detected - restarting pod %s (no deployment/statefulset found)", podName)
		}

	case "CrashLoopBackOff", "crash_loop":
		actions = []RemediationAction{
			{
				Type:       "pod",
				Target:     podName,
				Operation:  "restart",
				Parameters: map[string]interface{}{"grace_period_seconds": 30},
				Order:      0,
			},
		}
		reasoning = fmt.Sprintf("Crash loop detected - restarting pod %s", podName)

	case "high_cpu", "HighCPU", "cpu_spike":
		if deploymentName != "" {
			actions = []RemediationAction{
				{
					Type:       "deployment",
					Target:     deploymentName,
					Operation:  "increase_cpu",
					Parameters: map[string]interface{}{"factor": 1.5},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("High CPU usage - increasing CPU limits by 50%% for deployment %s", deploymentName)
		} else if statefulSetName != "" {
			actions = []RemediationAction{
				{
					Type:       "statefulset",
					Target:     statefulSetName,
					Operation:  "increase_cpu",
					Parameters: map[string]interface{}{"factor": 1.5},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("High CPU usage - increasing CPU limits by 50%% for statefulset %s", statefulSetName)
		} else {
			actions = []RemediationAction{
				{
					Type:       "pod",
					Target:     podName,
					Operation:  "restart",
					Parameters: map[string]interface{}{"grace_period_seconds": 30},
					Order:      0,
				},
			}
			reasoning = fmt.Sprintf("High CPU usage - restarting pod %s (no deployment/statefulset found)", podName)
		}

	case "ImagePullBackOff", "image_pull_backoff":
		actions = []RemediationAction{
			{
				Type:       "pod",
				Target:     podName,
				Operation:  "restart",
				Parameters: map[string]interface{}{"grace_period_seconds": 10},
				Order:      0,
			},
		}
		reasoning = fmt.Sprintf("Image pull failure - restarting pod %s to retry", podName)

	default:
		actions = []RemediationAction{
			{
				Type:       "pod",
				Target:     podName,
				Operation:  "restart",
				Parameters: map[string]interface{}{"grace_period_seconds": 30},
				Order:      0,
			},
		}
		reasoning = fmt.Sprintf("Unknown issue type %s - attempting pod restart for %s", issue.IssueType, podName)
	}

	return RemediationPlan{
		Actions:    actions,
		Reasoning:  reasoning,
		Confidence: confidence,
		RiskLevel:  riskLevel,
	}
}

func (r *Remediator) executePlan(ctx context.Context, _ *metrics.Issue, pod *corev1.Pod, plan *RemediationPlan) error {
	utils.Log.Infof("📝 Executing remediation plan with %d actions", len(plan.Actions))

	sort.Slice(plan.Actions, func(i, j int) bool {
		return plan.Actions[i].Order < plan.Actions[j].Order
	})

	// Check context cancellation before starting
	if ctx.Err() != nil {
		return fmt.Errorf("context cancelled before plan execution: %w", ctx.Err())
	}

	// Track executed actions for rollback
	executedActions := make([]ActionState, 0, len(plan.Actions))

	for _, action := range plan.Actions {
		// Check context cancellation before each action
		if ctx.Err() != nil {
			utils.Log.Warn("Context cancelled, attempting rollback of executed actions")
			r.rollbackActions(ctx, pod, executedActions)
			return fmt.Errorf("context cancelled during plan execution: %w", ctx.Err())
		}

		utils.Log.Infof("⚡ Executing action %d: %s on %s", action.Order, action.Operation, action.Target)

		if err := r.executor.ExecuteAction(ctx, pod, action); err != nil {
			utils.Log.WithError(err).Errorf("Action %d failed, attempting rollback", action.Order)
			// Attempt rollback of previously executed actions
			rollbackErr := r.rollbackActions(ctx, pod, executedActions)
			if rollbackErr != nil {
				utils.Log.WithError(rollbackErr).Error("Rollback failed - system may be in inconsistent state")
			}
			return fmt.Errorf("action %d failed: %w", action.Order, err)
		}

		// Mark action as executed
		executedActions = append(executedActions, ActionState{
			Action:    action,
			Executed:  true,
			Timestamp: time.Now(),
		})

		utils.Log.Infof("✅ Action %d completed", action.Order)

		// Configurable delay between actions in a plan (with context cancellation support)
		if action.Order < len(plan.Actions)-1 {
			actionDelay := getActionDelay()
			select {
			case <-ctx.Done():
				utils.Log.Warn("Context cancelled during delay, attempting rollback")
				r.rollbackActions(ctx, pod, executedActions)
				return fmt.Errorf("context cancelled during action delay: %w", ctx.Err())
			case <-time.After(actionDelay):
				// Continue to next action
			}
		}
	}

	return nil
}

// rollbackActions attempts to rollback executed actions (best-effort)
// Documents which operations cannot be rolled back and implements proper state tracking
func (r *Remediator) rollbackActions(ctx context.Context, pod *corev1.Pod, executedActions []ActionState) error {
	if len(executedActions) == 0 {
		return nil
	}

	utils.Log.Warnf("Attempting to rollback %d executed actions", len(executedActions))

	// Track rollback results
	rollbackResults := make(map[int]bool)

	// Rollback in reverse order
	for i := len(executedActions) - 1; i >= 0; i-- {
		actionState := executedActions[i]
		if !actionState.Executed {
			utils.Log.Debugf("Skipping non-executed action %d", actionState.Action.Order)
			continue
		}

		// Validate action state before attempting rollback
		if actionState.Timestamp.IsZero() {
			utils.Log.Warnf("Action %d has zero timestamp, may not be valid for rollback", actionState.Action.Order)
		}

		// Create reverse action for rollback
		rollbackAction := r.createRollbackAction(actionState.Action)
		if rollbackAction == nil {
			metrics.RollbackOperationsTotal.WithLabelValues("skipped").Inc()
			// Document which operations cannot be rolled back
			nonRollbackableOps := []string{"restart", "delete", "update_image", "restart_rollout"}
			isNonRollbackable := false
			for _, op := range nonRollbackableOps {
				if actionState.Action.Operation == op {
					isNonRollbackable = true
					break
				}
			}

			if isNonRollbackable {
				utils.Log.Warnf("Cannot rollback action %d: %s - operation is non-reversible. "+
					"Alternative recovery: Monitor pod health and manually intervene if needed.",
					actionState.Action.Order, actionState.Action.Operation)
			} else {
				utils.Log.Warnf("Cannot create rollback for action %d: %s - unknown operation",
					actionState.Action.Order, actionState.Action.Operation)
			}
			rollbackResults[actionState.Action.Order] = false
			continue
		}

		// Validate rollback action before execution
		if rollbackAction.Type == "" || rollbackAction.Operation == "" {
			utils.Log.Warnf("Invalid rollback action for action %d: missing type or operation",
				actionState.Action.Order)
			rollbackResults[actionState.Action.Order] = false
			continue
		}

		utils.Log.Infof("Rolling back action %d: %s", actionState.Action.Order, actionState.Action.Operation)

		// Execute rollback with timeout context
		rollbackCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()

		if err := r.executor.ExecuteAction(rollbackCtx, pod, *rollbackAction); err != nil {
			utils.Log.WithError(err).Errorf("Failed to rollback action %d", actionState.Action.Order)
			rollbackResults[actionState.Action.Order] = false
			metrics.RollbackOperationsTotal.WithLabelValues("failed").Inc()
			// Continue with other rollbacks - don't fail entire rollback process
		} else {
			utils.Log.Infof("Successfully rolled back action %d", actionState.Action.Order)
			rollbackResults[actionState.Action.Order] = true
			metrics.RollbackOperationsTotal.WithLabelValues("success").Inc()
		}
	}

	// Log rollback summary
	successCount := 0
	failureCount := 0
	for order, success := range rollbackResults {
		if success {
			successCount++
		} else {
			failureCount++
			utils.Log.Warnf("Rollback failed for action %d", order)
		}
	}

	if failureCount > 0 {
		utils.Log.Warnf("Rollback completed with %d successes and %d failures. System may be in inconsistent state.",
			successCount, failureCount)
		return fmt.Errorf("rollback completed with %d failures out of %d total actions", failureCount, successCount+failureCount)
	} else {
		utils.Log.Infof("All rollbacks completed successfully (%d actions)", successCount)
	}

	return nil
}

// createRollbackAction creates a reverse action for rollback
func (r *Remediator) createRollbackAction(action RemediationAction) *RemediationAction {
	switch action.Operation {
	case "increase_memory", "increase_cpu":
		// Rollback: decrease by same factor (1/factor)
		factorParam, hasFactor := action.Parameters["factor"]
		if !hasFactor {
			return nil
		}
		factor, err := ValidateFactor(factorParam, 0.1, 5.0)
		if err != nil || factor <= 0 {
			return nil
		}
		rollbackFactor := 1.0 / factor
		return &RemediationAction{
			Type:       action.Type,
			Target:     action.Target,
			Operation:  action.Operation,
			Parameters: map[string]interface{}{"factor": rollbackFactor},
			Order:      action.Order,
		}
	case "scale":
		// Rollback: scale in opposite direction
		direction := ""
		if d, ok := action.Parameters["direction"].(string); ok {
			direction = d
		}
		replicasParam, hasReplicas := action.Parameters["replicas"]
		if !hasReplicas {
			return nil
		}
		replicas, err := ValidateReplicas(replicasParam, 1, 100)
		if err != nil {
			return nil
		}
		var rollbackDirection string
		if direction == "up" {
			rollbackDirection = "down"
		} else {
			rollbackDirection = "up"
		}
		return &RemediationAction{
			Type:       action.Type,
			Target:     action.Target,
			Operation:  action.Operation,
			Parameters: map[string]interface{}{"replicas": replicas, "direction": rollbackDirection},
			Order:      action.Order,
		}
	case "restart", "delete", "update_image", "restart_rollout":
		// These actions are NOT REVERSIBLE and cannot be rolled back:
		//
		// - restart: Pod is deleted and recreated by controller. Previous pod state is lost.
		//   Recovery: Monitor pod health; if issues occur, manually restart or revert deployment.
		//
		// - delete: Pod is permanently removed from the cluster.
		//   Recovery: Controller will recreate if part of deployment/statefulset; otherwise manual recreation required.
		//
		// - update_image: Image change is applied to deployment template permanently.
		//   Recovery: Revert image in deployment spec to previous version, or rollback entire deployment.
		//
		// - restart_rollout: Triggers rolling restart of all pods in deployment.
		//   Recovery: Wait for rollout completion; if issues occur, rollback deployment to previous revision.
		//
		// IMPORTANT: These operations will return nil (no rollback action) to indicate they cannot be reversed.
		// The caller should handle this appropriately and not expect a rollback action.

		// Implement alternative recovery strategy based on operation type
		utils.Log.Warnf("Cannot rollback action: %s - implementing alternative recovery strategy", action.Operation)

		// For update_image, we can create a rollback action to revert the image
		if action.Operation == "update_image" {
			// Try to get previous image from deployment history or parameters
			// This is a simplified approach - in production, you'd query deployment history
			if prevImage, ok := action.Parameters["previous_image"].(string); ok && prevImage != "" {
				return &RemediationAction{
					Type:       action.Type,
					Target:     action.Target,
					Operation:  "update_image",
					Parameters: map[string]interface{}{"image": prevImage},
					Order:      action.Order,
				}
			}
			// If no previous image available, log warning
			utils.Log.Warnf("No previous image available for rollback of update_image operation")
		}

		// For restart_rollout, we can attempt to rollback the deployment
		// Improved implementation with better error handling
		if action.Operation == "restart_rollout" && action.Type == "deployment" {
			// Create a rollback action to revert deployment to previous revision
			// Note: This requires deployment history to be available
			// The executor will validate deployment history availability before attempting rollback
			return &RemediationAction{
				Type:       action.Type,
				Target:     action.Target,
				Operation:  "rollback_deployment",
				Parameters: map[string]interface{}{"to_revision": "previous"},
				Order:      action.Order,
			}
		}

		// For restart and delete, we can only monitor and potentially recreate
		// These are logged but no rollback action is created
		utils.Log.Warnf("Alternative recovery for %s: Monitor pod health and manually intervene if needed", action.Operation)
		return nil
	default:
		utils.Log.Warnf("Unknown operation for rollback: %s", action.Operation)
		return nil
	}
}

// validatePlanWithPod validates a remediation plan with optional pod context for target resolution
// Creates a copy of actions for validation to avoid modifying the original plan
func (r *Remediator) validatePlanWithPod(plan *RemediationPlan, pod *corev1.Pod) error {
	if plan == nil {
		return fmt.Errorf("plan is nil")
	}

	if len(plan.Actions) == 0 {
		return fmt.Errorf("plan has no actions")
	}

	// Create a copy of actions for validation to avoid modifying original
	actionsCopy := make([]RemediationAction, len(plan.Actions))
	for i, action := range plan.Actions {
		actionsCopy[i] = action
		// Deep copy parameters map
		if action.Parameters != nil {
			actionsCopy[i].Parameters = make(map[string]interface{})
			for k, v := range action.Parameters {
				actionsCopy[i].Parameters[k] = v
			}
		}
	}

	// Pre-execution validation: check resource existence, quotas, and permissions
	// This is done before execution to fail fast
	// Note: Full resource existence checks require pod context which is available during execution
	// For now, we validate format and structure - existence is checked in executor

	// Validate each action
	validTypes := map[string]bool{
		"pod":         true,
		"deployment":  true,
		"statefulset": true,
		"node":        true,
	}

	validPodOps := map[string]bool{
		"restart": true,
		"delete":  true,
	}

	validDeployOps := map[string]bool{
		"increase_memory":     true,
		"increase_cpu":        true,
		"scale":               true,
		"update_image":        true,
		"restart_rollout":     true,
		"rollback_deployment": true,
	}

	validSSOps := map[string]bool{
		"increase_memory":      true,
		"increase_cpu":         true,
		"rollback_statefulset": true,
	}

	validNodeOps := map[string]bool{
		"drain":    true,
		"uncordon": true,
		"taint":    true,
		"untaint":  true,
		"label":    true,
	}

	for i, action := range actionsCopy {
		if !validTypes[action.Type] {
			return fmt.Errorf("action %d: invalid type %s", i, action.Type)
		}

		if action.Target == "" {
			return fmt.Errorf("action %d: target is required", i)
		}

		// Validate target is not a placeholder - resolve when possible
		// Note: We work on the copy, so original plan is not modified
		if action.Target == "pod" || action.Target == "deployment" || action.Target == "statefulset" || action.Target == "node" {
			// Try to resolve placeholder if pod context is available
			if pod != nil {
				// Resolve pod placeholder
				if action.Target == "pod" && action.Type == "pod" {
					action.Target = pod.Name
					utils.Log.Debugf("Action %d: resolved placeholder 'pod' to actual pod name: %s", i, action.Target)
				}
				// Try to resolve deployment/statefulset from pod owner references
				if action.Type == "deployment" && (action.Target == "deployment" || action.Target == "") {
					if len(pod.OwnerReferences) > 0 {
						for _, owner := range pod.OwnerReferences {
							if owner.Kind == "ReplicaSet" {
								// Try to get deployment from replicaset
								ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
								rs, err := r.k8sClient.Clientset().AppsV1().ReplicaSets(pod.Namespace).Get(ctx, owner.Name, metav1.GetOptions{})
								cancel()
								if err == nil && len(rs.OwnerReferences) > 0 {
									for _, rsOwner := range rs.OwnerReferences {
										if rsOwner.Kind == "Deployment" {
											action.Target = rsOwner.Name
											utils.Log.Debugf("Action %d: resolved placeholder 'deployment' to actual deployment name: %s", i, action.Target)
											break
										}
									}
								}
							}
						}
					}
				}
				if action.Type == "statefulset" && (action.Target == "statefulset" || action.Target == "") {
					if len(pod.OwnerReferences) > 0 {
						for _, owner := range pod.OwnerReferences {
							if owner.Kind == "StatefulSet" {
								action.Target = owner.Name
								utils.Log.Debugf("Action %d: resolved placeholder 'statefulset' to actual statefulset name: %s", i, action.Target)
								break
							}
						}
					}
				}
			}

			// After resolution attempt, validate that placeholder is resolved for deployment/statefulset
			if action.Type == "deployment" || action.Type == "statefulset" {
				if action.Target == "deployment" || action.Target == "statefulset" || action.Target == "" {
					// Try one more time to resolve from pod if available
					if pod != nil && action.Type == "deployment" {
						// Try to get deployment directly from pod using k8s client helper
						resolveCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
						deployment, err := r.k8sClient.GetDeploymentForPod(resolveCtx, pod.Namespace, pod.Name)
						cancel()
						if err == nil && deployment != nil {
							action.Target = deployment.Name
							utils.Log.Debugf("Action %d: resolved deployment placeholder via GetDeploymentForPod: %s", i, action.Target)
						} else {
							// Provide more detailed error message
							if err != nil {
								return fmt.Errorf("action %d: target '%s' is still a placeholder - could not resolve deployment from pod %s/%s: %w. Ensure pod has proper owner references or provide explicit deployment name", i, action.Target, pod.Namespace, pod.Name, err)
							}
							return fmt.Errorf("action %d: target '%s' is still a placeholder - deployment not found for pod %s/%s. Ensure pod has proper owner references or provide explicit deployment name", i, action.Target, pod.Namespace, pod.Name)
						}
					} else {
						return fmt.Errorf("action %d: target '%s' is still a placeholder - actual resource name required for %s operations. Provide explicit resource name or ensure pod has proper owner references", i, action.Target, action.Type)
					}
				}
			}
			// For pod operations, placeholder is acceptable (will be resolved by executor if not resolved here)
			if action.Type == "pod" && action.Target == "pod" {
				if pod != nil {
					action.Target = pod.Name
					utils.Log.Debugf("Action %d: resolved pod placeholder to actual pod name: %s", i, action.Target)
				} else {
					utils.Log.Debugf("Action %d: target '%s' is a placeholder - will be resolved to actual pod name by executor", i, action.Target)
				}
			}
			// For node operations, resolve from pod if available
			if action.Type == "node" && (action.Target == "node" || action.Target == "") {
				if pod != nil && pod.Spec.NodeName != "" {
					action.Target = pod.Spec.NodeName
					utils.Log.Debugf("Action %d: resolved node placeholder to actual node name: %s", i, action.Target)
				} else if action.Target == "node" || action.Target == "" {
					if pod == nil {
						return fmt.Errorf("action %d: target '%s' is a placeholder and no pod context available - actual node name required", i, action.Target)
					}
					return fmt.Errorf("action %d: target '%s' is a placeholder and pod %s/%s has no node name assigned - actual node name required. Pod may not be scheduled yet", i, action.Target, pod.Namespace, pod.Name)
				}
			}
		}

		// Update original plan with resolved target (only if resolution was successful)
		if action.Target != plan.Actions[i].Target {
			plan.Actions[i].Target = action.Target
		}

		// Pre-execution validation: check resource existence
		// Note: This requires a pod context, which may not always be available during validation
		// For now, we validate that the target format is correct
		// Full existence check happens in executor
		if action.Type == "deployment" && action.Target != "" && action.Target != "deployment" {
			// Validate deployment name format (will be checked for existence in executor)
			if len(action.Target) > 253 {
				return fmt.Errorf("action %d: deployment name '%s' exceeds 253 characters", i, action.Target)
			}
		}

		if action.Type == "statefulset" && action.Target != "" && action.Target != "statefulset" {
			// Validate statefulset name format
			if len(action.Target) > 253 {
				return fmt.Errorf("action %d: statefulset name '%s' exceeds 253 characters", i, action.Target)
			}
		}

		switch action.Type {
		case "pod":
			if !validPodOps[action.Operation] {
				return fmt.Errorf("action %d: invalid pod operation %s", i, action.Operation)
			}
			// Validate pod operation parameters
			if action.Operation == "restart" || action.Operation == "delete" {
				if gracePeriodParam, hasGracePeriod := action.Parameters["grace_period_seconds"]; hasGracePeriod {
					gracePeriod, err := ValidateGracePeriod(gracePeriodParam)
					if err != nil {
						return fmt.Errorf("action %d: %w", i, err)
					}
					// Store validated grace period back
					action.Parameters["grace_period_seconds"] = float64(gracePeriod)
				}
			}
		case "deployment":
			if !validDeployOps[action.Operation] {
				return fmt.Errorf("action %d: invalid deployment operation %s", i, action.Operation)
			}
			// Validate parameters for deployment operations
			if action.Operation == "increase_memory" || action.Operation == "increase_cpu" {
				factorParam, hasFactor := action.Parameters["factor"]
				if !hasFactor {
					return fmt.Errorf("action %d: factor parameter is required for %s operation", i, action.Operation)
				}
				// Use validation helper with reasonable bounds (0.1 to 5.0)
				factor, err := ValidateFactor(factorParam, 0.1, 5.0)
				if err != nil {
					return fmt.Errorf("action %d: %w", i, err)
				}
				// Store validated factor back
				action.Parameters["factor"] = factor
			}
			if action.Operation == "scale" {
				replicasParam, hasReplicas := action.Parameters["replicas"]
				if !hasReplicas {
					return fmt.Errorf("action %d: replicas parameter is required for scale operation", i)
				}
				// Use validation helper with reasonable bounds (1 to 100)
				replicas, err := ValidateReplicas(replicasParam, 1, 100)
				if err != nil {
					return fmt.Errorf("action %d: %w", i, err)
				}
				// Store validated replicas back
				action.Parameters["replicas"] = float64(replicas)

				direction, hasDirection := action.Parameters["direction"]
				if !hasDirection {
					return fmt.Errorf("action %d: direction parameter is required for scale operation", i)
				}
				directionStr, ok := direction.(string)
				if !ok {
					return fmt.Errorf("action %d: direction parameter must be a string, got %T", i, direction)
				}
				if directionStr != "up" && directionStr != "down" {
					return fmt.Errorf("action %d: direction must be 'up' or 'down', got %s", i, directionStr)
				}
			}
			if action.Operation == "update_image" {
				imageParam, hasImage := action.Parameters["image"]
				if !hasImage {
					return fmt.Errorf("action %d: image parameter is required for update_image operation", i)
				}
				// Use validation helper
				imageStr, err := ValidateImage(imageParam)
				if err != nil {
					return fmt.Errorf("action %d: %w", i, err)
				}
				// Store validated image back
				action.Parameters["image"] = imageStr
				// Validate container parameter if provided
				if containerParam, hasContainer := action.Parameters["container"]; hasContainer {
					containerStr, err := ValidateContainer(containerParam)
					if err != nil {
						return fmt.Errorf("action %d: %w", i, err)
					}
					// Store validated container back
					action.Parameters["container"] = containerStr
				}
			}
			if action.Operation == "rollback_deployment" {
				// Validate optional to_revision parameter
				if toRevision, hasRevision := action.Parameters["to_revision"]; hasRevision {
					revisionStr, ok := toRevision.(string)
					if !ok {
						return fmt.Errorf("action %d: to_revision parameter must be a string, got %T", i, toRevision)
					}
					if revisionStr != "previous" && revisionStr != "" {
						// Could validate numeric revision if needed
						if _, err := strconv.Atoi(revisionStr); err != nil && revisionStr != "previous" {
							return fmt.Errorf("action %d: to_revision must be 'previous' or a numeric revision, got %s", i, revisionStr)
						}
					}
				}
			}
		case "statefulset":
			if !validSSOps[action.Operation] {
				return fmt.Errorf("action %d: invalid statefulset operation %s", i, action.Operation)
			}
			// Validate statefulset operation parameters
			if action.Operation == "increase_memory" || action.Operation == "increase_cpu" {
				factorParam, hasFactor := action.Parameters["factor"]
				if !hasFactor {
					return fmt.Errorf("action %d: factor parameter is required for %s operation", i, action.Operation)
				}
				// Use validation helper with reasonable bounds (0.1 to 5.0)
				factor, err := ValidateFactor(factorParam, 0.1, 5.0)
				if err != nil {
					return fmt.Errorf("action %d: %w", i, err)
				}
				// Store validated factor back
				action.Parameters["factor"] = factor
			}
		case "node":
			if !validNodeOps[action.Operation] {
				return fmt.Errorf("action %d: invalid node operation %s", i, action.Operation)
			}
			// Node operations typically don't require parameters, but validate if any are provided
			if len(action.Parameters) > 0 {
				// Allow grace_period for drain if provided
				if action.Operation == "drain" {
					if gracePeriodParam, hasGracePeriod := action.Parameters["grace_period_seconds"]; hasGracePeriod {
						gracePeriod, err := ValidateGracePeriod(gracePeriodParam)
						if err != nil {
							return fmt.Errorf("action %d: %w", i, err)
						}
						// Store validated grace period back
						action.Parameters["grace_period_seconds"] = float64(gracePeriod)
					}
				}
			}
		}
	}

	return nil
}

// isRetryableError checks if an error is retryable (transient failure)
func isRetryableError(err error) bool {
	if err == nil {
		return false
	}
	errStr := err.Error()
	// Retryable errors: network issues, timeouts, temporary service unavailability
	retryablePatterns := []string{
		"timeout",
		"connection refused",
		"temporary",
		"unavailable",
		"circuit breaker",
		"context deadline exceeded",
	}
	for _, pattern := range retryablePatterns {
		if contains(errStr, pattern) {
			return true
		}
	}
	return false
}

// contains checks if string contains substring (case-insensitive)
func contains(s, substr string) bool {
	sLower := strings.ToLower(s)
	substrLower := strings.ToLower(substr)
	return strings.Contains(sLower, substrLower)
}

func (r *Remediator) getRestartCount(pod *corev1.Pod) int32 {
	var total int32
	for _, cs := range pod.Status.ContainerStatuses {
		total += cs.RestartCount
	}
	return total
}

func (r *Remediator) markIssueResolved(ctx context.Context, issue *metrics.Issue, action, message string, success bool, executionDuration time.Duration) error {
	updateQuery := `UPDATE issues SET status = 'Resolved', resolved_at = NOW() WHERE id = $1`
	if _, err := r.db.ExecRaw(ctx, updateQuery, issue.ID); err != nil {
		utils.Log.WithError(err).Error("Failed to update issue status")
		return fmt.Errorf("failed to update issue status: %w", err)
	}

	// Set executed_at and completed_at with actual execution duration
	executedAt := time.Now().Add(-executionDuration) // Start time
	completedAt := time.Now() // End time (executed_at + duration)

	// Generate UUID using Go's uuid package instead of PostgreSQL gen_random_uuid()
	// This avoids dependency on pgcrypto extension
	remediationID := uuid.New().String()

	// Store comprehensive remediation history with action details
	actionDetailsJSON, _ := json.Marshal(map[string]interface{}{
		"action":    action,
		"message":   message,
		"success":   success,
		"timestamp": executedAt,
	})

	query := `INSERT INTO remediations (id, issue_id, pod_name, namespace, action, action_details, executed_at, success, error_message, completed_at, timestamp)
	          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`
	_, err := r.db.ExecRaw(ctx, query, remediationID, issue.ID, issue.PodName, issue.Namespace, action, string(actionDetailsJSON), executedAt, success, message, completedAt, executedAt)
	if err != nil {
		return fmt.Errorf("failed to record remediation: %w", err)
	}

	// Also save rollback history if this was a rollback operation
	if strings.Contains(action, "rollback") || strings.Contains(action, "Rollback") {
		utils.Log.WithField("remediation_id", remediationID).Info("Remediation rollback history recorded")
	}

	return nil
}

func (r *Remediator) recordFailedRemediation(ctx context.Context, issue *metrics.Issue, action, errorMsg string, executionDuration time.Duration) error {
	executedAt := time.Now().Add(-executionDuration) // Start time
	completedAt := time.Now() // End time (even for failures, track when it completed)
	success := false

	// Generate UUID using Go's uuid package instead of PostgreSQL gen_random_uuid()
	// This avoids dependency on pgcrypto extension
	remediationID := uuid.New().String()
	query := `INSERT INTO remediations (id, issue_id, pod_name, namespace, action, executed_at, success, error_message, completed_at)
	          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`
	_, err := r.db.ExecRaw(ctx, query, remediationID, issue.ID, issue.PodName, issue.Namespace, action, executedAt, success, errorMsg, completedAt)
	if err != nil {
		return fmt.Errorf("failed to record failed remediation: %w", err)
	}
	return nil
}
