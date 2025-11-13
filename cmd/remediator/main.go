package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/namansh70747/aura-k8s/pkg/k8s"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/storage"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"golang.org/x/time/rate"
)

var (
	// Prometheus metrics
	remediationsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "aura_remediator_remediations_total",
		Help: "Total number of remediations attempted",
	})
	remediationSuccess = promauto.NewCounter(prometheus.CounterOpts{
		Name: "aura_remediator_remediations_success_total",
		Help: "Total number of successful remediations",
	})
	remediationErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "aura_remediator_remediations_errors_total",
		Help: "Total number of remediation errors",
	})
	issuesProcessed = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "aura_remediator_issues_processed",
		Help: "Number of issues processed in last cycle",
	})
)

type AIRecommendation struct {
	Action        string  `json:"action"`
	ActionDetails string  `json:"action_details"`
	Reasoning     string  `json:"reasoning"`
	Confidence    float64 `json:"confidence"`
}

func main() {
	utils.Log.Info("Starting AURA K8s Remediator")

	// Get configuration
	dbURL := getEnv("DATABASE_URL", "postgres://aura:aura_password@localhost:5432/aura_metrics?sslmode=disable")
	mcpURL := getEnv("MCP_SERVER_URL", "http://mcp-server:8000")
	interval := getEnvDuration("REMEDIATION_INTERVAL", 30*time.Second)
	metricsPort := getEnv("METRICS_PORT", "9091")

	// Initialize K8s client
	k8sClient, err := k8s.NewClient()
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to create Kubernetes client")
	}

	// Initialize database
	db, err := storage.NewPostgresDB(dbURL)
	if err != nil {
		utils.Log.WithError(err).Fatal("Failed to connect to database")
	}
	defer db.Close()

	// Initialize rate limiter (10 remediations per second, burst of 5)
	limiter := rate.NewLimiter(rate.Limit(10), 5)
	utils.Log.Info("Rate limiter initialized: 10 ops/sec with burst of 5")

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
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	// Start remediation loop
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	utils.Log.Infof("Remediation started with interval: %s", interval)

	for {
		select {
		case <-ticker.C:
			if err := processIssues(ctx, k8sClient, db, mcpURL, limiter); err != nil {
				utils.Log.WithError(err).Error("Remediation cycle failed")
			}

		case <-stop:
			utils.Log.Info("Shutting down remediator gracefully...")
			cancel()   // Cancel context to stop ongoing operations
			db.Close() // Explicitly close database connection
			utils.Log.Info("Remediator stopped")
			return
		}
	}
}

func processIssues(ctx context.Context, k8sClient *k8s.Client, db *storage.PostgresDB, mcpURL string, limiter *rate.Limiter) error {
	issues, err := db.GetOpenIssues(ctx)
	if err != nil {
		return fmt.Errorf("failed to get open issues: %w", err)
	}

	utils.Log.Infof("Processing %d open issues", len(issues))
	issuesProcessed.Set(float64(len(issues)))

	for _, issue := range issues {
		// Rate limit API calls to prevent overwhelming K8s API
		if err := limiter.Wait(ctx); err != nil {
			utils.Log.WithError(err).Error("Rate limiter error")
			continue
		}

		remediationsTotal.Inc()
		if err := remediateIssue(ctx, k8sClient, db, mcpURL, issue); err != nil {
			utils.Log.WithError(err).WithField("issue_id", issue.ID).Error("Failed to remediate issue")
			remediationErrors.Inc()
		} else {
			remediationSuccess.Inc()
		}
	}

	return nil
}

