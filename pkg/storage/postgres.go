package storage

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"

	_ "github.com/lib/pq"
	"github.com/namansh70747/aura-k8s/pkg/metrics"
	"github.com/namansh70747/aura-k8s/pkg/utils"
)

type PostgresDB struct {
	db *sql.DB
}

// NewPostgresDB creates a new PostgreSQL database connection
func NewPostgresDB(connStr string) (*PostgresDB, error) {
	db, err := sql.Open("postgres", connStr)
	if err != nil {
		return nil, fmt.Errorf("failed to connect to database: %w", err)
	}

	// Configure connection pool
	db.SetMaxOpenConns(100)    // Maximum number of open connections
	db.SetMaxIdleConns(25)     // Maximum number of idle connections
	db.SetConnMaxLifetime(300) // Maximum connection lifetime (5 minutes)

	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("failed to ping database: %w", err)
	}

	utils.Log.Info("Connected to PostgreSQL database with connection pool configured")
	return &PostgresDB{db: db}, nil
}

// InitSchema initializes the database schema with TimescaleDB optimizations
func (p *PostgresDB) InitSchema(ctx context.Context) error {
	utils.Log.Info("Initializing database schema")

	schema := `
	-- Enable TimescaleDB extension
	CREATE EXTENSION IF NOT EXISTS timescaledb;

	-- Pod metrics table
	CREATE TABLE IF NOT EXISTS pod_metrics (
		id SERIAL,
		pod_name TEXT NOT NULL,
		namespace TEXT NOT NULL,
		node_name TEXT,
		container_name TEXT,
		timestamp TIMESTAMPTZ NOT NULL,
		cpu_usage_millicores DOUBLE PRECISION,
		memory_usage_bytes BIGINT,
		memory_limit_bytes BIGINT,
		cpu_limit_millicores DOUBLE PRECISION,
		cpu_utilization DOUBLE PRECISION,
		memory_utilization DOUBLE PRECISION,
		network_rx_bytes BIGINT,
		network_tx_bytes BIGINT,
		network_rx_errors BIGINT,
		network_tx_errors BIGINT,
		disk_usage_bytes BIGINT,
		disk_limit_bytes BIGINT,
		phase TEXT,
		ready BOOLEAN,
		restarts INTEGER,
		age BIGINT,
		container_ready BOOLEAN,
		container_state TEXT,
		last_state_reason TEXT,
		cpu_trend DOUBLE PRECISION,
		memory_trend DOUBLE PRECISION,
		restart_trend DOUBLE PRECISION,
		has_oom_kill BOOLEAN,
		has_crash_loop BOOLEAN,
		has_high_cpu BOOLEAN,
		has_network_issues BOOLEAN,
		PRIMARY KEY (timestamp, pod_name, namespace)
	);

	-- Convert to hypertable
	SELECT create_hypertable('pod_metrics', 'timestamp', if_not_exists => TRUE);

	-- Create indexes
	CREATE INDEX IF NOT EXISTS idx_pod_metrics_pod ON pod_metrics(pod_name, namespace, timestamp DESC);
	CREATE INDEX IF NOT EXISTS idx_pod_metrics_node ON pod_metrics(node_name, timestamp DESC);
	CREATE INDEX IF NOT EXISTS idx_pod_metrics_issues ON pod_metrics(timestamp DESC) WHERE has_oom_kill OR has_crash_loop OR has_high_cpu;

	-- Node metrics table
	CREATE TABLE IF NOT EXISTS node_metrics (
		id SERIAL,
		node_name TEXT NOT NULL,
		timestamp TIMESTAMPTZ NOT NULL,
		cpu_usage_millicores DOUBLE PRECISION,
		cpu_capacity_millicores DOUBLE PRECISION,
		memory_usage_bytes BIGINT,
		memory_capacity_bytes BIGINT,
		cpu_utilization DOUBLE PRECISION,
		memory_utilization DOUBLE PRECISION,
		pod_count INTEGER,
		pod_capacity INTEGER,
		disk_pressure BOOLEAN,
		memory_pressure BOOLEAN,
		network_unavailable BOOLEAN,
		ready BOOLEAN,
		PRIMARY KEY (timestamp, node_name)
	);

	SELECT create_hypertable('node_metrics', 'timestamp', if_not_exists => TRUE);
	CREATE INDEX IF NOT EXISTS idx_node_metrics_node ON node_metrics(node_name, timestamp DESC);

	-- Issues table
	CREATE TABLE IF NOT EXISTS issues (
		id TEXT PRIMARY KEY,
		pod_name TEXT NOT NULL,
		namespace TEXT NOT NULL,
		issue_type TEXT NOT NULL,
		severity TEXT NOT NULL,
		description TEXT,
		created_at TIMESTAMPTZ NOT NULL,
		resolved_at TIMESTAMPTZ,
		status TEXT NOT NULL,
		confidence DOUBLE PRECISION,
		predicted_time_horizon INTEGER,
		root_cause TEXT
	);

	CREATE INDEX IF NOT EXISTS idx_issues_pod ON issues(pod_name, namespace, created_at DESC);
	CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status, created_at DESC);
	CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(issue_type, created_at DESC);

	-- Remediations table
	CREATE TABLE IF NOT EXISTS remediations (
		id TEXT PRIMARY KEY,
		issue_id TEXT REFERENCES issues(id),
		pod_name TEXT NOT NULL,
		namespace TEXT NOT NULL,
		action TEXT NOT NULL,
		action_details TEXT,
		executed_at TIMESTAMPTZ NOT NULL,
		success BOOLEAN NOT NULL,
		error_message TEXT,
		ai_recommendation TEXT,
		time_to_resolve INTEGER
	);

	CREATE INDEX IF NOT EXISTS idx_remediations_issue ON remediations(issue_id);
	CREATE INDEX IF NOT EXISTS idx_remediations_pod ON remediations(pod_name, namespace, executed_at DESC);

	-- ML predictions table
	CREATE TABLE IF NOT EXISTS ml_predictions (
		id SERIAL,
		pod_name TEXT NOT NULL,
		namespace TEXT NOT NULL,
		timestamp TIMESTAMPTZ NOT NULL,
		predicted_issue TEXT,
		confidence DOUBLE PRECISION,
		time_horizon_seconds INTEGER,
		xgboost_prediction TEXT,
		random_forest_prediction TEXT,
		gradient_boost_prediction TEXT,
		neural_net_prediction TEXT,
		oom_score DOUBLE PRECISION,
		crash_loop_score DOUBLE PRECISION,
		high_cpu_score DOUBLE PRECISION,
		disk_pressure_score DOUBLE PRECISION,
		network_error_score DOUBLE PRECISION,
		top_features JSONB,
		explanation TEXT,
		PRIMARY KEY (timestamp, pod_name, namespace)
	);

	SELECT create_hypertable('ml_predictions', 'timestamp', if_not_exists => TRUE);
	CREATE INDEX IF NOT EXISTS idx_ml_predictions_pod ON ml_predictions(pod_name, namespace, timestamp DESC);
	CREATE INDEX IF NOT EXISTS idx_ml_predictions_issue ON ml_predictions(predicted_issue, timestamp DESC);

	-- Create continuous aggregates for efficient querying
	CREATE MATERIALIZED VIEW IF NOT EXISTS pod_metrics_hourly
	WITH (timescaledb.continuous) AS
	SELECT time_bucket('1 hour', timestamp) AS bucket,
		pod_name,
		namespace,
		AVG(cpu_utilization) AS avg_cpu,
		MAX(cpu_utilization) AS max_cpu,
		AVG(memory_utilization) AS avg_memory,
		MAX(memory_utilization) AS max_memory,
		SUM(restarts) AS total_restarts
	FROM pod_metrics
	GROUP BY bucket, pod_name, namespace
	WITH NO DATA;

	-- Add refresh policy
	SELECT add_continuous_aggregate_policy('pod_metrics_hourly',
		start_offset => INTERVAL '3 hours',
		end_offset => INTERVAL '1 hour',
		schedule_interval => INTERVAL '1 hour',
		if_not_exists => TRUE);

	-- Retention policy: keep raw data for 7 days
	SELECT add_retention_policy('pod_metrics', INTERVAL '7 days', if_not_exists => TRUE);
	SELECT add_retention_policy('node_metrics', INTERVAL '7 days', if_not_exists => TRUE);
	SELECT add_retention_policy('ml_predictions', INTERVAL '30 days', if_not_exists => TRUE);
	`

	_, err := p.db.ExecContext(ctx, schema)
	if err != nil {
		return fmt.Errorf("failed to initialize schema: %w", err)
	}

	utils.Log.Info("Database schema initialized successfully")
	return nil
}

