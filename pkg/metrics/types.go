package metrics

import "time"

// PodMetrics represents comprehensive metrics for a single pod
type PodMetrics struct {
	PodName       string    `json:"pod_name"`
	Namespace     string    `json:"namespace"`
	NodeName      string    `json:"node_name"`
	ContainerName string    `json:"container_name"`
	Timestamp     time.Time `json:"timestamp"`

	// Resource usage
	CPUUsageMillicores float64 `json:"cpu_usage_millicores"`
	MemoryUsageBytes   int64   `json:"memory_usage_bytes"`
	MemoryLimitBytes   int64   `json:"memory_limit_bytes"`
	CPULimitMillicores float64 `json:"cpu_limit_millicores"`

	// Calculated percentages
	CPUUtilization    float64 `json:"cpu_utilization"`
	MemoryUtilization float64 `json:"memory_utilization"`

	// Network metrics
	NetworkRxBytes  int64 `json:"network_rx_bytes"`
	NetworkTxBytes  int64 `json:"network_tx_bytes"`
	NetworkRxErrors int64 `json:"network_rx_errors"`
	NetworkTxErrors int64 `json:"network_tx_errors"`

	// Disk metrics
	DiskUsageBytes int64 `json:"disk_usage_bytes"`
	DiskLimitBytes int64 `json:"disk_limit_bytes"`

	// Pod state
	Phase    string `json:"phase"`
	Ready    bool   `json:"ready"`
	Restarts int32  `json:"restarts"`
	Age      int64  `json:"age"` // seconds

	// Container state
	ContainerReady  bool   `json:"container_ready"`
	ContainerState  string `json:"container_state"` // Running, Waiting, Terminated
	LastStateReason string `json:"last_state_reason"`

	// Trends (calculated from historical data)
	CPUTrend     float64 `json:"cpu_trend"`
	MemoryTrend  float64 `json:"memory_trend"`
	RestartTrend float64 `json:"restart_trend"`

	// Health indicators
	HasOOMKill       bool `json:"has_oom_kill"`
	HasCrashLoop     bool `json:"has_crash_loop"`
	HasHighCPU       bool `json:"has_high_cpu"`
	HasNetworkIssues bool `json:"has_network_issues"`
}

// NodeMetrics represents metrics for a Kubernetes node
type NodeMetrics struct {
	NodeName  string    `json:"node_name"`
	Timestamp time.Time `json:"timestamp"`

	CPUUsageMillicores    float64 `json:"cpu_usage_millicores"`
	CPUCapacityMillicores float64 `json:"cpu_capacity_millicores"`
	MemoryUsageBytes      int64   `json:"memory_usage_bytes"`
	MemoryCapacityBytes   int64   `json:"memory_capacity_bytes"`

	CPUUtilization    float64 `json:"cpu_utilization"`
	MemoryUtilization float64 `json:"memory_utilization"`

	PodCount    int `json:"pod_count"`
	PodCapacity int `json:"pod_capacity"`

	DiskPressure       bool `json:"disk_pressure"`
	MemoryPressure     bool `json:"memory_pressure"`
	NetworkUnavailable bool `json:"network_unavailable"`
	Ready              bool `json:"ready"`
}

// Issue represents a detected problem
type Issue struct {
	ID                   string     `json:"id"`
	PodName              string     `json:"pod_name"`
	Namespace            string     `json:"namespace"`
	IssueType            string     `json:"issue_type"`
	Severity             string     `json:"severity"` // Critical, High, Medium, Low
	Description          string     `json:"description"`
	CreatedAt            time.Time  `json:"created_at"`
	ResolvedAt           *time.Time `json:"resolved_at,omitempty"`
	Status               string     `json:"status"` // Open, InProgress, Resolved
	Confidence           float64    `json:"confidence"`
	PredictedTimeHorizon *int       `json:"predicted_time_horizon"` // seconds until issue occurs
	RootCause            *string    `json:"root_cause"`
}

