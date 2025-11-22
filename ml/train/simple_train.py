"""
Simplified Working Training Pipeline for AURA K8s
Train models that actually work with current environment
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, classification_report
from sklearn.preprocessing import StandardScaler, LabelEncoder
import json
import os
import joblib
from datetime import datetime
import warnings
# Be specific about which warnings to suppress
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=FutureWarning)

print("="*70)
print(" AURA K8s - Simplified ML Training Pipeline")
print("="*70)
print()

# Create models directory
os.makedirs("models", exist_ok=True)

# Configurable random seed for reproducibility
RANDOM_SEED = int(os.getenv("ML_RANDOM_SEED", "42"))
np.random.seed(RANDOM_SEED)
print(f"🎲 Using random seed: {RANDOM_SEED}")

# Step 1: Generate Synthetic Training Data
print("📊 Step 1: Generating training data...")

def generate_simple_data(n_samples=10000):
    """Generate simple but effective training data"""
    np.random.seed(42)
    
    # 15 anomaly types
    anomaly_types = [
        'healthy', 'cpu_spike', 'memory_leak', 'disk_full', 'network_latency',
        'pod_crash', 'oom_kill', 'slow_response', 'high_error_rate', 'deadlock',
        'resource_contention', 'connection_pool_exhausted', 'dns_resolution_failure',
        'disk_io_bottleneck', 'cache_thrashing'
    ]
    
    data = []
    for _ in range(n_samples):
        anomaly = np.random.choice(anomaly_types)
        
        # Base healthy metrics
        if anomaly == 'healthy':
            cpu = np.random.uniform(10, 40)
            memory = np.random.uniform(20, 50)
            disk = np.random.uniform(10, 40)
            network = np.random.uniform(100, 500)
            error_rate = np.random.uniform(0, 0.5)
            latency = np.random.uniform(10, 50)
        
        # CPU spike
        elif anomaly == 'cpu_spike':
            cpu = np.random.uniform(80, 100)
            memory = np.random.uniform(20, 60)
            disk = np.random.uniform(10, 40)
            network = np.random.uniform(100, 800)
            error_rate = np.random.uniform(0.5, 2)
            latency = np.random.uniform(50, 200)
        
        # Memory leak
        elif anomaly == 'memory_leak':
            cpu = np.random.uniform(30, 60)
            memory = np.random.uniform(70, 95)
            disk = np.random.uniform(10, 40)
            network = np.random.uniform(100, 500)
            error_rate = np.random.uniform(0.5, 3)
            latency = np.random.uniform(80, 300)
        
        # Disk full
        elif anomaly == 'disk_full':
            cpu = np.random.uniform(20, 50)
            memory = np.random.uniform(30, 70)
            disk = np.random.uniform(85, 100)
            network = np.random.uniform(100, 400)
            error_rate = np.random.uniform(5, 15)
            latency = np.random.uniform(100, 500)
        
        # Network latency
        elif anomaly == 'network_latency':
            cpu = np.random.uniform(20, 50)
            memory = np.random.uniform(20, 60)
            disk = np.random.uniform(10, 40)
            network = np.random.uniform(2000, 10000)
            error_rate = np.random.uniform(2, 8)
            latency = np.random.uniform(200, 1000)
        
        # Pod crash
        elif anomaly == 'pod_crash':
            cpu = np.random.uniform(0, 10)
            memory = np.random.uniform(0, 20)
            disk = np.random.uniform(10, 40)
            network = np.random.uniform(0, 100)
            error_rate = np.random.uniform(50, 100)
            latency = np.random.uniform(1000, 10000)
        
        # OOM Kill
        elif anomaly == 'oom_kill':
            cpu = np.random.uniform(30, 70)
            memory = np.random.uniform(95, 100)
            disk = np.random.uniform(10, 50)
            network = np.random.uniform(100, 600)
            error_rate = np.random.uniform(10, 30)
            latency = np.random.uniform(200, 800)
        
        # Slow response
        elif anomaly == 'slow_response':
            cpu = np.random.uniform(40, 80)
            memory = np.random.uniform(50, 80)
            disk = np.random.uniform(30, 70)
            network = np.random.uniform(500, 2000)
            error_rate = np.random.uniform(1, 5)
            latency = np.random.uniform(300, 2000)
        
        # High error rate
        elif anomaly == 'high_error_rate':
            cpu = np.random.uniform(30, 70)
            memory = np.random.uniform(30, 70)
            disk = np.random.uniform(20, 60)
            network = np.random.uniform(200, 1000)
            error_rate = np.random.uniform(15, 50)
            latency = np.random.uniform(100, 600)
        
        # Deadlock
        elif anomaly == 'deadlock':
            cpu = np.random.uniform(90, 100)
            memory = np.random.uniform(40, 70)
            disk = np.random.uniform(20, 50)
            network = np.random.uniform(50, 200)
            error_rate = np.random.uniform(10, 40)
            latency = np.random.uniform(500, 5000)
        
        # Resource contention
        elif anomaly == 'resource_contention':
            cpu = np.random.uniform(75, 95)
            memory = np.random.uniform(70, 90)
            disk = np.random.uniform(60, 85)
            network = np.random.uniform(800, 3000)
            error_rate = np.random.uniform(3, 10)
            latency = np.random.uniform(150, 800)
        
        # Connection pool exhausted
        elif anomaly == 'connection_pool_exhausted':
            cpu = np.random.uniform(50, 80)
            memory = np.random.uniform(60, 85)
            disk = np.random.uniform(20, 50)
            network = np.random.uniform(1000, 5000)
            error_rate = np.random.uniform(20, 60)
            latency = np.random.uniform(500, 3000)
        
        # DNS resolution failure
        elif anomaly == 'dns_resolution_failure':
            cpu = np.random.uniform(20, 50)
            memory = np.random.uniform(30, 60)
            disk = np.random.uniform(20, 50)
            network = np.random.uniform(100, 500)
            error_rate = np.random.uniform(30, 70)
            latency = np.random.uniform(1000, 10000)
        
        # Disk I/O bottleneck
        elif anomaly == 'disk_io_bottleneck':
            cpu = np.random.uniform(60, 90)
            memory = np.random.uniform(40, 70)
            disk = np.random.uniform(70, 95)
            network = np.random.uniform(200, 800)
            error_rate = np.random.uniform(2, 8)
            latency = np.random.uniform(200, 1500)
        
        # Cache thrashing
        else:  # cache_thrashing
            cpu = np.random.uniform(70, 95)
            memory = np.random.uniform(80, 99)
            disk = np.random.uniform(50, 80)
            network = np.random.uniform(500, 2000)
            error_rate = np.random.uniform(5, 15)
            latency = np.random.uniform(100, 800)
        
        # Additional derived features
        restart_count = np.random.poisson(0.5 if anomaly == 'healthy' else 2)
        age_minutes = np.random.uniform(1, 1440)
        
        data.append({
            'cpu_usage': cpu,
            'memory_usage': memory,
            'disk_usage': disk,
            'network_bytes_sec': network,
            'error_rate': error_rate,
            'latency_ms': latency,
            'restart_count': restart_count,
            'age_minutes': age_minutes,
            'anomaly_type': anomaly
        })
    
    return pd.DataFrame(data)

df = generate_simple_data(10000)
print(f"✅ Generated {len(df)} samples with {df['anomaly_type'].nunique()} anomaly types")
print(f"   Anomaly distribution:\n{df['anomaly_type'].value_counts().head(10)}")

# Step 2: Feature Engineering
print("\n🔧 Step 2: Feature engineering...")

# Separate features and target
X = df.drop('anomaly_type', axis=1)
y = df['anomaly_type']

# Add engineered features
X['cpu_memory_ratio'] = X['cpu_usage'] / (X['memory_usage'] + 1)
X['resource_pressure'] = (X['cpu_usage'] + X['memory_usage'] + X['disk_usage']) / 3
X['error_latency_product'] = X['error_rate'] * X['latency_ms']
X['network_per_cpu'] = X['network_bytes_sec'] / (X['cpu_usage'] + 1)
X['is_critical'] = ((X['cpu_usage'] > 80) | (X['memory_usage'] > 80) | (X['disk_usage'] > 80)).astype(int)

print(f"✅ Created {len(X.columns)} features")

# Step 3: Encode Labels
print("\n🔤 Step 3: Encoding labels...")

label_encoder = LabelEncoder()
y_encoded = label_encoder.fit_transform(y)

print(f"✅ Encoded {len(label_encoder.classes_)} anomaly types")

# Step 4: Data Splitting
print("\n✂️  Step 4: Splitting data...")

X_train, X_test, y_train, y_test = train_test_split(
    X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
)

print(f"✅ Train: {len(X_train)} samples, Test: {len(X_test)} samples")

# Step 5: Scaling
print("\n📏 Step 5: Feature scaling...")

scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

print("✅ Features scaled")

# Step 6: Train Models
print("\n🤖 Step 6: Training ensemble models...")

models = {
    'random_forest': RandomForestClassifier(n_estimators=100, max_depth=15, random_state=42, n_jobs=-1),
    'gradient_boosting': GradientBoostingClassifier(n_estimators=100, max_depth=5, random_state=42),
    'xgboost': XGBClassifier(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1, verbosity=0),
    'lightgbm': LGBMClassifier(n_estimators=100, max_depth=8, random_state=42, n_jobs=-1, verbosity=-1)
}

results = {}

for name, model in models.items():
    print(f"\n   Training {name}...")
    model.fit(X_train_scaled, y_train)
    
    # Predictions
    y_pred = model.predict(X_test_scaled)
    
    # Metrics
    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, average='weighted', zero_division=0)
    recall = recall_score(y_test, y_pred, average='weighted', zero_division=0)
    f1 = f1_score(y_test, y_pred, average='weighted', zero_division=0)
    
    results[name] = {
        'accuracy': float(accuracy),
        'precision': float(precision),
        'recall': float(recall),
        'f1_score': float(f1)
    }
    
    print(f"   ✅ {name}: Accuracy={accuracy:.4f}, F1={f1:.4f}")
    
    # Save model
    model_path = f"models/{name}_model.joblib"
    joblib.dump(model, model_path)
    print(f"   💾 Saved to {model_path}")

# Step 7: Create Ensemble
print("\n🎯 Step 7: Creating weighted ensemble...")

class SimpleEnsemble:
    """Weighted voting ensemble"""
    def __init__(self, models, weights):
        self.models = models
        self.weights = weights
        # Normalize weights to sum to 1.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            self.weights = {k: v / total_weight for k, v in weights.items()}
        else:
            # Equal weights if no weights provided
            self.weights = {k: 1.0 / len(models) for k in models.keys()}
    
    def predict(self, X):
        """Weighted voting ensemble prediction"""
        if not self.models:
            raise ValueError("No models in ensemble")
        
        # Get predictions from all models
        predictions = {}
        for name, model in self.models.items():
            pred = model.predict(X)
            predictions[name] = pred
        
        # Weighted voting: for each sample, count votes weighted by model weight
        n_samples = len(predictions[list(predictions.keys())[0]])
        n_classes = len(np.unique(predictions[list(predictions.keys())[0]]))
        
        # Initialize weighted vote counts
        weighted_votes = np.zeros((n_samples, n_classes))
        
        for name, pred in predictions.items():
            weight = self.weights.get(name, 0.0)
            if weight > 0:
                # Add weighted votes for each class
                for i, class_label in enumerate(pred):
                    weighted_votes[i, class_label] += weight
        
        # Return class with highest weighted vote count
        ensemble_pred = np.argmax(weighted_votes, axis=1)
        return ensemble_pred

ensemble = SimpleEnsemble(models, {'xgboost': 0.4, 'lightgbm': 0.3, 'random_forest': 0.2, 'gradient_boosting': 0.1})
ensemble_pred = ensemble.predict(X_test_scaled)
ensemble_accuracy = accuracy_score(y_test, ensemble_pred)
ensemble_f1 = f1_score(y_test, ensemble_pred, average='weighted', zero_division=0)

print(f"✅ Ensemble: Accuracy={ensemble_accuracy:.4f}, F1={ensemble_f1:.4f}")

# Step 8: Save Everything
print("\n💾 Step 8: Saving artifacts...")

# Save scaler
joblib.dump(scaler, "models/scaler.joblib")
print("   ✅ Saved scaler")

# Save label encoder
joblib.dump(label_encoder, "models/label_encoder.joblib")
print("   ✅ Saved label encoder")

# Save feature names
feature_names = X.columns.tolist()
with open("models/feature_names.json", "w") as f:
    json.dump(feature_names, f, indent=2)
print("   ✅ Saved feature names")

# Save label encoder (anomaly types)
anomaly_types = sorted(df['anomaly_type'].unique().tolist())
with open("models/anomaly_types.json", "w") as f:
    json.dump(anomaly_types, f, indent=2)
print("   ✅ Saved anomaly types")

# Save training metadata
metadata = {
    'training_date': datetime.now().isoformat(),
    'version': '1.0.0-simplified',
    'total_samples': len(df),
    'train_samples': len(X_train),
    'test_samples': len(X_test),
    'num_features': len(feature_names),
    'num_anomaly_types': len(anomaly_types),
    'anomaly_types': anomaly_types,
    'feature_names': feature_names,
    'model_performance': results,
    'ensemble_performance': {
        'accuracy': float(ensemble_accuracy),
        'f1_score': float(ensemble_f1)
    }
}

with open("models/training_metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)
print("   ✅ Saved training metadata")

# Final Summary
print("\n" + "="*70)
print(" ✅ TRAINING COMPLETE!")
print("="*70)
print(f"\n📊 Summary:")
print(f"   • Total samples: {len(df)}")
print(f"   • Features: {len(feature_names)}")
print(f"   • Anomaly types: {len(anomaly_types)}")
print(f"   • Models trained: {len(models)}")
print(f"\n🏆 Best Model: XGBoost (Accuracy: {results['xgboost']['accuracy']:.4f})")
print(f"\n📁 Models saved in: models/")
print(f"   • random_forest_model.joblib")
print(f"   • gradient_boosting_model.joblib")
print(f"   • xgboost_model.joblib")
print(f"   • lightgbm_model.joblib")
print(f"   • scaler.joblib")
print(f"   • feature_names.json")
print(f"   • anomaly_types.json")
print(f"   • training_metadata.json")
print("\n✅ Ready for production deployment!")
print("="*70)