// SavePodMetrics saves pod metrics to the database
func (p *PostgresDB) SavePodMetrics(ctx context.Context, m *metrics.PodMetrics) error {
	query := `
	INSERT INTO pod_metrics (
		pod_name, namespace, node_name, container_name, timestamp,
		cpu_usage_millicores, memory_usage_bytes, memory_limit_bytes, cpu_limit_millicores,
		cpu_utilization, memory_utilization,
		network_rx_bytes, network_tx_bytes, network_rx_errors, network_tx_errors,
		disk_usage_bytes, disk_limit_bytes,
		phase, ready, restarts, age,
		container_ready, container_state, last_state_reason,
		cpu_trend, memory_trend, restart_trend,
		has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues
	) VALUES (
		$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
		$18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31
	) ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
	`

	_, err := p.db.ExecContext(ctx, query,
		m.PodName, m.Namespace, m.NodeName, m.ContainerName, m.Timestamp,
		m.CPUUsageMillicores, m.MemoryUsageBytes, m.MemoryLimitBytes, m.CPULimitMillicores,
		m.CPUUtilization, m.MemoryUtilization,
		m.NetworkRxBytes, m.NetworkTxBytes, m.NetworkRxErrors, m.NetworkTxErrors,
		m.DiskUsageBytes, m.DiskLimitBytes,
		m.Phase, m.Ready, m.Restarts, m.Age,
		m.ContainerReady, m.ContainerState, m.LastStateReason,
		m.CPUTrend, m.MemoryTrend, m.RestartTrend,
		m.HasOOMKill, m.HasCrashLoop, m.HasHighCPU, m.HasNetworkIssues,
	)

	return err
}

