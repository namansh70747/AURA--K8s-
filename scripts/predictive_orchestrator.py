#!/usr/bin/env python3
"""
Predictive Orchestrator for AURA K8s
Processes metrics → Forecasts → Early Warnings → Preventive Actions
Runs continuously to provide proactive anomaly detection
"""

import os
import asyncio
import time
import psycopg2
import psycopg2.extensions
import psycopg2.pool
import httpx
import json
import logging
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import uuid

# Import config helper for service discovery
import sys
from pathlib import Path
scripts_dir = Path(__file__).parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
from config_helper import get_database_url, get_service_url

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
# Configuration with validation - use config helper for environment-aware URLs
DATABASE_URL = get_database_url()
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is required")
    raise ValueError("DATABASE_URL environment variable is required")

ML_SERVICE_URL = get_service_url("ML_SERVICE", "8001")
if not ML_SERVICE_URL:
    logger.error("ML_SERVICE_URL environment variable is required")
    raise ValueError("ML_SERVICE_URL environment variable is required")

FORECAST_INTERVAL = int(os.getenv("FORECAST_INTERVAL", "5"))  # seconds
if FORECAST_INTERVAL < 1:
    logger.warning(f"FORECAST_INTERVAL too low ({FORECAST_INTERVAL}s), setting to minimum 1s")
    FORECAST_INTERVAL = 1

PREDICTION_HORIZON = int(os.getenv("PREDICTION_HORIZON", "900"))  # 15 minutes in seconds
if PREDICTION_HORIZON < 60:
    logger.warning(f"PREDICTION_HORIZON too low ({PREDICTION_HORIZON}s), setting to minimum 60s")
    PREDICTION_HORIZON = 60

ENABLE_PREVENTIVE_ACTIONS = os.getenv("ENABLE_PREVENTIVE_ACTIONS", "true").lower() == "true"

# Connection pool
db_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


