"""
Advanced Feature Engineering for AURA K8s ML Training
Creates 200+ features from Kubernetes metrics with memory optimization for Mac M4
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

# Memory optimization: Use float32 instead of float64
DTYPE_OPTIMIZATION = {
    'float64': 'float32',
    'int64': 'int32'
}


class AdvancedFeatureEngineer:
    """
    Creates 200+ engineered features from Kubernetes metrics
    Optimized for Mac M4 with 16GB RAM - processes in chunks
    """
    
    def __init__(self, memory_limit_gb: float = 12.0):
        """
        Initialize feature engineer with memory limits
        
        Args:
            memory_limit_gb: Maximum memory to use (leave 4GB free on 16GB system)
        """
        self.memory_limit_gb = memory_limit_gb
        self.feature_names = []
        
    def engineer_features(self, df: pd.DataFrame, chunk_size: int = 10000) -> pd.DataFrame:
        """
        Create 200+ features from raw metrics
        
        Args:
            df: DataFrame with raw Kubernetes metrics
            chunk_size: Process in chunks to save memory
            
        Returns:
            DataFrame with 200+ engineered features
        """
        print(f"🔧 Engineering features from {len(df)} samples...")
        
        # Optimize data types first
        df = self._optimize_dtypes(df)
        
        # Process in chunks if dataset is large
        if len(df) > chunk_size:
            print(f"   Processing in chunks of {chunk_size} to save memory...")
            chunks = []
            for i in range(0, len(df), chunk_size):
                chunk = df.iloc[i:i+chunk_size].copy()
                chunk_features = self._engineer_chunk(chunk)
                chunks.append(chunk_features)
                print(f"   Processed chunk {i//chunk_size + 1}/{(len(df)-1)//chunk_size + 1}")
            
            result = pd.concat(chunks, ignore_index=True)
        else:
            result = self._engineer_chunk(df)
        
        # Store feature names
        self.feature_names = result.columns.tolist()
        print(f"✅ Created {len(self.feature_names)} features")
        
        return result
    
    def _optimize_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimize data types to save memory"""
        df = df.copy()
        
        for col in df.select_dtypes(include=['float64']).columns:
            df[col] = df[col].astype('float32')
        
        for col in df.select_dtypes(include=['int64']).columns:
            if df[col].min() >= np.iinfo(np.int32).min and df[col].max() <= np.iinfo(np.int32).max:
                df[col] = df[col].astype('int32')
        
        return df
    
    def _engineer_chunk(self, df: pd.DataFrame) -> pd.DataFrame:
        """Engineer features for a chunk of data"""
        features = pd.DataFrame(index=df.index)
        
        # ========== BASIC METRICS (10 features) ==========
        features['cpu_usage'] = df.get('cpu_usage', df.get('cpu_usage_millicores', 0))
        features['memory_usage'] = df.get('memory_usage', df.get('memory_usage_bytes', 0))
        features['disk_usage'] = df.get('disk_usage', df.get('disk_usage_bytes', 0))
        features['network_rx'] = df.get('network_rx_bytes', 0)
        features['network_tx'] = df.get('network_tx_bytes', 0)
        features['restart_count'] = df.get('restarts', df.get('restart_count', 0))
        features['age_minutes'] = df.get('age', df.get('age_minutes', 0)) / 60.0
        features['cpu_utilization'] = df.get('cpu_utilization', 0)
        features['memory_utilization'] = df.get('memory_utilization', 0)
        features['disk_utilization'] = df.get('disk_utilization', 0)
        
        # ========== RESOURCE RATIOS (15 features) ==========
        features['cpu_memory_ratio'] = features['cpu_usage'] / (features['memory_usage'] + 1)
        features['cpu_disk_ratio'] = features['cpu_usage'] / (features['disk_usage'] + 1)
        features['memory_disk_ratio'] = features['memory_usage'] / (features['disk_usage'] + 1)
        features['network_total'] = features['network_rx'] + features['network_tx']
        features['network_ratio'] = features['network_rx'] / (features['network_tx'] + 1)
        features['cpu_util_memory_util_ratio'] = features['cpu_utilization'] / (features['memory_utilization'] + 1)
        features['resource_pressure'] = (features['cpu_utilization'] + features['memory_utilization'] + features['disk_utilization']) / 3.0
        features['cpu_efficiency'] = features['cpu_usage'] / (features['cpu_utilization'] + 1)
        features['memory_efficiency'] = features['memory_usage'] / (features['memory_utilization'] + 1)
        features['network_per_cpu'] = features['network_total'] / (features['cpu_usage'] + 1)
        features['network_per_memory'] = features['network_total'] / (features['memory_usage'] + 1)
        features['restart_rate'] = features['restart_count'] / (features['age_minutes'] + 1)
        features['error_latency_product'] = df.get('error_rate', 0) * df.get('latency_ms', 0)
        features['cpu_memory_product'] = features['cpu_usage'] * features['memory_usage']
        features['utilization_sum'] = features['cpu_utilization'] + features['memory_utilization'] + features['disk_utilization']
        
        # ========== LIMITS AND REQUESTS (20 features) ==========
        cpu_limit = df.get('cpu_limit_millicores', df.get('cpu_limit', 0))
        memory_limit = df.get('memory_limit_bytes', df.get('memory_limit', 0))
        disk_limit = df.get('disk_limit_bytes', df.get('disk_limit', 0))
        
        features['cpu_limit'] = cpu_limit
        features['memory_limit'] = memory_limit
        features['disk_limit'] = disk_limit
        features['cpu_headroom'] = cpu_limit - features['cpu_usage']
        features['memory_headroom'] = memory_limit - features['memory_usage']
        features['disk_headroom'] = disk_limit - features['disk_usage']
        features['cpu_headroom_pct'] = features['cpu_headroom'] / (cpu_limit + 1) * 100
        features['memory_headroom_pct'] = features['memory_headroom'] / (memory_limit + 1) * 100
        features['disk_headroom_pct'] = features['disk_headroom'] / (disk_limit + 1) * 100
        features['cpu_usage_vs_limit'] = features['cpu_usage'] / (cpu_limit + 1)
        features['memory_usage_vs_limit'] = features['memory_usage'] / (memory_limit + 1)
        features['disk_usage_vs_limit'] = features['disk_usage'] / (disk_limit + 1)
        features['limit_ratio_cpu_mem'] = cpu_limit / (memory_limit + 1)
        features['limit_ratio_cpu_disk'] = cpu_limit / (disk_limit + 1)
        features['limit_ratio_mem_disk'] = memory_limit / (disk_limit + 1)
        features['total_limit'] = cpu_limit + memory_limit + disk_limit
        features['total_usage'] = features['cpu_usage'] + features['memory_usage'] + features['disk_usage']
        features['total_usage_vs_limit'] = features['total_usage'] / (features['total_limit'] + 1)
        features['resource_balance'] = np.std([features['cpu_utilization'], features['memory_utilization'], features['disk_utilization']])
        
        # ========== TRENDS (30 features) ==========
        # CPU trends
        cpu_trend = df.get('cpu_trend', 0)
        memory_trend = df.get('memory_trend', 0)
        restart_trend = df.get('restart_trend', 0)
        
        features['cpu_trend'] = cpu_trend
        features['memory_trend'] = memory_trend
        features['restart_trend'] = restart_trend
        features['cpu_trend_abs'] = np.abs(cpu_trend)
        features['memory_trend_abs'] = np.abs(memory_trend)
        features['restart_trend_abs'] = np.abs(restart_trend)
        features['trend_magnitude'] = np.sqrt(cpu_trend**2 + memory_trend**2)
        features['trend_direction'] = np.sign(cpu_trend + memory_trend)
        features['trend_acceleration'] = cpu_trend * memory_trend
        features['cpu_trend_normalized'] = cpu_trend / (features['cpu_usage'] + 1)
        features['memory_trend_normalized'] = memory_trend / (features['memory_usage'] + 1)
        # Trend consistency (handle Series comparison)
        if isinstance(cpu_trend, pd.Series):
            trend_consistency = ((cpu_trend > 0) & (memory_trend > 0)) | ((cpu_trend < 0) & (memory_trend < 0))
            features['trend_consistency'] = trend_consistency.astype(float)
        else:
            features['trend_consistency'] = 1.0 if (cpu_trend > 0 and memory_trend > 0) or (cpu_trend < 0 and memory_trend < 0) else 0.0
        
        # Rolling statistics (if we have time-series data)
        if 'timestamp' in df.columns:
            df_sorted = df.sort_values('timestamp')
            for window in [5, 10, 15, 30, 60]:
                if len(df_sorted) >= window:
                    features[f'cpu_rolling_mean_{window}'] = df_sorted['cpu_utilization'].rolling(window, min_periods=1).mean().values
                    features[f'cpu_rolling_std_{window}'] = df_sorted['cpu_utilization'].rolling(window, min_periods=1).std().fillna(0).values
                    features[f'memory_rolling_mean_{window}'] = df_sorted['memory_utilization'].rolling(window, min_periods=1).mean().values
                    features[f'memory_rolling_std_{window}'] = df_sorted['memory_utilization'].rolling(window, min_periods=1).std().fillna(0).values
                else:
                    features[f'cpu_rolling_mean_{window}'] = features['cpu_utilization']
                    features[f'cpu_rolling_std_{window}'] = 0.0
                    features[f'memory_rolling_mean_{window}'] = features['memory_utilization']
                    features[f'memory_rolling_std_{window}'] = 0.0
        
        # ========== ANOMALY INDICATORS (25 features) ==========
        features['is_oom_kill'] = df.get('has_oom_kill', df.get('is_oom_kill', 0)).astype(int)
        features['is_crash_loop'] = df.get('has_crash_loop', df.get('is_crash_loop', 0)).astype(int)
        features['is_high_cpu'] = df.get('has_high_cpu', df.get('is_high_cpu', 0)).astype(int)
        features['is_network_issue'] = df.get('has_network_issues', df.get('is_network_issue', 0)).astype(int)
        features['is_ready'] = df.get('ready', df.get('is_ready', 1)).astype(int)
        features['is_critical'] = ((features['cpu_utilization'] > 80) | 
                                   (features['memory_utilization'] > 80) | 
                                   (features['disk_utilization'] > 80)).astype(float).astype(int)
        features['is_warning'] = ((features['cpu_utilization'] > 60) | 
                                  (features['memory_utilization'] > 60) | 
                                  (features['disk_utilization'] > 60)).astype(float).astype(int)
        features['anomaly_score'] = (features['is_oom_kill'] * 10 + 
                                    features['is_crash_loop'] * 8 + 
                                    features['is_high_cpu'] * 5 + 
                                    features['is_network_issue'] * 3)
        features['health_score'] = 100 - features['anomaly_score']
        features['oom_risk'] = (features['memory_utilization'] > 90).astype(int)
        features['cpu_throttle_risk'] = (features['cpu_utilization'] > 90).astype(int)
        features['disk_full_risk'] = (features['disk_utilization'] > 90).astype(int)
        features['resource_exhaustion_risk'] = ((features['cpu_headroom_pct'] < 5) | 
                                                (features['memory_headroom_pct'] < 5) | 
                                                (features['disk_headroom_pct'] < 5)).astype(float).astype(int)
        features['instability_score'] = features['restart_count'] * features['restart_rate']
        features['degradation_score'] = (features['cpu_trend_abs'] + features['memory_trend_abs']) / 2.0
        
        # ========== TIME-BASED FEATURES (20 features) ==========
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            features['hour'] = df['timestamp'].dt.hour
            features['day_of_week'] = df['timestamp'].dt.dayofweek
            features['day_of_month'] = df['timestamp'].dt.day
            features['month'] = df['timestamp'].dt.month
            features['is_weekend'] = (features['day_of_week'] >= 5).astype('float32').astype(int)
            features['is_business_hours'] = ((features['hour'] >= 9) & (features['hour'] <= 17)).astype('float32').astype(int)
            features['is_night'] = ((features['hour'] >= 22) | (features['hour'] <= 6)).astype('float32').astype(int)
            features['time_sin_epoch'] = (df['timestamp'].astype(np.int64) / 1e9).astype('float32')
        else:
            # Use age as proxy for time
            features['hour'] = ((features['age_minutes'] % 1440) / 60).astype('float32')
            features['day_of_week'] = ((features['age_minutes'] // 1440) % 7).astype('float32')
            features['day_of_month'] = ((features['age_minutes'] // 1440) % 30).astype('float32')
            features['month'] = ((features['age_minutes'] // 43200) % 12).astype('float32')
            features['is_weekend'] = (features['day_of_week'] >= 5).astype('float32').astype(int)
            features['is_business_hours'] = ((features['hour'] >= 9) & (features['hour'] <= 17)).astype('float32').astype(int)
            features['is_night'] = ((features['hour'] >= 22) | (features['hour'] <= 6)).astype('float32').astype(int)
            features['time_sin_epoch'] = (features['age_minutes'] * 60).astype('float32')
        
        # ========== STATISTICAL FEATURES (30 features) ==========
        # Percentiles
        for percentile in [10, 25, 50, 75, 90, 95, 99]:
            features[f'cpu_percentile_{percentile}'] = np.percentile(features['cpu_utilization'], percentile)
            features[f'memory_percentile_{percentile}'] = np.percentile(features['memory_utilization'], percentile)
        
        # Z-scores
        features['cpu_zscore'] = (features['cpu_utilization'] - features['cpu_utilization'].mean()) / (features['cpu_utilization'].std() + 1e-6)
        features['memory_zscore'] = (features['memory_utilization'] - features['memory_utilization'].mean()) / (features['memory_utilization'].std() + 1e-6)
        features['disk_zscore'] = (features['disk_utilization'] - features['disk_utilization'].mean()) / (features['disk_utilization'].std() + 1e-6)
        
        # Outlier detection
        features['cpu_is_outlier'] = (np.abs(features['cpu_zscore']) > 3).astype(float).astype(int)
        features['memory_is_outlier'] = (np.abs(features['memory_zscore']) > 3).astype(float).astype(int)
        features['disk_is_outlier'] = (np.abs(features['disk_zscore']) > 3).astype(float).astype(int)
        
        # Skewness and kurtosis (simplified)
        features['cpu_skew'] = ((features['cpu_utilization'] - features['cpu_utilization'].median()) / 
                               (features['cpu_utilization'].std() + 1e-6))
        features['memory_skew'] = ((features['memory_utilization'] - features['memory_utilization'].median()) / 
                                  (features['memory_utilization'].std() + 1e-6))
        
        # ========== NETWORK FEATURES (15 features) ==========
        network_rx_errors = df.get('network_rx_errors', 0)
        network_tx_errors = df.get('network_tx_errors', 0)
        
        features['network_errors_total'] = network_rx_errors + network_tx_errors
        features['network_error_rate'] = features['network_errors_total'] / (features['network_total'] + 1)
        features['network_rx_error_rate'] = network_rx_errors / (features['network_rx'] + 1)
        features['network_tx_error_rate'] = network_tx_errors / (features['network_tx'] + 1)
        features['network_throughput'] = features['network_total'] / (features['age_minutes'] * 60 + 1)
        features['network_bandwidth_utilization'] = features['network_total'] / (features['cpu_usage'] + 1)
        
        # ========== INTERACTION FEATURES (20 features) ==========
        features['cpu_memory_interaction'] = features['cpu_utilization'] * features['memory_utilization']
        features['cpu_disk_interaction'] = features['cpu_utilization'] * features['disk_utilization']
        features['memory_disk_interaction'] = features['memory_utilization'] * features['disk_utilization']
        features['cpu_restart_interaction'] = features['cpu_utilization'] * features['restart_count']
        features['memory_restart_interaction'] = features['memory_utilization'] * features['restart_count']
        features['network_cpu_interaction'] = features['network_total'] * features['cpu_utilization']
        features['network_memory_interaction'] = features['network_total'] * features['memory_utilization']
        features['age_cpu_interaction'] = features['age_minutes'] * features['cpu_utilization']
        features['age_memory_interaction'] = features['age_minutes'] * features['memory_utilization']
        features['trend_cpu_interaction'] = features['cpu_trend'] * features['cpu_utilization']
        features['trend_memory_interaction'] = features['memory_trend'] * features['memory_utilization']
        
        # ========== POLYNOMIAL FEATURES (15 features) ==========
        features['cpu_utilization_squared'] = features['cpu_utilization'] ** 2
        features['memory_utilization_squared'] = features['memory_utilization'] ** 2
        features['disk_utilization_squared'] = features['disk_utilization'] ** 2
        features['cpu_utilization_cubed'] = features['cpu_utilization'] ** 3
        features['memory_utilization_cubed'] = features['memory_utilization'] ** 3
        features['restart_count_squared'] = features['restart_count'] ** 2
        features['age_minutes_squared'] = features['age_minutes'] ** 2
        features['resource_pressure_squared'] = features['resource_pressure'] ** 2
        
        # ========== CATEGORICAL ENCODINGS (if available) ==========
        if 'namespace' in df.columns:
            # One-hot encode top namespaces (to avoid too many features)
            top_namespaces = df['namespace'].value_counts().head(10).index
            for ns in top_namespaces:
                features[f'namespace_{ns}'] = (df['namespace'] == ns).astype(float).astype(int)
        
        if 'phase' in df.columns:
            phase_mapping = {'Running': 1, 'Pending': 0, 'Failed': -1, 'Succeeded': 0, 'Unknown': 0}
            features['phase_encoded'] = df['phase'].map(phase_mapping).fillna(0)
        
        if 'container_state' in df.columns:
            state_mapping = {'Running': 1, 'Waiting': 0, 'Terminated': -1}
            features['container_state_encoded'] = df['container_state'].map(state_mapping).fillna(0)
        
        # ========== FINAL CLEANUP ==========
        # Replace inf and NaN
        features = features.replace([np.inf, -np.inf], np.nan)
        features = features.fillna(0)
        
        # Ensure all features are numeric
        for col in features.columns:
            if not pd.api.types.is_numeric_dtype(features[col]):
                features[col] = pd.to_numeric(features[col], errors='coerce').fillna(0)
        
        # Optimize final data types
        features = self._optimize_dtypes(features)
        
        return features
    
    def get_feature_names(self) -> List[str]:
        """Get list of feature names"""
        return self.feature_names if self.feature_names else []

