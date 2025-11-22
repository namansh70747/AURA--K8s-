package metrics

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Prometheus metrics for collector and remediator
var (
	// Collector metrics
	PodsCollected = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_pods_collected_total",
			Help: "Total number of pods metrics collected",
		},
		[]string{"namespace"},
	)

	NodesCollected = promauto.NewCounter(
		prometheus.CounterOpts{
			Name: "aura_nodes_collected_total",
			Help: "Total number of node metrics collected",
		},
	)

	CollectionDuration = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Name:    "aura_collection_duration_seconds",
			Help:    "Duration of metrics collection in seconds",
			Buckets: prometheus.DefBuckets,
		},
	)

	CollectionErrors = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_collection_errors_total",
			Help: "Total number of collection errors",
		},
		[]string{"type"}, // pod, node
	)

	MLPredictionsTotal = promauto.NewCounter(
		prometheus.CounterOpts{
			Name: "aura_ml_predictions_total",
			Help: "Total number of ML predictions made",
		},
	)

	MLPredictionDuration = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Name:    "aura_ml_prediction_duration_seconds",
			Help:    "Duration of ML prediction in seconds",
			Buckets: []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
		},
	)

	// Remediator metrics
	RemediationsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_remediations_total",
			Help: "Total number of remediation actions",
		},
		[]string{"status"}, // success, failed
	)

	RemediationDuration = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Name:    "aura_remediation_duration_seconds",
			Help:    "Duration of remediation processing in seconds",
			Buckets: prometheus.DefBuckets,
		},
	)

	IssuesOpen = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_issues_open",
			Help: "Current number of open issues",
		},
		[]string{"severity"}, // critical, high, medium, low
	)

	RemediationSuccessRate = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "aura_remediation_success_rate",
			Help: "Success rate of remediations (0-1)",
		},
	)

	// Service health metrics
	ServiceHealth = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_service_health",
			Help: "Service health status (1=healthy, 0=unhealthy)",
		},
		[]string{"service"}, // collector, remediator, ml_service, mcp_server
	)

	// Cost savings metrics
	CostSavingsTotal = promauto.NewCounter(
		prometheus.CounterOpts{
			Name: "aura_cost_savings_total",
			Help: "Total cost savings calculated",
		},
	)

	CostSavingsPerHour = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_cost_savings_per_hour",
			Help: "Cost savings per hour by optimization type",
		},
		[]string{"optimization_type"}, // memory_rightsizing, cpu_rightsizing, etc.
	)

	MonthlyCostSavings = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "aura_monthly_cost_savings",
			Help: "Estimated monthly cost savings in USD",
		},
	)

	// Additional metrics for comprehensive monitoring
	DatabaseOperationsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_database_operations_total",
			Help: "Total number of database operations",
		},
		[]string{"operation", "status"}, // operation: save, query, etc. status: success, error
	)

	DatabaseOperationDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_database_operation_duration_seconds",
			Help:    "Duration of database operations in seconds",
			Buckets: []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
		},
		[]string{"operation"},
	)

	MCPRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_mcp_requests_total",
			Help: "Total number of MCP server requests",
		},
		[]string{"endpoint", "status"}, // endpoint: analyze-with-plan, health, etc. status: success, error
	)

	MCPRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_mcp_request_duration_seconds",
			Help:    "Duration of MCP server requests in seconds",
			Buckets: []float64{0.1, 0.5, 1, 2, 5, 10, 30, 60, 120},
		},
		[]string{"endpoint"},
	)

	CircuitBreakerState = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_circuit_breaker_state",
			Help: "Circuit breaker state (0=closed, 1=open, 2=half-open)",
		},
		[]string{"service"}, // service: ml_service, mcp_server, etc.
	)

	CircuitBreakerFailures = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_circuit_breaker_failures_total",
			Help: "Total number of circuit breaker failures",
		},
		[]string{"service"},
	)

	FeatureValidationErrors = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_feature_validation_errors_total",
			Help: "Total number of feature validation errors",
		},
		[]string{"feature_name", "error_type"}, // error_type: nan, infinite, negative, etc.
	)

	RemediationPlanValidationErrors = promauto.NewCounter(
		prometheus.CounterOpts{
			Name: "aura_remediation_plan_validation_errors_total",
			Help: "Total number of remediation plan validation errors",
		},
	)

	RollbackOperationsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_rollback_operations_total",
			Help: "Total number of rollback operations",
		},
		[]string{"status"}, // status: success, failed, skipped
	)

	// Connection pool metrics
	ConnectionPoolSize = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_connection_pool_size",
			Help: "Current connection pool size",
		},
		[]string{"pool_type"}, // pool_type: database, etc.
	)

	ConnectionPoolActive = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_connection_pool_active",
			Help: "Number of active connections in pool",
		},
		[]string{"pool_type"},
	)

	ConnectionPoolWaitTime = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_connection_pool_wait_seconds",
			Help:    "Time spent waiting for connection from pool",
			Buckets: []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
		},
		[]string{"pool_type"},
	)

	ConnectionPoolErrors = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_connection_pool_errors_total",
			Help: "Total number of connection pool errors",
		},
		[]string{"pool_type", "error_type"}, // error_type: pool_full, connection_failed, etc.
	)

	// Deployment history API health metrics
	DeploymentHistoryAPIHealth = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "aura_deployment_history_api_health",
			Help: "Deployment history API health status (1=healthy, 0=unhealthy)",
		},
		[]string{"namespace"},
	)

	DeploymentHistoryAPICheckDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_deployment_history_api_check_duration_seconds",
			Help:    "Duration of deployment history API health checks",
			Buckets: []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
		},
		[]string{"namespace"},
	)

	// Remediation action metrics
	RemediationActionDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_remediation_action_duration_seconds",
			Help:    "Duration of individual remediation actions",
			Buckets: []float64{0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300},
		},
		[]string{"action_type", "operation"}, // action_type: pod, deployment, etc. operation: restart, scale, etc.
	)

	RemediationActionErrors = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_remediation_action_errors_total",
			Help: "Total number of remediation action errors",
		},
		[]string{"action_type", "operation", "error_type"}, // error_type: timeout, permission_denied, etc.
	)

	// Resource quota validation metrics
	ResourceQuotaCheckDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "aura_resource_quota_check_duration_seconds",
			Help:    "Duration of resource quota checks",
			Buckets: []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1},
		},
		[]string{"namespace", "resource_type"}, // resource_type: cpu, memory, etc.
	)

	ResourceQuotaViolations = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_resource_quota_violations_total",
			Help: "Total number of resource quota violations detected",
		},
		[]string{"namespace", "resource_type"},
	)

	ResourceQuotaCheckErrors = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "aura_resource_quota_check_errors_total",
			Help: "Total number of resource quota check errors",
		},
		[]string{"namespace", "resource_type"},
	)
)

// RecordCollectionDuration records the duration of a collection operation
func RecordCollectionDuration(duration time.Duration) {
	CollectionDuration.Observe(duration.Seconds())
}

// RecordMLPredictionDuration records the duration of an ML prediction
func RecordMLPredictionDuration(duration time.Duration) {
	MLPredictionDuration.Observe(duration.Seconds())
}

// RecordRemediationDuration records the duration of a remediation operation
func RecordRemediationDuration(duration time.Duration) {
	RemediationDuration.Observe(duration.Seconds())
}

// SetServiceHealth sets the health status of a service
func SetServiceHealth(service string, healthy bool) {
	value := 0.0
	if healthy {
		value = 1.0
	}
	ServiceHealth.WithLabelValues(service).Set(value)
}