class PredictiveOrchestrator:
    """Main orchestrator for predictive anomaly detection"""
    
    def __init__(self):
        self.ml_service_url = ML_SERVICE_URL
        self.forecast_interval = FORECAST_INTERVAL
        self.prediction_horizon = PREDICTION_HORIZON
        self.enable_preventive = ENABLE_PREVENTIVE_ACTIONS
        self.running = False
        
    async def run_predictive_loop(self):
        """Main async loop for predictive processing"""
        self.running = True
        logger.info("🚀 Starting predictive orchestrator loop")
        logger.info(f"   Forecast interval: {self.forecast_interval}s")
        logger.info(f"   Prediction horizon: {self.prediction_horizon}s")
        logger.info(f"   Preventive actions: {'✅ enabled' if self.enable_preventive else '❌ disabled'}")
        logger.info("   ⚡ Fast detection mode: Issues detected BEFORE they occur")
        
        while self.running:
            try:
                start_time = time.time()
                
                # Get recent metrics
                metrics = await self.get_recent_metrics()
                if not metrics:
                    logger.debug("No recent metrics found, waiting...")
                    await asyncio.sleep(self.forecast_interval)
                    continue
                
                logger.info(f"📊 Processing {len(metrics)} pod metrics")
                
                # Generate forecasts for all pods (fast parallel processing)
                forecast_start = time.time()
                forecasts = await self.generate_forecasts(metrics)
                forecast_time = time.time() - forecast_start
                
                # Log forecast generation results
                logger.info(f"📈 Generated {len(forecasts)} forecasts from {len(metrics)} metrics in {forecast_time:.3f}s")
                if len(forecasts) > 0:
                    sample_forecast = forecasts[0]
                    logger.debug(f"Sample forecast: pod={sample_forecast.get('pod_name')}, risk={sample_forecast.get('risk_score', 0):.1f}, cpu={sample_forecast.get('predictions', {}).get('cpu_utilization', {}).get('predicted_value', 0):.1f}%")
                
                # Detect future anomalies (very fast - threshold checks)
                detection_start = time.time()
                warnings = await self.detect_future_anomalies(forecasts)
                detection_time = time.time() - detection_start
                
                if len(warnings) > 0:
                    logger.info(f"⚠️  Detected {len(warnings)} future anomalies in {detection_time:.3f}s")
                elif len(forecasts) > 0:
                    logger.debug(f"No warnings generated from {len(forecasts)} forecasts (all below thresholds)")
                
                # Trigger preventive actions (saves to DB, remediator processes automatically)
                if self.enable_preventive and warnings:
                    action_start = time.time()
                    await self.trigger_preventive_actions(warnings)
                    action_time = time.time() - action_start
                    logger.info(f"⚠️  Generated {len(warnings)} early warnings (action time: {action_time:.3f}s)")
                
                elapsed = time.time() - start_time
                logger.info(f"✅ Predictive cycle completed in {elapsed:.2f}s (forecast: {forecast_time:.3f}s, detection: {detection_time:.3f}s)")
                
                # Sleep until next cycle
                sleep_time = max(0, self.forecast_interval - elapsed)
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Error in predictive loop: {e}", exc_info=True)
                await asyncio.sleep(self.forecast_interval)
    
    async def get_recent_metrics(self) -> List[Dict]:
        """Get recent metrics - tries circular buffer first, falls back to database"""
        # Import here to avoid circular dependencies
        import sys
        from pathlib import Path
        scripts_dir = Path(__file__).parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from config_helper import get_service_url
        collector_url = get_service_url("COLLECTOR", "9090")
        use_buffer = os.getenv("USE_CIRCULAR_BUFFER", "true").lower() == "true"
        
        # Try circular buffer first (faster)
        if use_buffer:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{collector_url}/api/v1/buffer/metrics?limit=1000")
                    if response.status_code == 200:
                        data = response.json()
                        buffer_metrics = data.get("metrics", [])
                        if buffer_metrics:
                            logger.debug(f"Using {len(buffer_metrics)} metrics from circular buffer")
                            # Convert buffer format to our format
                            metrics = []
                            for m in buffer_metrics:
                                metrics.append({
                                    'pod_name': m['pod_name'],
                                    'namespace': m['namespace'],
                                    'timestamp': datetime.fromisoformat(m['timestamp'].replace('Z', '+00:00')),
                                    'cpu_utilization': float(m.get('cpu_utilization', 0)),
                                    'memory_utilization': float(m.get('memory_utilization', 0)),
                                    'network_rx_bytes': int(m.get('network_rx_bytes', 0)),
                                    'network_tx_bytes': int(m.get('network_tx_bytes', 0)),
                                    'network_rx_errors': 0,  # Buffer may not have this
                                    'network_tx_errors': 0,  # Buffer may not have this
                                    'restarts': int(m.get('restarts', 0)),
                                    'age': 0,  # Will calculate if needed
                                })
                            return metrics
            except Exception as e:
                logger.debug(f"Circular buffer unavailable, falling back to database: {e}")
        
        # Fallback to database query
        try:
            conn = db_pool.getconn()
            try:
                cur = conn.cursor()
                
                # Get metrics from last 5 minutes
                query = """
                    SELECT DISTINCT ON (pod_name, namespace)
                        pod_name, namespace, timestamp,
                        cpu_utilization, memory_utilization,
                        network_rx_bytes, network_tx_bytes,
                        network_rx_errors, network_tx_errors,
                        restarts, age
                    FROM pod_metrics
                    WHERE timestamp > NOW() - INTERVAL '5 minutes'
                        AND namespace NOT IN ('kube-system', 'kube-public', 'kube-node-lease', 'local-path-storage')
                    ORDER BY pod_name, namespace, timestamp DESC
                """
                
                cur.execute(query)
                rows = cur.fetchall()
                
                metrics = []
                for row in rows:
                    metrics.append({
                        'pod_name': row[0],
                        'namespace': row[1],
                        'timestamp': row[2],
                        'cpu_utilization': float(row[3] or 0),
                        'memory_utilization': float(row[4] or 0),
                        'network_rx_bytes': int(row[5] or 0),
                        'network_tx_bytes': int(row[6] or 0),
                        'network_rx_errors': int(row[7] or 0),
                        'network_tx_errors': int(row[8] or 0),
                        'restarts': int(row[9] or 0),
                        'age': int(row[10] or 0),
                    })
                
                cur.close()
                return metrics
                
            finally:
                db_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to get recent metrics: {e}", exc_info=True)
            return []
    
    async def generate_forecast_for_pod(self, metric: Dict, client: httpx.AsyncClient, retry_count: int = 0) -> Optional[Dict]:
        """Generate forecast for a single pod (used for parallel processing) with retry logic"""
        max_retries = 3
        retry_delay = 1.0  # seconds
        
        try:
            # Get historical data for this pod
            historical = await self.get_historical_data(metric['pod_name'], metric['namespace'])
            
            if len(historical) < 3:
                # Not enough data for reliable forecast (lowered from 5 to 3 to allow earlier forecasts)
                logger.debug(f"Insufficient historical data for {metric['pod_name']}: {len(historical)} points (need 3+)")
                return None
            
            # Prepare enhanced forecast request with comprehensive metrics
            forecast_request = {
                'pod_name': metric['pod_name'],
                'namespace': metric['namespace'],
                'metrics': {
                    'cpu_utilization': [h['cpu_utilization'] for h in historical],
                    'memory_utilization': [h['memory_utilization'] for h in historical],
                    # Include additional metrics for better forecasting
                    'network_rx_bytes': [h.get('network_rx_bytes', 0) for h in historical],
                    'network_tx_bytes': [h.get('network_tx_bytes', 0) for h in historical],
                    'restarts': [h.get('restarts', 0) for h in historical],
                },
                'horizon_seconds': self.prediction_horizon,
                'metrics_to_forecast': ['cpu_utilization', 'memory_utilization'],
                # Request confidence intervals for risk assessment
                'include_confidence_intervals': True,
                'include_anomaly_probabilities': True,
            }
            
            # Call forecasting service asynchronously with retry
            try:
                response = await client.post(
                    f"{self.ml_service_url}/v1/forecast",
                    json=forecast_request,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    forecast = response.json()
                    forecast['pod_name'] = metric['pod_name']
                    forecast['namespace'] = metric['namespace']
                    return forecast
                elif response.status_code >= 500 and retry_count < max_retries:
                    # Server error - retry
                    logger.warning(f"Forecast service error {response.status_code} for {metric['pod_name']}, retrying ({retry_count + 1}/{max_retries})")
                    await asyncio.sleep(retry_delay * (retry_count + 1))
                    return await self.generate_forecast_for_pod(metric, client, retry_count + 1)
                else:
                    logger.warning(f"Forecast failed for {metric['pod_name']}: {response.status_code}")
                    return None
                    
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                # Network error - retry
                if retry_count < max_retries:
                    logger.warning(f"Forecast service connection error for {metric['pod_name']}, retrying ({retry_count + 1}/{max_retries}): {e}")
                    await asyncio.sleep(retry_delay * (retry_count + 1))
                    return await self.generate_forecast_for_pod(metric, client, retry_count + 1)
                else:
                    logger.error(f"Forecast service unavailable after {max_retries} retries for {metric['pod_name']}: {e}")
                    return None
                
        except Exception as e:
            logger.error(f"Error generating forecast for {metric['pod_name']}: {e}")
            return None
    
    async def generate_forecasts(self, metrics: List[Dict]) -> List[Dict]:
        """Generate forecasts for all pods in parallel"""
        forecasts = []
        
        # Use async HTTP client for parallel requests
        async with httpx.AsyncClient() as client:
            # Create tasks for all pods
            tasks = [self.generate_forecast_for_pod(metric, client) for metric in metrics]
            
            # Execute all forecasts in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Collect successful forecasts
            for result in results:
                if isinstance(result, dict) and result is not None:
                    forecasts.append(result)
                elif isinstance(result, Exception):
                    logger.error(f"Forecast task failed: {result}")
        
        return forecasts
    
    async def get_historical_data(self, pod_name: str, namespace: str, limit: int = 100) -> List[Dict]:
        """Get comprehensive historical metrics for enhanced forecasting"""
        try:
            conn = db_pool.getconn()
            try:
                cur = conn.cursor()
                
                # Enhanced query with more metrics for better forecasting
                query = """
                    SELECT timestamp, cpu_utilization, memory_utilization,
                           network_rx_bytes, network_tx_bytes, network_rx_errors, network_tx_errors,
                           restarts, has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues,
                           cpu_trend, memory_trend, restart_trend
                    FROM pod_metrics
                    WHERE pod_name = %s AND namespace = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """
                
                cur.execute(query, (pod_name, namespace, limit))
                rows = cur.fetchall()
                
                historical = []
                for row in rows:
                    historical.append({
                        'timestamp': row[0],
                        'cpu_utilization': float(row[1] or 0),
                        'memory_utilization': float(row[2] or 0),
                        'network_rx_bytes': int(row[3] or 0),
                        'network_tx_bytes': int(row[4] or 0),
                        'network_rx_errors': int(row[5] or 0),
                        'network_tx_errors': int(row[6] or 0),
                        'restarts': int(row[7] or 0),
                        'has_oom_kill': bool(row[8] or False),
                        'has_crash_loop': bool(row[9] or False),
                        'has_high_cpu': bool(row[10] or False),
                        'has_network_issues': bool(row[11] or False),
                        'cpu_trend': float(row[12] or 0),
                        'memory_trend': float(row[13] or 0),
                        'restart_trend': float(row[14] or 0),
                    })
                
                # Reverse to get chronological order (oldest first)
                historical.reverse()
                
                cur.close()
                return historical
                
            finally:
                db_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to get historical data for {pod_name}/{namespace}: {e}")
            return []
    
    async def detect_future_anomalies(self, forecasts: List[Dict]) -> List[Dict]:
        """Enhanced anomaly detection with comprehensive analysis"""
        warnings = []
        
        for forecast in forecasts:
            try:
                # Extract forecast data with comprehensive analysis
                risk_score = forecast.get('risk_score', 0.0)
                anomaly_probs = forecast.get('anomaly_probabilities', {})
                max_prob = max(anomaly_probs.values()) if anomaly_probs else 0.0
                confidence = forecast.get('confidence', 0.5)
                
                # Get predicted metrics with detailed analysis
                predictions = forecast.get('predictions', {})
                cpu_forecast = predictions.get('cpu_utilization', {}).get('predicted_value', 0) if isinstance(predictions.get('cpu_utilization'), dict) else 0
                mem_forecast = predictions.get('memory_utilization', {}).get('predicted_value', 0) if isinstance(predictions.get('memory_utilization'), dict) else 0
                
                # Get forecast confidence intervals for better risk assessment
                cpu_upper = predictions.get('cpu_utilization', {}).get('upper_bound', cpu_forecast) if isinstance(predictions.get('cpu_utilization'), dict) else cpu_forecast
                cpu_lower = predictions.get('cpu_utilization', {}).get('lower_bound', cpu_forecast) if isinstance(predictions.get('cpu_utilization'), dict) else cpu_forecast
                mem_upper = predictions.get('memory_utilization', {}).get('upper_bound', mem_forecast) if isinstance(predictions.get('memory_utilization'), dict) else mem_forecast
                mem_lower = predictions.get('memory_utilization', {}).get('lower_bound', mem_forecast) if isinstance(predictions.get('memory_utilization'), dict) else mem_forecast
                
                # Enhanced risk calculation with multiple factors
                # Factor 1: Risk score from forecast service
                base_risk = risk_score
                
                # Factor 2: Forecasted values exceeding thresholds
                cpu_risk = 0.0
                if cpu_forecast > 80:
                    cpu_risk = min((cpu_forecast - 80) * 2, 50.0)  # 0-50 points for CPU > 80%
                elif cpu_forecast > 70:
                    cpu_risk = (cpu_forecast - 70) * 1.5  # 0-15 points for CPU 70-80%
                
                mem_risk = 0.0
                if mem_forecast > 80:
                    mem_risk = min((mem_forecast - 80) * 2, 50.0)  # 0-50 points for Memory > 80%
                elif mem_forecast > 70:
                    mem_risk = (mem_forecast - 70) * 1.5  # 0-15 points for Memory 70-80%
                
                # Factor 3: Upper bound risk (worst case scenario)
                worst_case_risk = 0.0
                if cpu_upper > 90 or mem_upper > 90:
                    worst_case_risk = 30.0  # High risk if worst case exceeds 90%
                elif cpu_upper > 80 or mem_upper > 80:
                    worst_case_risk = 15.0  # Medium risk if worst case exceeds 80%
                
                # Factor 4: Anomaly probability
                prob_risk = max_prob * 30.0  # 0-30 points based on probability
                
                # Factor 5: Confidence adjustment (lower confidence = higher uncertainty risk)
                uncertainty_risk = (1.0 - confidence) * 10.0  # 0-10 points for uncertainty
                
                # Calculate enhanced risk score (weighted combination)
                enhanced_risk = (
                    base_risk * 0.4 +  # Base risk from forecast (40%)
                    max(cpu_risk, mem_risk) * 0.25 +  # Forecasted values (25%)
                    worst_case_risk * 0.15 +  # Worst case scenario (15%)
                    prob_risk * 0.15 +  # Anomaly probability (15%)
                    uncertainty_risk * 0.05  # Uncertainty penalty (5%)
                )
                enhanced_risk = min(enhanced_risk, 100.0)  # Cap at 100
                
                # Enhanced detection thresholds (multi-tier)
                # CRITICAL: Multiple conditions must be met for high confidence warnings
                critical_conditions = [
                    enhanced_risk > 80,
                    max_prob > 0.7,
                    (cpu_forecast > 85 or mem_forecast > 85),
                    (cpu_upper > 90 or mem_upper > 90),
                ]
                critical_warn = sum(critical_conditions) >= 2  # At least 2 critical conditions
                
                # HIGH: Strong indicators
                high_conditions = [
                    enhanced_risk > 60,
                    max_prob > 0.6,
                    (cpu_forecast > 75 or mem_forecast > 75),
                    (cpu_upper > 85 or mem_upper > 85),
                ]
                high_warn = sum(high_conditions) >= 2  # At least 2 high conditions
                
                # MEDIUM: Moderate indicators
                medium_conditions = [
                    enhanced_risk > 40,
                    max_prob > 0.5,
                    (cpu_forecast > 70 or mem_forecast > 70),
                ]
                medium_warn = sum(medium_conditions) >= 2  # At least 2 medium conditions
                
                # Determine if warning should be generated
                should_warn = critical_warn or high_warn or medium_warn
                
                # Enhanced severity classification
                if critical_warn:
                    severity = "Critical"
                elif high_warn:
                    severity = "High"
                elif medium_warn:
                    severity = "Medium"
                else:
                    severity = "Low"
                
                # Calculate time to anomaly with enhanced logic
                time_to_anomaly = self._calculate_time_to_anomaly(
                    cpu_forecast, mem_forecast, cpu_upper, mem_upper,
                    forecast.get('time_to_anomaly')
                )
                
                logger.debug(f"Enhanced forecast analysis for {forecast['pod_name']}: "
                           f"risk={enhanced_risk:.1f} (base={base_risk:.1f}), "
                           f"cpu={cpu_forecast:.1f}% (upper={cpu_upper:.1f}%), "
                           f"mem={mem_forecast:.1f}% (upper={mem_upper:.1f}%), "
                           f"prob={max_prob:.2f}, confidence={confidence:.2f}, "
                           f"severity={severity}, time_to_anomaly={time_to_anomaly}s")
                
                if should_warn:
                    logger.info(f"🔮 Early warning triggered for {forecast['pod_name']}: "
                              f"{severity} risk ({enhanced_risk:.1f}), "
                              f"cpu={cpu_forecast:.1f}% (worst={cpu_upper:.1f}%), "
                              f"mem={mem_forecast:.1f}% (worst={mem_upper:.1f}%), "
                              f"time_to_anomaly={time_to_anomaly}s")
                    
                    # Build comprehensive predicted metrics
                    flattened_metrics = {}
                    for metric_name, metric_data in predictions.items():
                        if isinstance(metric_data, dict):
                            flattened_metrics[metric_name] = metric_data.get('predicted_value', 0.0)
                            # Include upper/lower bounds for risk assessment
                            if 'upper_bound' in metric_data:
                                flattened_metrics[f"{metric_name}_upper"] = metric_data.get('upper_bound', 0.0)
                            if 'lower_bound' in metric_data:
                                flattened_metrics[f"{metric_name}_lower"] = metric_data.get('lower_bound', 0.0)
                        else:
                            flattened_metrics[metric_name] = float(metric_data) if metric_data is not None else 0.0
                    
                    # Add risk analysis metrics
                    flattened_metrics['enhanced_risk_score'] = enhanced_risk
                    flattened_metrics['base_risk_score'] = base_risk
                    flattened_metrics['cpu_risk'] = cpu_risk
                    flattened_metrics['memory_risk'] = mem_risk
                    flattened_metrics['worst_case_risk'] = worst_case_risk
                    flattened_metrics['anomaly_probability'] = max_prob
                    flattened_metrics['forecast_confidence'] = confidence
                    
                    warning = {
                        'pod_name': forecast['pod_name'],
                        'namespace': forecast['namespace'],
                        'warning_type': self._determine_warning_type(cpu_forecast, mem_forecast, max_prob),
                        'severity': severity,
                        'risk_score': enhanced_risk,  # Use enhanced risk score
                        'time_to_anomaly': time_to_anomaly,
                        'confidence': confidence,
                        'recommended_action': self._get_recommended_action(enhanced_risk, severity, time_to_anomaly),
                        'predicted_metrics': flattened_metrics,
                        'timestamp': datetime.now(),
                    }
                    warnings.append(warning)
                    
            except Exception as e:
                logger.error(f"Error detecting anomaly for {forecast.get('pod_name', 'unknown')}: {e}", exc_info=True)
                continue
        
        return warnings
    
    def _calculate_time_to_anomaly(self, cpu_forecast: float, mem_forecast: float,
                                   cpu_upper: float, mem_upper: float,
                                   forecast_time_to_anomaly: Optional[Any]) -> Optional[int]:
        """Calculate time to anomaly with enhanced logic"""
        # Use forecast service's time_to_anomaly if available and valid
        if forecast_time_to_anomaly is not None:
            try:
                if isinstance(forecast_time_to_anomaly, (int, float)):
                    time_seconds = int(forecast_time_to_anomaly)
                    if 60 <= time_seconds <= 3600:  # 1 minute to 1 hour
                        return time_seconds
                elif isinstance(forecast_time_to_anomaly, str):
                    # Try to parse duration string
                    import re
                    match = re.match(r'(\d+)([smh])', forecast_time_to_anomaly.lower())
                    if match:
                        value, unit = match.groups()
                        value = int(value)
                        if unit == 's':
                            time_seconds = value
                        elif unit == 'm':
                            time_seconds = value * 60
                        elif unit == 'h':
                            time_seconds = value * 3600
                        else:
                            time_seconds = value
                        if 60 <= time_seconds <= 3600:
                            return time_seconds
            except Exception as e:
                logger.debug(f"Failed to parse time_to_anomaly: {e}")
        
        # Calculate based on forecasted values and thresholds
        # Estimate time to reach 80% threshold
        threshold = 80.0
        times_to_threshold = []
        
        # CPU time to threshold (assuming linear growth)
        if cpu_forecast < threshold and cpu_upper > threshold:
            # Estimate growth rate: (upper - current) / prediction_horizon
            growth_rate = (cpu_upper - cpu_forecast) / self.prediction_horizon
            if growth_rate > 0:
                time_to_cpu = int((threshold - cpu_forecast) / growth_rate)
                if 60 <= time_to_cpu <= 3600:
                    times_to_threshold.append(time_to_cpu)
        
        # Memory time to threshold
        if mem_forecast < threshold and mem_upper > threshold:
            growth_rate = (mem_upper - mem_forecast) / self.prediction_horizon
            if growth_rate > 0:
                time_to_mem = int((threshold - mem_forecast) / growth_rate)
                if 60 <= time_to_mem <= 3600:
                    times_to_threshold.append(time_to_mem)
        
        # If already above threshold, use minimum time
        if cpu_forecast >= threshold or mem_forecast >= threshold:
            return 60  # 1 minute (immediate action needed)
        
        # Return minimum time if multiple estimates available
        if times_to_threshold:
            return min(times_to_threshold)
        
        # Default: use prediction horizon as estimate
        return min(self.prediction_horizon, 1800)  # Cap at 30 minutes
    
    def _determine_warning_type(self, cpu_forecast: float, mem_forecast: float, max_prob: float) -> str:
        """Determine warning type based on forecasted metrics"""
        if mem_forecast > 85:
            return "memory_exhaustion_predicted"
        elif cpu_forecast > 85:
            return "cpu_exhaustion_predicted"
        elif mem_forecast > 75 or cpu_forecast > 75:
            return "resource_pressure_predicted"
        elif max_prob > 0.7:
            return "anomaly_predicted"
        else:
            return "risk_detected"
    
    def _get_recommended_action(self, risk_score: float, severity: str, time_to_anomaly: Optional[int] = None) -> str:
        """Get recommended action based on risk, severity, and urgency"""
        # Urgency-based action selection (time_to_anomaly is critical)
        if time_to_anomaly is not None:
            if time_to_anomaly < 60:  # Less than 1 minute
                return "scale_up_immediately"  # Fastest action
            elif time_to_anomaly < 300:  # Less than 5 minutes
                if severity == "Critical" or risk_score > 80:
                    return "scale_up_immediately"
                else:
                    return "scale_up"
            elif time_to_anomaly < 900:  # Less than 15 minutes
                if severity == "Critical" or risk_score > 80:
                    return "scale_up"
                elif severity == "High" or risk_score > 60:
                    return "increase_resources"
                else:
                    return "increase_resources"
        
        # Fallback to severity/risk-based (if time_to_anomaly not available)
        if severity == "Critical" or risk_score > 80:
            return "scale_up_immediately"
        elif severity == "High" or risk_score > 60:
            return "scale_up"
        elif severity == "Medium" or risk_score > 40:
            return "increase_resources"
        return "monitor"
    
    async def trigger_preventive_actions(self, warnings: List[Dict]):
        """Trigger preventive actions for warnings"""
        remediator_url = get_service_url("REMEDIATOR", "9091")
        
        for warning in warnings:
            try:
                # Save warning to database first
                await self.save_warning(warning)
                
                # Log warning
                logger.info(f"⚠️  Early Warning: {warning['namespace']}/{warning['pod_name']} - "
                          f"{warning['severity']} risk ({warning['risk_score']:.1f}) - "
                          f"Action: {warning['recommended_action']}")
                
                # Trigger preventive action via remediator API (immediate action)
                remediator_url = get_service_url("REMEDIATOR", "9091")
                use_immediate_trigger = os.getenv("USE_IMMEDIATE_PREVENTIVE_TRIGGER", "true").lower() == "true"
                
                if use_immediate_trigger:
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.post(f"{remediator_url}/api/v1/trigger-preventive")
                            if response.status_code == 200:
                                logger.info(f"✅ Preventive action triggered immediately via API")
                            else:
                                logger.warning(f"Remediator API returned {response.status_code}, will process from DB")
                    except Exception as api_err:
                        logger.debug(f"Remediator API unavailable (will process from DB): {api_err}")
                else:
                    logger.info(f"✅ Warning saved - remediator will process automatically (polling mode)")
                
            except Exception as e:
                logger.error(f"Error triggering preventive action: {e}")
                continue
    
    async def save_warning(self, warning: Dict):
        """Save early warning to database with duplicate prevention"""
        try:
            conn = db_pool.getconn()
            try:
                cur = conn.cursor()
                
                # Enhanced duplicate prevention: Check for any active warning of same type
                # Also check for similar warning types to avoid duplicates
                warning_type_pattern = warning['warning_type'].replace('_predicted', '%').replace('predicted', '%')
                
                check_query = """
                    SELECT id, severity, risk_score, time_to_anomaly_seconds
                    FROM early_warnings
                    WHERE pod_name = %s AND namespace = %s
                        AND (
                            warning_type = %s
                            OR warning_type LIKE %s
                        )
                        AND (expires_at IS NULL OR expires_at > NOW())
                        AND (acknowledged IS NULL OR acknowledged = FALSE)
                    ORDER BY created_at DESC
                    LIMIT 1
                """
                
                cur.execute(check_query, (
                    warning['pod_name'],
                    warning['namespace'],
                    warning['warning_type'],
                    warning_type_pattern
                ))
                existing = cur.fetchone()
                
                if existing:
                    # Check if update is needed (only update if new warning is more severe or risk is higher)
                    existing_id, existing_severity, existing_risk, existing_time = existing
                    new_risk = warning['risk_score']
                    new_severity = warning['severity']
                    
                    # Severity order for comparison
                    severity_order = {'Critical': 4, 'High': 3, 'Medium': 2, 'Low': 1}
                    existing_severity_order = severity_order.get(existing_severity, 0)
                    new_severity_order = severity_order.get(new_severity, 0)
                    
                    # Update if new warning is more severe OR risk is significantly higher (>10 points)
                    should_update = (
                        new_severity_order > existing_severity_order or
                        (new_risk > existing_risk + 10) or
                        (new_severity_order == existing_severity_order and new_risk > existing_risk + 5)
                    )
                    
                    if not should_update:
                        logger.debug(f"Skipping update for {warning['pod_name']}: existing warning is similar or more severe")
                        cur.close()
                        conn.commit()
                        return
                    # Update existing warning with enhanced logic
                    warning_id = existing[0]
                    predicted_metrics_json = json.dumps(warning.get('predicted_metrics', {}))
                    time_to_anomaly_seconds = warning.get('time_to_anomaly')
                    
                    # Calculate expiration time based on time_to_anomaly (auto-expire after anomaly time)
                    expires_at = None
                    if time_to_anomaly_seconds:
                        # Expire 5 minutes after predicted anomaly time (allows for action completion)
                        expires_at = warning['timestamp'] + timedelta(seconds=time_to_anomaly_seconds + 300)
                    
                    update_query = """
                        UPDATE early_warnings SET
                            severity = %s,
                            risk_score = %s,
                            time_to_anomaly_seconds = %s,
                            confidence = %s,
                            recommended_action = %s,
                            predicted_metrics = %s,
                            created_at = %s,
                            expires_at = %s
                        WHERE id = %s
                    """
                    cur.execute(update_query, (
                        warning['severity'],
                        warning['risk_score'],
                        time_to_anomaly_seconds,
                        warning['confidence'],
                        warning['recommended_action'],
                        predicted_metrics_json,
                        warning['timestamp'],
                        expires_at,
                        warning_id,
                    ))
                    logger.debug(f"Updated existing warning for {warning['pod_name']} (expires at: {expires_at})")
                else:
                    # Create new warning with auto-expiration
                    warning_id = str(uuid.uuid4())
                    predicted_metrics_json = json.dumps(warning.get('predicted_metrics', {}))
                    time_to_anomaly_seconds = warning.get('time_to_anomaly')
                    
                    # Calculate expiration time based on time_to_anomaly (auto-expire after anomaly time)
                    expires_at = None
                    if time_to_anomaly_seconds:
                        # Expire 5 minutes after predicted anomaly time (allows for action completion)
                        expires_at = warning['timestamp'] + timedelta(seconds=time_to_anomaly_seconds + 300)
                    else:
                        # Default expiration: 1 hour if time_to_anomaly not available
                        expires_at = warning['timestamp'] + timedelta(hours=1)
                    
                    insert_query = """
                        INSERT INTO early_warnings (
                            id, pod_name, namespace, warning_type, severity, risk_score,
                            time_to_anomaly_seconds, confidence, recommended_action,
                            predicted_metrics, created_at, expires_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    cur.execute(insert_query, (
                        warning_id,
                        warning['pod_name'],
                        warning['namespace'],
                        warning['warning_type'],
                        warning['severity'],
                        warning['risk_score'],
                        time_to_anomaly_seconds,
                        warning['confidence'],
                        warning['recommended_action'],
                        predicted_metrics_json,
                        warning['timestamp'],
                        expires_at,
                    ))
                    logger.debug(f"Created new warning for {warning['pod_name']} (expires at: {expires_at})")
                
                conn.commit()
                cur.close()
                
            finally:
                db_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to save warning: {e}")
            if conn:
                conn.rollback()


def init_db_pool():
    """Initialize database connection pool"""
    global db_pool
    try:
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL
        )
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        sys.exit(1)


def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("Shutting down predictive orchestrator...")
    global db_pool
    if db_pool:
        db_pool.closeall()
    sys.exit(0)


async def main():
    """Main entry point"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initialize database pool
    init_db_pool()
    
    # Create orchestrator
    orchestrator = PredictiveOrchestrator()
    
    # Run predictive loop
    try:
        await orchestrator.run_predictive_loop()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("Predictive orchestrator stopped")


if __name__ == '__main__':
    asyncio.run(main())

