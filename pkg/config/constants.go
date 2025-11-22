package config

import (
	"os"
	"strconv"
	"time"
)

// GetHighCPUThreshold returns CPU threshold from environment or default
func GetHighCPUThreshold() float64 {
	if val := os.Getenv("HIGH_CPU_THRESHOLD"); val != "" {
		if threshold, err := strconv.ParseFloat(val, 64); err == nil && threshold > 0 && threshold <= 100 {
			return threshold
		}
	}
	return 80.0 // Default percentage
}

// GetHighMemoryThreshold returns memory threshold from environment or default
func GetHighMemoryThreshold() float64 {
	if val := os.Getenv("HIGH_MEMORY_THRESHOLD"); val != "" {
		if threshold, err := strconv.ParseFloat(val, 64); err == nil && threshold > 0 && threshold <= 100 {
			return threshold
		}
	}
	return 80.0 // Default percentage
}

// GetMinConfidenceForRemediation returns minimum confidence threshold from environment or default
func GetMinConfidenceForRemediation() float64 {
	if val := os.Getenv("MIN_CONFIDENCE_FOR_REMEDIATION"); val != "" {
		if confidence, err := strconv.ParseFloat(val, 64); err == nil && confidence >= 0 && confidence <= 1 {
			return confidence
		}
	}
	return 0.5 // Default
}

// GetHighConfidenceThreshold returns high confidence threshold from environment or default
func GetHighConfidenceThreshold() float64 {
	if val := os.Getenv("HIGH_CONFIDENCE_THRESHOLD"); val != "" {
		if confidence, err := strconv.ParseFloat(val, 64); err == nil && confidence >= 0 && confidence <= 1 {
			return confidence
		}
	}
	return 0.8 // Default
}

// GetMediumConfidenceThreshold returns medium confidence threshold from environment or default
func GetMediumConfidenceThreshold() float64 {
	if val := os.Getenv("MEDIUM_CONFIDENCE_THRESHOLD"); val != "" {
		if confidence, err := strconv.ParseFloat(val, 64); err == nil && confidence >= 0 && confidence <= 1 {
			return confidence
		}
	}
	return 0.6 // Default
}

// Thresholds for issue detection
// NOTE: These constants are deprecated. Use the getter functions instead:
// - GetHighCPUThreshold() instead of HighCPUThreshold
// - GetHighMemoryThreshold() instead of HighMemoryThreshold
// - GetMinConfidenceForRemediation() instead of MinConfidenceForRemediation
// - GetHighConfidenceThreshold() instead of HighConfidenceThreshold
// - GetMediumConfidenceThreshold() instead of MediumConfidenceThreshold
//
// These constants are kept for backward compatibility but should not be used in new code.
const (
	// CPU thresholds (deprecated)
	HighCPUThreshold = 80.0 // Percentage - use GetHighCPUThreshold()

	// Memory thresholds (deprecated)
	HighMemoryThreshold = 80.0 // Percentage - use GetHighMemoryThreshold()

	// Confidence thresholds (deprecated)
	MinConfidenceForRemediation = 0.5 // Minimum confidence to trigger remediation - use GetMinConfidenceForRemediation()
	HighConfidenceThreshold     = 0.8 // High confidence threshold - use GetHighConfidenceThreshold()
	MediumConfidenceThreshold   = 0.6 // Medium confidence threshold - use GetMediumConfidenceThreshold()

	// Retry configuration
	DefaultMaxRetries      = 3
	DefaultRetryBackoff    = 500 * time.Millisecond
	DefaultRequestTimeout  = 90 * time.Second
	DefaultShutdownTimeout = 30 * time.Second

	// Intervals
	DefaultCollectionInterval  = 15 * time.Second
	DefaultRemediationInterval = 30 * time.Second

	// Connection pool settings
	DefaultMaxOpenConns    = 25
	DefaultMaxIdleConns    = 5
	DefaultConnMaxLifetime = 30 * time.Minute
	DefaultConnMaxIdleTime = 5 * time.Minute

	// Timeout values
	DefaultK8sTimeout    = 30 * time.Second
	DefaultMCPTimeout    = 90 * time.Second
	DefaultMLTimeout     = 10 * time.Second
	DefaultHealthTimeout = 5 * time.Second

	// Circuit breaker settings
	CircuitBreakerFailureThreshold = 5
	CircuitBreakerResetTimeout     = 60 * time.Second

	// Batch processing
	DefaultBatchSize = 20

	// Resource request/limit ratio
	DefaultResourceRequestRatio = 0.8 // Request is 80% of limit by default

	// Cost calculation (simplified pricing) - externalized to orchestrator.py
	CPUCostPerCorePerHour  = 0.10 // USD - use environment variable
	MemoryCostPerGBPerHour = 0.05 // USD - use environment variable
	EstimatedMonthlyDays   = 30
	EstimatedDailyHours    = 24

	// Remediation risk levels
	RiskLevelLow    = "low"
	RiskLevelMedium = "medium"
	RiskLevelHigh   = "high"

	// Issue severity levels
	SeverityCritical = "critical"
	SeverityHigh     = "high"
	SeverityMedium   = "medium"
	SeverityLow      = "low"
)

// GetRetryBackoff calculates exponential backoff for retries
func GetRetryBackoff(attempt int) time.Duration {
	baseBackoff := GetRetryBackoffBase()
	return time.Duration(attempt+1) * baseBackoff
}

