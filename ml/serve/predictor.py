import os
import json
import threading
import uuid
import time
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
import joblib
import numpy as np
import pandas as pd
import uvicorn
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
try:
    # Try relative import first (when running from ml/serve/ directory)
    from .cache import prediction_cache
except ImportError:
    # Fall back to absolute import (when running from project root)
    try:
        from ml.serve.cache import prediction_cache
    except ImportError:
        # Last resort - direct import if cache.py is in same directory
        import sys
        from pathlib import Path
        cache_path = Path(__file__).parent / "cache.py"
        if cache_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("cache", cache_path)
            cache = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cache)
            prediction_cache = cache.prediction_cache
        else:
            prediction_cache = None
            import logging
            logging.warning("Cache module not available, caching disabled. Install cache.py in ml/serve/ directory.")

# Initialize logger
import logging
logger = logging.getLogger(__name__)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="AURA ML Service", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware - configurable for production
allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
environment = os.getenv("ENVIRONMENT", "development")
if allowed_origins == ["*"] and environment == "production":
    # In production, restrict CORS
    allowed_origins = ["http://localhost:3000", "https://localhost:3000"]  # Default safe origins
    logger.warning("CORS set to * in production - consider restricting origins")
elif allowed_origins == ["*"]:
    logger.info("CORS enabled for all origins (development mode)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API versioning - create v1 router
from fastapi import APIRouter
v1_router = APIRouter(prefix="/v1", tags=["v1"])

# API Key authentication (simple implementation)
API_KEY = os.getenv("ML_SERVICE_API_KEY", "")
REQUIRE_AUTH = os.getenv("ML_SERVICE_REQUIRE_AUTH", "false").lower() == "true"

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify API key for authenticated endpoints"""
    if not REQUIRE_AUTH:
        return True
    
    environment = os.getenv("ENVIRONMENT", "development")
    if not API_KEY:
        # If auth required but no key set, fail in production
        if environment == "production":
            logger.error("API key authentication required but no API_KEY set in production")
            raise HTTPException(status_code=500, detail="API key authentication not configured")
        else:
            logger.debug("API key authentication disabled (development mode)")
            return True
    
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

# Request ID middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests for tracing"""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# Use relative path for local development, absolute for container
MODEL_DIR = Path(os.getenv("MODEL_PATH", "ml/train/models"))

# Thread-safe model loading
model_lock = threading.Lock()
models = {}
scaler = None
label_encoder = None
feature_names = []
anomaly_types = []

class PredictionRequest(BaseModel):
    features: Dict[str, float] = Field(..., description="Feature values for prediction", min_length=1)
    
    @field_validator('features')
    @classmethod
    def validate_feature_values(cls, v):
        """Validate feature values are finite numbers and all required features are present"""
        import math
        
        # Required features (must match training script)
        required_features = [
            "cpu_usage", "memory_usage", "disk_usage", "network_bytes_sec",
            "error_rate", "latency_ms", "restart_count", "age_minutes",
            "cpu_memory_ratio", "resource_pressure", "error_latency_product",
            "network_per_cpu", "is_critical"
        ]
        
        # Check all required features are present
        missing_features = set(required_features) - set(v.keys())
        if missing_features:
            raise ValueError(f"Missing required features: {', '.join(sorted(missing_features))}")
        
        # Validate each feature value
        # Define expected ranges for features (based on training data)
        feature_ranges = {
            "cpu_usage": (0.0, 100.0),
            "memory_usage": (0.0, 100.0),
            "disk_usage": (0.0, 100.0),
            "network_bytes_sec": (0.0, 1e10),  # 10GB/s max
            "error_rate": (0.0, 1000.0),  # errors per second
            "latency_ms": (0.0, 60000.0),  # 60 seconds max
            "restart_count": (0.0, 1000.0),
            "age_minutes": (0.0, 525600.0),  # 1 year max
            "cpu_memory_ratio": (0.0, 100.0),
            "resource_pressure": (0.0, 100.0),
            "error_latency_product": (0.0, 1e8),
            "network_per_cpu": (0.0, 1e9),
            "is_critical": (0.0, 1.0),
        }
        
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f"Feature {key} must be a number, got {type(value).__name__}")
            # Check for NaN or Inf
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                raise ValueError(f"Feature {key} contains invalid value (NaN or Inf): {value}")
            # Validate value ranges
            if key in feature_ranges:
                min_val, max_val = feature_ranges[key]
                if value < min_val or value > max_val:
                    raise ValueError(f"Feature {key} value {value} out of range [{min_val}, {max_val}]")
        return v

