"""
BEAST LEVEL ML Training Pipeline for AURA K8s
Mac M4 Optimized - CPU-only training with 200+ features
"""

import os
import sys
import json
import time
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import joblib

# ML Libraries
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix, roc_auc_score
)
from sklearn.feature_selection import SelectKBest, f_classif, mutual_info_classif

# Gradient Boosting
import xgboost as xgb
import lightgbm as lgb
try:
    from catboost import CatBoostClassifier
    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False
    print("⚠️  CatBoost not available, skipping...")

# Hyperparameter optimization
try:
    import optuna
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False
    print("⚠️  Optuna not available, using default hyperparameters...")

# Feature engineering
from feature_engineering import AdvancedFeatureEngineer

warnings.filterwarnings('ignore')

# Configuration
RANDOM_SEED = int(os.getenv("ML_RANDOM_SEED", "42"))
np.random.seed(RANDOM_SEED)

# Memory optimization for Mac M4 (16GB RAM)
MEMORY_LIMIT_GB = float(os.getenv("ML_MEMORY_LIMIT_GB", "12.0"))
CHUNK_SIZE = int(os.getenv("ML_CHUNK_SIZE", "10000"))

# Model directory
MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)


class BeastLevelTrainer:
    """
    BEAST LEVEL ML Trainer - Optimized for Mac M4
    Trains multiple models with 200+ features, CPU-optimized
    """
    
    def __init__(self, memory_limit_gb: float = MEMORY_LIMIT_GB):
        self.memory_limit_gb = memory_limit_gb
        self.feature_engineer = AdvancedFeatureEngineer(memory_limit_gb=memory_limit_gb)
        self.scaler = StandardScaler()
        self.label_encoder = LabelEncoder()
        self.feature_selector = None
        self.models = {}
        self.training_results = {}
        self.feature_names = []
        self.anomaly_types = []
        
    def load_data(self, data_path: Optional[str] = None, 
                  from_database: bool = False,
                  db_connection_string: Optional[str] = None) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Load training data from file or database
        
        Args:
            data_path: Path to CSV file
            from_database: Load from PostgreSQL database
            db_connection_string: Database connection string
            
        Returns:
            Tuple of (features DataFrame, labels Series)
        """
        print("📊 Loading training data...")
        
        if from_database and db_connection_string:
            df = self._load_from_database(db_connection_string)
        elif data_path and os.path.exists(data_path):
            df = pd.read_csv(data_path, low_memory=False)
            print(f"   Loaded {len(df)} samples from {data_path}")
        else:
            # Generate synthetic data for testing
            print("   No data source provided, generating synthetic data...")
            df = self._generate_synthetic_data(n_samples=50000)
        
        # Separate features and labels
        if 'anomaly_type' in df.columns:
            y = df['anomaly_type']
            X_raw = df.drop('anomaly_type', axis=1)
        else:
            # Create labels from anomaly indicators
            y = self._create_labels_from_indicators(df)
            X_raw = df
        
        # Store anomaly types
        self.anomaly_types = sorted(y.unique().tolist())
        print(f"   Found {len(self.anomaly_types)} anomaly types: {self.anomaly_types[:10]}...")
        
        return X_raw, y
    
    def _load_from_database(self, conn_string: str) -> pd.DataFrame:
        """Load data from PostgreSQL database"""
        try:
            import psycopg
            with psycopg.connect(conn_string) as conn:
                query = """
                    SELECT 
                        cpu_usage_millicores as cpu_usage,
                        memory_usage_bytes as memory_usage,
                        disk_usage_bytes as disk_usage,
                        network_rx_bytes, network_tx_bytes,
                        network_rx_errors, network_tx_errors,
                        cpu_utilization, memory_utilization,
                        restarts as restart_count,
                        age as age_seconds,
                        cpu_limit_millicores as cpu_limit,
                        memory_limit_bytes as memory_limit,
                        disk_limit_bytes as disk_limit,
                        cpu_trend, memory_trend, restart_trend,
                        has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues,
                        ready, phase, container_state, last_state_reason,
                        timestamp, namespace, pod_name
                    FROM pod_metrics
                    WHERE timestamp > NOW() - INTERVAL '30 days'
                    ORDER BY timestamp DESC
                    LIMIT 100000
                """
                df = pd.read_sql(query, conn)
                print(f"   Loaded {len(df)} samples from database")
                return df
        except Exception as e:
            print(f"   ⚠️  Failed to load from database: {e}")
            print("   Falling back to synthetic data...")
            return self._generate_synthetic_data(n_samples=50000)
    
    def _generate_synthetic_data(self, n_samples: int = 50000) -> pd.DataFrame:
        """Generate synthetic training data"""
        print(f"   Generating {n_samples} synthetic samples...")
        
        anomaly_types = [
            'healthy', 'cpu_spike', 'memory_leak', 'disk_full', 'network_latency',
            'pod_crash', 'oom_kill', 'slow_response', 'high_error_rate', 'deadlock',
            'resource_contention', 'connection_pool_exhausted', 'dns_resolution_failure',
            'disk_io_bottleneck', 'cache_thrashing', 'image_pull_error', 'crash_loop',
            'cpu_throttling', 'memory_pressure', 'network_partition'
        ]
        
        data = []
        np.random.seed(RANDOM_SEED)
        
        for _ in range(n_samples):
            anomaly = np.random.choice(anomaly_types, p=[0.6] + [0.4/19]*19)  # 60% healthy
            
            # Generate metrics based on anomaly type
            if anomaly == 'healthy':
                cpu = np.random.uniform(10, 40)
                memory = np.random.uniform(20, 50)
                disk = np.random.uniform(10, 40)
            elif anomaly == 'cpu_spike':
                cpu = np.random.uniform(80, 100)
                memory = np.random.uniform(20, 60)
                disk = np.random.uniform(10, 40)
            elif anomaly == 'memory_leak':
                cpu = np.random.uniform(30, 60)
                memory = np.random.uniform(70, 95)
                disk = np.random.uniform(10, 40)
            elif anomaly == 'oom_kill':
                cpu = np.random.uniform(30, 70)
                memory = np.random.uniform(95, 100)
                disk = np.random.uniform(10, 50)
            elif anomaly == 'crash_loop':
                cpu = np.random.uniform(0, 20)
                memory = np.random.uniform(0, 30)
                disk = np.random.uniform(10, 40)
            else:
                cpu = np.random.uniform(20, 80)
                memory = np.random.uniform(30, 80)
                disk = np.random.uniform(20, 70)
            
            network_rx = np.random.uniform(100, 5000)
            network_tx = np.random.uniform(100, 5000)
            restarts = np.random.poisson(0.5 if anomaly == 'healthy' else 3)
            age = np.random.uniform(60, 86400)  # 1 minute to 1 day
            
            data.append({
                'cpu_usage': cpu,
                'memory_usage': memory * 1024 * 1024,  # Convert to bytes
                'disk_usage': disk * 1024 * 1024,
                'network_rx_bytes': network_rx,
                'network_tx_bytes': network_tx,
                'network_rx_errors': 0 if anomaly != 'network_latency' else np.random.poisson(10),
                'network_tx_errors': 0 if anomaly != 'network_latency' else np.random.poisson(10),
                'cpu_utilization': cpu,
                'memory_utilization': memory,
                'restart_count': restarts,
                'age_minutes': age / 60,
                'cpu_limit': 1000,
                'memory_limit': 2 * 1024 * 1024 * 1024,  # 2GB
                'disk_limit': 10 * 1024 * 1024 * 1024,  # 10GB
                'cpu_trend': np.random.uniform(-5, 5),
                'memory_trend': np.random.uniform(-5, 5),
                'restart_trend': np.random.uniform(-1, 1),
                'has_oom_kill': 1 if anomaly == 'oom_kill' else 0,
                'has_crash_loop': 1 if anomaly == 'crash_loop' else 0,
                'has_high_cpu': 1 if anomaly == 'cpu_spike' else 0,
                'has_network_issues': 1 if 'network' in anomaly.lower() else 0,
                'ready': 1 if anomaly == 'healthy' else 0,
                'phase': 'Running' if anomaly == 'healthy' else 'Pending',
                'container_state': 'Running' if anomaly == 'healthy' else 'Waiting',
                'last_state_reason': anomaly if anomaly != 'healthy' else '',
                'anomaly_type': anomaly,
                'timestamp': datetime.now(),
                'namespace': 'default',
                'pod_name': f'pod-{np.random.randint(1000, 9999)}'
            })
        
        return pd.DataFrame(data)
    
    def _create_labels_from_indicators(self, df: pd.DataFrame) -> pd.Series:
        """Create labels from anomaly indicators"""
        labels = []
        for _, row in df.iterrows():
            if row.get('has_oom_kill', 0):
                labels.append('oom_kill')
            elif row.get('has_crash_loop', 0):
                labels.append('crash_loop')
            elif row.get('has_high_cpu', 0):
                labels.append('cpu_spike')
            elif row.get('has_network_issues', 0):
                labels.append('network_latency')
            else:
                labels.append('healthy')
        return pd.Series(labels)
    
    def train(self, X_raw: pd.DataFrame, y: pd.Series, 
              optimize_hyperparameters: bool = False,
              n_trials: int = 50) -> Dict:
        """
        Train all models with feature engineering
        
        Args:
            X_raw: Raw features DataFrame
            y: Labels Series
            optimize_hyperparameters: Whether to optimize hyperparameters
            n_trials: Number of optimization trials
            
        Returns:
            Dictionary with training results
        """
        print("\n" + "="*70)
        print("🚀 BEAST LEVEL ML TRAINING - Mac M4 Optimized")
        print("="*70)
        
        start_time = time.time()
        
        # Step 1: Feature Engineering
        print("\n🔧 Step 1: Feature Engineering (200+ features)...")
        X = self.feature_engineer.engineer_features(X_raw, chunk_size=CHUNK_SIZE)
        self.feature_names = self.feature_engineer.get_feature_names()
        print(f"✅ Created {len(self.feature_names)} features")
        
        # Step 2: Encode Labels
        print("\n🔤 Step 2: Encoding labels...")
        y_encoded = self.label_encoder.fit_transform(y)
        print(f"✅ Encoded {len(self.label_encoder.classes_)} classes")
        
        # Step 3: Split Data
        print("\n✂️  Step 3: Splitting data...")
        X_train, X_test, y_train, y_test = train_test_split(
            X, y_encoded, test_size=0.2, random_state=RANDOM_SEED, stratify=y_encoded
        )
        print(f"✅ Train: {len(X_train)}, Test: {len(X_test)}")
        
        # Step 4: Feature Scaling
        print("\n📏 Step 4: Feature scaling...")
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        print("✅ Features scaled")
        
        # Step 5: Feature Selection (optional - select top 150 features)
        print("\n🎯 Step 5: Feature selection (top 150 features)...")
        n_features_to_select = min(150, len(self.feature_names))
        self.feature_selector = SelectKBest(f_classif, k=n_features_to_select)
        X_train_selected = self.feature_selector.fit_transform(X_train_scaled, y_train)
        X_test_selected = self.feature_selector.transform(X_test_scaled)
        selected_features = [self.feature_names[i] for i in self.feature_selector.get_support(indices=True)]
        print(f"✅ Selected {len(selected_features)} top features")
        
        # Step 6: Train Models
        print("\n🤖 Step 6: Training models...")
        self.models = {}
        
        # XGBoost
        print("\n   Training XGBoost...")
        xgb_model = self._train_xgboost(X_train_selected, y_train, optimize_hyperparameters, n_trials)
        self.models['xgboost'] = xgb_model
        self._evaluate_model('xgboost', xgb_model, X_test_selected, y_test)
        
        # LightGBM
        print("\n   Training LightGBM...")
        lgb_model = self._train_lightgbm(X_train_selected, y_train, optimize_hyperparameters, n_trials)
        self.models['lightgbm'] = lgb_model
        self._evaluate_model('lightgbm', lgb_model, X_test_selected, y_test)
        
        # CatBoost
        if CATBOOST_AVAILABLE:
            print("\n   Training CatBoost...")
            cat_model = self._train_catboost(X_train_selected, y_train, optimize_hyperparameters, n_trials)
            self.models['catboost'] = cat_model
            self._evaluate_model('catboost', cat_model, X_test_selected, y_test)
        
        # Random Forest
        print("\n   Training Random Forest...")
        rf_model = self._train_random_forest(X_train_selected, y_train)
        self.models['random_forest'] = rf_model
        self._evaluate_model('random_forest', rf_model, X_test_selected, y_test)
        
        # Isolation Forest (unsupervised)
        print("\n   Training Isolation Forest...")
        iso_model = self._train_isolation_forest(X_train_selected, y_train)
        self.models['isolation_forest'] = iso_model
        self._evaluate_model('isolation_forest', iso_model, X_test_selected, y_test)
        
        # Step 7: Create Ensemble
        print("\n🎯 Step 7: Creating weighted ensemble...")
        ensemble = self._create_ensemble()
        self.models['ensemble'] = ensemble
        self._evaluate_model('ensemble', ensemble, X_test_selected, y_test)
        
        # Step 8: Save Everything
        print("\n💾 Step 8: Saving models and artifacts...")
        self._save_models()
        
        training_time = time.time() - start_time
        print(f"\n✅ Training completed in {training_time:.2f} seconds ({training_time/60:.2f} minutes)")
        
        return self.training_results
    
    def _train_xgboost(self, X_train, y_train, optimize: bool, n_trials: int):
        """Train XGBoost model"""
        if optimize and OPTUNA_AVAILABLE:
            def objective(trial):
                params = {
                    'n_estimators': trial.suggest_int('n_estimators', 100, 500),
                    'max_depth': trial.suggest_int('max_depth', 5, 15),
                    'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                    'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                    'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
                    'min_child_weight': trial.suggest_int('min_child_weight', 1, 10),
                    'gamma': trial.suggest_float('gamma', 0, 0.5),
                    'reg_alpha': trial.suggest_float('reg_alpha', 0, 1),
                    'reg_lambda': trial.suggest_float('reg_lambda', 0, 2),
                }
                model = xgb.XGBClassifier(**params, n_jobs=-1, tree_method='hist', 
                                        device='cpu', random_state=RANDOM_SEED, verbosity=0)
                scores = cross_val_score(model, X_train, y_train, cv=3, scoring='f1_weighted', n_jobs=-1)
                return scores.mean()
            
            study = optuna.create_study(direction='maximize')
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            best_params = study.best_params
            print(f"      Best params: {best_params}")
        else:
            best_params = {
                'n_estimators': 300,
                'max_depth': 10,
                'learning_rate': 0.05,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'min_child_weight': 3,
                'gamma': 0.1,
                'reg_alpha': 0.1,
                'reg_lambda': 1.0,
            }
        
        model = xgb.XGBClassifier(
            **best_params,
            n_jobs=-1,
            tree_method='hist',
            device='cpu',
            random_state=RANDOM_SEED,
            verbosity=0
        )
        model.fit(X_train, y_train)
        return model
    
    def _train_lightgbm(self, X_train, y_train, optimize: bool, n_trials: int):
        """Train LightGBM model"""
        if optimize and OPTUNA_AVAILABLE:
            def objective(trial):
                params = {
                    'n_estimators': trial.suggest_int('n_estimators', 100, 500),
                    'max_depth': trial.suggest_int('max_depth', 5, 15),
                    'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                    'num_leaves': trial.suggest_int('num_leaves', 20, 100),
                    'feature_fraction': trial.suggest_float('feature_fraction', 0.6, 1.0),
                    'bagging_fraction': trial.suggest_float('bagging_fraction', 0.6, 1.0),
                    'bagging_freq': trial.suggest_int('bagging_freq', 1, 10),
                    'min_child_samples': trial.suggest_int('min_child_samples', 10, 50),
                }
                model = lgb.LGBMClassifier(**params, n_jobs=-1, device='cpu', 
                                         random_state=RANDOM_SEED, verbosity=-1)
                scores = cross_val_score(model, X_train, y_train, cv=3, scoring='f1_weighted', n_jobs=-1)
                return scores.mean()
            
            study = optuna.create_study(direction='maximize')
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            best_params = study.best_params
            print(f"      Best params: {best_params}")
        else:
            best_params = {
                'n_estimators': 300,
                'max_depth': 10,
                'learning_rate': 0.05,
                'num_leaves': 31,
                'feature_fraction': 0.8,
                'bagging_fraction': 0.8,
                'bagging_freq': 5,
                'min_child_samples': 20,
            }
        
        model = lgb.LGBMClassifier(
            **best_params,
            n_jobs=-1,
            device='cpu',
            random_state=RANDOM_SEED,
            verbosity=-1
        )
        model.fit(X_train, y_train)
        return model
    
    def _train_catboost(self, X_train, y_train, optimize: bool, n_trials: int):
        """Train CatBoost model"""
        if optimize and OPTUNA_AVAILABLE:
            def objective(trial):
                params = {
                    'iterations': trial.suggest_int('iterations', 100, 500),
                    'depth': trial.suggest_int('depth', 5, 15),
                    'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
                    'l2_leaf_reg': trial.suggest_float('l2_leaf_reg', 1, 10),
                }
                model = CatBoostClassifier(**params, thread_count=-1, random_seed=RANDOM_SEED, verbose=False)
                scores = cross_val_score(model, X_train, y_train, cv=3, scoring='f1_weighted', n_jobs=-1)
                return scores.mean()
            
            study = optuna.create_study(direction='maximize')
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            best_params = study.best_params
            print(f"      Best params: {best_params}")
        else:
            best_params = {
                'iterations': 300,
                'depth': 10,
                'learning_rate': 0.05,
                'l2_leaf_reg': 3,
            }
        
        model = CatBoostClassifier(
            **best_params,
            thread_count=-1,
            random_seed=RANDOM_SEED,
            verbose=False
        )
        model.fit(X_train, y_train)
        return model
    
    def _train_random_forest(self, X_train, y_train):
        """Train Random Forest model"""
        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=15,
            min_samples_split=5,
            min_samples_leaf=2,
            n_jobs=-1,
            random_state=RANDOM_SEED
        )
        model.fit(X_train, y_train)
        return model
    
    def _train_isolation_forest(self, X_train, y_train):
        """Train Isolation Forest (unsupervised anomaly detection)"""
        model = IsolationForest(
            n_estimators=200,
            max_samples='auto',
            contamination=0.1,
            max_features=1.0,
            bootstrap=False,
            n_jobs=-1,
            random_state=RANDOM_SEED
        )
        # Fit on all data (unsupervised)
        model.fit(X_train)
        return model
    
    def _create_ensemble(self):
        """Create weighted ensemble"""
        class WeightedEnsemble:
            def __init__(self, models, weights):
                self.models = models
                self.weights = weights
            
            def predict(self, X):
                predictions = []
                probabilities = []
                
                for name, model in self.models.items():
                    if name == 'isolation_forest':
                        # Isolation Forest returns -1 (anomaly) or 1 (normal)
                        pred = model.predict(X)
                        # Convert to probability-like scores
                        proba = np.zeros((len(X), len(self.models['xgboost'].classes_)))
                        # Simple mapping: -1 -> high anomaly probability for first class
                        proba[:, 0] = (pred == -1).astype(float)
                        proba[:, 1:] = (pred == 1).astype(float) / (len(proba[0]) - 1)
                    else:
                        proba = model.predict_proba(X)
                    
                    predictions.append(proba)
                
                # Weighted average
                final_proba = np.zeros_like(predictions[0])
                for i, (name, proba) in enumerate(zip(self.models.keys(), predictions)):
                    weight = self.weights.get(name, 0.0)
                    final_proba += weight * proba
                
                # Normalize
                final_proba = final_proba / final_proba.sum(axis=1, keepdims=True)
                
                return np.argmax(final_proba, axis=1)
            
            def predict_proba(self, X):
                predictions = []
                
                for name, model in self.models.items():
                    if name == 'isolation_forest':
                        pred = model.predict(X)
                        proba = np.zeros((len(X), len(self.models['xgboost'].classes_)))
                        proba[:, 0] = (pred == -1).astype(float)
                        proba[:, 1:] = (pred == 1).astype(float) / (len(proba[0]) - 1)
                    else:
                        proba = model.predict_proba(X)
                    predictions.append(proba)
                
                final_proba = np.zeros_like(predictions[0])
                for i, (name, proba) in enumerate(zip(self.models.keys(), predictions)):
                    weight = self.weights.get(name, 0.0)
                    final_proba += weight * proba
                
                final_proba = final_proba / final_proba.sum(axis=1, keepdims=True)
                return final_proba
        
        # Weights based on expected performance
        weights = {
            'xgboost': 0.35,
            'lightgbm': 0.30,
            'catboost': 0.20 if 'catboost' in self.models else 0.0,
            'random_forest': 0.15,
            'isolation_forest': 0.0  # Not used in weighted voting
        }
        
        # Normalize weights
        total = sum(w for w in weights.values() if w > 0)
        weights = {k: v/total if v > 0 else 0 for k, v in weights.items()}
        
        return WeightedEnsemble(self.models, weights)
    
    def _evaluate_model(self, name: str, model, X_test, y_test):
        """Evaluate model and store results"""
        y_pred = model.predict(X_test)
        
        # Handle Isolation Forest differently
        if name == 'isolation_forest':
            # Convert -1/1 to class labels
            y_pred_mapped = np.zeros_like(y_pred)
            y_pred_mapped[y_pred == -1] = 0  # Anomaly -> first class
            y_pred_mapped[y_pred == 1] = len(np.unique(y_test)) - 1  # Normal -> last class
            y_pred = y_pred_mapped
        
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred, average='weighted', zero_division=0)
        recall = recall_score(y_test, y_pred, average='weighted', zero_division=0)
        f1 = f1_score(y_test, y_pred, average='weighted', zero_division=0)
        
        self.training_results[name] = {
            'accuracy': float(accuracy),
            'precision': float(precision),
            'recall': float(recall),
            'f1_score': float(f1)
        }
        
        print(f"      ✅ {name}: Accuracy={accuracy:.4f}, F1={f1:.4f}")
    
    def _save_models(self):
        """Save all models and artifacts"""
        # Save models
        for name, model in self.models.items():
            if name != 'ensemble':  # Ensemble is a wrapper, save individual models
                model_path = MODEL_DIR / f"{name}_model.joblib"
                joblib.dump(model, model_path)
                print(f"   💾 Saved {name} model")
        
        # Save ensemble separately (as a pickle of the wrapper)
        if 'ensemble' in self.models:
            ensemble_path = MODEL_DIR / "ensemble_model.joblib"
            joblib.dump(self.models['ensemble'], ensemble_path)
            print(f"   💾 Saved ensemble model")
        
        # Save scaler
        joblib.dump(self.scaler, MODEL_DIR / "scaler.joblib")
        print("   💾 Saved scaler")
        
        # Save label encoder
        joblib.dump(self.label_encoder, MODEL_DIR / "label_encoder.joblib")
        print("   💾 Saved label encoder")
        
        # Save feature selector
        if self.feature_selector:
            joblib.dump(self.feature_selector, MODEL_DIR / "feature_selector.joblib")
            print("   💾 Saved feature selector")
        
        # Save feature names
        with open(MODEL_DIR / "feature_names.json", "w") as f:
            json.dump(self.feature_names, f, indent=2)
        print("   💾 Saved feature names")
        
        # Save selected feature names
        if self.feature_selector:
            selected_feature_names = [self.feature_names[i] for i in self.feature_selector.get_support(indices=True)]
            with open(MODEL_DIR / "selected_feature_names.json", "w") as f:
                json.dump(selected_feature_names, f, indent=2)
            print("   💾 Saved selected feature names")
        
        # Save anomaly types
        with open(MODEL_DIR / "anomaly_types.json", "w") as f:
            json.dump(self.anomaly_types, f, indent=2)
        print("   💾 Saved anomaly types")
        
        # Save training metadata
        metadata = {
            'training_date': datetime.now().isoformat(),
            'version': '2.0.0-beast-level',
            'num_features': len(self.feature_names),
            'num_selected_features': len(selected_feature_names) if self.feature_selector else len(self.feature_names),
            'num_anomaly_types': len(self.anomaly_types),
            'anomaly_types': self.anomaly_types,
            'feature_names': self.feature_names,
            'selected_feature_names': selected_feature_names if self.feature_selector else self.feature_names,
            'model_performance': self.training_results,
            'random_seed': RANDOM_SEED,
            'memory_limit_gb': self.memory_limit_gb
        }
        
        with open(MODEL_DIR / "training_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        print("   💾 Saved training metadata")


def main():
    """Main training function"""
    print("="*70)
    print("🚀 AURA K8s - BEAST LEVEL ML TRAINING")
    print("   Mac M4 Optimized - CPU-Only Training")
    print("="*70)
    
    # Initialize trainer
    trainer = BeastLevelTrainer(memory_limit_gb=MEMORY_LIMIT_GB)
    
    # Load data
    data_path = os.getenv("TRAINING_DATA_PATH", None)
    from_db = os.getenv("LOAD_FROM_DATABASE", "false").lower() == "true"
    db_conn = os.getenv("DATABASE_URL", None)
    
    X_raw, y = trainer.load_data(
        data_path=data_path,
        from_database=from_db,
        db_connection_string=db_conn
    )
    
    # Train models
    optimize = os.getenv("OPTIMIZE_HYPERPARAMETERS", "false").lower() == "true"
    n_trials = int(os.getenv("OPTUNA_TRIALS", "50"))
    
    results = trainer.train(
        X_raw, y,
        optimize_hyperparameters=optimize,
        n_trials=n_trials
    )
    
    # Print summary
    print("\n" + "="*70)
    print("📊 TRAINING SUMMARY")
    print("="*70)
    for name, metrics in results.items():
        print(f"{name:20s}: Accuracy={metrics['accuracy']:.4f}, F1={metrics['f1_score']:.4f}")
    
    best_model = max(results.items(), key=lambda x: x[1]['f1_score'])
    print(f"\n🏆 Best Model: {best_model[0]} (F1: {best_model[1]['f1_score']:.4f})")
    print("\n✅ Training complete! Models saved in models/ directory")
    print("="*70)


if __name__ == "__main__":
    main()

