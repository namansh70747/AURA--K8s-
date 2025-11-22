package errors

import (
	"fmt"
)

// ErrorCode represents a specific error type in the system
type ErrorCode string

const (
	// Database errors (1000-1999)
	ErrCodeDatabaseConnection  ErrorCode = "DB_001"
	ErrCodeDatabaseQuery       ErrorCode = "DB_002"
	ErrCodeDatabaseSchema      ErrorCode = "DB_003"
	ErrCodeDatabaseNotFound    ErrorCode = "DB_004"

	// Kubernetes errors (2000-2999)
	ErrCodeK8sConnection       ErrorCode = "K8S_001"
	ErrCodeK8sNotFound         ErrorCode = "K8S_002"
	ErrCodeK8sUnauthorized     ErrorCode = "K8S_003"
	ErrCodeK8sForbidden        ErrorCode = "K8S_004"
	ErrCodeK8sConflict         ErrorCode = "K8S_005"

	// Metrics collection errors (3000-3999)
	ErrCodeMetricsUnavailable  ErrorCode = "METRICS_001"
	ErrCodeMetricsInvalid      ErrorCode = "METRICS_002"
	ErrCodeMetricsTimeout      ErrorCode = "METRICS_003"

	// ML service errors (4000-4999)
	ErrCodeMLServiceUnavailable ErrorCode = "ML_001"
	ErrCodeMLPredictionFailed   ErrorCode = "ML_002"
	ErrCodeMLModelNotFound      ErrorCode = "ML_003"
	ErrCodeMLInvalidFeatures    ErrorCode = "ML_004"

	// Remediation errors (5000-5999)
	ErrCodeRemediationFailed    ErrorCode = "REMED_001"
	ErrCodeRemediationInvalid   ErrorCode = "REMED_002"
	ErrCodeRemediationTimeout   ErrorCode = "REMED_003"
	ErrCodeRemediationNotFound  ErrorCode = "REMED_004"

	// MCP service errors (6000-6999)
	ErrCodeMCPUnavailable       ErrorCode = "MCP_001"
	ErrCodeMCPTimeout           ErrorCode = "MCP_002"
	ErrCodeMCPInvalidResponse   ErrorCode = "MCP_003"

	// Validation errors (7000-7999)
	ErrCodeValidationFailed     ErrorCode = "VAL_001"
	ErrCodeInvalidParameter     ErrorCode = "VAL_002"
	ErrCodeMissingParameter     ErrorCode = "VAL_003"
)

// AuraError represents a structured error with code and context
type AuraError struct {
	Code    ErrorCode
	Message string
	Details map[string]interface{}
	Cause   error
}

func (e *AuraError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("[%s] %s: %v", e.Code, e.Message, e.Cause)
	}
	return fmt.Sprintf("[%s] %s", e.Code, e.Message)
}

func (e *AuraError) Unwrap() error {
	return e.Cause
}

// New creates a new AuraError
func New(code ErrorCode, message string) *AuraError {
	return &AuraError{
		Code:    code,
		Message: message,
		Details: make(map[string]interface{}),
	}
}

// WithCause adds a cause error
func (e *AuraError) WithCause(cause error) *AuraError {
	e.Cause = cause
	return e
}

// WithDetail adds a detail field
func (e *AuraError) WithDetail(key string, value interface{}) *AuraError {
	if e.Details == nil {
		e.Details = make(map[string]interface{})
	}
	e.Details[key] = value
	return e
}

// WithDetails adds multiple detail fields
func (e *AuraError) WithDetails(details map[string]interface{}) *AuraError {
	if e.Details == nil {
		e.Details = make(map[string]interface{})
	}
	for k, v := range details {
		e.Details[k] = v
	}
	return e
}

// Common error constructors
func ErrDatabaseConnection(cause error) *AuraError {
	return New(ErrCodeDatabaseConnection, "database connection failed").WithCause(cause)
}

func ErrK8sNotFound(resourceType, namespace, name string) *AuraError {
	return New(ErrCodeK8sNotFound, fmt.Sprintf("%s not found", resourceType)).
		WithDetail("resource_type", resourceType).
		WithDetail("namespace", namespace).
		WithDetail("name", name)
}

func ErrMetricsUnavailable(cause error) *AuraError {
	return New(ErrCodeMetricsUnavailable, "metrics server unavailable").WithCause(cause)
}

func ErrMLServiceUnavailable(cause error) *AuraError {
	return New(ErrCodeMLServiceUnavailable, "ML service unavailable").WithCause(cause)
}

func ErrMLPredictionFailed(cause error) *AuraError {
	return New(ErrCodeMLPredictionFailed, "ML prediction failed").WithCause(cause)
}

func ErrRemediationFailed(action string, cause error) *AuraError {
	return New(ErrCodeRemediationFailed, fmt.Sprintf("remediation action failed: %s", action)).
		WithDetail("action", action).
		WithCause(cause)
}

func ErrValidationFailed(field, reason string) *AuraError {
	return New(ErrCodeValidationFailed, fmt.Sprintf("validation failed for %s: %s", field, reason)).
		WithDetail("field", field).
		WithDetail("reason", reason)
}

