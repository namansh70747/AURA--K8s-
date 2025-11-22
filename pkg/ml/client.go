package ml

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"sync"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/utils"
)

// CircuitBreakerState represents the state of a circuit breaker
type CircuitBreakerState int

const (
	CircuitBreakerClosed CircuitBreakerState = iota
	CircuitBreakerOpen
	CircuitBreakerHalfOpen
)

// CircuitBreaker implements circuit breaker pattern for ML service calls
type CircuitBreaker struct {
	mu               sync.RWMutex
	state            CircuitBreakerState
	failureCount     int
	lastFailureTime  time.Time
	failureThreshold int
	resetTimeout     time.Duration
}

// MLClient handles communication with the ML prediction service
type MLClient struct {
	baseURL        string
	client         *http.Client
	circuitBreaker *CircuitBreaker
}

// PredictionRequest represents the request payload for ML predictions
type PredictionRequest struct {
	Features map[string]float64 `json:"features"`
}

// PredictionResponse represents the response from ML service
type PredictionResponse struct {
	AnomalyType   string             `json:"anomaly_type"`
	Confidence    float64            `json:"confidence"`
	Probabilities map[string]float64 `json:"probabilities"`
	ModelUsed     string             `json:"model_used"`
	Explanation   string             `json:"explanation"`
}

// NewCircuitBreaker creates a new circuit breaker
func NewCircuitBreaker(failureThreshold int, resetTimeout time.Duration) *CircuitBreaker {
	return &CircuitBreaker{
		state:            CircuitBreakerClosed,
		failureThreshold: failureThreshold,
		resetTimeout:     resetTimeout,
	}
}

// canAttempt checks if a request can be attempted with atomic state transitions
func (cb *CircuitBreaker) canAttempt() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	oldState := cb.state
	switch cb.state {
	case CircuitBreakerClosed:
		return true
	case CircuitBreakerOpen:
		// Check if reset timeout has passed
		if time.Since(cb.lastFailureTime) >= cb.resetTimeout {
			// Atomically transition to half-open state
			// State is already locked, so this is atomic
			cb.state = CircuitBreakerHalfOpen
			utils.Log.Debug("Circuit breaker transitioned to HALF_OPEN state")
			// Update metrics if state changed
			if oldState != cb.state {
				metrics.CircuitBreakerState.WithLabelValues("ml_service").Set(float64(cb.state))
			}
			return true
		}
		return false
	case CircuitBreakerHalfOpen:
		return true
	default:
		return false
	}
}

// recordSuccess records a successful call
func (cb *CircuitBreaker) recordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	oldState := cb.state
	cb.failureCount = 0
	if cb.state == CircuitBreakerHalfOpen {
		cb.state = CircuitBreakerClosed
		utils.Log.Debug("Circuit breaker closed after successful call")
	}

	// Update Prometheus metrics if state changed
	if oldState != cb.state {
		metrics.CircuitBreakerState.WithLabelValues("ml_service").Set(float64(cb.state))
	}
}

// recordFailure records a failed call
func (cb *CircuitBreaker) recordFailure() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	oldState := cb.state
	cb.failureCount++
	cb.lastFailureTime = time.Now()

	if cb.failureCount >= cb.failureThreshold {
		if cb.state != CircuitBreakerOpen {
			cb.state = CircuitBreakerOpen
			utils.Log.Warnf("Circuit breaker OPENED after %d failures", cb.failureCount)
		}
	}

	// Update Prometheus metrics if state changed
	if oldState != cb.state {
		metrics.CircuitBreakerState.WithLabelValues("ml_service").Set(float64(cb.state))
		metrics.CircuitBreakerFailures.WithLabelValues("ml_service").Inc()
	}
}

// NewMLClient creates a new ML client with circuit breaker
func NewMLClient(baseURL string) *MLClient {
	return &MLClient{
		baseURL: baseURL,
		client: &http.Client{
			Timeout: config.DefaultMLTimeout,
		},
		circuitBreaker: NewCircuitBreaker(
			config.CircuitBreakerFailureThreshold,
			config.CircuitBreakerResetTimeout,
		),
	}
}