// SaveNodeMetrics saves node metrics to the database
func (p *PostgresDB) SaveNodeMetrics(ctx context.Context, m *metrics.NodeMetrics) error {
	query := `
	INSERT INTO node_metrics (
		node_name, timestamp,
		cpu_usage_millicores, cpu_capacity_millicores,
		memory_usage_bytes, memory_capacity_bytes,
		cpu_utilization, memory_utilization,
		pod_count, pod_capacity,
		disk_pressure, memory_pressure, network_unavailable, ready
	) VALUES (
		$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
	) ON CONFLICT (timestamp, node_name) DO NOTHING
	`

	_, err := p.db.ExecContext(ctx, query,
		m.NodeName, m.Timestamp,
		m.CPUUsageMillicores, m.CPUCapacityMillicores,
		m.MemoryUsageBytes, m.MemoryCapacityBytes,
		m.CPUUtilization, m.MemoryUtilization,
		m.PodCount, m.PodCapacity,
		m.DiskPressure, m.MemoryPressure, m.NetworkUnavailable, m.Ready,
	)

	return err
}

// GetRecentPodMetrics returns recent metrics for a pod
func (p *PostgresDB) GetRecentPodMetrics(ctx context.Context, podName, namespace string, limit int) ([]*metrics.PodMetrics, error) {
	query := `
	SELECT pod_name, namespace, node_name, container_name, timestamp,
		cpu_usage_millicores, memory_usage_bytes, memory_limit_bytes, cpu_limit_millicores,
		cpu_utilization, memory_utilization,
		network_rx_bytes, network_tx_bytes, network_rx_errors, network_tx_errors,
		disk_usage_bytes, disk_limit_bytes,
		phase, ready, restarts, age,
		container_ready, container_state, last_state_reason,
		cpu_trend, memory_trend, restart_trend,
		has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues
	FROM pod_metrics
	WHERE pod_name = $1 AND namespace = $2
	ORDER BY timestamp DESC
	LIMIT $3
	`

	rows, err := p.db.QueryContext(ctx, query, podName, namespace, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []*metrics.PodMetrics
	for rows.Next() {
		m := &metrics.PodMetrics{}
		err := rows.Scan(
			&m.PodName, &m.Namespace, &m.NodeName, &m.ContainerName, &m.Timestamp,
			&m.CPUUsageMillicores, &m.MemoryUsageBytes, &m.MemoryLimitBytes, &m.CPULimitMillicores,
			&m.CPUUtilization, &m.MemoryUtilization,
			&m.NetworkRxBytes, &m.NetworkTxBytes, &m.NetworkRxErrors, &m.NetworkTxErrors,
			&m.DiskUsageBytes, &m.DiskLimitBytes,
			&m.Phase, &m.Ready, &m.Restarts, &m.Age,
			&m.ContainerReady, &m.ContainerState, &m.LastStateReason,
			&m.CPUTrend, &m.MemoryTrend, &m.RestartTrend,
			&m.HasOOMKill, &m.HasCrashLoop, &m.HasHighCPU, &m.HasNetworkIssues,
		)
		if err != nil {
			return nil, err
		}
		results = append(results, m)
	}

	return results, rows.Err()
}

// SaveIssue saves an issue to the database
func (p *PostgresDB) SaveIssue(ctx context.Context, issue *metrics.Issue) error {
	query := `
	INSERT INTO issues (
		id, pod_name, namespace, issue_type, severity, description,
		created_at, resolved_at, status, confidence, predicted_time_horizon, root_cause
	) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
	ON CONFLICT (id) DO UPDATE SET
		resolved_at = EXCLUDED.resolved_at,
		status = EXCLUDED.status
	`

	_, err := p.db.ExecContext(ctx, query,
		issue.ID, issue.PodName, issue.Namespace, issue.IssueType, issue.Severity,
		issue.Description, issue.CreatedAt, issue.ResolvedAt, issue.Status,
		issue.Confidence, issue.PredictedTimeHorizon, issue.RootCause,
	)

	return err
}

// GetOpenIssues returns all open issues
func (p *PostgresDB) GetOpenIssues(ctx context.Context) ([]*metrics.Issue, error) {
	query := `
	SELECT id, pod_name, namespace, issue_type, severity, description,
		created_at, resolved_at, status, confidence, predicted_time_horizon, root_cause
	FROM issues
	WHERE status IN ('Open', 'InProgress')
	ORDER BY created_at DESC
	`

	rows, err := p.db.QueryContext(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []*metrics.Issue
	for rows.Next() {
		issue := &metrics.Issue{}
		err := rows.Scan(
			&issue.ID, &issue.PodName, &issue.Namespace, &issue.IssueType,
			&issue.Severity, &issue.Description, &issue.CreatedAt, &issue.ResolvedAt,
			&issue.Status, &issue.Confidence, &issue.PredictedTimeHorizon, &issue.RootCause,
		)
		if err != nil {
			return nil, err
		}
		results = append(results, issue)
	}

	return results, rows.Err()
}

// SaveRemediation saves a remediation action to the database
func (p *PostgresDB) SaveRemediation(ctx context.Context, r *metrics.Remediation) error {
	query := `
	INSERT INTO remediations (
		id, issue_id, pod_name, namespace, action, action_details,
		executed_at, success, error_message, ai_recommendation, time_to_resolve
	) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
	`

	_, err := p.db.ExecContext(ctx, query,
		r.ID, r.IssueID, r.PodName, r.Namespace, r.Action, r.ActionDetails,
		r.ExecutedAt, r.Success, r.ErrorMessage, r.AIRecommendation, r.TimeToResolve,
	)

	return err
}

// SaveMLPrediction saves an ML prediction to the database
func (p *PostgresDB) SaveMLPrediction(ctx context.Context, pred *metrics.MLPrediction) error {
	topFeaturesJSON, _ := json.Marshal(pred.TopFeatures)

	query := `
	INSERT INTO ml_predictions (
		pod_name, namespace, timestamp, predicted_issue, confidence, time_horizon_seconds,
		xgboost_prediction, random_forest_prediction, gradient_boost_prediction, neural_net_prediction,
		oom_score, crash_loop_score, high_cpu_score, disk_pressure_score, network_error_score,
		top_features, explanation
	) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
	ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
	`

	_, err := p.db.ExecContext(ctx, query,
		pred.PodName, pred.Namespace, pred.Timestamp, pred.PredictedIssue, pred.Confidence, pred.TimeHorizonSeconds,
		pred.XGBoostPrediction, pred.RandomForestPred, pred.GradientBoostPred, pred.NeuralNetPrediction,
		pred.OOMScore, pred.CrashLoopScore, pred.HighCPUScore, pred.DiskPressureScore, pred.NetworkErrorScore,
		topFeaturesJSON, pred.Explanation,
	)

	return err
}

// Close closes the database connection
func (p *PostgresDB) Close() error {
	return p.db.Close()
}
