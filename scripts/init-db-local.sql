-- AURA K8s Database Initialization Script (Local - Without TimescaleDB)
-- This script creates the necessary tables for local development

-- Create time_bucket function for PostgreSQL (TimescaleDB compatibility)
-- Only create if TimescaleDB extension is not available
-- TimescaleDB provides this natively, so we only need it for pure PostgreSQL
DO $$
BEGIN
    -- Check if TimescaleDB extension exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
    ) THEN
        -- Create time_bucket function only if TimescaleDB is not available
        CREATE OR REPLACE FUNCTION time_bucket(bucket_width INTERVAL, ts TIMESTAMPTZ)
        RETURNS TIMESTAMPTZ AS $$
        DECLARE
            bucket_seconds DOUBLE PRECISION;
            epoch_seconds DOUBLE PRECISION;
            bucket_number BIGINT;
        BEGIN
            -- Convert bucket width to seconds
            bucket_seconds := EXTRACT(EPOCH FROM bucket_width);
            
            -- Get timestamp as epoch seconds
            epoch_seconds := EXTRACT(EPOCH FROM ts);
            
            -- Calculate which bucket this timestamp falls into
            bucket_number := FLOOR(epoch_seconds / bucket_seconds);
            
            -- Return the start of the bucket
            RETURN TO_TIMESTAMP(bucket_number * bucket_seconds);
        END;
        $$ LANGUAGE plpgsql IMMUTABLE;
        
        RAISE NOTICE 'Created time_bucket function for PostgreSQL compatibility';
    ELSE
        RAISE NOTICE 'TimescaleDB extension found - using native time_bucket function';
    END IF;
END $$;

-- Pod metrics table
DROP TABLE IF EXISTS pod_metrics CASCADE;
CREATE TABLE pod_metrics (
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
    -- Composite primary key to prevent duplicates
    PRIMARY KEY (timestamp, pod_name, namespace)
);

-- Create additional indexes for efficient queries
CREATE INDEX idx_pod_metrics_timestamp ON pod_metrics(timestamp DESC);
CREATE INDEX idx_pod_metrics_pod_namespace ON pod_metrics(pod_name, namespace);

-- Convert to hypertable if TimescaleDB is available and table is empty
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables 
            WHERE hypertable_name = 'pod_metrics'
        ) THEN
            IF (SELECT COUNT(*) FROM pod_metrics) = 0 THEN
                PERFORM create_hypertable('pod_metrics', 'timestamp', if_not_exists => TRUE);
                RAISE NOTICE 'Converted pod_metrics to hypertable';
            ELSE
                RAISE NOTICE 'pod_metrics has data, skipping hypertable conversion';
            END IF;
        END IF;
    END IF;
END $$;

-- Node metrics table
DROP TABLE IF EXISTS node_metrics CASCADE;
CREATE TABLE node_metrics (
    id BIGSERIAL,
    node_name TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    cpu_usage_millicores DOUBLE PRECISION,
    cpu_capacity_millicores DOUBLE PRECISION,
    cpu_utilization DOUBLE PRECISION,
    memory_usage_bytes BIGINT,
    memory_capacity_bytes BIGINT,
    memory_utilization DOUBLE PRECISION,
    disk_usage_bytes BIGINT,
    disk_capacity_bytes BIGINT,
    disk_utilization DOUBLE PRECISION,
    network_rx_bytes BIGINT,
    network_tx_bytes BIGINT,
    pod_count INTEGER,
    pod_capacity INTEGER,
    disk_pressure BOOLEAN,
    memory_pressure BOOLEAN,
    network_unavailable BOOLEAN,
    ready BOOLEAN,
    PRIMARY KEY (timestamp, node_name)
);

CREATE INDEX idx_node_metrics_timestamp ON node_metrics(timestamp DESC);
CREATE INDEX idx_node_metrics_node ON node_metrics(node_name, timestamp DESC);

-- Convert to hypertable if TimescaleDB is available and table is empty
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables 
            WHERE hypertable_name = 'node_metrics'
        ) THEN
            IF (SELECT COUNT(*) FROM node_metrics) = 0 THEN
                PERFORM create_hypertable('node_metrics', 'timestamp', if_not_exists => TRUE);
                RAISE NOTICE 'Converted node_metrics to hypertable';
            ELSE
                RAISE NOTICE 'node_metrics has data, skipping hypertable conversion';
            END IF;
        END IF;
    END IF;
END $$;

-- ML predictions table
DROP TABLE IF EXISTS ml_predictions CASCADE;
CREATE TABLE ml_predictions (
    id BIGSERIAL,
    pod_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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
    resource_type TEXT DEFAULT 'pod',
    resource_name TEXT,
    prediction_type TEXT,
    prediction_value DOUBLE PRECISION,
    model_version TEXT DEFAULT 'ensemble',
    features JSONB,
    is_anomaly INTEGER DEFAULT 0,
    anomaly_type TEXT,
    PRIMARY KEY (timestamp, pod_name, namespace)
);

CREATE INDEX idx_ml_predictions_timestamp ON ml_predictions(timestamp DESC);
CREATE INDEX idx_ml_predictions_pod ON ml_predictions(pod_name, namespace);
CREATE INDEX idx_ml_predictions_issue ON ml_predictions(predicted_issue, timestamp DESC);