// Predict sends pod metrics to ML service and returns prediction
func (c *MLClient) Predict(ctx context.Context, podMetrics *metrics.PodMetrics) (*metrics.MLPrediction, error) {
	// Validate input metrics before processing
	if podMetrics == nil {
		return nil, fmt.Errorf("podMetrics cannot be nil")
	}
	if podMetrics.PodName == "" {
		return nil, fmt.Errorf("podMetrics.PodName is required")
	}
	if podMetrics.Namespace == "" {
		return nil, fmt.Errorf("podMetrics.Namespace is required")
	}

	// Validate numeric values for NaN and infinity
	if isNaNOrInf(podMetrics.CPUUtilization) {
		return nil, fmt.Errorf("invalid CPUUtilization: NaN or Infinity")
	}
	if isNaNOrInf(podMetrics.MemoryUtilization) {
		return nil, fmt.Errorf("invalid MemoryUtilization: NaN or Infinity")
	}
	if podMetrics.CPUUtilization < 0 || podMetrics.CPUUtilization > 100 {
		return nil, fmt.Errorf("invalid CPUUtilization: must be between 0 and 100, got %f", podMetrics.CPUUtilization)
	}
	if podMetrics.MemoryUtilization < 0 || podMetrics.MemoryUtilization > 100 {
		return nil, fmt.Errorf("invalid MemoryUtilization: must be between 0 and 100, got %f", podMetrics.MemoryUtilization)
	}
	if podMetrics.Restarts < 0 {
		return nil, fmt.Errorf("invalid Restarts: must be non-negative, got %d", podMetrics.Restarts)
	}
	if podMetrics.Age < 0 {
		return nil, fmt.Errorf("invalid Age: must be non-negative, got %d", podMetrics.Age)
	}

	// Convert pod metrics to ML features (13 features expected by model)
	// Calculate network bytes per second from available metrics
	// Use actual collection interval from config
	networkBytesSec := 0.0
	if podMetrics.NetworkRxBytes > 0 || podMetrics.NetworkTxBytes > 0 {
		// Calculate bytes/sec: divide total bytes by collection interval
		collectionIntervalSec := config.DefaultCollectionInterval.Seconds()
		if collectionIntervalSec > 0 {
			networkBytesSec = float64(podMetrics.NetworkRxBytes+podMetrics.NetworkTxBytes) / collectionIntervalSec
		} else {
			// Fallback: assume 15 seconds if config is invalid
			networkBytesSec = float64(podMetrics.NetworkRxBytes+podMetrics.NetworkTxBytes) / 15.0
		}
	}

	// Calculate disk usage percentage if limits are available
	diskUsage := 0.0
	if podMetrics.DiskLimitBytes > 0 {
		diskUsage = (float64(podMetrics.DiskUsageBytes) / float64(podMetrics.DiskLimitBytes)) * 100
	}

	// Calculate error rate as errors per second
	// Use collection interval to convert error count to rate
	errorRate := 0.0
	if podMetrics.NetworkRxErrors > 0 || podMetrics.NetworkTxErrors > 0 {
		// Calculate errors per second based on collection interval
		collectionIntervalSec := config.DefaultCollectionInterval.Seconds()
		if collectionIntervalSec > 0 {
			errorRate = float64(podMetrics.NetworkRxErrors+podMetrics.NetworkTxErrors) / collectionIntervalSec
		} else {
			// Fallback: use error count directly if interval is invalid
			errorRate = float64(podMetrics.NetworkRxErrors + podMetrics.NetworkTxErrors)
		}
	}

	// Estimate latency based on CPU utilization and network state
	// More sophisticated estimation: base latency + CPU overhead + network issues
	latencyMs := 10.0 // Base latency for normal operations
	if podMetrics.CPUUtilization > 80 {
		// Add latency proportional to CPU overload
		cpuOverhead := (podMetrics.CPUUtilization - 80) * 1.5 // 1.5ms per % over 80%
		if cpuOverhead > 100 {
			cpuOverhead = 100 // Cap at 100ms
		}
		latencyMs += cpuOverhead
	}
	if podMetrics.HasNetworkIssues {
		latencyMs += 50.0 // Add base latency for network issues
	}
	// Cap latency at reasonable maximum
	if latencyMs > 500 {
		latencyMs = 500
	}

	// Calculate network per CPU ratio with explicit division by zero check
	networkPerCPU := 0.0
	if podMetrics.CPUUtilization > 0 {
		denominator := podMetrics.CPUUtilization + 1
		if denominator > 0 {
			networkPerCPU = networkBytesSec / denominator
		}
	}

	features := map[string]float64{
		"cpu_usage":         podMetrics.CPUUtilization,
		"memory_usage":      podMetrics.MemoryUtilization,
		"disk_usage":        diskUsage,
		"network_bytes_sec": networkBytesSec,
		"error_rate":        errorRate,
		"latency_ms":        latencyMs,
		"restart_count":     float64(podMetrics.Restarts),
		"age_minutes":       float64(podMetrics.Age) / 60,
		"cpu_memory_ratio": func() float64 {
			denominator := podMetrics.MemoryUtilization + 1
			if denominator > 0 {
				return podMetrics.CPUUtilization / denominator
			}
			return 0.0
		}(),
		"resource_pressure":     (podMetrics.CPUUtilization + podMetrics.MemoryUtilization) / 2,
		"error_latency_product": errorRate * latencyMs,
		"network_per_cpu":       networkPerCPU,
		"is_critical":           boolToFloat(podMetrics.HasHighCPU || podMetrics.HasOOMKill),
	}

	reqBody := PredictionRequest{Features: features}
	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Check circuit breaker before making request
	if !c.circuitBreaker.canAttempt() {
		// Log metrics for circuit breaker state
		utils.Log.WithField("service", "ml_service").
			Warn("Circuit breaker is OPEN - ML service unavailable, request rejected")
		metrics.CircuitBreakerFailures.WithLabelValues("ml_service").Inc()
		return nil, fmt.Errorf("circuit breaker is OPEN - ML service unavailable")
	}

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/predict", bytes.NewBuffer(jsonData))
	if err != nil {
		c.circuitBreaker.recordFailure()
		return nil, fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		c.circuitBreaker.recordFailure()
		return nil, fmt.Errorf("failed to call ML service: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		c.circuitBreaker.recordFailure()
		// Read response body for better error context
		body, readErr := io.ReadAll(resp.Body)
		if readErr == nil && len(body) > 0 {
			// Limit body length to prevent huge error messages
			bodyStr := string(body)
			if len(bodyStr) > 500 {
				bodyStr = bodyStr[:500] + "..."
			}
			return nil, fmt.Errorf("ML service returned status %d: %s", resp.StatusCode, bodyStr)
		}
		return nil, fmt.Errorf("ML service returned status %d", resp.StatusCode)
	}

	// Success - reset circuit breaker
	c.circuitBreaker.recordSuccess()

	var predResp PredictionResponse
	if err := json.NewDecoder(resp.Body).Decode(&predResp); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	// Convert to MLPrediction
	// Use configurable time horizon from config package
	timeHorizonSeconds := int(config.DefaultRemediationInterval.Seconds())
	if timeHorizonSeconds < 60 {
		timeHorizonSeconds = 300 // Fallback to 5 minutes if config is too low
	}

	prediction := &metrics.MLPrediction{
		PodName:             podMetrics.PodName,
		Namespace:           podMetrics.Namespace,
		Timestamp:           time.Now(),
		PredictedIssue:      predResp.AnomalyType,
		Confidence:          predResp.Confidence,
		TimeHorizonSeconds:  timeHorizonSeconds,
		Explanation:         predResp.Explanation,
		TopFeatures:         []string{"cpu_usage", "memory_usage", "restart_count"},
		XGBoostPrediction:   predResp.AnomalyType,
		RandomForestPred:    predResp.AnomalyType,
		GradientBoostPred:   predResp.AnomalyType,
		NeuralNetPrediction: predResp.AnomalyType,
	}

	// Extract individual scores from probabilities
	if predResp.Probabilities != nil {
		prediction.OOMScore = predResp.Probabilities["oom_kill"]
		prediction.CrashLoopScore = predResp.Probabilities["pod_crash"]
		prediction.HighCPUScore = predResp.Probabilities["cpu_spike"]
		prediction.DiskPressureScore = predResp.Probabilities["disk_full"]
		prediction.NetworkErrorScore = predResp.Probabilities["network_latency"]
	}

	return prediction, nil
}

// boolToFloat converts boolean to float64
func boolToFloat(b bool) float64 {
	if b {
		return 1.0
	}
	return 0.0
}

// isNaNOrInf checks if a float64 is NaN or Infinity
func isNaNOrInf(f float64) bool {
	inf := math.Inf(1)
	negInf := math.Inf(-1)
	return f != f || f == inf || f == negInf
}
