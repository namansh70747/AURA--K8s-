package metrics

import "time"

// PodMetrics represents comprehensive metrics for a single pod
type PodMetrics struct {
	PodName              string    `json:"pod_name"`
	Namespace            string    `json:"namespace"`
	NodeName             string    `json:"node_name"`
	ContainerName        string    `json:"container_name"`
	Timestamp            time.Time `json:"timestamp"`
	
	// Resource usage
	CPUUsageMillicores   float64   `json:"cpu_usage_millicores"`
	MemoryUsageBytes     int64     `json:"memory_usage_bytes"`
	MemoryLimitBytes     int64     `json:"memory_limit_bytes"`
	CPULimitMillicores   float64   `json:"cpu_limit_millicores"`
	
	// Calculated percentages
	CPUUtilization       float64   `json:"cpu_utilization"`
	MemoryUtilization    float64   `json:"memory_utilization"`
	
	// Network metrics
	NetworkRxBytes       int64     `json:"network_rx_bytes"`
	NetworkTxBytes       int64     `json:"network_tx_bytes"`
	NetworkRxErrors      int64     `json:"network_rx_errors"`
	NetworkTxErrors      int64     `json:"network_tx_errors"`
	
	// Disk metrics
	DiskUsageBytes       int64     `json:"disk_usage_bytes"`
	DiskLimitBytes       int64     `json:"disk_limit_bytes"`
	
	// Pod state
	Phase                string    `json:"phase"`
	Ready                bool      `json:"ready"`
	Restarts             int32     `json:"restarts"`
	Age                  int64     `json:"age"` // seconds
	
	// Container state
	ContainerReady       bool      `json:"container_ready"`
	ContainerState       string    `json:"container_state"` // Running, Waiting, Terminated
	LastStateReason      string    `json:"last_state_reason"`
	
	// Trends (calculated from historical data)
	CPUTrend             float64   `json:"cpu_trend"`
	MemoryTrend          float64   `json:"memory_trend"`
	RestartTrend         float64   `json:"restart_trend"`
	
	// Health indicators
	HasOOMKill           bool      `json:"has_oom_kill"`
	HasCrashLoop         bool      `json:"has_crash_loop"`
	HasHighCPU           bool      `json:"has_high_cpu"`
	HasNetworkIssues     bool      `json:"has_network_issues"`
}

// NodeMetrics represents metrics for a Kubernetes node
type NodeMetrics struct {
	NodeName             string    `json:"node_name"`
	Timestamp            time.Time `json:"timestamp"`
	
	CPUUsageMillicores   float64   `json:"cpu_usage_millicores"`
	CPUCapacityMillicores float64  `json:"cpu_capacity_millicores"`
	MemoryUsageBytes     int64     `json:"memory_usage_bytes"`
	MemoryCapacityBytes  int64     `json:"memory_capacity_bytes"`
	
	CPUUtilization       float64   `json:"cpu_utilization"`
	MemoryUtilization    float64   `json:"memory_utilization"`
	
	PodCount             int       `json:"pod_count"`
	PodCapacity          int       `json:"pod_capacity"`
	
	DiskPressure         bool      `json:"disk_pressure"`
	MemoryPressure       bool      `json:"memory_pressure"`
	NetworkUnavailable   bool      `json:"network_unavailable"`
	Ready                bool      `json:"ready"`
}

// Issue represents a detected problem
type Issue struct {
	ID                   string    `json:"id"`
	PodName              string    `json:"pod_name"`
	Namespace            string    `json:"namespace"`
	IssueType            string    `json:"issue_type"`
	Severity             string    `json:"severity"` // Critical, High, Medium, Low
	Description          string    `json:"description"`
	CreatedAt            time.Time `json:"created_at"`
	ResolvedAt           *time.Time `json:"resolved_at,omitempty"`
	Status               string    `json:"status"` // Open, InProgress, Resolved
	Confidence           float64   `json:"confidence"`
	PredictedTimeHorizon int       `json:"predicted_time_horizon"` // seconds until issue occurs
	RootCause            string    `json:"root_cause"`
}

// Remediation represents an action taken to fix an issue
type Remediation struct {
	ID                   string    `json:"id"`
	IssueID              string    `json:"issue_id"`
	PodName              string    `json:"pod_name"`
	Namespace            string    `json:"namespace"`
	Action               string    `json:"action"`
	ActionDetails        string    `json:"action_details"`
	ExecutedAt           time.Time `json:"executed_at"`
	Success              bool      `json:"success"`
	ErrorMessage         string    `json:"error_message,omitempty"`
	AIRecommendation     string    `json:"ai_recommendation"`
	TimeToResolve        int       `json:"time_to_resolve"` // seconds
}

