import os
import json
import threading
from pathlib import Path
from typing import Dict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import joblib
import numpy as np
import uvicorn

app = FastAPI(title="AURA ML Service")

MODEL_DIR = Path(os.getenv("MODEL_PATH", "/app/ml/train/models"))

# Thread-safe model loading
model_lock = threading.Lock()
models = {}
scaler = None
label_encoder = None
feature_names = []
anomaly_types = []
EXPECTED_FEATURES = 13  # Expected feature count

class PredictionRequest(BaseModel):
    features: Dict[str, float]

class PredictionResponse(BaseModel):
    anomaly_type: str
    confidence: float
    probabilities: Dict[str, float]
    model_used: str

@app.on_event("startup")
async def load_models():
    global models, scaler, label_encoder, feature_names, anomaly_types, EXPECTED_FEATURES
    
    with model_lock:
        print("Loading ML models...")
    
    try:
        scaler_path = MODEL_DIR / "scaler.joblib"
        if scaler_path.exists():
            scaler = joblib.load(scaler_path)
            print("Scaler loaded")
        
        encoder_path = MODEL_DIR / "label_encoder.joblib"
        if encoder_path.exists():
            label_encoder = joblib.load(encoder_path)
            print("Label encoder loaded")
        
        feature_path = MODEL_DIR / "feature_names.json"
        if feature_path.exists():
            with open(feature_path) as f:
                feature_names = json.load(f)
            print(f"Feature names loaded: {len(feature_names)} features")
        
        types_path = MODEL_DIR / "anomaly_types.json"
        if types_path.exists():
            with open(types_path) as f:
                anomaly_types = json.load(f)
            print(f"Anomaly types loaded: {len(anomaly_types)} types")
        
        model_files = {
            "random_forest": "random_forest_model.joblib",
            "gradient_boosting": "gradient_boosting_model.joblib",
            "xgboost": "xgboost_model.joblib",
            "lightgbm": "lightgbm_model.joblib"
        }
        
        for name, filename in model_files.items():
            model_path = MODEL_DIR / filename
            if model_path.exists():
                models[name] = joblib.load(model_path)
                print(f"{name} model loaded")
        
        if not models:
            print("No models loaded! Training required.")
        else:
            print(f"Total models loaded: {len(models)}")
            
    except Exception as e:
        print(f"Error loading models: {e}")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "models_loaded": len(models),
        "models": list(models.keys())
    }

@app.post("/predict", response_model=PredictionResponse)
async def predict(request: PredictionRequest):
    if not models:
        raise HTTPException(status_code=503, detail="No models loaded.")
    
    try:
        if not feature_names:
            raise HTTPException(status_code=500, detail="Feature names not loaded")
        
        feature_vector = np.array([request.features.get(name, 0.0) for name in feature_names])
        
        # Validate feature count
        if len(feature_vector) != len(feature_names):
            raise HTTPException(status_code=400, detail=f"Expected {len(feature_names)} features, got {len(feature_vector)}")
        
        feature_vector = feature_vector.reshape(1, -1)
        
        if scaler:
            feature_vector = scaler.transform(feature_vector)
        
        probabilities_list = []
        
        for model_name, model in models.items():
            pred_proba = model.predict_proba(feature_vector)[0]
            probabilities_list.append(pred_proba)
        
        avg_proba = np.mean(probabilities_list, axis=0)
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
        
        return PredictionResponse(
            anomaly_type=anomaly_type,
            confidence=confidence,
            probabilities=prob_dict,
            model_used="ensemble"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")

@app.get("/models")
async def list_models():
    return {
        "models": list(models.keys()),
        "feature_names": feature_names,
        "anomaly_types": anomaly_types
    }

if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn not installed. Install with: pip install uvicorn")
        exit(1)
    
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
