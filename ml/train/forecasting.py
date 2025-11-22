"""
Time-Series Forecasting for Predictive Anomaly Detection
CPU-optimized forecasting models for Mac M4
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    ARIMA_AVAILABLE = True
except ImportError:
    ARIMA_AVAILABLE = False
    print("⚠️  statsmodels not available, ARIMA forecasting disabled")

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    print("⚠️  Prophet not available, using ARIMA/XGBoost for forecasting")


class TimeSeriesForecaster:
    """
    CPU-optimized time-series forecasting for anomaly prediction
    Predicts metrics 5-15 minutes ahead
    """
    
    def __init__(self):
        self.models = {}
        self.metric_names = ['cpu_utilization', 'memory_utilization', 'error_rate', 'latency_ms']
        
    def train_forecasters(self, historical_data: pd.DataFrame, 
                         metric_columns: Optional[List[str]] = None) -> Dict:
        """
        Train forecasting models for each metric
        
        Args:
            historical_data: DataFrame with timestamp and metric columns
            metric_columns: List of metric column names to forecast
            
        Returns:
            Dictionary of trained models
        """
        print("🔮 Training time-series forecasting models...")
        
        if metric_columns is None:
            metric_columns = self.metric_names
        
        # Ensure timestamp column exists
        if 'timestamp' not in historical_data.columns:
            if 'time' in historical_data.columns:
                historical_data['timestamp'] = pd.to_datetime(historical_data['time'])
            else:
                historical_data['timestamp'] = pd.date_range(
                    start=datetime.now() - timedelta(days=len(historical_data)),
                    periods=len(historical_data),
                    freq='1min'
                )
        
        historical_data = historical_data.sort_values('timestamp')
        
        for metric in metric_columns:
            if metric not in historical_data.columns:
                continue
            
            print(f"   Training forecaster for {metric}...")
            
            # Prepare time series
            ts_data = historical_data[['timestamp', metric]].copy()
            ts_data = ts_data.dropna()
            
            if len(ts_data) < 30:  # Need at least 30 data points
                print(f"      ⚠️  Insufficient data for {metric}, skipping...")
                continue
            
            # Train multiple models and select best
            models = {}
            
            # 1. Prophet (if available)
            if PROPHET_AVAILABLE:
                try:
                    prophet_model = self._train_prophet(ts_data, metric)
                    models['prophet'] = prophet_model
                    print(f"      ✅ Prophet model trained")
                except Exception as e:
                    print(f"      ⚠️  Prophet training failed: {e}")
            
            # 2. ARIMA
            if ARIMA_AVAILABLE:
                try:
                    arima_model = self._train_arima(ts_data[metric].values)
                    models['arima'] = arima_model
                    print(f"      ✅ ARIMA model trained")
                except Exception as e:
                    print(f"      ⚠️  ARIMA training failed: {e}")
            
            # 3. Exponential Smoothing
            if ARIMA_AVAILABLE:
                try:
                    es_model = self._train_exponential_smoothing(ts_data[metric].values)
                    models['exponential_smoothing'] = es_model
                    print(f"      ✅ Exponential Smoothing model trained")
                except Exception as e:
                    print(f"      ⚠️  Exponential Smoothing training failed: {e}")
            
            # 4. Simple Moving Average (baseline)
            sma_model = self._train_simple_moving_average(ts_data[metric].values)
            models['moving_average'] = sma_model
            
            self.models[metric] = models
        
        print(f"✅ Trained forecasters for {len(self.models)} metrics")
        return self.models
    
    def _train_prophet(self, ts_data: pd.DataFrame, metric: str) -> Prophet:
        """Train Prophet model"""
        df_prophet = pd.DataFrame({
            'ds': ts_data['timestamp'],
            'y': ts_data[metric]
        })
        
        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=True,
            daily_seasonality=True,
            seasonality_mode='multiplicative'
        )
        model.fit(df_prophet)
        return model
    
    def _train_arima(self, values: np.ndarray) -> ARIMA:
        """Train ARIMA model"""
        # Auto-select order (simplified - use (5,1,2) as default)
        model = ARIMA(values, order=(5, 1, 2))
        fitted = model.fit()
        return fitted
    
    def _train_exponential_smoothing(self, values: np.ndarray) -> ExponentialSmoothing:
        """Train Exponential Smoothing model"""
        model = ExponentialSmoothing(values, trend='add', seasonal=None)
        fitted = model.fit()
        return fitted
    
    def _train_simple_moving_average(self, values: np.ndarray, window: int = 10) -> Dict:
        """Train simple moving average (baseline)"""
        return {'window': window, 'last_values': values[-window:].tolist()}
    
    def forecast(self, metric: str, hours_ahead: float = 1.0, 
                current_value: Optional[float] = None) -> Dict:
        """
        Forecast metric value hours ahead
        
        Args:
            metric: Metric name to forecast
            hours_ahead: Hours to forecast ahead (default 1 hour = 60 minutes)
            current_value: Current metric value (optional)
            
        Returns:
            Dictionary with forecasted value, confidence intervals, and model used
        """
        if metric not in self.models:
            return {
                'forecasted_value': current_value if current_value else 0.0,
                'confidence_lower': current_value if current_value else 0.0,
                'confidence_upper': current_value if current_value else 0.0,
                'model_used': 'none',
                'confidence': 0.0
            }
        
        models = self.models[metric]
        forecasts = []
        
        # Try Prophet first (most accurate)
        if 'prophet' in models:
            try:
                future = models['prophet'].make_future_dataframe(periods=int(hours_ahead * 60), freq='min')
                forecast = models['prophet'].predict(future)
                forecasted = forecast['yhat'].iloc[-1]
                lower = forecast['yhat_lower'].iloc[-1]
                upper = forecast['yhat_upper'].iloc[-1]
                forecasts.append({
                    'value': forecasted,
                    'lower': lower,
                    'upper': upper,
                    'model': 'prophet',
                    'confidence': 0.9
                })
            except Exception as e:
                print(f"   ⚠️  Prophet forecast failed: {e}")
        
        # Try ARIMA
        if 'arima' in models:
            try:
                forecast_result = models['arima'].forecast(steps=int(hours_ahead * 60))
                forecasted = forecast_result.iloc[-1] if hasattr(forecast_result, 'iloc') else forecast_result[-1]
                # Simple confidence interval (ARIMA doesn't always provide it)
                std = np.std(models['arima'].resid) if hasattr(models['arima'], 'resid') else forecasted * 0.1
                forecasts.append({
                    'value': forecasted,
                    'lower': forecasted - 1.96 * std,
                    'upper': forecasted + 1.96 * std,
                    'model': 'arima',
                    'confidence': 0.8
                })
            except Exception as e:
                print(f"   ⚠️  ARIMA forecast failed: {e}")
        
        # Try Exponential Smoothing
        if 'exponential_smoothing' in models:
            try:
                forecast_result = models['exponential_smoothing'].forecast(steps=int(hours_ahead * 60))
                forecasted = forecast_result.iloc[-1] if hasattr(forecast_result, 'iloc') else forecast_result[-1]
                forecasts.append({
                    'value': forecasted,
                    'lower': forecasted * 0.9,
                    'upper': forecasted * 1.1,
                    'model': 'exponential_smoothing',
                    'confidence': 0.75
                })
            except Exception as e:
                print(f"   ⚠️  Exponential Smoothing forecast failed: {e}")
        
        # Fallback to Moving Average
        if 'moving_average' in models:
            ma_model = models['moving_average']
            last_values = np.array(ma_model['last_values'])
            forecasted = np.mean(last_values)
            std = np.std(last_values)
            forecasts.append({
                'value': forecasted,
                'lower': forecasted - std,
                'upper': forecasted + std,
                'model': 'moving_average',
                'confidence': 0.6
            })
        
        if not forecasts:
            return {
                'forecasted_value': current_value if current_value else 0.0,
                'confidence_lower': current_value if current_value else 0.0,
                'confidence_upper': current_value if current_value else 0.0,
                'model_used': 'none',
                'confidence': 0.0
            }
        
        # Weighted average of forecasts (weight by confidence)
        total_weight = sum(f['confidence'] for f in forecasts)
        if total_weight > 0:
            forecasted_value = sum(f['value'] * f['confidence'] for f in forecasts) / total_weight
            confidence_lower = sum(f['lower'] * f['confidence'] for f in forecasts) / total_weight
            confidence_upper = sum(f['upper'] * f['confidence'] for f in forecasts) / total_weight
            avg_confidence = total_weight / len(forecasts)
            best_model = max(forecasts, key=lambda x: x['confidence'])['model']
        else:
            forecasted_value = forecasts[0]['value']
            confidence_lower = forecasts[0]['lower']
            confidence_upper = forecasts[0]['upper']
            avg_confidence = forecasts[0]['confidence']
            best_model = forecasts[0]['model']
        
        return {
            'forecasted_value': float(forecasted_value),
            'confidence_lower': float(confidence_lower),
            'confidence_upper': float(confidence_upper),
            'model_used': best_model,
            'confidence': float(avg_confidence),
            'all_forecasts': forecasts
        }


class EarlyWarningSystem:
    """
    Early Warning System for Predictive Anomaly Detection
    Detects anomalies 5-15 minutes before they occur
    """
    
    def __init__(self, forecaster: TimeSeriesForecaster):
        self.forecaster = forecaster
        self.thresholds = {
            'cpu_utilization': 80.0,
            'memory_utilization': 85.0,
            'error_rate': 10.0,
            'latency_ms': 1000.0
        }
        self.precursor_patterns = self._load_precursor_patterns()
    
    def _load_precursor_patterns(self) -> Dict:
        """Load precursor patterns that indicate future anomalies"""
        return {
            'memory_leak_precursor': {
                'pattern': 'gradual_memory_increase',
                'threshold': 5.0,  # 5% increase per hour
                'time_window': 60,  # 60 minutes
                'leads_to': 'oom_kill',
                'severity': 'critical'
            },
            'cpu_spike_precursor': {
                'pattern': 'rapid_cpu_increase',
                'threshold': 20.0,  # 20% increase in 5 minutes
                'time_window': 5,
                'leads_to': 'cpu_throttling',
                'severity': 'high'
            },
            'error_rate_increase': {
                'pattern': 'increasing_error_rate',
                'threshold': 50.0,  # 50% increase
                'time_window': 15,
                'leads_to': 'high_error_rate',
                'severity': 'medium'
            },
            'latency_degradation': {
                'pattern': 'increasing_latency',
                'threshold': 30.0,  # 30% increase
                'time_window': 10,
                'leads_to': 'slow_response',
                'severity': 'medium'
            }
        }
    
    def check_early_warnings(self, current_metrics: Dict[str, float],
                            historical_data: Optional[pd.DataFrame] = None) -> List[Dict]:
        """
        Check for early warning signs of future anomalies
        
        Args:
            current_metrics: Dictionary of current metric values
            historical_data: Historical data for trend analysis
            
        Returns:
            List of warning dictionaries
        """
        warnings = []
        
        # Forecast each metric
        for metric_name, current_value in current_metrics.items():
            if metric_name not in self.forecaster.models:
                continue
            
            # Forecast 15 minutes ahead
            forecast = self.forecaster.forecast(metric_name, hours_ahead=0.25, current_value=current_value)
            
            # Check if forecast exceeds threshold
            threshold = self.thresholds.get(metric_name, 100.0)
            
            if forecast['forecasted_value'] > threshold:
                # Calculate time to anomaly
                if current_value < threshold:
                    # Estimate time to reach threshold
                    rate = (forecast['forecasted_value'] - current_value) / 15.0  # per minute
                    if rate > 0:
                        time_to_anomaly = (threshold - current_value) / rate
                    else:
                        time_to_anomaly = 15.0
                else:
                    time_to_anomaly = 0.0
                
                warnings.append({
                    'metric': metric_name,
                    'anomaly_type': self._map_metric_to_anomaly(metric_name),
                    'current_value': current_value,
                    'forecasted_value': forecast['forecasted_value'],
                    'threshold': threshold,
                    'time_to_anomaly_minutes': max(0.0, time_to_anomaly),
                    'confidence': forecast['confidence'],
                    'severity': self._get_severity(metric_name, forecast['forecasted_value']),
                    'recommended_action': self._get_recommendation(metric_name, forecast['forecasted_value']),
                    'model_used': forecast['model_used']
                })
            
            # Check for precursor patterns
            if historical_data is not None and metric_name in historical_data.columns:
                precursor_warnings = self._detect_precursor_patterns(
                    metric_name, current_value, historical_data
                )
                warnings.extend(precursor_warnings)
        
        return warnings
    
    def _map_metric_to_anomaly(self, metric: str) -> str:
        """Map metric name to anomaly type"""
        mapping = {
            'cpu_utilization': 'cpu_spike',
            'memory_utilization': 'memory_leak',
            'error_rate': 'high_error_rate',
            'latency_ms': 'slow_response'
        }
        return mapping.get(metric, 'unknown_anomaly')
    
    def _get_severity(self, metric: str, forecasted_value: float) -> str:
        """Determine severity based on forecasted value"""
        threshold = self.thresholds.get(metric, 100.0)
        excess = (forecasted_value - threshold) / threshold * 100
        
        if excess > 50:
            return 'critical'
        elif excess > 25:
            return 'high'
        elif excess > 10:
            return 'medium'
        else:
            return 'low'
    
    def _get_recommendation(self, metric: str, forecasted_value: float) -> str:
        """Get recommended action based on forecast"""
        if metric == 'cpu_utilization':
            return "Scale horizontally or increase CPU limits"
        elif metric == 'memory_utilization':
            return "Increase memory limits or investigate memory leak"
        elif metric == 'error_rate':
            return "Check application logs and restart pod if needed"
        elif metric == 'latency_ms':
            return "Investigate network issues or scale up resources"
        else:
            return "Monitor closely and prepare for remediation"
    
    def _detect_precursor_patterns(self, metric: str, current_value: float,
                                   historical_data: pd.DataFrame) -> List[Dict]:
        """Detect precursor patterns that indicate future anomalies"""
        warnings = []
        
        if len(historical_data) < 30:
            return warnings
        
        # Get recent values
        recent_values = historical_data[metric].tail(30).values
        
        # Check each precursor pattern
        for pattern_name, pattern_def in self.precursor_patterns.items():
            if pattern_def['leads_to'] == self._map_metric_to_anomaly(metric):
                # Calculate trend
                window = min(pattern_def['time_window'], len(recent_values))
                if window < 2:
                    continue
                
                recent_window = recent_values[-window:]
                trend = (recent_window[-1] - recent_window[0]) / window
                percent_change = (trend / (recent_window[0] + 1e-6)) * 100
                
                if abs(percent_change) > pattern_def['threshold']:
                    # Estimate time to anomaly
                    if trend > 0 and current_value < self.thresholds.get(metric, 100.0):
                        time_to_anomaly = (self.thresholds.get(metric, 100.0) - current_value) / (trend + 1e-6)
                    else:
                        time_to_anomaly = pattern_def['time_window']
                    
                    warnings.append({
                        'metric': metric,
                        'pattern': pattern_name,
                        'anomaly_type': pattern_def['leads_to'],
                        'current_value': current_value,
                        'trend': trend,
                        'percent_change': percent_change,
                        'time_to_anomaly_minutes': max(0.0, time_to_anomaly),
                        'confidence': 0.8,
                        'severity': pattern_def['severity'],
                        'recommended_action': self._get_recommendation(metric, current_value + trend * time_to_anomaly)
                    })
        
        return warnings


def main():
    """Test forecasting and early warning system"""
    print("🔮 Testing Time-Series Forecasting and Early Warning System...")
    
    # Generate sample data
    dates = pd.date_range(start=datetime.now() - timedelta(days=7), periods=10080, freq='1min')
    data = pd.DataFrame({
        'timestamp': dates,
        'cpu_utilization': 50 + 20 * np.sin(np.arange(10080) * 2 * np.pi / 1440) + np.random.normal(0, 5, 10080),
        'memory_utilization': 60 + 15 * np.sin(np.arange(10080) * 2 * np.pi / 1440) + np.random.normal(0, 3, 10080),
        'error_rate': np.random.poisson(2, 10080),
        'latency_ms': 100 + 50 * np.sin(np.arange(10080) * 2 * np.pi / 1440) + np.random.normal(0, 10, 10080)
    })
    
    # Train forecasters
    forecaster = TimeSeriesForecaster()
    forecaster.train_forecasters(data)
    
    # Test forecasting
    print("\n📊 Testing forecasts...")
    current_metrics = {
        'cpu_utilization': 75.0,
        'memory_utilization': 80.0,
        'error_rate': 5.0,
        'latency_ms': 200.0
    }
    
    for metric, value in current_metrics.items():
        forecast = forecaster.forecast(metric, hours_ahead=0.25, current_value=value)
        print(f"   {metric}: {value:.2f} -> {forecast['forecasted_value']:.2f} "
              f"(confidence: {forecast['confidence']:.2f}, model: {forecast['model_used']})")
    
    # Test early warning system
    print("\n⚠️  Testing Early Warning System...")
    ews = EarlyWarningSystem(forecaster)
    warnings = ews.check_early_warnings(current_metrics, data)
    
    for warning in warnings:
        print(f"   ⚠️  {warning['metric']}: {warning['anomaly_type']} "
              f"in {warning['time_to_anomaly_minutes']:.1f} minutes "
              f"(severity: {warning['severity']})")
    
    print("\n✅ Testing complete!")


if __name__ == "__main__":
    main()