// Remediation represents an action taken to fix an issue
type Remediation struct {
	ID               string     `json:"id"`
	IssueID          string     `json:"issue_id"`
	PodName          string     `json:"pod_name"`
	Namespace        string     `json:"namespace"`
	Action           string     `json:"action"`
	ActionDetails    string     `json:"action_details"`
	ExecutedAt       time.Time  `json:"executed_at"`
	Success          bool       `json:"success"`
	ErrorMessage     string     `json:"error_message,omitempty"`
	AIRecommendation string     `json:"ai_recommendation"`
	TimeToResolve    int        `json:"time_to_resolve"` // seconds
	Strategy         string     `json:"strategy"`
	CompletedAt      *time.Time `json:"completed_at,omitempty"`
	Timestamp        time.Time  `json:"timestamp"`
}

// MLFeatures represents engineered features for ML prediction.
// Currently, the ML service uses a flexible map[string]float64 for features,
// but this struct documents the feature schema for reference.
// Only the actively used features are listed here to reduce confusion.
// 
// The actual features sent to the ML service (as defined in pkg/ml/client.go):
// 1. cpu_usage - CPU utilization percentage
// 2. memory_usage - Memory utilization percentage  
// 3. disk_usage - Disk usage percentage
// 4. network_bytes_sec - Network bytes per second
// 5. error_rate - Network error rate
// 6. latency_ms - Estimated latency in milliseconds
// 7. restart_count - Number of pod restarts
// 8. age_minutes - Pod age in minutes
// 9. cpu_memory_ratio - CPU to memory utilization ratio
// 10. resource_pressure - Average of CPU and memory utilization
// 11. error_latency_product - Error rate * latency (composite metric)
// 12. network_per_cpu - Network bytes per CPU unit
// 13. is_critical - Boolean flag for critical conditions (HighCPU or OOMKill)
//
// NOTE: Additional features (trends, statistical features, time-based features)
// can be added here in the future when they are implemented in the feature engineering pipeline.
type MLFeatures struct {
	CPUUsage            float64 `json:"cpu_usage"`              // CPU utilization percentage
	MemoryUsage         float64 `json:"memory_usage"`           // Memory utilization percentage
	DiskUsage           float64 `json:"disk_usage"`             // Disk usage percentage
	NetworkBytesSec     float64 `json:"network_bytes_sec"`      // Network bytes per second
	ErrorRate           float64 `json:"error_rate"`             // Network error rate
	LatencyMs           float64 `json:"latency_ms"`             // Estimated latency in milliseconds
	RestartCount        float64 `json:"restart_count"`          // Number of pod restarts
	AgeMinutes          float64 `json:"age_minutes"`            // Pod age in minutes
	CPUMemoryRatio      float64 `json:"cpu_memory_ratio"`       // CPU to memory utilization ratio
	ResourcePressure    float64 `json:"resource_pressure"`      // Average of CPU and memory utilization
	ErrorLatencyProduct float64 `json:"error_latency_product"`  // Error rate * latency (composite metric)
	NetworkPerCPU       float64 `json:"network_per_cpu"`        // Network bytes per CPU unit
	IsCritical          float64 `json:"is_critical"`            // Boolean flag for critical conditions
}

// MLPrediction represents the output of ML model prediction
type MLPrediction struct {
	PodName   string    `json:"pod_name"`
	Namespace string    `json:"namespace"`
	Timestamp time.Time `json:"timestamp"`

	// Ensemble predictions
	PredictedIssue     string  `json:"predicted_issue"`
	Confidence         float64 `json:"confidence"`
	TimeHorizonSeconds int     `json:"time_horizon_seconds"`

	// Individual model predictions
	XGBoostPrediction   string `json:"xgboost_prediction"`
	RandomForestPred    string `json:"random_forest_prediction"`
	GradientBoostPred   string `json:"gradient_boost_prediction"`
	NeuralNetPrediction string `json:"neural_net_prediction"`

	// Problem-specific scores
	OOMScore          float64 `json:"oom_score"`
	CrashLoopScore    float64 `json:"crash_loop_score"`
	HighCPUScore      float64 `json:"high_cpu_score"`
	DiskPressureScore float64 `json:"disk_pressure_score"`
	NetworkErrorScore float64 `json:"network_error_score"`

	// Feature importance
	TopFeatures []string `json:"top_features"`

	// Explanation
	Explanation string `json:"explanation"`
}