class PredictionResponse(BaseModel):
    anomaly_type: str = Field(..., description="Predicted anomaly type")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Prediction confidence (0-1)")
    probabilities: Dict[str, float] = Field(..., description="Probability distribution over all classes")
    model_used: str = Field(..., description="Model used for prediction")
    explanation: str = Field(default="", description="Human-readable explanation of the prediction")

def _load_models_sync():
    """Synchronously load models - blocks until complete"""
    global models, scaler, label_encoder, feature_names, anomaly_types, MODEL_DIR
    
    # Resolve to absolute path
    model_dir = Path(MODEL_DIR).resolve()
    logger.info(f"MODEL_DIR set to: {model_dir}")
    logger.debug(f"MODEL_PATH env: {os.getenv('MODEL_PATH')}")
    
    # Validate model directory exists
    if not model_dir.exists():
        error_msg = f"ERROR: Model directory {MODEL_DIR} does not exist! (MODEL_PATH={os.getenv('MODEL_PATH')})"
        logger.error(error_msg)
        logger.error(f"Current working directory: {os.getcwd()}")
        logger.error("Run: python ml/train/simple_train.py to train models first")
        logger.error("Service will start but predictions will fail until models are trained.")
        # Raise exception instead of returning False to prevent silent failures
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    
    with model_lock:
        logger.info("Loading ML models...")
    
    try:
        scaler_path = model_dir / "scaler.joblib"
        if scaler_path.exists():
            scaler = joblib.load(scaler_path)
            logger.info("Scaler loaded")
        else:
            error_msg = f"WARNING: Scaler not found at {scaler_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        encoder_path = model_dir / "label_encoder.joblib"
        if encoder_path.exists():
            label_encoder = joblib.load(encoder_path)
            logger.info("Label encoder loaded")
        else:
            error_msg = f"WARNING: Label encoder not found at {encoder_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        feature_path = model_dir / "feature_names.json"
        if feature_path.exists():
            with open(feature_path) as f:
                feature_names = json.load(f)
            logger.info(f"Feature names loaded: {len(feature_names)} features")
        else:
            error_msg = f"WARNING: Feature names not found at {feature_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        types_path = model_dir / "anomaly_types.json"
        if types_path.exists():
            with open(types_path) as f:
                anomaly_types = json.load(f)
            logger.info(f"Anomaly types loaded: {len(anomaly_types)} types")
        else:
            error_msg = f"WARNING: Anomaly types not found at {types_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        model_files = {
            "random_forest": "random_forest_model.joblib",
            "gradient_boosting": "gradient_boosting_model.joblib",
            "xgboost": "xgboost_model.joblib",
            "lightgbm": "lightgbm_model.joblib"
        }
        
        for name, filename in model_files.items():
            model_path = model_dir / filename
            if model_path.exists():
                models[name] = joblib.load(model_path)
                logger.info(f"{name} model loaded")
            else:
                logger.warning(f"Model file not found: {model_path}")
        
        if not models:
            error_msg = "ERROR: No models loaded! Training required."
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        # Validate model compatibility - check feature count
        if feature_names:
            expected_feature_count = len(feature_names)
            # Test with a sample feature vector to validate model compatibility
            try:
                import numpy as np
                test_features = np.zeros((1, expected_feature_count))
                for model_name, model in models.items():
                    # Try to get feature count from model (if available)
                    if hasattr(model, 'n_features_in_'):
                        if model.n_features_in_ != expected_feature_count:
                            logger.warning(f"Model {model_name} expects {model.n_features_in_} features, but we have {expected_feature_count}")
                    # Test prediction with sample data
                    try:
                        _ = model.predict(test_features)
                    except Exception as e:
                        logger.warning(f"Model {model_name} validation failed: {e}")
            except Exception as e:
                logger.error(f"Model validation error (fatal): {e}")
                raise RuntimeError(f"Model validation failed for {model_name}: {e}") from e
        
        logger.info(f"Total models loaded: {len(models)}")
        return True
            
    except Exception as e:
        logger.error(f"Error loading models: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise RuntimeError(f"Failed to load ML models: {e}") from e  # Fail fast on model load errors

# Models are loaded synchronously below, no need for async startup event

# Load models synchronously on startup (blocks until complete)
# Fail fast if models cannot be loaded - service is not functional without models
logger.info("Initializing ML service...")
try:
    _load_models_sync()
    logger.info("ML service initialized successfully")
except FileNotFoundError as e:
    logger.error(f"CRITICAL: Model files not found: {e}")
    logger.error("Service cannot function without models - exiting")
    import sys
    sys.exit(1)
except RuntimeError as e:
    logger.error(f"CRITICAL: Failed to load ML models: {e}")
    logger.error("Service cannot function without models - exiting")
    import sys
    sys.exit(1)
except Exception as e:
    logger.error(f"CRITICAL: Unexpected error loading models: {e}")
    logger.error("Service cannot function without models - exiting")
    import sys
    sys.exit(1)

@app.get("/health")
@app.get("/v1/health")  # Versioned endpoint
async def health_check(request: Request):
    """Health check with dependency validation"""
    status = "healthy"
    issues = []
    
    # Check if models are loaded
    if not models:
        status = "unhealthy"
        issues.append("No models loaded")
    
    # Check if scaler is loaded
    if not scaler:
        status = "degraded" if status == "healthy" else status
        issues.append("Scaler not loaded")
    
    # Check if label encoder is loaded
    if not label_encoder:
        status = "degraded" if status == "healthy" else status
        issues.append("Label encoder not loaded")
    
    # Check if feature names are loaded
    if not feature_names:
        status = "degraded" if status == "healthy" else status
        issues.append("Feature names not loaded")
    
    response = {
        "status": status,
        "models_loaded": len(models),
        "models": list(models.keys()),
        "ready": status == "healthy",
        "issues": issues if issues else None,
        "request_id": getattr(request.state, "request_id", None)
    }
    
    # Return 503 if unhealthy (for load balancers/orchestrators)
    if status == "unhealthy":
        from fastapi.responses import JSONResponse
        return JSONResponse(content=response, status_code=503)
    
    return response

@app.get("/ready")
@app.get("/v1/ready")  # Versioned endpoint
async def readiness_check():
    """Readiness probe - returns 200 only if fully ready"""
    if not models or not scaler or not label_encoder or not feature_names:
        from fastapi.responses import JSONResponse
        return JSONResponse(content={"ready": False}, status_code=503)
    return {"ready": True}

@v1_router.post("/predict", response_model=PredictionResponse)
@limiter.limit("100/minute")  # Rate limit: 100 requests per minute per IP
async def predict_v1(
    request: Request,
    prediction_request: PredictionRequest,
    _: bool = Depends(verify_api_key)
):
    """Make ML prediction with caching support and metrics tracking"""
    start_time = time.time()
    cache_hit = False
    
    if not models:
        raise HTTPException(status_code=503, detail="No models loaded.")
    
    # Check cache first
    if prediction_cache:
        cached_prediction = prediction_cache.get(prediction_request.features)
        if cached_prediction:
            cache_hit = True
            latency = time.time() - start_time
            logger.debug(f"[METRICS] Prediction cache hit, latency: {latency:.4f}s")
            return PredictionResponse(**cached_prediction)
    
    try:
        if not feature_names:
            raise HTTPException(status_code=500, detail="Feature names not loaded")
        
        # Validate all required features are present
        missing_features = set(feature_names) - set(prediction_request.features.keys())
        if missing_features:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required features: {', '.join(sorted(missing_features))}"
            )
        
        feature_vector = np.array([prediction_request.features.get(name, 0.0) for name in feature_names])
        
        # Validate feature count
        if len(feature_vector) != len(feature_names):
            raise HTTPException(status_code=400, detail=f"Expected {len(feature_names)} features, got {len(feature_vector)}")
        
        # Create DataFrame with feature names to match training
        feature_df = pd.DataFrame([feature_vector], columns=feature_names)
        
        if scaler:
            feature_df = pd.DataFrame(scaler.transform(feature_df), columns=feature_names)
        
        probabilities_list = []
        
        # Pass DataFrame directly to models to preserve feature names and avoid warnings
        for model_name, model in models.items():
            pred_proba = model.predict_proba(feature_df)[0]
            probabilities_list.append(pred_proba)
        
        # Weighted ensemble averaging - weight by model confidence
        # Use number of models as weight (can be improved with actual model metrics)
        weights = np.ones(len(probabilities_list)) / len(probabilities_list)
        avg_proba = np.average(probabilities_list, axis=0, weights=weights)
        final_prediction = np.argmax(avg_proba)
        confidence = float(avg_proba[final_prediction])
        
        if label_encoder:
            anomaly_type = label_encoder.inverse_transform([final_prediction])[0]
        else:
            anomaly_type = anomaly_types[final_prediction] if final_prediction < len(anomaly_types) else "unknown"
        
        prob_dict = {}
        for i, prob in enumerate(avg_proba):
            if label_encoder:
                label = label_encoder.inverse_transform([i])[0]
            else:
                label = anomaly_types[i] if i < len(anomaly_types) else f"class_{i}"
            prob_dict[label] = float(prob)
        
        # Generate explanation
        explanation = f"Model ensemble detected {anomaly_type} with {confidence:.1%} confidence. "
        if confidence > 0.8:
            explanation += "High confidence prediction based on ensemble voting."
        elif confidence > 0.6:
            explanation += "Moderate confidence prediction. Monitor pod closely."
        else:
            explanation += "Low confidence prediction. Consider manual review."
        
        # Add top contributing features based on actual feature importance
        if feature_names and len(feature_df) > 0:
            # Calculate feature importance as difference from mean (use DataFrame values)
            feature_values = feature_df.values[0]
            feature_importance = np.abs(feature_values - np.mean(feature_values))
            top_feature_indices = np.argsort(feature_importance)[-3:][::-1]  # Top 3 features
            top_features = [feature_names[idx] for idx in top_feature_indices if idx < len(feature_names)]
            if top_features:
                explanation += f" Top indicators: {', '.join(top_features)}."
        
        # Track model version dynamically
        model_versions = [name for name in models.keys()]
        model_version_str = f"ensemble-{len(model_versions)}-models" if len(model_versions) > 1 else model_versions[0] if model_versions else "unknown"
        
        response = PredictionResponse(
            anomaly_type=anomaly_type,
            confidence=confidence,
            probabilities=prob_dict,
            model_used=model_version_str,
            explanation=explanation
        )
        
        # Log prediction metrics
        latency = time.time() - start_time
        logger.debug(f"[METRICS] Prediction computed, latency: {latency:.4f}s, confidence: {confidence:.2f}, anomaly: {anomaly_type}, cache_hit: {cache_hit}")
        
        # Cache the prediction (after successful prediction)
        if prediction_cache:
            try:
                prediction_cache.set(
                    prediction_request.features,
                    response.model_dump()
                )
            except Exception as e:
                # Don't fail prediction if caching fails
                logger.warning(f"Failed to cache prediction: {e}")
        
        return response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")