// GetK8sTimeout returns Kubernetes timeout from environment or default
func GetK8sTimeout() time.Duration {
	if val := os.Getenv("K8S_TIMEOUT_SECONDS"); val != "" {
		if timeout, err := strconv.Atoi(val); err == nil && timeout > 0 {
			return time.Duration(timeout) * time.Second
		}
	}
	return DefaultK8sTimeout
}

// GetK8sQPS returns Kubernetes QPS from environment or default
func GetK8sQPS() float32 {
	if val := os.Getenv("K8S_QPS"); val != "" {
		if qps, err := strconv.ParseFloat(val, 32); err == nil && qps > 0 {
			return float32(qps)
		}
	}
	return 50.0 // Default
}

// GetK8sBurst returns Kubernetes burst from environment or default
func GetK8sBurst() int {
	if val := os.Getenv("K8S_BURST"); val != "" {
		if burst, err := strconv.Atoi(val); err == nil && burst > 0 {
			return burst
		}
	}
	return 100 // Default
}

// GetMaxRetries returns max retries from environment or default
func GetMaxRetries() int {
	if val := os.Getenv("MAX_RETRIES"); val != "" {
		if retries, err := strconv.Atoi(val); err == nil && retries > 0 {
			return retries
		}
	}
	return DefaultMaxRetries
}

// GetRetryBackoffBase returns base retry backoff from environment or default
func GetRetryBackoffBase() time.Duration {
	if val := os.Getenv("RETRY_BACKOFF_MS"); val != "" {
		if backoffMs, err := strconv.Atoi(val); err == nil && backoffMs > 0 {
			return time.Duration(backoffMs) * time.Millisecond
		}
	}
	return DefaultRetryBackoff
}

// GetMCPTimeout returns MCP timeout from environment or default
func GetMCPTimeout() time.Duration {
	if val := os.Getenv("MCP_TIMEOUT_SECONDS"); val != "" {
		if timeout, err := strconv.Atoi(val); err == nil && timeout > 0 {
			return time.Duration(timeout) * time.Second
		}
	}
	return DefaultMCPTimeout
}

// GetMLTimeout returns ML timeout from environment or default
func GetMLTimeout() time.Duration {
	if val := os.Getenv("ML_TIMEOUT_SECONDS"); val != "" {
		if timeout, err := strconv.Atoi(val); err == nil && timeout > 0 {
			return time.Duration(timeout) * time.Second
		}
	}
	return DefaultMLTimeout
}

// GetHealthTimeout returns health check timeout from environment or default
func GetHealthTimeout() time.Duration {
	if val := os.Getenv("HEALTH_TIMEOUT_SECONDS"); val != "" {
		if timeout, err := strconv.Atoi(val); err == nil && timeout > 0 {
			return time.Duration(timeout) * time.Second
		}
	}
	return DefaultHealthTimeout
}

// GetCollectionInterval returns collection interval from environment or default
func GetCollectionInterval() time.Duration {
	if val := os.Getenv("COLLECTION_INTERVAL"); val != "" {
		if interval, err := time.ParseDuration(val); err == nil && interval > 0 {
			return interval
		}
	}
	return DefaultCollectionInterval
}

// GetRemediationInterval returns remediation interval from environment or default
func GetRemediationInterval() time.Duration {
	if val := os.Getenv("REMEDIATION_INTERVAL"); val != "" {
		if interval, err := time.ParseDuration(val); err == nil && interval > 0 {
			return interval
		}
	}
	return DefaultRemediationInterval
}

// GetCircuitBreakerFailureThreshold returns circuit breaker failure threshold from environment or default
func GetCircuitBreakerFailureThreshold() int {
	if val := os.Getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD"); val != "" {
		if threshold, err := strconv.Atoi(val); err == nil && threshold > 0 {
			return threshold
		}
	}
	return CircuitBreakerFailureThreshold
}

// GetCircuitBreakerResetTimeout returns circuit breaker reset timeout from environment or default
func GetCircuitBreakerResetTimeout() time.Duration {
	if val := os.Getenv("CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS"); val != "" {
		if timeout, err := strconv.Atoi(val); err == nil && timeout > 0 {
			return time.Duration(timeout) * time.Second
		}
	}
	return CircuitBreakerResetTimeout
}

// GetBatchSize returns batch size from environment or default
func GetBatchSize() int {
	if val := os.Getenv("BATCH_SIZE"); val != "" {
		if size, err := strconv.Atoi(val); err == nil && size > 0 {
			return size
		}
	}
	return DefaultBatchSize
}

// GetResourceRequestRatio returns resource request/limit ratio from environment or default
func GetResourceRequestRatio() float64 {
	if val := os.Getenv("RESOURCE_REQUEST_RATIO"); val != "" {
		if ratio, err := strconv.ParseFloat(val, 64); err == nil && ratio > 0 && ratio <= 1.0 {
			return ratio
		}
	}
	return DefaultResourceRequestRatio
}

// GetCPUCostPerCorePerHour returns CPU cost per core per hour from environment or default
func GetCPUCostPerCorePerHour() float64 {
	if val := os.Getenv("CPU_COST_PER_CORE_PER_HOUR"); val != "" {
		if cost, err := strconv.ParseFloat(val, 64); err == nil && cost > 0 {
			return cost
		}
	}
	return CPUCostPerCorePerHour
}

// GetMemoryCostPerGBPerHour returns memory cost per GB per hour from environment or default
func GetMemoryCostPerGBPerHour() float64 {
	if val := os.Getenv("MEMORY_COST_PER_GB_PER_HOUR"); val != "" {
		if cost, err := strconv.ParseFloat(val, 64); err == nil && cost > 0 {
			return cost
		}
	}
	return MemoryCostPerGBPerHour
}