func remediateIssue(ctx context.Context, k8sClient *k8s.Client, db *storage.PostgresDB, mcpURL string, issue *metrics.Issue) error {
	utils.Log.WithField("issue_id", issue.ID).WithField("type", issue.IssueType).Info("Remediating issue")

	startTime := time.Now()

	// Get AI recommendation
	recommendation, err := getAIRecommendation(mcpURL, issue)
	if err != nil {
		utils.Log.WithError(err).Warn("Failed to get AI recommendation, using fallback")
		recommendation = getFallbackRecommendation(issue)
	}

	// Execute remediation
	success, errorMsg := executeRemediation(ctx, k8sClient, issue, recommendation)

	// Save remediation record
	remediation := &metrics.Remediation{
		ID:               uuid.New().String(),
		IssueID:          issue.ID,
		PodName:          issue.PodName,
		Namespace:        issue.Namespace,
		Action:           recommendation.Action,
		ActionDetails:    recommendation.ActionDetails,
		ExecutedAt:       time.Now(),
		Success:          success,
		ErrorMessage:     errorMsg,
		AIRecommendation: recommendation.Reasoning,
		TimeToResolve:    int(time.Since(startTime).Seconds()),
	}

	if err := db.SaveRemediation(ctx, remediation); err != nil {
		return fmt.Errorf("failed to save remediation: %w", err)
	}

	// Update issue status
	if success {
		now := time.Now()
		issue.ResolvedAt = &now
		issue.Status = "Resolved"
	} else {
		issue.Status = "InProgress"
	}

	if err := db.SaveIssue(ctx, issue); err != nil {
		return fmt.Errorf("failed to update issue: %w", err)
	}

	return nil
}

func getAIRecommendation(mcpURL string, issue *metrics.Issue) (*AIRecommendation, error) {
	reqBody := map[string]interface{}{
		"issue_id":    issue.ID,
		"pod_name":    issue.PodName,
		"namespace":   issue.Namespace,
		"issue_type":  issue.IssueType,
		"severity":    issue.Severity,
		"description": issue.Description,
	}

	jsonData, _ := json.Marshal(reqBody)
	resp, err := http.Post(mcpURL+"/analyze", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("MCP server returned status: %d", resp.StatusCode)
	}

	var recommendation AIRecommendation
	if err := json.NewDecoder(resp.Body).Decode(&recommendation); err != nil {
		return nil, err
	}

	return &recommendation, nil
}

func getFallbackRecommendation(issue *metrics.Issue) *AIRecommendation {
	rec := &AIRecommendation{
		Confidence: 0.7,
		Reasoning:  "Fallback recommendation based on issue type",
	}

	switch issue.IssueType {
	case "OOMKilled":
		rec.Action = "increase_memory"
		rec.ActionDetails = "Increase memory limit by 50%"

	case "CrashLoopBackOff":
		rec.Action = "restart_pod"
		rec.ActionDetails = "Restart pod to recover from crash loop"

	case "HighCPU":
		rec.Action = "scale_deployment"
		rec.ActionDetails = "Scale deployment to handle CPU load"

	case "DiskPressure":
		rec.Action = "clean_logs"
		rec.ActionDetails = "Clean up logs and temporary files"

	case "NetworkErrors":
		rec.Action = "restart_pod"
		rec.ActionDetails = "Restart pod to reset network state"

	default:
		rec.Action = "restart_pod"
		rec.ActionDetails = "Generic restart to recover from issue"
	}

	return rec
}

func executeRemediation(ctx context.Context, k8sClient *k8s.Client, issue *metrics.Issue, rec *AIRecommendation) (bool, string) {
	utils.Log.WithField("action", rec.Action).Info("Executing remediation action")

	switch rec.Action {
	case "restart_pod":
		err := k8sClient.RestartPod(ctx, issue.Namespace, issue.PodName)
		if err != nil {
			return false, err.Error()
		}
		return true, ""

	case "increase_memory":
		err := k8sClient.UpdatePodResourceLimits(ctx, issue.Namespace, issue.PodName, "deployment", "", "4Gi")
		if err != nil {
			return false, err.Error()
		}
		return true, ""

	case "increase_cpu":
		err := k8sClient.UpdatePodResourceLimits(ctx, issue.Namespace, issue.PodName, "deployment", "2000m", "")
		if err != nil {
			return false, err.Error()
		}
		return true, ""

	case "scale_deployment":
		deployment, err := k8sClient.GetDeploymentForPod(ctx, issue.Namespace, issue.PodName)
		if err != nil {
			return false, err.Error()
		}
		newReplicas := *deployment.Spec.Replicas + 1
		err = k8sClient.ScaleDeployment(ctx, issue.Namespace, deployment.Name, newReplicas)
		if err != nil {
			return false, err.Error()
		}
		return true, ""

	default:
		return false, "Unknown action: " + rec.Action
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