// MLFeatures represents engineered features for ML prediction
type MLFeatures struct {
	// Current state (12 features)
	CPUUsage             float64   `json:"cpu_usage"`
	MemoryUsage          float64   `json:"memory_usage"`
	CPUUtilization       float64   `json:"cpu_utilization"`
	MemoryUtilization    float64   `json:"memory_utilization"`
	NetworkRxRate        float64   `json:"network_rx_rate"`
	NetworkTxRate        float64   `json:"network_tx_rate"`
	NetworkErrorRate     float64   `json:"network_error_rate"`
	DiskUsagePercent     float64   `json:"disk_usage_percent"`
	RestartCount         float64   `json:"restart_count"`
	PodAge               float64   `json:"pod_age"`
	IsReady              float64   `json:"is_ready"`
	ContainerReady       float64   `json:"container_ready"`
	
	// Trends (8 features)
	CPUTrend             float64   `json:"cpu_trend"`
	MemoryTrend          float64   `json:"memory_trend"`
	NetworkRxTrend       float64   `json:"network_rx_trend"`
	NetworkTxTrend       float64   `json:"network_tx_trend"`
	RestartTrend         float64   `json:"restart_trend"`
	CPUVolatility        float64   `json:"cpu_volatility"`
	MemoryVolatility     float64   `json:"memory_volatility"`
	NetworkVolatility    float64   `json:"network_volatility"`
	
	// Statistical features (15 features)
	CPUMean              float64   `json:"cpu_mean"`
	CPUStd               float64   `json:"cpu_std"`
	CPUMax               float64   `json:"cpu_max"`
	CPUMin               float64   `json:"cpu_min"`
	MemoryMean           float64   `json:"memory_mean"`
	MemoryStd            float64   `json:"memory_std"`
	MemoryMax            float64   `json:"memory_max"`
	MemoryMin            float64   `json:"memory_min"`
	NetworkMean          float64   `json:"network_mean"`
	NetworkStd           float64   `json:"network_std"`
	NetworkMax           float64   `json:"network_max"`
	RestartMean          float64   `json:"restart_mean"`
	RestartStd           float64   `json:"restart_std"`
	CrashRate            float64   `json:"crash_rate"`
	AvgTimeBetweenRestart float64  `json:"avg_time_between_restart"`
	
	// Ratios and derived (8 features)
	CPUToMemoryRatio     float64   `json:"cpu_to_memory_ratio"`
	NetworkToComputeRatio float64  `json:"network_to_compute_ratio"`
	MemoryGrowthRate     float64   `json:"memory_growth_rate"`
	CPUGrowthRate        float64   `json:"cpu_growth_rate"`
	ResourceEfficiency   float64   `json:"resource_efficiency"`
	HealthScore          float64   `json:"health_score"`
	StabilityScore       float64   `json:"stability_score"`
	PerformanceScore     float64   `json:"performance_score"`
	
	// Time-based (5 features)
	HourOfDay            float64   `json:"hour_of_day"`
	DayOfWeek            float64   `json:"day_of_week"`
	IsBusinessHours      float64   `json:"is_business_hours"`
	TimeSinceLastRestart float64   `json:"time_since_last_restart"`
	UptimeHours          float64   `json:"uptime_hours"`
}

// MLPrediction represents the output of ML model prediction
type MLPrediction struct {
	PodName              string    `json:"pod_name"`
	Namespace            string    `json:"namespace"`
	Timestamp            time.Time `json:"timestamp"`
	
	// Ensemble predictions
	PredictedIssue       string    `json:"predicted_issue"`
	Confidence           float64   `json:"confidence"`
	TimeHorizonSeconds   int       `json:"time_horizon_seconds"`
	
	// Individual model predictions
	XGBoostPrediction    string    `json:"xgboost_prediction"`
	RandomForestPred     string    `json:"random_forest_prediction"`
	GradientBoostPred    string    `json:"gradient_boost_prediction"`
	NeuralNetPrediction  string    `json:"neural_net_prediction"`
	
	// Problem-specific scores
	OOMScore             float64   `json:"oom_score"`
	CrashLoopScore       float64   `json:"crash_loop_score"`
	HighCPUScore         float64   `json:"high_cpu_score"`
	DiskPressureScore    float64   `json:"disk_pressure_score"`
	NetworkErrorScore    float64   `json:"network_error_score"`
	
	// Feature importance
	TopFeatures          []string  `json:"top_features"`
	
	// Explanation
	Explanation          string    `json:"explanation"`
}
