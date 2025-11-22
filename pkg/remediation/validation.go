package remediation

import (
	"fmt"
	"math"
)

// ValidateFactor validates a factor parameter for resource increase/decrease operations
// Returns the validated factor and an error if validation fails
func ValidateFactor(factor interface{}, minFactor, maxFactor float64) (float64, error) {
	var f float64
	var ok bool
	
	// Type assertion with validation
	switch v := factor.(type) {
	case float64:
		f = v
		ok = true
	case float32:
		f = float64(v)
		ok = true
	case int:
		f = float64(v)
		ok = true
	case int32:
		f = float64(v)
		ok = true
	case int64:
		f = float64(v)
		ok = true
	default:
		return 0, fmt.Errorf("factor must be a number, got %T", factor)
	}
	
	if !ok {
		return 0, fmt.Errorf("factor must be a number, got %T", factor)
	}
	
	// Check for NaN or Infinity
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, fmt.Errorf("factor must be a valid number, got NaN or Infinity")
	}
	
	// Validate bounds
	if f < minFactor || f > maxFactor {
		return 0, fmt.Errorf("factor must be between %.1f and %.1f, got %f", minFactor, maxFactor, f)
	}
	
	return f, nil
}

// ValidateReplicas validates a replicas parameter for scale operations
// Returns the validated replicas count and an error if validation fails
func ValidateReplicas(replicas interface{}, minReplicas, maxReplicas int32) (int32, error) {
	var r float64
	var ok bool
	
	// Type assertion with validation
	switch v := replicas.(type) {
	case float64:
		r = v
		ok = true
	case float32:
		r = float64(v)
		ok = true
	case int:
		r = float64(v)
		ok = true
	case int32:
		return v, nil // Already int32
	case int64:
		r = float64(v)
		ok = true
	default:
		return 0, fmt.Errorf("replicas must be a number, got %T", replicas)
	}
	
	if !ok {
		return 0, fmt.Errorf("replicas must be a number, got %T", replicas)
	}
	
	// Check for NaN or Infinity
	if math.IsNaN(r) || math.IsInf(r, 0) {
		return 0, fmt.Errorf("replicas must be a valid number, got NaN or Infinity")
	}
	
	// Must be an integer
	if r != float64(int64(r)) {
		return 0, fmt.Errorf("replicas must be an integer, got %f", r)
	}
	
	replicasInt := int32(r)
	
	// Validate bounds
	if replicasInt < minReplicas || replicasInt > maxReplicas {
		return 0, fmt.Errorf("replicas must be between %d and %d, got %d", minReplicas, maxReplicas, replicasInt)
	}
	
	return replicasInt, nil
}

// ValidateImage validates an image parameter for update_image operations
func ValidateImage(image interface{}) (string, error) {
	img, ok := image.(string)
	if !ok {
		return "", fmt.Errorf("image must be a string, got %T", image)
	}
	
	if img == "" {
		return "", fmt.Errorf("image parameter cannot be empty")
	}
	
	// Basic validation: should contain at least one colon or slash
	hasColon := false
	hasSlash := false
	for _, char := range img {
		if char == ':' {
			hasColon = true
		}
		if char == '/' {
			hasSlash = true
		}
	}
	
	if !hasColon && !hasSlash {
		return "", fmt.Errorf("image parameter appears invalid (should be in format 'registry/repo:tag' or 'repo:tag')")
	}
	
	return img, nil
}

// ValidateContainer validates a container name parameter
func ValidateContainer(container interface{}) (string, error) {
	cont, ok := container.(string)
	if !ok {
		return "", fmt.Errorf("container must be a string, got %T", container)
	}
	
	if cont == "" {
		return "", fmt.Errorf("container parameter cannot be empty if provided")
	}
	
	return cont, nil
}

// ValidateGracePeriod validates a grace period parameter
func ValidateGracePeriod(gracePeriod interface{}) (int64, error) {
	var gp float64
	var ok bool
	
	switch v := gracePeriod.(type) {
	case float64:
		gp = v
		ok = true
	case float32:
		gp = float64(v)
		ok = true
	case int:
		gp = float64(v)
		ok = true
	case int32:
		gp = float64(v)
		ok = true
	case int64:
		return v, nil // Already int64
	default:
		return 0, fmt.Errorf("grace_period_seconds must be a number, got %T", gracePeriod)
	}
	
	if !ok {
		return 0, fmt.Errorf("grace_period_seconds must be a number, got %T", gracePeriod)
	}
	
	// Check for NaN or Infinity
	if math.IsNaN(gp) || math.IsInf(gp, 0) {
		return 0, fmt.Errorf("grace_period_seconds must be a valid number, got NaN or Infinity")
	}
	
	// Must be an integer
	if gp != float64(int64(gp)) {
		return 0, fmt.Errorf("grace_period_seconds must be an integer, got %f", gp)
	}
	
	gracePeriodInt := int64(gp)
	
	// Validate bounds (0 to 300 seconds)
	if gracePeriodInt < 0 || gracePeriodInt > 300 {
		return 0, fmt.Errorf("grace_period_seconds must be between 0 and 300, got %d", gracePeriodInt)
	}
	
	return gracePeriodInt, nil
}