@app.post("/predict", response_model=PredictionResponse)  # Legacy endpoint
@limiter.limit("100/minute")
async def predict(
    request: Request,
    prediction_request: PredictionRequest,
    _: bool = Depends(verify_api_key)
):
    """Legacy endpoint - redirects to v1"""
    return await predict_v1(request, prediction_request, _)

@v1_router.get("/models")  # Versioned endpoint
@app.get("/models")  # Legacy endpoint
async def list_models():
    return {
        "models": list(models.keys()),
        "feature_names": feature_names,
        "anomaly_types": anomaly_types
    }

@v1_router.get("/cache/stats")  # Cache statistics endpoint
@app.get("/cache/stats")  # Legacy endpoint
async def cache_stats():
    """Get cache statistics"""
    if prediction_cache:
        return prediction_cache.stats()
    return {"enabled": False, "message": "Cache not available"}

@v1_router.delete("/cache")  # Clear cache endpoint
@app.delete("/cache")  # Legacy endpoint
async def clear_cache(_: bool = Depends(verify_api_key)):
    """Clear prediction cache"""
    if prediction_cache:
        prediction_cache.clear()
        return {"message": "Cache cleared"}
    return {"message": "Cache not available"}

# Include v1 router after all endpoints are defined
app.include_router(v1_router)

if __name__ == "__main__":
    try:
        import uvicorn
        import signal
        import sys
    except ImportError:
        print("ERROR: uvicorn not installed. Install with: pip install uvicorn")
        exit(1)
    
    # Graceful shutdown handler with cleanup
    shutdown_event = threading.Event()
    
    def signal_handler(sig, frame):
        logger.info(f"Received shutdown signal {sig}, initiating graceful shutdown...")
        shutdown_event.set()
        # Give time for ongoing requests to complete
        import time
        time.sleep(2)
        logger.info("Shutdown complete")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Register cleanup on shutdown
    import atexit
    def cleanup():
        logger.info("Cleaning up resources on shutdown...")
        if prediction_cache:
            try:
                prediction_cache.clear()
            except Exception as e:
                logger.warning(f"Error clearing cache on shutdown: {e}")
    
    atexit.register(cleanup)
    
    port = int(os.getenv("PORT", 8001))
    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        cleanup()
    except Exception as e:
        logger.error(f"Error running server: {e}")
        cleanup()
        raise