-- Convert to hypertable if TimescaleDB is available and table is empty
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables 
            WHERE hypertable_name = 'ml_predictions'
        ) THEN
            IF (SELECT COUNT(*) FROM ml_predictions) = 0 THEN
                PERFORM create_hypertable('ml_predictions', 'timestamp', if_not_exists => TRUE);
                RAISE NOTICE 'Converted ml_predictions to hypertable';
            ELSE
                RAISE NOTICE 'ml_predictions has data, skipping hypertable conversion';
            END IF;
        END IF;
    END IF;
END $$;

-- Issues table (matching Go code schema)
DROP TABLE IF EXISTS issues CASCADE;
CREATE TABLE issues (
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

CREATE INDEX idx_issues_pod ON issues(pod_name, namespace, created_at DESC);
CREATE INDEX idx_issues_status ON issues(status, created_at DESC);
CREATE INDEX idx_issues_type ON issues(issue_type, created_at DESC);

-- Remediations table (matching Go code schema)
DROP TABLE IF EXISTS remediations CASCADE;
CREATE TABLE remediations (
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
    strategy TEXT,
    -- New fields for enhanced AI remediation
    confidence DOUBLE PRECISION,
    risk_level TEXT,
    plan_details JSONB
);

CREATE INDEX idx_remediations_issue ON remediations(issue_id);
CREATE INDEX idx_remediations_pod ON remediations(pod_name, namespace, executed_at DESC);
CREATE INDEX idx_remediations_timestamp ON remediations(timestamp DESC);

-- Trigger function to auto-populate is_anomaly and anomaly_type
CREATE OR REPLACE FUNCTION update_ml_predictions_fields()
RETURNS TRIGGER AS $$
BEGIN
    -- Set is_anomaly based on predicted_issue
    IF NEW.predicted_issue IS NOT NULL AND NEW.predicted_issue != 'Normal' THEN
        NEW.is_anomaly := 1;
        NEW.anomaly_type := NEW.predicted_issue;
    ELSE
        NEW.is_anomaly := 0;
        NEW.anomaly_type := NULL;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger
DROP TRIGGER IF EXISTS trigger_update_ml_predictions_fields ON ml_predictions;
CREATE TRIGGER trigger_update_ml_predictions_fields
    BEFORE INSERT OR UPDATE ON ml_predictions
    FOR EACH ROW
    EXECUTE FUNCTION update_ml_predictions_fields();

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

-- Cost savings table for tracking optimization benefits
DROP TABLE IF EXISTS cost_savings CASCADE;
CREATE TABLE cost_savings (
    id BIGSERIAL PRIMARY KEY,
    pod_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    issue_type TEXT NOT NULL,
    original_cost_per_hour DOUBLE PRECISION,
    optimized_cost_per_hour DOUBLE PRECISION,
    savings_per_hour DOUBLE PRECISION,
    estimated_monthly_savings DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    optimization_type TEXT,
    description TEXT,
    UNIQUE (pod_name, namespace, timestamp, issue_type)
);

-- Optimization actions table for tracking what optimizations were applied
DROP TABLE IF EXISTS optimization_actions CASCADE;
CREATE TABLE optimization_actions (
    id BIGSERIAL PRIMARY KEY,
    pod_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action_type TEXT NOT NULL,
    action_details TEXT,
    estimated_savings DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    status TEXT DEFAULT 'applied',
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (pod_name, namespace, timestamp, action_type)
);

-- Remediation actions table for tracking remediation attempts and outcomes
DROP TABLE IF EXISTS remediation_actions CASCADE;
CREATE TABLE remediation_actions (
    id BIGSERIAL PRIMARY KEY,
    issue_id TEXT,
    pod_name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action_type TEXT NOT NULL,
    action_details TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    execution_time_seconds INTEGER,
    ai_recommendation TEXT,
    strategy_used TEXT,
    confidence DOUBLE PRECISION,
    status TEXT DEFAULT 'attempted'
);

-- Create indexes for performance
CREATE INDEX idx_cost_savings_timestamp ON cost_savings(timestamp DESC);
CREATE INDEX idx_cost_savings_pod ON cost_savings(pod_name, namespace);
CREATE INDEX idx_optimization_actions_timestamp ON optimization_actions(timestamp DESC);
CREATE INDEX idx_optimization_actions_pod ON optimization_actions(pod_name, namespace);
CREATE INDEX idx_remediation_actions_timestamp ON remediation_actions(timestamp DESC);
CREATE INDEX idx_remediation_actions_pod ON remediation_actions(pod_name, namespace);
CREATE INDEX idx_remediation_actions_issue ON remediation_actions(issue_id);

-- Create predictions view for Grafana compatibility
CREATE OR REPLACE VIEW predictions AS 
SELECT 
    timestamp,
    timestamp as time,
    pod_name,
    namespace,
    COALESCE(predicted_issue, prediction_type, 'unknown') AS anomaly_type,
    CASE 
        WHEN confidence IS NOT NULL THEN confidence
        WHEN prediction_value IS NOT NULL THEN prediction_value
        ELSE 0.0
    END AS confidence,
    is_anomaly,
    COALESCE(oom_score, 0) as oom_score,
    COALESCE(crash_loop_score, 0) as crash_loop_score,
    COALESCE(high_cpu_score, 0) as high_cpu_score,
    COALESCE(disk_pressure_score, 0) as disk_pressure_score,
    COALESCE(network_error_score, 0) as network_error_score,
    model_version,
    explanation,
    features,
    top_features
FROM ml_predictions;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO aura;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO aura;

-- Success message
DO $$ 
BEGIN 
    RAISE NOTICE 'Database schema initialized successfully!';
END $$;
