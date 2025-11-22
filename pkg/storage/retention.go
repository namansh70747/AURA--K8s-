package storage

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	"github.com/namansh70747/aura-k8s/pkg/utils"
)

// RetentionPolicy represents a TimescaleDB retention policy
type RetentionPolicy struct {
	Hypertable       string
	Interval         string
	JobID            int64
	ScheduleInterval string
	InitialStart     string
}

// VerifyRetentionPolicies checks if retention policies are active for TimescaleDB hypertables
func (p *PostgresDB) VerifyRetentionPolicies(ctx context.Context) ([]RetentionPolicy, error) {
	// Check if TimescaleDB is available
	if !p.isTimescaleEnabled(ctx) {
		utils.Log.Warn("TimescaleDB not available, skipping retention policy verification")
		return nil, nil
	}

	query := `
	SELECT 
		ht.table_name AS hypertable,
		j.config->>'drop_after' AS interval,
		j.job_id,
		j.config->>'schedule_interval' AS schedule_interval,
		j.config->>'initial_start' AS initial_start
	FROM timescaledb_information.jobs j
	JOIN _timescaledb_catalog.hypertable ht ON ht.id = (j.config->>'hypertable_id')::bigint
	WHERE j.proc_name = 'policy_retention'
		AND j.config->>'hypertable_id' IS NOT NULL
	ORDER BY hypertable;
	`

	rows, err := p.db.QueryContext(ctx, query)
	if err != nil {
		// If the query fails, TimescaleDB might not be available or tables don't exist
		utils.Log.WithError(err).Warn("Failed to query retention policies - TimescaleDB may not be fully configured")
		return nil, nil
	}
	defer rows.Close()

	var policies []RetentionPolicy
	for rows.Next() {
		var policy RetentionPolicy
		var interval sql.NullString
		var jobID sql.NullInt64
		var scheduleInterval sql.NullString
		var initialStart sql.NullString

		err := rows.Scan(
			&policy.Hypertable,
			&interval,
			&jobID,
			&scheduleInterval,
			&initialStart,
		)
		if err != nil {
			utils.Log.WithError(err).Warn("Failed to scan retention policy row")
			continue
		}

		if interval.Valid {
			policy.Interval = interval.String
		}
		if jobID.Valid {
			policy.JobID = jobID.Int64
		}
		if scheduleInterval.Valid {
			policy.ScheduleInterval = scheduleInterval.String
		}
		if initialStart.Valid {
			policy.InitialStart = initialStart.String
		}

		policies = append(policies, policy)
	}

	if err = rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating retention policies: %w", err)
	}

	return policies, nil
}

// EnsureRetentionPolicies creates retention policies if they don't exist
func (p *PostgresDB) EnsureRetentionPolicies(ctx context.Context) error {
	if !p.isTimescaleEnabled(ctx) {
		utils.Log.Warn("TimescaleDB not available, skipping retention policy setup")
		return nil
	}

	policies := map[string]string{
		"pod_metrics":    "7 days",
		"node_metrics":   "7 days",
		"ml_predictions": "30 days",
	}

	for hypertable, interval := range policies {
		// Validate hypertable name to prevent SQL injection (whitelist approach)
		validHypertables := map[string]bool{
			"pod_metrics":    true,
			"node_metrics":   true,
			"ml_predictions": true,
		}
		if !validHypertables[hypertable] {
			utils.Log.WithField("hypertable", hypertable).Warn("Invalid hypertable name, skipping retention policy")
			continue
		}

		// Validate interval format (must be a valid PostgreSQL interval)
		// Only allow digits followed by space and valid time units
		validIntervalPattern := true
		if len(interval) == 0 {
			validIntervalPattern = false
		}
		// Basic validation: should contain digits and time units
		hasDigits := false
		hasTimeUnit := false
		for _, r := range interval {
			if r >= '0' && r <= '9' {
				hasDigits = true
			}
			if r == 'd' || r == 'h' || r == 'm' || r == 's' {
				hasTimeUnit = true
			}
		}
		if !hasDigits || !hasTimeUnit {
			validIntervalPattern = false
		}

		if !validIntervalPattern {
			utils.Log.WithFields(map[string]interface{}{
				"hypertable": hypertable,
				"interval":   interval,
			}).Warn("Invalid interval format, skipping retention policy")
			continue
		}

		// Use parameterized query approach: validate and use safe string formatting
		// Since add_retention_policy doesn't support parameterized queries directly,
		// we validate inputs strictly and use quoted identifiers
		query := fmt.Sprintf(
			"SELECT add_retention_policy(%s, INTERVAL %s, if_not_exists => TRUE);",
			fmt.Sprintf("'%s'", strings.ReplaceAll(hypertable, "'", "''")), // Escape single quotes
			fmt.Sprintf("'%s'", strings.ReplaceAll(interval, "'", "''")),   // Escape single quotes
		)

		_, err := p.db.ExecContext(ctx, query)
		if err != nil {
			utils.Log.WithError(err).WithField("hypertable", hypertable).Warn("Failed to create retention policy")
			// Continue with other policies
			continue
		}

		utils.Log.WithFields(map[string]interface{}{
			"hypertable": hypertable,
			"interval":   interval,
		}).Info("Retention policy ensured")
	}

	// Verify policies were created
	verifiedPolicies, err := p.VerifyRetentionPolicies(ctx)
	if err != nil {
		utils.Log.WithError(err).Warn("Failed to verify retention policies after creation")
	} else if len(verifiedPolicies) > 0 {
		utils.Log.Infof("Successfully verified %d retention policies", len(verifiedPolicies))
		for _, policy := range verifiedPolicies {
			utils.Log.WithFields(map[string]interface{}{
				"hypertable":        policy.Hypertable,
				"interval":          policy.Interval,
				"job_id":            policy.JobID,
				"schedule_interval": policy.ScheduleInterval,
			}).Debug("Active retention policy")
		}
	} else {
		utils.Log.Warn("No retention policies found - they may be created on next schema initialization")
	}

	return nil
}

// isTimescaleEnabled checks if TimescaleDB is available
func (p *PostgresDB) isTimescaleEnabled(ctx context.Context) bool {
	var exists bool
	query := `SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'timescaledb');`
	err := p.db.QueryRowContext(ctx, query).Scan(&exists)
	if err != nil {
		return false
	}
	return exists
}
