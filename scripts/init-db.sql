-- AURA K8s Database Initialization Script
-- This script creates the necessary tables and TimescaleDB hypertables

-- Wrap in transaction for atomicity (rollback on any error)
BEGIN;

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Pod metrics table (with improved schema)
-- Note: For TimescaleDB hypertables, the partitioning column (timestamp) must be part of PRIMARY KEY
CREATE TABLE IF NOT EXISTS pod_metrics (
    id BIGSERIAL,
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
    -- Composite primary key including timestamp (required for hypertables)
    PRIMARY KEY (timestamp, id),
    -- Add unique constraint to prevent duplicate entries for same pod/time
    UNIQUE(timestamp, pod_name, namespace)
);

-- Convert to hypertable
SELECT create_hypertable('pod_metrics', 'timestamp', if_not_exists => TRUE);

-- Create optimized indexes
CREATE INDEX IF NOT EXISTS idx_pod_metrics_pod ON pod_metrics(pod_name, namespace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pod_metrics_node ON pod_metrics(node_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pod_metrics_timestamp ON pod_metrics(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pod_metrics_issues ON pod_metrics(timestamp DESC) 
    WHERE has_oom_kill OR has_crash_loop OR has_high_cpu;

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

-- Issues table (matching Go code schema)
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

-- Remediations table (matching Go code schema)
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
    time_to_resolve INTEGER,
    -- Additional fields for dashboard compatibility
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    strategy TEXT
);

CREATE INDEX IF NOT EXISTS idx_remediations_issue ON remediations(issue_id);
CREATE INDEX IF NOT EXISTS idx_remediations_pod ON remediations(pod_name, namespace, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediations_timestamp ON remediations(timestamp DESC);

-- ML predictions table (supporting both schemas)
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
    -- Additional fields for view compatibility
    resource_type TEXT DEFAULT 'pod',
    resource_name TEXT,
    prediction_type TEXT,
    prediction_value DOUBLE PRECISION,
    model_version TEXT,
    features JSONB,
    PRIMARY KEY (timestamp, pod_name, namespace)
);

SELECT create_hypertable('ml_predictions', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_pod ON ml_predictions(pod_name, namespace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_issue ON ml_predictions(predicted_issue, timestamp DESC);

-- Create metrics view for dashboard compatibility (aliases pod_metrics)
CREATE OR REPLACE VIEW metrics AS 
SELECT 
    timestamp,
    pod_name,
    namespace,
    node_name,
    cpu_utilization,
    memory_utilization,
    network_rx_bytes,
    network_tx_bytes,
    restarts as pod_restarts,
    restarts,
    -- Calculate disk_io_read and disk_io_write from available data
    COALESCE(disk_usage_bytes, 0) as disk_io_read,
    COALESCE(disk_usage_bytes, 0) as disk_io_write,
    cpu_usage_millicores,
    memory_usage_bytes,
    phase,
    ready,
    has_oom_kill,
    has_crash_loop,
    has_high_cpu,
    has_network_issues
FROM pod_metrics;

-- Commit transaction
COMMIT;

-- Create predictions view for Grafana compatibility (outside transaction as views are safe)
CREATE OR REPLACE VIEW predictions AS 
SELECT 
    timestamp,
    timestamp as time,
    pod_name,
    namespace,
    COALESCE(predicted_issue, prediction_type, 'unknown') AS anomaly_type,
    CASE 
        WHEN predicted_issue IS NOT NULL THEN 1
        WHEN prediction_value > 0.5 THEN 1 
        WHEN oom_score > 0.7 OR crash_loop_score > 0.7 OR high_cpu_score > 0.7 THEN 1
        ELSE 0 
    END AS is_anomaly,
    COALESCE(confidence, 0.5) as confidence,
    COALESCE(model_version, 'ensemble') as model_version,
    COALESCE(features, top_features, '{}'::jsonb) AS features_used,
    FALSE AS remediation_applied
FROM ml_predictions;

-- Create continuous aggregates for better query performance
CREATE MATERIALIZED VIEW IF NOT EXISTS pod_metrics_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS bucket,
    pod_name,
    namespace,
    node_name,
    AVG(cpu_utilization) AS avg_cpu_utilization,
    MAX(cpu_utilization) AS max_cpu_utilization,
    AVG(memory_utilization) AS avg_memory_utilization,
    MAX(memory_utilization) AS max_memory_utilization,
    SUM(restarts) AS total_restarts,
    COUNT(*) AS sample_count
FROM pod_metrics
GROUP BY bucket, pod_name, namespace, node_name;

-- Add retention policy (keep data for 30 days)
SELECT add_retention_policy('pod_metrics', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('node_metrics', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('ml_predictions', INTERVAL '30 days', if_not_exists => TRUE);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO aura;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO aura;

-- Update remediations table to auto-populate strategy from action
CREATE OR REPLACE FUNCTION update_remediation_strategy()
RETURNS TRIGGER AS $$
BEGIN
    NEW.strategy := CASE
        WHEN NEW.action LIKE '%scale_down%' OR NEW.action LIKE '%scale%' THEN 'scale_down'
        WHEN NEW.action LIKE '%replica%' OR NEW.action LIKE '%reduce%' THEN 'reduce_replicas'
        WHEN NEW.action LIKE '%optimize%' OR NEW.action LIKE '%rightsize%' THEN 'optimize_resources'
        WHEN NEW.action LIKE '%restart%' THEN 'restart_pod'
        WHEN NEW.action LIKE '%evict%' THEN 'evict_pod'
        ELSE 'other'
    END;
    IF NEW.timestamp IS NULL THEN
        NEW.timestamp := NEW.executed_at;
    END IF;
    IF NEW.completed_at IS NULL AND NEW.success = true AND NEW.time_to_resolve IS NOT NULL THEN
        NEW.completed_at := NEW.executed_at + (NEW.time_to_resolve::text || ' seconds')::INTERVAL;
    ELSIF NEW.completed_at IS NULL AND NEW.success = true THEN
        NEW.completed_at := NEW.executed_at;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_remediation_strategy
    BEFORE INSERT OR UPDATE ON remediations
    FOR EACH ROW
    EXECUTE FUNCTION update_remediation_strategy();

-- Display initialization success
SELECT 'Database initialized successfully!' AS status;
