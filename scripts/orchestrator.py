#!/usr/bin/env python3
"""
AURA K8s Orchestrator
Processes metrics → ML predictions → issues → remediation
Correctly matches feature engineering with training script
Includes circuit breaker and batch processing
"""

import os
import time
import psycopg2
import psycopg2.extensions
import psycopg2.pool
import requests
import json
import logging
import threading
import signal
import sys
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, TYPE_CHECKING
import uuid
from enum import Enum

if TYPE_CHECKING:
    from psycopg2.extensions import connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration with validation and fail-fast in production
environment = os.getenv("ENVIRONMENT", "development")

# Validate DATABASE_URL in all environments (not just production)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    if environment == "production":
        logger.error("DATABASE_URL environment variable is required in production")
        raise ValueError("DATABASE_URL environment variable is required in production")
    # Use default only in development, but warn about it - standardize connection string format
    DATABASE_URL = "postgresql://aura:aura_password@localhost:5432/aura_metrics?sslmode=disable"
    logger.warning("Using default DATABASE_URL (development only). Set DATABASE_URL environment variable for production.")

# Validate DATABASE_URL format
if DATABASE_URL:
    if not DATABASE_URL.startswith(("postgresql://", "postgres://")):
        error_msg = f"Invalid DATABASE_URL format: must start with 'postgresql://' or 'postgres://'"
        if environment == "production":
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.warning(f"{error_msg}. Continuing with provided URL.")

ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8001")

# Validate PREDICTION_INTERVAL with fail-fast in production
prediction_interval_raw = os.getenv("PREDICTION_INTERVAL", "30")
try:
    PREDICTION_INTERVAL = int(prediction_interval_raw)
    if PREDICTION_INTERVAL < 1 or PREDICTION_INTERVAL > 3600:
        error_msg = f"PREDICTION_INTERVAL {PREDICTION_INTERVAL} out of range (1-3600)"
        if environment == "production":
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.warning(f"{error_msg}, using default 30")
        PREDICTION_INTERVAL = 30
except ValueError as e:
    error_msg = f"Invalid PREDICTION_INTERVAL '{prediction_interval_raw}'"
    if environment == "production" and "out of range" not in str(e):
        logger.error(error_msg)
        raise ValueError(error_msg) from e
    logger.warning(f"{error_msg}, using default 30")
    PREDICTION_INTERVAL = 30

# Validate CONFIDENCE_THRESHOLD
confidence_threshold_raw = os.getenv("CONFIDENCE_THRESHOLD", "0.50")
try:
    CONFIDENCE_THRESHOLD = float(confidence_threshold_raw)
    if CONFIDENCE_THRESHOLD < 0.0 or CONFIDENCE_THRESHOLD > 1.0:
        logger.warning(f"CONFIDENCE_THRESHOLD {CONFIDENCE_THRESHOLD} out of range (0.0-1.0), clamping to valid range")
        CONFIDENCE_THRESHOLD = max(0.0, min(1.0, CONFIDENCE_THRESHOLD))
except ValueError:
    logger.warning(f"Invalid CONFIDENCE_THRESHOLD '{confidence_threshold_raw}', using default 0.50")
    CONFIDENCE_THRESHOLD = 0.50

# Validate BATCH_SIZE with memory-aware limits
batch_size_raw = os.getenv("BATCH_SIZE", "20")
try:
    BATCH_SIZE = int(batch_size_raw)
    # Memory-aware batch size limits
    # Estimate: each metric ~1KB, batch processing ~10MB per batch
    # Conservative limit: 100 batches max to avoid OOM
    max_batch_size = 100
    # Check available memory if possible
    try:
        import psutil  # type: ignore
        available_memory_gb = psutil.virtual_memory().available / (1024**3)
        # Allow up to 10% of available memory for batch processing
        # Estimate: 10MB per batch, so max_batch = (available_memory_gb * 0.1 * 1024) / 10
        memory_aware_max = int((available_memory_gb * 0.1 * 1024) / 10)
        if memory_aware_max > 0:
            max_batch_size = min(100, max(20, memory_aware_max))
    except ImportError:
        # psutil not available, use conservative default
        pass
    
    if BATCH_SIZE < 1 or BATCH_SIZE > max_batch_size:
        logger.warning(f"BATCH_SIZE {BATCH_SIZE} out of range (1-{max_batch_size}), using default 20")
        BATCH_SIZE = 20
except ValueError:
    logger.warning(f"Invalid BATCH_SIZE '{batch_size_raw}', using default 20")
    BATCH_SIZE = 20

# Validate circuit breaker thresholds
circuit_breaker_threshold_raw = os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
try:
    CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(circuit_breaker_threshold_raw)
    if CIRCUIT_BREAKER_FAILURE_THRESHOLD < 1 or CIRCUIT_BREAKER_FAILURE_THRESHOLD > 100:
        logger.warning(f"CIRCUIT_BREAKER_FAILURE_THRESHOLD {CIRCUIT_BREAKER_FAILURE_THRESHOLD} out of range (1-100), using default 5")
        CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
except ValueError:
    logger.warning(f"Invalid CIRCUIT_BREAKER_FAILURE_THRESHOLD '{circuit_breaker_threshold_raw}', using default 5")
    CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5

circuit_breaker_reset_raw = os.getenv("CIRCUIT_BREAKER_RESET_TIMEOUT", "60")
try:
    CIRCUIT_BREAKER_RESET_TIMEOUT = int(circuit_breaker_reset_raw)
    if CIRCUIT_BREAKER_RESET_TIMEOUT < 1 or CIRCUIT_BREAKER_RESET_TIMEOUT > 3600:
        logger.warning(f"CIRCUIT_BREAKER_RESET_TIMEOUT {CIRCUIT_BREAKER_RESET_TIMEOUT} out of range (1-3600), using default 60")
        CIRCUIT_BREAKER_RESET_TIMEOUT = 60
except ValueError:
    logger.warning(f"Invalid CIRCUIT_BREAKER_RESET_TIMEOUT '{circuit_breaker_reset_raw}', using default 60")
    CIRCUIT_BREAKER_RESET_TIMEOUT = 60

# Circuit breaker state
class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered

class CircuitBreaker:
    """Improved circuit breaker with success rate tracking"""
    def __init__(self, failure_threshold=5, reset_timeout=60, success_rate_threshold=0.5, window_size=20):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_rate_threshold = success_rate_threshold
        self.window_size = window_size
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        # Track recent calls for success rate calculation
        self.recent_calls = []  # List of (timestamp, success) tuples
        self.lock = threading.Lock()
    
    def record_success(self):
        """Record successful call"""
        with self.lock:
            self.failure_count = 0
            self.recent_calls.append((datetime.now(), True))
            # Keep only recent calls in window
            if len(self.recent_calls) > self.window_size:
                self.recent_calls.pop(0)
            
            # Calculate success rate
            if len(self.recent_calls) >= 10:  # Need minimum samples
                success_count = sum(1 for _, success in self.recent_calls if success)
                success_rate = success_count / len(self.recent_calls)
                
                if self.state == CircuitState.HALF_OPEN and success_rate >= self.success_rate_threshold:
                    self.state = CircuitState.CLOSED
                    logger.info(f"Circuit breaker CLOSED - success rate {success_rate:.2%} above threshold")
                elif self.state == CircuitState.OPEN:
                    # If we're in OPEN state and getting successes, move to HALF_OPEN
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker HALF_OPEN - testing recovery")
            else:
                # Not enough samples yet, but success is good
                if self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.CLOSED
                    logger.info("Circuit breaker CLOSED - successful call in HALF_OPEN")
                elif self.state == CircuitState.OPEN:
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker HALF_OPEN - testing recovery")
    
    def record_failure(self):
        """Record failed call"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            self.recent_calls.append((datetime.now(), False))
            # Keep only recent calls in window
            if len(self.recent_calls) > self.window_size:
                self.recent_calls.pop(0)
            
            # Calculate success rate
            if len(self.recent_calls) >= 10:
                success_count = sum(1 for _, success in self.recent_calls if success)
                success_rate = success_count / len(self.recent_calls)
                
                if success_rate < self.success_rate_threshold or self.failure_count >= self.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        self.state = CircuitState.OPEN
                        logger.warning(f"Circuit breaker OPENED - failure_count={self.failure_count}, success_rate={success_rate:.2%}")
            else:
                # Not enough samples, use failure count
                if self.failure_count >= self.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        self.state = CircuitState.OPEN
                        logger.warning(f"Circuit breaker OPENED after {self.failure_count} failures")
    
    def can_attempt(self) -> bool:
        """Check if we can attempt a call"""
        with self.lock:
            if self.state == CircuitState.CLOSED:
                return True
            
            if self.state == CircuitState.OPEN:
                # Check if reset timeout has passed
                if self.last_failure_time:
                    elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                    if elapsed >= self.reset_timeout:
                        self.state = CircuitState.HALF_OPEN
                        logger.info("Circuit breaker HALF_OPEN - testing recovery")
                        return True
                return False
            
            # HALF_OPEN - allow one attempt
            return True

# Global circuit breaker instance
ml_circuit_breaker = CircuitBreaker(
    failure_threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    reset_timeout=CIRCUIT_BREAKER_RESET_TIMEOUT
)

# Feature names must match training script exactly
FEATURE_NAMES = [
    "cpu_usage",
    "memory_usage",
    "disk_usage",
    "network_bytes_sec",
    "error_rate",
    "latency_ms",
    "restart_count",
    "age_minutes",
    "cpu_memory_ratio",
    "resource_pressure",
    "error_latency_product",
    "network_per_cpu",
    "is_critical"
]


# Connection pool for database connections
_db_pool = None
_pool_stats = {
    "total_connections": 0,
    "active_connections": 0,
    "failed_connections": 0,
    "pool_created": None,
    "pool_size": 0,
    "pool_max_size": 0,
    "pool_available": 0,
    "pool_wait_time": 0.0,
    "pool_errors": 0
}

def get_pool_stats() -> Dict[str, Any]:
    """Get connection pool statistics for monitoring"""
    global _db_pool, _pool_stats
    stats = _pool_stats.copy()
    if _db_pool:
        try:
            # Try to get pool size information
            if hasattr(_db_pool, 'maxconn'):
                stats["pool_max_size"] = _db_pool.maxconn
            if hasattr(_db_pool, '_pool'):
                stats["pool_size"] = len(_db_pool._pool)
                stats["pool_available"] = len(_db_pool._pool)
            if hasattr(_db_pool, 'minconn'):
                stats["pool_min_size"] = _db_pool.minconn
        except Exception as e:
            logger.warning(f"Error getting pool stats: {e}")
    return stats

def log_pool_stats() -> None:
    """Log connection pool statistics"""
    stats = get_pool_stats()
    logger.info(f"Connection pool stats: {stats}")

def get_db_connection() -> "connection":
    """Get database connection with retry logic and connection pooling"""
    global _db_pool
    
    # Use connection pooling if available (psycopg2.pool)
    try:
        import psycopg2.pool
        if _db_pool is None:
            # Create connection pool
            _db_pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=DATABASE_URL
            )
            _pool_stats["pool_created"] = datetime.now()
            logger.info("Database connection pool created")
        
        # Get connection from pool with monitoring
        import time
        wait_start = time.time()
        conn = _db_pool.getconn()
        wait_time = time.time() - wait_start
        _pool_stats["pool_wait_time"] = wait_time
        
        if conn:
            try:
                # Ensure connection is in a clean state (no active transaction)
                # This prevents "set_session cannot be used inside a transaction" errors
                try:
                    # Check if we're in a transaction
                    conn.rollback()  # Rollback any existing transaction
                    conn.autocommit = True  # Set to autocommit first
                except Exception as e:
                    logger.debug(f"Connection state reset warning: {e}")
                
                if hasattr(_db_pool, '_pool'):
                    pool_size = len(_db_pool._pool) if hasattr(_db_pool, '_pool') else 0
                    _pool_stats["active_connections"] = _db_pool.maxconn - pool_size if hasattr(_db_pool, 'maxconn') else 0
                    _pool_stats["pool_available"] = pool_size
                    _pool_stats["pool_size"] = pool_size
                _pool_stats["total_connections"] += 1
                if wait_time > 1.0:  # Log if wait time is significant
                    logger.warning(f"Connection pool wait time: {wait_time:.2f}s")
            except Exception as e:
                logger.warning(f"Error updating pool stats: {e}")
            return conn
    except ImportError:
        # psycopg2.pool not available, fall back to direct connection
        pass
    except Exception as e:
        logger.warning(f"Connection pool error, using direct connection: {e}")
    
    # Fallback to direct connection with retry logic
    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except psycopg2.OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts")
                raise

def return_db_connection(conn: "connection", reset_state: bool = True) -> None:
    """Return connection to pool with comprehensive error handling"""
    global _db_pool
    
    if conn is None:
        logger.warning("Attempted to return None connection to pool")
        return
    
    if _db_pool is None:
        # No pool exists, close connection directly
        try:
            if conn.closed == 0:  # Connection is open
                conn.close()
        except Exception as e:
            logger.warning(f"Error closing connection when pool is None: {e}")
        return
    
    try:
        # Check if connection is still valid before returning to pool
        if conn.closed != 0:
            logger.warning("Connection is closed, not returning to pool")
            # Try to close it explicitly to ensure cleanup
            try:
                conn.close()
            except Exception:
                pass
            return
        
        # Validate connection is not in a bad state
        try:
            # Quick validation query to ensure connection is usable
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
        except Exception as validation_err:
            logger.warning(f"Connection validation failed, not returning to pool: {validation_err}")
            # Connection is in bad state, close it
            try:
                conn.close()
            except Exception:
                pass
            return
        
        # Connection is valid, return to pool
        _db_pool.putconn(conn)
    except psycopg2.pool.PoolError as e:
        logger.error(f"Pool error returning connection: {e}")
        _pool_stats["pool_errors"] += 1
        # Pool may be full or closed, close connection
        try:
            conn.close()
        except Exception:
            pass
    except psycopg2.Error as e:
        logger.error(f"PostgreSQL error returning connection: {e}")
        # Database error, close connection
        try:
            conn.close()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Unexpected error returning connection to pool: {e}")
        # Unexpected error, attempt to close connection
        try:
            conn.close()
        except Exception:
            pass

def close_db_pool() -> None:
    """Close database connection pool on shutdown"""
    global _db_pool
    if _db_pool:
        try:
            _db_pool.closeall()
            logger.info("Database connection pool closed")
        except Exception as e:
            logger.warning(f"Error closing connection pool: {e}")
        finally:
            _db_pool = None

import atexit
atexit.register(close_db_pool)

# Register signal handlers for explicit cleanup
_shutdown_event = threading.Event()

def signal_handler(signum, frame):
    """Handle shutdown signals with graceful shutdown"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_event.set()
    # Log pool stats before closing
    log_pool_stats()
    close_db_pool()
    import sys
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def generate_predictions(conn: "connection", ml_service_url: str) -> int:
    """
    Generate ML predictions for recent metrics
    Properly engineers features to match training script
    """
    logger.info("🤖 Generating ML predictions...")

    cur = conn.cursor()

    try:
        # Get recent metrics without predictions
        # Exclude system namespaces - only process user-created pods
        cur.execute("""
            SELECT DISTINCT ON (pm.pod_name, pm.namespace)
                pm.pod_name, pm.namespace, pm.timestamp,
                pm.cpu_utilization, pm.memory_utilization,
                COALESCE(pm.disk_usage_bytes::float / NULLIF(pm.disk_limit_bytes, 0) * 100, 0) as disk_usage_percent,
                COALESCE(pm.network_rx_bytes + pm.network_tx_bytes, 0) as network_bytes,
                COALESCE(pm.network_rx_errors + pm.network_tx_errors, 0) as error_rate,
                pm.restarts,
                EXTRACT(EPOCH FROM (NOW() - pm.timestamp)) / 60.0 as age_minutes,
                pm.cpu_trend, pm.memory_trend, pm.restart_trend,
                pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues
            FROM pod_metrics pm
            LEFT JOIN ml_predictions mp ON 
                pm.pod_name = mp.pod_name 
                AND pm.namespace = mp.namespace 
                AND pm.timestamp = mp.timestamp
            WHERE pm.timestamp > NOW() - INTERVAL '1 hour'
                AND mp.timestamp IS NULL
                AND pm.namespace NOT IN ('kube-system', 'kube-public', 'kube-node-lease', 'local-path-storage', 'default')
            ORDER BY pm.pod_name, pm.namespace, pm.timestamp DESC
            LIMIT 50
        """)

        metrics = cur.fetchall()
        logger.info(f"   Found {len(metrics)} metrics to analyze")

        if len(metrics) == 0:
            return 0

        predictions_made = 0

        # Process in batches for better performance and transaction management
        try:
            for batch_start in range(0, len(metrics), BATCH_SIZE):
                batch = metrics[batch_start:batch_start+BATCH_SIZE]
                batch_num = batch_start//BATCH_SIZE + 1
                total_batches = (len(metrics) + BATCH_SIZE - 1) // BATCH_SIZE
                logger.debug(f"   Processing batch {batch_num}/{total_batches} ({len(batch)} metrics)")

                # Start transaction for this batch
                batch_predictions = 0
                
                # Track transaction state for proper rollback
                transaction_started = False
                
                try:
                    # Ensure connection is in autocommit mode first, then start transaction
                    # This prevents "set_session cannot be used inside a transaction" errors
                    if not conn.autocommit:
                        conn.rollback()  # Rollback any existing transaction
                    conn.autocommit = False  # Now we can safely set to transaction mode
                    transaction_started = True
                except Exception as e:
                    logger.warning(f"Failed to initialize transaction: {e}")
                    transaction_started = False
                    # Try to reset connection state
                    try:
                        conn.rollback()
                        conn.autocommit = True
                    except Exception:
                        pass
                
                for metric in batch:
                    # Validate metric tuple has expected number of elements before unpacking
                    expected_fields = 17
                    if len(metric) != expected_fields:
                        logger.warning(f"Metric tuple has {len(metric)} fields, expected {expected_fields}. Skipping metric.")
                        continue
                    
                    (pod_name, namespace, timestamp, cpu_util, mem_util, disk_pct,
                     network_bytes, err_rate, restarts, age_minutes, cpu_trend,
                     mem_trend, restart_trend, has_oom, has_crash, has_high_cpu, has_network) = metric

                try:
                    # Engineer features to match training script EXACTLY
                    cpu_usage = float(cpu_util or 0)
                    memory_usage = float(mem_util or 0)
                    disk_usage = float(disk_pct or 0)
                    # Use actual collection interval (15 seconds) for network bytes/sec calculation
                    collection_interval_sec = 15.0  # Matches COLLECTION_INTERVAL default
                    network_bytes_sec = float(network_bytes or 0) / collection_interval_sec
                    # Error rate should be errors per second, not raw error count
                    error_rate_val = float(err_rate or 0) / collection_interval_sec if err_rate else 0.0
                    latency_ms = 0  # Would come from actual metrics if available
                    restart_count = int(restarts or 0)
                    age_minutes_val = float(age_minutes or 0)

                    # Engineered features (must match simple_train.py)
                    # Comprehensive validation to prevent division by zero and NaN values
                    cpu_memory_ratio = cpu_usage / max(memory_usage, 0.1) if memory_usage >= 0 else 0.0
                    # Validate ratio is finite
                    if not (math.isfinite(cpu_memory_ratio) and cpu_memory_ratio >= 0):
                        cpu_memory_ratio = 0.0
                    
                    resource_pressure = (cpu_usage + memory_usage + disk_usage) / 3.0
                    # Validate resource pressure is finite
                    if not (math.isfinite(resource_pressure) and resource_pressure >= 0):
                        resource_pressure = 0.0
                    
                    error_latency_product = error_rate_val * (latency_ms + 1.0)
                    # Validate error_latency_product is finite
                    if not (math.isfinite(error_latency_product) and error_latency_product >= 0):
                        error_latency_product = 0.0
                    
                    # Network per CPU with comprehensive validation
                    if cpu_usage > 0.1:
                        network_per_cpu = network_bytes_sec / cpu_usage
                    else:
                        network_per_cpu = network_bytes_sec / 0.1  # Use minimum CPU value
                    # Validate network_per_cpu is finite
                    if not (math.isfinite(network_per_cpu) and network_per_cpu >= 0):
                        network_per_cpu = 0.0
                    
                    is_critical = 1.0 if (has_oom or has_crash or has_high_cpu) else 0.0

                    # Build feature vector in correct order
                    features = {
                        "cpu_usage": cpu_usage,
                        "memory_usage": memory_usage,
                        "disk_usage": disk_usage,
                        "network_bytes_sec": network_bytes_sec,
                        "error_rate": error_rate_val,
                        "latency_ms": latency_ms,
                        "restart_count": restart_count,
                        "age_minutes": age_minutes_val,
                        "cpu_memory_ratio": cpu_memory_ratio,
                        "resource_pressure": resource_pressure,
                        "error_latency_product": error_latency_product,
                        "network_per_cpu": network_per_cpu,
                        "is_critical": is_critical,
                    }

                    # Validate feature count matches
                    if len(features) != len(FEATURE_NAMES):
                        logger.error(f"Feature count mismatch: {len(features)} vs {len(FEATURE_NAMES)}")
                        continue
                    
                    # Comprehensive validation: ensure all features are finite numbers
                    invalid_features = []
                    for key, value in features.items():
                        if not isinstance(value, (int, float)):
                            invalid_features.append(f"{key}: {type(value).__name__}")
                        elif not math.isfinite(value):
                            invalid_features.append(f"{key}: {value} (not finite)")
                        elif value < 0 and key not in ['cpu_memory_ratio', 'network_per_cpu']:  # Some ratios can be negative
                            # Most features should be non-negative
                            if key in ['cpu_usage', 'memory_usage', 'disk_usage', 'error_rate', 'latency_ms', 
                                       'restart_count', 'age_minutes', 'resource_pressure', 'error_latency_product', 'is_critical']:
                                invalid_features.append(f"{key}: {value} (negative)")
                    
                    if invalid_features:
                        logger.warning(f"Invalid features for {pod_name}: {invalid_features}. Skipping prediction.")
                        continue

                    # Call ML service with circuit breaker
                    if not ml_circuit_breaker.can_attempt():
                        logger.warning(f"Circuit breaker OPEN - skipping prediction for {pod_name}")
                        continue
                    
                    try:
                        response = requests.post(
                            f"{ml_service_url}/predict",
                            json={"features": features},
                            timeout=10
                        )

                        if response.status_code != 200:
                            logger.warning(f"ML service returned {response.status_code}")
                            ml_circuit_breaker.record_failure()
                            continue

                        prediction = response.json()
                        ml_circuit_breaker.record_success()

                        predicted_issue = prediction.get('anomaly_type', 'healthy')
                        confidence_raw = prediction.get('confidence', 0.5)
                        
                        # Validate confidence is in valid range [0, 1] - always validate and clamp
                        try:
                            confidence_float = float(confidence_raw)
                            if confidence_float < 0.0 or confidence_float > 1.0:
                                logger.warning(f"Confidence value {confidence_raw} out of range [0, 1] for {pod_name}, clamping to valid range")
                                confidence = max(0.0, min(1.0, confidence_float))
                            else:
                                confidence = confidence_float
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid confidence value {confidence_raw} for {pod_name}, using default 0.5")
                            confidence = 0.5

                        # Override ML prediction if metrics indicate crash loop, OOM, or other critical issues
                        # This ensures we detect issues even if ML model doesn't
                        if has_crash:
                            predicted_issue = 'crash_loop'
                            confidence = 0.95  # High confidence for direct metric detection
                            logger.info(f"      Overriding ML prediction for {pod_name}: crash loop detected in metrics")
                        elif has_oom:
                            predicted_issue = 'OOMKilled'
                            confidence = 0.95
                            logger.info(f"      Overriding ML prediction for {pod_name}: OOM kill detected in metrics")
                        elif has_high_cpu and cpu_util and cpu_util > 80:
                            predicted_issue = 'high_cpu'
                            confidence = max(confidence, 0.85)
                            logger.info(f"      Overriding ML prediction for {pod_name}: high CPU detected in metrics")
                        elif has_network:
                            predicted_issue = 'NetworkErrors'
                            confidence = max(confidence, 0.80)
                            logger.info(f"      Overriding ML prediction for {pod_name}: network issues detected in metrics")

                        # Save prediction
                        # Note: explanation field from ML service, fallback to empty string
                        explanation = prediction.get('explanation', prediction.get('reasoning', ''))
                        if has_crash:
                            explanation = "Crash loop detected from pod metrics (restart count and container state)"
                        elif has_oom:
                            explanation = "OOM kill detected from pod metrics"
                        
                        # Calculate is_anomaly and anomaly_type explicitly
                        is_anomaly = 1 if predicted_issue != 'healthy' and confidence > CONFIDENCE_THRESHOLD else 0
                        anomaly_type = predicted_issue if predicted_issue else 'unknown'
                        
                        cur.execute("""
                            INSERT INTO ml_predictions (
                                pod_name, namespace, timestamp,
                                predicted_issue, confidence, time_horizon_seconds,
                                top_features, explanation,
                                resource_type, resource_name, prediction_type,
                                prediction_value, model_version, features,
                                is_anomaly, anomaly_type
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
                        """, (
                            pod_name, namespace, timestamp,
                            predicted_issue, confidence, 3600,
                            json.dumps(list(features.keys())), explanation,
                            'pod', pod_name, predicted_issue,
                            1.0 if predicted_issue != 'healthy' else 0.0,
                            prediction.get('model_used', 'ensemble'),
                            json.dumps(features),
                            is_anomaly, anomaly_type
                        ))

                        batch_predictions += 1

                        if predicted_issue != 'healthy' and confidence > CONFIDENCE_THRESHOLD:
                            logger.info(f"      🔴 {namespace}/{pod_name}: {predicted_issue} ({confidence:.1%})")

                    except requests.exceptions.RequestException as e:
                        logger.warning(f"ML service request failed for {pod_name}: {e}")
                        ml_circuit_breaker.record_failure()
                        # Continue to next metric instead of failing
                        continue

                    except (KeyError, ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Invalid prediction response for {pod_name}: {e}")
                        continue

                    except psycopg2.Error as e:
                        logger.warning(f"Database error for {pod_name}: {e}")
                        try:
                            conn.rollback()
                            transaction_started = True
                        except Exception as rollback_err:
                            logger.error(f"Error during rollback: {rollback_err}")
                            transaction_started = False
                        # Start new transaction for next metric
                        conn.autocommit = False
                        continue

                except (ValueError, KeyError, TypeError) as e:
                    # Critical data errors - log and skip this metric
                    logger.error(f"Critical data error processing {pod_name}: {e}")
                    try:
                        conn.rollback()
                        transaction_started = True
                    except Exception as rollback_err:
                        logger.error(f"Error during rollback: {rollback_err}")
                        transaction_started = False
                    conn.autocommit = False
                    continue
                except psycopg2.Error as e:
                    # Database errors - critical, need to handle properly
                    logger.error(f"Database error processing {pod_name}: {e}")
                    try:
                        conn.rollback()
                        transaction_started = True
                    except Exception as rollback_err:
                        logger.error(f"Error during rollback: {rollback_err}")
                        transaction_started = False
                    conn.autocommit = False
                    continue
                except Exception as e:
                    # Transient errors - may be retryable
                    logger.warning(f"Transient error processing {pod_name}: {e}")
                    try:
                        conn.rollback()
                        transaction_started = True
                    except Exception as rollback_err:
                        logger.error(f"Error during rollback: {rollback_err}")
                        transaction_started = False
                    conn.autocommit = False
                    continue
                
                # Commit batch to avoid large transaction
                try:
                    if transaction_started:
                        conn.commit()
                        predictions_made += batch_predictions
                        logger.debug(f"   Batch {batch_num} committed: {batch_predictions} predictions")
                    else:
                        logger.warning(f"   Batch {batch_num} skipped - transaction not started")
                except psycopg2.Error as e:
                    logger.warning(f"Error committing batch {batch_num}: {e}")
                    try:
                        conn.rollback()
                        transaction_started = True
                    except psycopg2.Error as rollback_err:
                        logger.error(f"Error during rollback: {rollback_err}")
                        transaction_started = False
                        # Try to reset connection state
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    # Continue with next batch instead of failing entire operation
                    # Reset autocommit for next batch
                    conn.autocommit = False
        except psycopg2.Error as e:
            # Critical database error - rollback and re-raise
            logger.error(f"Critical database error in batch processing: {e}")
            try:
                conn.rollback()
            except psycopg2.Error as rollback_err:
                logger.error(f"Error during rollback: {rollback_err}")
                # Try to reset connection state
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        except (ValueError, KeyError, TypeError) as e:
            # Critical data errors - log and re-raise
            logger.error(f"Critical data error in batch processing: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        except Exception as e:
            # Transient errors - log and re-raise for retry
            logger.warning(f"Transient error in batch processing: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            raise

        logger.info(f"   ✅ Generated {predictions_made} predictions")
        return predictions_made

    except psycopg2.Error as e:
        logger.error(f"Database error in generate_predictions: {e}")
        conn.rollback()
        return 0
    finally:
        cur.close()


def validate_pod_exists(namespace: str, pod_name: str) -> bool:
    """Validate that a pod exists in the cluster"""
    try:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
        
        # Try to load kubeconfig
        try:
            config.load_incluster_config()
        except:
            try:
                config.load_kube_config()
            except Exception as e:
                logger.debug(f"Could not load kubeconfig for pod validation: {e}")
                return True  # Assume pod exists if we can't check
        
        v1 = client.CoreV1Api()
        try:
            v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            # For other errors, assume pod exists (may be transient)
            logger.debug(f"Error checking pod existence: {e}")
            return True
    except Exception as e:
        logger.debug(f"Pod validation error: {e}")
        return True  # Assume pod exists if validation fails

def create_issues_from_predictions(conn: "connection") -> int:
    """Create issues from anomaly predictions"""
    logger.info("📋 Creating issues from predictions...")

    cur = conn.cursor()
    issues_created = 0

    try:
        # Get recent anomaly predictions without issues
        cur.execute("""
            SELECT DISTINCT ON (mp.pod_name, mp.namespace, mp.predicted_issue)
                mp.pod_name, mp.namespace, mp.predicted_issue, mp.confidence, mp.timestamp,
                pm.cpu_utilization, pm.memory_utilization, pm.has_oom_kill,
                pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues
            FROM ml_predictions mp
            JOIN pod_metrics pm ON
                mp.pod_name = pm.pod_name
                AND mp.namespace = pm.namespace
                AND mp.timestamp = pm.timestamp
            LEFT JOIN issues i ON
                mp.pod_name = i.pod_name
                AND mp.namespace = i.namespace
                AND mp.predicted_issue = i.issue_type
                AND i.status IN ('Open', 'InProgress')
            WHERE mp.timestamp > NOW() - INTERVAL '1 hour'
                AND mp.predicted_issue != 'healthy'
                AND mp.confidence > %s
                AND i.id IS NULL
            ORDER BY mp.pod_name, mp.namespace, mp.predicted_issue, mp.timestamp DESC
            LIMIT 20
        """, (CONFIDENCE_THRESHOLD,))

        predictions = cur.fetchall()
        logger.info(f"   Found {len(predictions)} anomaly predictions")

        # Use batch insert with transaction to prevent race conditions
        issue_values = []
        for pred in predictions:
            # Validate tuple length before unpacking
            expected_fields = 11
            if len(pred) != expected_fields:
                logger.warning(f"Prediction tuple has {len(pred)} fields, expected {expected_fields}. Skipping prediction.")
                continue
            
            try:
                (pod_name, namespace, issue_type, confidence, timestamp,
                 cpu_util, mem_util, has_oom, has_crash, has_high_cpu, has_network) = pred
            except ValueError as e:
                logger.warning(f"Failed to unpack prediction tuple: {e}. Skipping prediction.")
                continue

            # Validate pod exists in cluster before creating issue
            if not validate_pod_exists(namespace, pod_name):
                logger.warning(f"Pod {namespace}/{pod_name} does not exist, skipping issue creation")
                continue

            # Validate confidence
            confidence = max(0.0, min(1.0, float(confidence)))

            # Determine severity based on confidence and issue flags
            if confidence > 0.9 or (has_oom or has_crash):
                severity = "critical"
            elif confidence > 0.8:
                severity = "high"
            else:
                severity = "medium"

            # Build description
            description_parts = [f"ML model detected {issue_type} with {confidence:.1%} confidence"]
            if cpu_util:
                description_parts.append(f"CPU: {cpu_util:.1f}%")
            if mem_util:
                description_parts.append(f"Memory: {mem_util:.1f}%")
            description = ". ".join(description_parts)

            issue_id = str(uuid.uuid4())
            issue_values.append((
                issue_id, pod_name, namespace, issue_type, severity,
                description, timestamp, 'Open', confidence, 3600
            ))

        # Batch insert with conflict handling to prevent duplicates
        if issue_values:
            try:
                # Use DO UPDATE to handle unique constraint on (pod_name, namespace, issue_type) for open issues
                # Note: PostgreSQL doesn't support multiple ON CONFLICT, so we handle id conflicts separately
                cur.executemany("""
                    INSERT INTO issues (
                        id, pod_name, namespace, issue_type, severity,
                        description, created_at, status, confidence, predicted_time_horizon
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ON CONSTRAINT idx_issues_unique_open DO UPDATE SET
                        severity = EXCLUDED.severity,
                        description = EXCLUDED.description,
                        confidence = EXCLUDED.confidence,
                        created_at = EXCLUDED.created_at
                    WHERE issues.status IN ('Open', 'open', 'InProgress', 'in_progress')
                """, issue_values)
                
                issues_created = cur.rowcount
                conn.commit()
                
                for (issue_id, pod_name, namespace, issue_type, severity, _, _, _, _, _) in issue_values:
                    logger.info(f"      🔴 Issue created: {namespace}/{pod_name} - {issue_type} ({severity})")
            except psycopg2.Error as e:
                logger.warning(f"Database error creating issues: {e}")
                conn.rollback()
                # Fallback to individual inserts
                for issue_val in issue_values:
                    try:
                        cur.execute("""
                            INSERT INTO issues (
                                id, pod_name, namespace, issue_type, severity,
                                description, created_at, status, confidence, predicted_time_horizon
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, issue_val)
                        issues_created += 1
                    except psycopg2.Error:
                        continue
                conn.commit()
        logger.info(f"   ✅ Created {issues_created} issues from predictions")
        return issues_created

    except psycopg2.Error as e:
        logger.error(f"Database error in create_issues_from_predictions: {e}")
        conn.rollback()
        return 0
    finally:
        cur.close()


def create_issues_from_thresholds(conn: "connection") -> int:
    """Create issues based on direct metric thresholds (fallback when ML fails)"""
    logger.info("📏 Creating issues from metric thresholds...")

    cur = conn.cursor()
    issues_created = 0

    try:
        # Get recent metrics that exceed thresholds
        cur.execute("""
            SELECT DISTINCT ON (pm.pod_name, pm.namespace)
                pm.pod_name, pm.namespace, pm.timestamp,
                pm.cpu_utilization, pm.memory_utilization,
                pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues,
                pm.restarts
            FROM pod_metrics pm
            LEFT JOIN issues i ON
                pm.pod_name = i.pod_name
                AND pm.namespace = i.namespace
                AND i.status IN ('Open', 'InProgress')
                AND i.created_at > NOW() - INTERVAL '1 hour'
            WHERE pm.timestamp > NOW() - INTERVAL '10 minutes'
                AND i.id IS NULL
                AND pm.namespace NOT IN ('kube-system', 'kube-public', 'kube-node-lease', 'local-path-storage', 'default')
                AND (
                    pm.cpu_utilization > 80 OR
                    pm.memory_utilization > 85 OR
                    pm.has_oom_kill = true OR
                    pm.has_crash_loop = true OR
                    pm.restarts > 3
                )
            ORDER BY pm.pod_name, pm.namespace, pm.timestamp DESC
            LIMIT 20
        """)

        metrics = cur.fetchall()
        logger.info(f"   Found {len(metrics)} metrics exceeding thresholds")

        # Use batch insert for better performance
        issue_values = []
        for metric in metrics:
            # Validate tuple length before unpacking
            expected_fields = 10
            if len(metric) != expected_fields:
                logger.warning(f"Metric tuple has {len(metric)} fields, expected {expected_fields}. Skipping metric.")
                continue
            
            (pod_name, namespace, timestamp, cpu_util, mem_util,
             has_oom, has_crash, has_high_cpu, has_network, restarts) = metric

            # Validate pod exists in cluster before creating issue
            if not validate_pod_exists(namespace, pod_name):
                logger.warning(f"Pod {namespace}/{pod_name} does not exist, skipping issue creation")
                continue

            # Determine issue type and severity
            issue_type = "healthy"
            severity = "low"
            confidence = 0.9  # High confidence for direct threshold violations
            description_parts = ["Direct metric threshold violation detected"]

            if has_oom:
                issue_type = "OOMKilled"
                severity = "critical"
                description_parts.append("Pod was killed due to out-of-memory condition")
            elif mem_util and mem_util > 85:
                issue_type = "high_memory"
                severity = "high"
                description_parts.append(f"Memory utilization: {mem_util:.1f}%")
            elif cpu_util and cpu_util > 80:
                issue_type = "high_cpu"
                severity = "high"
                description_parts.append(f"CPU utilization: {cpu_util:.1f}%")
            elif has_crash:
                issue_type = "crash_loop"
                severity = "critical"
                description_parts.append("Pod is in crash loop")
            elif restarts and restarts > 3:
                issue_type = "frequent_restarts"
                severity = "medium"
                description_parts.append(f"Pod restarted {restarts} times recently")

            if issue_type == "healthy":
                continue  # Skip if no threshold violated

            description = ". ".join(description_parts)
            issue_id = str(uuid.uuid4())
            issue_values.append((
                issue_id, pod_name, namespace, issue_type, severity,
                description, timestamp, 'Open', confidence, 3600
            ))

        # Batch insert with conflict handling
        if issue_values:
            try:
                cur.executemany("""
                    INSERT INTO issues (
                        id, pod_name, namespace, issue_type, severity,
                        description, created_at, status, confidence, predicted_time_horizon
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, issue_values)
                
                issues_created = cur.rowcount
                conn.commit()
                
                for (issue_id, pod_name, namespace, issue_type, severity, _, _, _, _, _) in issue_values:
                    logger.info(f"      🔴 Threshold issue created: {namespace}/{pod_name} - {issue_type} ({severity})")
            except psycopg2.Error as e:
                logger.warning(f"Database error creating threshold issues: {e}")
                conn.rollback()
                # Fallback to individual inserts
                for issue_val in issue_values:
                    try:
                        cur.execute("""
                            INSERT INTO issues (
                                id, pod_name, namespace, issue_type, severity,
                                description, created_at, status, confidence, predicted_time_horizon
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, issue_val)
                        issues_created += 1
                    except psycopg2.Error:
                        continue
                conn.commit()
        logger.info(f"   ✅ Created {issues_created} issues from thresholds")
        return issues_created

    except psycopg2.Error as e:
        logger.error(f"Database error in create_issues_from_thresholds: {e}")
        conn.rollback()
        return 0
    finally:
        cur.close()


def calculate_costs_and_savings(conn: "connection") -> int:
    """Calculate cost savings from optimizations and populate cost_savings table"""
    logger.info("💰 Calculating costs and savings...")

    cur = conn.cursor()
    savings_calculated = 0

    try:
        # Get recent issues that could lead to cost savings
        cur.execute("""
            SELECT DISTINCT ON (i.pod_name, i.namespace, i.issue_type)
                i.pod_name, i.namespace, i.issue_type, i.confidence, i.created_at,
                pm.cpu_utilization, pm.memory_utilization, pm.cpu_limit_millicores, pm.memory_limit_bytes,
                pm.restarts, pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu
            FROM issues i
            JOIN pod_metrics pm ON
                i.pod_name = pm.pod_name
                AND i.namespace = pm.namespace
            LEFT JOIN cost_savings cs ON
                i.pod_name = cs.pod_name
                AND i.namespace = cs.namespace
                AND i.issue_type = cs.issue_type
                AND cs.timestamp > NOW() - INTERVAL '24 hours'
            WHERE i.created_at > NOW() - INTERVAL '24 hours'
                AND i.status IN ('Open', 'open', 'InProgress', 'in_progress', 'resolved')
                AND cs.id IS NULL
            ORDER BY i.pod_name, i.namespace, i.issue_type, i.created_at DESC
            LIMIT 50
        """)

        issues = cur.fetchall()
        logger.info(f"   Found {len(issues)} issues for cost analysis")

        for issue in issues:
            # Validate tuple length before unpacking
            expected_fields = 13
            if len(issue) != expected_fields:
                logger.warning(f"Issue tuple has {len(issue)} fields, expected {expected_fields}. Skipping issue.")
                continue
            
            (pod_name, namespace, issue_type, confidence, created_at,
             cpu_util, mem_util, cpu_limit, mem_limit, restarts, has_oom, has_crash, has_high_cpu) = issue

            try:
                # Calculate original costs with configurable rates
                # In production, these should be set via environment variables or cloud provider pricing API
                # Defaults are estimates and may not reflect actual cloud provider costs
                # Support for different cloud providers
                cloud_provider = os.getenv("CLOUD_PROVIDER", "generic").lower()
                
                # Provider-specific default rates (can be overridden by env vars)
                provider_defaults = {
                    "aws": {"cpu": 0.10, "mem": 0.05},
                    "gcp": {"cpu": 0.08, "mem": 0.04},
                    "azure": {"cpu": 0.09, "mem": 0.045},
                    "generic": {"cpu": 0.10, "mem": 0.05}
                }
                
                defaults = provider_defaults.get(cloud_provider, provider_defaults["generic"])
                
                cpu_cost_per_core = float(os.getenv("CPU_COST_PER_CORE_PER_HOUR", str(defaults["cpu"])))
                mem_cost_per_gb = float(os.getenv("MEMORY_COST_PER_GB_PER_HOUR", str(defaults["mem"])))
                
                # Support for regional pricing differences
                region = os.getenv("CLOUD_REGION", "")
                if region:
                    # Regional multiplier (can be configured per region)
                    region_multiplier = float(os.getenv(f"REGION_{region.upper()}_MULTIPLIER", "1.0"))
                    cpu_cost_per_core *= region_multiplier
                    mem_cost_per_gb *= region_multiplier
                
                # Support for reserved instance discounts
                reserved_instance_discount = float(os.getenv("RESERVED_INSTANCE_DISCOUNT", "0.0"))
                if reserved_instance_discount > 0 and reserved_instance_discount < 1.0:
                    cpu_cost_per_core *= (1.0 - reserved_instance_discount)
                    mem_cost_per_gb *= (1.0 - reserved_instance_discount)
                
                # Support for spot instance pricing
                is_spot_instance = os.getenv("SPOT_INSTANCE", "false").lower() == "true"
                if is_spot_instance:
                    spot_discount = float(os.getenv("SPOT_INSTANCE_DISCOUNT", "0.7"))  # 70% discount default
                    if spot_discount > 0 and spot_discount < 1.0:
                        cpu_cost_per_core *= spot_discount
                        mem_cost_per_gb *= spot_discount
                
                # Validate cost rates are positive
                if cpu_cost_per_core <= 0 or mem_cost_per_gb <= 0:
                    error_msg = f"Invalid cost rates: cpu={cpu_cost_per_core}, mem={mem_cost_per_gb}. Must be positive."
                    if environment == "production":
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    logger.warning(f"{error_msg} Using defaults.")
                    cpu_cost_per_core = 0.10
                    mem_cost_per_gb = 0.05
                
                # Log cost rate source for transparency
                if environment == "production":
                    logger.debug(f"Using cost rates: CPU=${cpu_cost_per_core}/core/hr, Memory=${mem_cost_per_gb}/GB/hr")
                else:
                    logger.info(f"Using cost rates (development): CPU=${cpu_cost_per_core}/core/hr, Memory=${mem_cost_per_gb}/GB/hr")
                    logger.info("NOTE: For production, configure CPU_COST_PER_CORE_PER_HOUR and MEMORY_COST_PER_GB_PER_HOUR")
                    logger.info("      or implement cloud provider pricing API integration for accurate costs")
                
                # Validate and calculate resource amounts
                cpu_limit_val = float(cpu_limit or 1000)
                mem_limit_val = float(mem_limit or (1024*1024*1024))
                
                if cpu_limit_val <= 0 or mem_limit_val <= 0:
                    logger.warning(f"Invalid resource limits for {pod_name}: cpu={cpu_limit_val}, mem={mem_limit_val}")
                    continue
                
                cpu_cores = cpu_limit_val / 1000.0  # Convert millicores to cores
                mem_gb = mem_limit_val / (1024*1024*1024.0)  # Convert bytes to GB

                original_cost_per_hour = (cpu_cores * cpu_cost_per_core) + (mem_gb * mem_cost_per_gb)

                # Validate resource utilizations are positive
                if cpu_util and cpu_util < 0:
                    logger.warning(f"Invalid CPU utilization for {pod_name}: {cpu_util}")
                    continue
                if mem_util and mem_util < 0:
                    logger.warning(f"Invalid memory utilization for {pod_name}: {mem_util}")
                    continue

                # Calculate optimized costs based on issue type
                optimized_cost_per_hour = original_cost_per_hour
                optimization_type = None
                description = f"Cost analysis for {issue_type}"

                if issue_type in ['high_memory', 'OOMKilled', 'oom_killed'] and mem_util and mem_util < 80:
                    # Reduce memory allocation if utilization is low
                    optimized_mem_gb = mem_gb * 0.7  # 30% reduction
                    optimized_cost_per_hour = (cpu_cores * 0.10) + (optimized_mem_gb * 0.05)
                    optimization_type = "memory_rightsizing"
                    description = f"Memory rightsizing: {mem_gb:.1f}GB → {optimized_mem_gb:.1f}GB"

                elif issue_type in ['high_cpu', 'cpu_spike'] and cpu_util and cpu_util < 70:
                    # Reduce CPU allocation if utilization is low
                    optimized_cpu_cores = cpu_cores * 0.8  # 20% reduction
                    optimized_cost_per_hour = (optimized_cpu_cores * 0.10) + (mem_gb * 0.05)
                    optimization_type = "cpu_rightsizing"
                    description = f"CPU rightsizing: {cpu_cores:.1f} cores → {optimized_cpu_cores:.1f} cores"

                elif issue_type == 'crash_loop' and restarts and restarts > 5:
                    # Crash loops waste resources - assume 50% efficiency loss
                    optimized_cost_per_hour = original_cost_per_hour * 0.5
                    optimization_type = "stability_improvement"
                    description = f"Stability improvement: reduced crash loop overhead"

                elif issue_type in ['NetworkErrors', 'network_errors', 'network_latency']:
                    # Network latency can cause inefficient resource usage
                    optimized_cost_per_hour = original_cost_per_hour * 0.95  # 5% savings from better network efficiency
                    optimization_type = "network_optimization"
                    description = f"Network optimization: reduced latency overhead"

                savings_per_hour = original_cost_per_hour - optimized_cost_per_hour
                monthly_savings = savings_per_hour * 24 * 30  # Rough monthly estimate

                if savings_per_hour > 0:
                    cur.execute("""
                        INSERT INTO cost_savings (
                            pod_name, namespace, timestamp, issue_type,
                            original_cost_per_hour, optimized_cost_per_hour,
                            savings_per_hour, estimated_monthly_savings,
                            confidence, optimization_type, description
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (pod_name, namespace, timestamp, issue_type) DO NOTHING
                    """, (
                        pod_name, namespace, created_at, issue_type,
                        original_cost_per_hour, optimized_cost_per_hour,
                        savings_per_hour, monthly_savings,
                        confidence, optimization_type, description
                    ))

                    savings_calculated += 1
                    logger.info(f"      💰 {namespace}/{pod_name}: ${savings_per_hour:.3f}/hr saved ({optimization_type})")

            except psycopg2.Error as e:
                logger.warning(f"Database error calculating costs: {e}")
                conn.rollback()
                continue

        conn.commit()
        logger.info(f"   ✅ Calculated {savings_calculated} cost savings")
        return savings_calculated

    except psycopg2.Error as e:
        logger.error(f"Database error in calculate_costs_and_savings: {e}")
        conn.rollback()
        return 0
    finally:
        cur.close()


def trigger_remediations(conn: "connection") -> int:
    """Trigger remediation actions for open issues and populate remediation_actions table"""
    logger.info("🔧 Triggering remediations...")

    cur = conn.cursor()
    remediations_triggered = 0

    try:
        # Get open issues that need remediation
        cur.execute("""
            SELECT i.id, i.pod_name, i.namespace, i.issue_type, i.severity, i.confidence,
                   i.created_at, i.description, pm.cpu_utilization, pm.memory_utilization,
                   pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues
            FROM issues i
            JOIN pod_metrics pm ON
                i.pod_name = pm.pod_name
                AND i.namespace = pm.namespace
            LEFT JOIN remediation_actions ra ON
                i.id = ra.issue_id
                AND ra.timestamp > NOW() - INTERVAL '1 hour'
            WHERE i.status IN ('Open', 'InProgress')
                AND i.created_at > NOW() - INTERVAL '24 hours'
                AND ra.id IS NULL
            ORDER BY
                CASE i.severity
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                END,
                i.confidence DESC,
                i.created_at DESC
            LIMIT 20
        """)

        issues = cur.fetchall()
        logger.info(f"   Found {len(issues)} issues needing remediation")

        for issue in issues:
            # Validate tuple length before unpacking
            expected_fields = 14
            if len(issue) != expected_fields:
                logger.warning(f"Issue tuple has {len(issue)} fields, expected {expected_fields}. Skipping issue.")
                continue
            
            (issue_id, pod_name, namespace, issue_type, severity, confidence,
             created_at, description, cpu_util, mem_util, has_oom, has_crash, has_high_cpu, has_network) = issue

            try:
                # Validate confidence is in valid range [0, 1]
                confidence = max(0.0, min(1.0, float(confidence or 0.5)))
                
                # Determine remediation action based on issue type
                action_type = "restart_pod"  # Default action
                action_details = "Restart pod to recover from issue"
                strategy = "automated_restart"

                if issue_type in ['OOMKilled', 'oom_killed', 'high_memory'] or has_oom:
                    action_type = "increase_memory"
                    action_details = "Increase memory limit by 50%"
                    strategy = "resource_scaling"

                elif issue_type in ['CrashLoopBackOff', 'crash_loop'] or has_crash:
                    action_type = "restart_pod"
                    action_details = "Restart pod to break crash loop"
                    strategy = "pod_restart"

                elif issue_type in ['HighCPU', 'high_cpu', 'cpu_spike'] or has_high_cpu:
                    action_type = "scale_deployment"
                    action_details = "Scale deployment to handle CPU load"
                    strategy = "horizontal_scaling"

                elif issue_type == 'DiskPressure':
                    action_type = "clean_logs"
                    action_details = "Clean up logs and temporary files"
                    strategy = "log_cleanup"

                elif issue_type == 'NetworkErrors' or has_network:
                    action_type = "restart_pod"
                    action_details = "Restart pod to reset network state"
                    strategy = "network_reset"

                # Insert remediation action record
                cur.execute("""
                    INSERT INTO remediation_actions (
                        issue_id, pod_name, namespace, timestamp, action_type,
                        action_details, success, execution_time_seconds,
                        ai_recommendation, strategy_used, confidence, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    issue_id, pod_name, namespace, datetime.now(),
                    action_type, action_details, False,  # success=False initially, will be updated by remediator
                    30,  # estimated execution time
                    f"Automated remediation for {issue_type} with {confidence:.1%} confidence",
                    strategy, confidence, 'pending'
                ))

                remediations_triggered += 1
                logger.info(f"      🔧 Triggered {action_type} for {namespace}/{pod_name} ({issue_type})")

            except psycopg2.Error as e:
                logger.warning(f"Database error triggering remediation: {e}")
                conn.rollback()
                continue

        conn.commit()
        logger.info(f"   ✅ Triggered {remediations_triggered} remediations")
        return remediations_triggered

    except psycopg2.Error as e:
        logger.error(f"Database error in trigger_remediations: {e}")
        conn.rollback()
        return 0
    finally:
        cur.close()


def main():
    """Main orchestrator loop with graceful shutdown"""
    logger.info("=" * 70)
    logger.info("   AURA K8s Orchestrator")
    logger.info("   Processing: Metrics → Predictions → Issues → Costs → Remediation")
    logger.info("=" * 70)

    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info(f"\n⏹️  Received signal {signum}, shutting down gracefully...")
        close_db_pool()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    iteration = 0

    while True:
        iteration += 1
        logger.info(f"\n🔄 Iteration {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            conn = get_db_connection()
            try:
                # Step 1: Generate predictions from recent metrics
                predictions = generate_predictions(conn, ML_SERVICE_URL)

                # Step 2: Create issues from predictions (only if we have predictions)
                issues_from_ml = 0
                if predictions > 0:
                    issues_from_ml = create_issues_from_predictions(conn)

                # Step 3: Create issues from direct metric thresholds (always run as fallback)
                issues_from_thresholds = create_issues_from_thresholds(conn)

                total_issues = issues_from_ml + issues_from_thresholds

                # Step 4: Calculate costs and savings from issues
                savings = 0
                if total_issues > 0 or predictions > 0:
                    savings = calculate_costs_and_savings(conn)

                # Step 5: Trigger remediations for open issues
                remediations = 0
                if total_issues > 0:
                    remediations = trigger_remediations(conn)

                logger.info(f"   📊 Cycle complete: {predictions} predictions, {issues_from_ml} ML issues, {issues_from_thresholds} threshold issues, {savings} cost analyses, {remediations} remediations triggered")
            finally:
                return_db_connection(conn)

            # Wait before next iteration
            logger.info(f"   ⏳ Waiting {PREDICTION_INTERVAL}s before next iteration...")
            time.sleep(PREDICTION_INTERVAL)

        except KeyboardInterrupt:
            logger.info("\n⏹️  Stopping orchestrator...")
            break

        except psycopg2.OperationalError as e:
            logger.error(f"   ❌ Database connection error: {e}")
            logger.info(f"   ⏳ Retrying in {PREDICTION_INTERVAL}s...")
            time.sleep(PREDICTION_INTERVAL)

        except requests.exceptions.RequestException as e:
            logger.error(f"   ❌ Service request error: {e}")
            logger.info(f"   ⏳ Retrying in {PREDICTION_INTERVAL}s...")
            time.sleep(PREDICTION_INTERVAL)

        except Exception as e:
            logger.error(f"   ❌ Unexpected error in iteration: {e}")
            logger.info(f"   ⏳ Retrying in {PREDICTION_INTERVAL}s...")
            time.sleep(PREDICTION_INTERVAL)


if __name__ == "__main__":
    main()
