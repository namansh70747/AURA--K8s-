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

# Import config helper for service discovery
import sys
from pathlib import Path
# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
from config_helper import get_database_url, get_service_url, validate_database_url

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

# Get DATABASE_URL using config helper (environment-aware)
DATABASE_URL = get_database_url()
if not DATABASE_URL:
    if environment == "production":
        logger.error("DATABASE_URL environment variable is required in production")
        raise ValueError("DATABASE_URL environment variable is required in production")
    logger.warning("Using default DATABASE_URL (development only). Set DATABASE_URL environment variable for production.")

# Validate DATABASE_URL format
if not validate_database_url(DATABASE_URL):
    error_msg = f"Invalid DATABASE_URL format: must start with 'postgresql://' or 'postgres://'"
    if environment == "production":
        logger.error(error_msg)
        raise ValueError(error_msg)
    logger.warning(f"{error_msg}. Continuing with provided URL.")

# Get ML service URL using config helper (environment-aware)
ML_SERVICE_URL = get_service_url("ML_SERVICE", "8001")

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
    reset_timeout=10  # Reduced to 10 seconds for faster recovery
)

# Load feature names from feature_names.json (matches trained models)
FEATURE_NAMES_PATH = os.path.join(os.path.dirname(__file__), "..", "ml", "train", "models", "feature_names.json")
FEATURE_NAMES = []
try:
    if os.path.exists(FEATURE_NAMES_PATH):
        with open(FEATURE_NAMES_PATH, 'r') as f:
            FEATURE_NAMES = json.load(f)
        logger.info(f"✅ Loaded {len(FEATURE_NAMES)} features from feature_names.json")
    else:
        logger.warning(f"⚠️  {FEATURE_NAMES_PATH} not found, using fallback 13 features")
        FEATURE_NAMES = [
            "cpu_usage", "memory_usage", "disk_usage", "network_bytes_sec", "error_rate",
            "latency_ms", "restart_count", "age_minutes", "cpu_memory_ratio", "resource_pressure",
            "error_latency_product", "network_per_cpu", "is_critical"
        ]
except Exception as e:
    logger.error(f"Failed to load feature names: {e}, using fallback")
    FEATURE_NAMES = [
        "cpu_usage", "memory_usage", "disk_usage", "network_bytes_sec", "error_rate",
        "latency_ms", "restart_count", "age_minutes", "cpu_memory_ratio", "resource_pressure",
        "error_latency_product", "network_per_cpu", "is_critical"
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


def engineer_simple_features(cpu_util, mem_util, disk_pct,
                           cpu_usage_mc, mem_usage_bytes, disk_usage_bytes,
                           network_rx, network_tx, network_bytes,
                           network_rx_err, network_tx_err, err_rate,
                           restarts, age_minutes, cpu_trend, mem_trend, restart_trend,
                           has_oom, has_crash, has_high_cpu, has_network,
                           cpu_limit_mc, mem_limit_bytes, disk_limit_bytes,
                           cpu_request_mc, mem_request_bytes,
                           timestamp, historical):
    """
    Simple feature engineering matching the 13 features used in training
    """
    features = {}
    
    # Basic metrics
    features['cpu_usage'] = float(cpu_util or 0.0)
    features['memory_usage'] = float(mem_util or 0.0)
    features['disk_usage'] = float(disk_pct or 0.0)
    
    # Network (convert to bytes/sec - approximate from total bytes)
    network_bytes_sec = float((network_bytes or 0) / 60.0)  # Approximate per second
    features['network_bytes_sec'] = network_bytes_sec
    
    # Error rate
    features['error_rate'] = float(err_rate or 0.0)
    
    # Latency (approximate from error rate and network issues)
    latency_ms = 100.0 if has_network else 50.0
    if err_rate and err_rate > 10:
        latency_ms = 200.0
    features['latency_ms'] = latency_ms
    
    # Restart count
    features['restart_count'] = float(restarts or 0)
    
    # Age
    features['age_minutes'] = float(age_minutes or 0.0)
    
    # Ratios
    cpu_memory_ratio = (cpu_util / mem_util) if mem_util and mem_util > 0 else 0.0
    features['cpu_memory_ratio'] = float(cpu_memory_ratio)
    
    # Resource pressure (combination of CPU, memory, disk)
    resource_pressure = (cpu_util + mem_util + disk_pct) / 3.0
    features['resource_pressure'] = float(resource_pressure)
    
    # Error-latency product
    features['error_latency_product'] = float((err_rate or 0) * latency_ms)
    
    # Network per CPU
    network_per_cpu = (network_bytes_sec / cpu_util) if cpu_util and cpu_util > 0 else 0.0
    features['network_per_cpu'] = float(network_per_cpu)
    
    # Is critical (based on multiple indicators)
    is_critical = 1.0 if (has_oom or has_crash or cpu_util > 90 or mem_util > 90) else 0.0
    features['is_critical'] = float(is_critical)
    
    return features

def engineer_beast_features(cpu_util, mem_util, disk_pct,
                           cpu_usage_mc, mem_usage_bytes, disk_usage_bytes,
                           network_rx, network_tx, network_bytes,
                           network_rx_err, network_tx_err, err_rate,
                           restarts, age_minutes, cpu_trend, mem_trend, restart_trend,
                           has_oom, has_crash, has_high_cpu, has_network,
                           cpu_limit_mc, mem_limit_bytes, disk_limit_bytes,
                           cpu_request_mc, mem_request_bytes,
                           timestamp, historical):
    """
    Engineer all 150 features matching beast_train.py AdvancedFeatureEngineer
    This is a simplified version that works with single metric rows
    """
    import numpy as np  # Import numpy here to ensure it's available
    features = {}
    
    # ========== BASIC METRICS ==========
    cpu_usage = float(cpu_usage_mc or cpu_util or 0)
    memory_usage = float(mem_usage_bytes or mem_util or 0)
    disk_usage = float(disk_usage_bytes or disk_pct or 0)
    network_rx_val = float(network_rx or 0)
    network_tx_val = float(network_tx or 0)
    restart_count = int(restarts or 0)
    age_minutes_val = float(age_minutes or 0)
    cpu_utilization = float(cpu_util or 0)
    memory_utilization = float(mem_util or 0)
    disk_utilization = float(disk_pct or 0)
    error_rate_val = float(err_rate or 0)
    latency_ms = 0.0  # Not available in current metrics
    network_total = float(network_bytes or network_rx_val + network_tx_val or 0)
    network_errors = float((network_rx_err or 0) + (network_tx_err or 0))
    
    # ========== RESOURCE RATIOS ==========
    features['cpu_usage'] = cpu_usage
    features['memory_usage'] = memory_usage
    features['disk_usage'] = disk_usage
    features['network_rx'] = network_rx_val
    features['restart_count'] = restart_count
    features['age_minutes'] = age_minutes_val
    features['cpu_utilization'] = cpu_utilization
    features['memory_utilization'] = memory_utilization
    features['disk_utilization'] = disk_utilization
    features['network_total'] = network_total
    features['network_errors'] = network_errors
    
    # Ratios
    features['cpu_memory_ratio'] = cpu_usage / max(memory_usage, 1)
    features['cpu_disk_ratio'] = cpu_usage / max(disk_usage, 1)
    features['memory_disk_ratio'] = memory_usage / max(disk_usage, 1)
    features['cpu_util_memory_util_ratio'] = cpu_utilization / max(memory_utilization, 1)
    features['resource_pressure'] = (cpu_utilization + memory_utilization + disk_utilization) / 3.0
    features['cpu_efficiency'] = cpu_usage / max(cpu_utilization, 1)
    features['memory_efficiency'] = memory_usage / max(memory_utilization, 1)
    features['network_per_cpu'] = network_total / max(cpu_usage, 1)
    features['network_per_memory'] = network_total / max(memory_usage, 1)
    features['restart_rate'] = restart_count / max(age_minutes_val, 1)
    features['cpu_memory_product'] = cpu_usage * memory_usage
    features['utilization_sum'] = cpu_utilization + memory_utilization + disk_utilization
    
    # ========== LIMITS AND REQUESTS ==========
    cpu_limit = float(cpu_limit_mc or 1000)
    memory_limit = float(mem_limit_bytes or 2147483648)
    disk_limit = float(disk_limit_bytes or 10737418240)
    
    features['cpu_limit'] = cpu_limit
    features['memory_limit'] = memory_limit
    features['disk_limit'] = disk_limit
    features['cpu_headroom'] = max(0, cpu_limit - cpu_usage)
    features['memory_headroom'] = max(0, memory_limit - memory_usage)
    features['disk_headroom'] = max(0, disk_limit - disk_usage)
    features['cpu_headroom_pct'] = (features['cpu_headroom'] / max(cpu_limit, 1)) * 100
    features['memory_headroom_pct'] = (features['memory_headroom'] / max(memory_limit, 1)) * 100
    features['disk_headroom_pct'] = (features['disk_headroom'] / max(disk_limit, 1)) * 100
    features['cpu_usage_vs_limit'] = cpu_usage / max(cpu_limit, 1)
    features['memory_usage_vs_limit'] = memory_usage / max(memory_limit, 1)
    features['disk_usage_vs_limit'] = disk_usage / max(disk_limit, 1)
    features['total_usage'] = cpu_usage + memory_usage + disk_usage
    features['total_usage_vs_limit'] = features['total_usage'] / max(cpu_limit + memory_limit + disk_limit, 1)
    
    # Resource balance (std of utilizations)
    util_values = [cpu_utilization, memory_utilization, disk_utilization]
    features['resource_balance'] = float(np.std(util_values)) if len(util_values) > 1 else 0.0
    
    # ========== TRENDS ==========
    cpu_trend_val = float(cpu_trend or 0)
    memory_trend_val = float(mem_trend or 0)
    restart_trend_val = float(restart_trend or 0)
    
    features['memory_trend'] = memory_trend_val
    features['restart_trend'] = restart_trend_val
    features['memory_trend_abs'] = abs(memory_trend_val)
    features['restart_trend_abs'] = abs(restart_trend_val)
    features['trend_magnitude'] = math.sqrt(cpu_trend_val**2 + memory_trend_val**2)
    features['trend_acceleration'] = cpu_trend_val * memory_trend_val
    features['cpu_trend_normalized'] = cpu_trend_val / max(cpu_usage, 1)
    features['trend_consistency'] = 1.0 if (cpu_trend_val > 0 and memory_trend_val > 0) or (cpu_trend_val < 0 and memory_trend_val < 0) else 0.0
    
    # ========== ROLLING STATISTICS (from historical data) ==========
    if historical and len(historical) > 0:
        cpu_vals = [float(h[0] or 0) for h in historical]
        mem_vals = [float(h[1] or 0) for h in historical]
        for window in [5, 10, 15, 30, 60]:
            if len(cpu_vals) >= window:
                window_cpu = cpu_vals[:window]
                window_mem = mem_vals[:window]
                features[f'cpu_rolling_mean_{window}'] = float(np.mean(window_cpu))
                features[f'cpu_rolling_std_{window}'] = float(np.std(window_cpu)) if len(window_cpu) > 1 else 0.0
                features[f'memory_rolling_mean_{window}'] = float(np.mean(window_mem))
                features[f'memory_rolling_std_{window}'] = float(np.std(window_mem)) if len(window_mem) > 1 else 0.0
            else:
                features[f'cpu_rolling_mean_{window}'] = cpu_utilization
                features[f'cpu_rolling_std_{window}'] = 0.0
                features[f'memory_rolling_mean_{window}'] = memory_utilization
                features[f'memory_rolling_std_{window}'] = 0.0
    else:
        for window in [5, 10, 15, 30, 60]:
            features[f'cpu_rolling_mean_{window}'] = cpu_utilization
            features[f'cpu_rolling_std_{window}'] = 0.0
            features[f'memory_rolling_mean_{window}'] = memory_utilization
            features[f'memory_rolling_std_{window}'] = 0.0
    
    # ========== ANOMALY INDICATORS ==========
    features['is_network_issue'] = 1.0 if has_network else 0.0
    features['is_ready'] = 1.0  # Assume ready if we have metrics
    features['is_critical'] = 1.0 if (cpu_utilization > 80 or memory_utilization > 80 or disk_utilization > 80) else 0.0
    features['is_warning'] = 1.0 if (cpu_utilization > 60 or memory_utilization > 60 or disk_utilization > 60) else 0.0
    features['oom_risk'] = 1.0 if memory_utilization > 90 else 0.0
    features['cpu_throttle_risk'] = 1.0 if cpu_utilization > 90 else 0.0
    features['instability_score'] = restart_count * features['restart_rate']
    features['degradation_score'] = (abs(cpu_trend_val) + abs(memory_trend_val)) / 2.0
    
    # ========== STATISTICAL FEATURES (from historical) ==========
    if historical and len(historical) > 0:
        cpu_hist = [float(h[0] or 0) for h in historical]
        mem_hist = [float(h[1] or 0) for h in historical]
        disk_hist = [float(h[2] or 0) for h in historical] if len(historical[0]) > 2 else [disk_utilization]
        
        for p in [10, 25, 50, 75, 90, 95, 99]:
            features[f'memory_percentile_{p}'] = float(np.percentile(mem_hist, p)) if len(mem_hist) > 0 else memory_utilization
            features[f'disk_percentile_{p}'] = float(np.percentile(disk_hist, p)) if len(disk_hist) > 0 else disk_utilization
            if p in [50, 75, 90, 95, 99]:
                features[f'cpu_percentile_{p}'] = float(np.percentile(cpu_hist, p)) if len(cpu_hist) > 0 else cpu_utilization
        
        # Z-scores
        cpu_mean = np.mean(cpu_hist) if len(cpu_hist) > 0 else cpu_utilization
        cpu_std = np.std(cpu_hist) if len(cpu_hist) > 1 else 1.0
        mem_mean = np.mean(mem_hist) if len(mem_hist) > 0 else memory_utilization
        mem_std = np.std(mem_hist) if len(mem_hist) > 1 else 1.0
        disk_mean = np.mean(disk_hist) if len(disk_hist) > 0 else disk_utilization
        disk_std = np.std(disk_hist) if len(disk_hist) > 1 else 1.0
        
        features['cpu_zscore'] = (cpu_utilization - cpu_mean) / max(cpu_std, 1e-6)
        features['memory_zscore'] = (memory_utilization - mem_mean) / max(mem_std, 1e-6)
        features['disk_zscore'] = (disk_utilization - disk_mean) / max(disk_std, 1e-6)
        
        # Outliers
        cpu_q1, cpu_q3 = np.percentile(cpu_hist, [25, 75]) if len(cpu_hist) > 0 else (cpu_utilization, cpu_utilization)
        cpu_iqr = cpu_q3 - cpu_q1
        mem_q1, mem_q3 = np.percentile(mem_hist, [25, 75]) if len(mem_hist) > 0 else (memory_utilization, memory_utilization)
        mem_iqr = mem_q3 - mem_q1
        
        features['cpu_is_outlier_iqr'] = 1.0 if (cpu_utilization < cpu_q1 - 1.5*cpu_iqr or cpu_utilization > cpu_q3 + 1.5*cpu_iqr) else 0.0
        features['memory_is_outlier_iqr'] = 1.0 if (memory_utilization < mem_q1 - 1.5*mem_iqr or memory_utilization > mem_q3 + 1.5*mem_iqr) else 0.0
        features['cpu_is_outlier_zscore'] = 1.0 if abs(features['cpu_zscore']) > 3 else 0.0
        features['memory_is_outlier_zscore'] = 1.0 if abs(features['memory_zscore']) > 3 else 0.0
        
        # Advanced stats
        try:
            from scipy import stats
            features['cpu_skewness'] = float(stats.skew(cpu_hist)) if len(cpu_hist) > 2 else 0.0
            features['memory_skewness'] = float(stats.skew(mem_hist)) if len(mem_hist) > 2 else 0.0
            features['cpu_kurtosis'] = float(stats.kurtosis(cpu_hist)) if len(cpu_hist) > 3 else 0.0
            features['memory_kurtosis'] = float(stats.kurtosis(mem_hist)) if len(mem_hist) > 3 else 0.0
        except:
            features['cpu_skewness'] = 0.0
            features['memory_skewness'] = 0.0
            features['cpu_kurtosis'] = 0.0
            features['memory_kurtosis'] = 0.0
        
        features['cpu_cv'] = (cpu_std / max(cpu_mean, 1e-6))
        features['memory_cv'] = (mem_std / max(mem_mean, 1e-6))
        features['disk_cv'] = (disk_std / max(disk_mean, 1e-6))
        features['cpu_range'] = float(np.max(cpu_hist) - np.min(cpu_hist)) if len(cpu_hist) > 0 else 0.0
        features['memory_range'] = float(np.max(mem_hist) - np.min(mem_hist)) if len(mem_hist) > 0 else 0.0
        features['cpu_iqr'] = float(cpu_iqr)
        features['memory_iqr'] = float(mem_iqr)
        
        # Correlations
        if len(cpu_hist) > 1 and len(mem_hist) > 1:
            corr = np.corrcoef(cpu_hist, mem_hist)[0, 1] if len(cpu_hist) == len(mem_hist) else 0.0
            features['cpu_memory_correlation'] = float(corr) if not np.isnan(corr) else 0.0
            if len(disk_hist) > 1 and len(disk_hist) == len(mem_hist):
                corr2 = np.corrcoef(mem_hist, disk_hist)[0, 1]
                features['memory_disk_correlation'] = float(corr2) if not np.isnan(corr2) else 0.0
            else:
                features['memory_disk_correlation'] = 0.0
        else:
            features['cpu_memory_correlation'] = 0.0
            features['memory_disk_correlation'] = 0.0
    else:
        # No historical data - use defaults
        for p in [10, 25, 50, 75, 90, 95, 99]:
            features[f'memory_percentile_{p}'] = memory_utilization
            features[f'disk_percentile_{p}'] = disk_utilization
            if p in [50, 75, 90, 95, 99]:
                features[f'cpu_percentile_{p}'] = cpu_utilization
        features['cpu_zscore'] = 0.0
        features['memory_zscore'] = 0.0
        features['disk_zscore'] = 0.0
        features['cpu_is_outlier_iqr'] = 0.0
        features['memory_is_outlier_iqr'] = 0.0
        features['cpu_is_outlier_zscore'] = 0.0
        features['memory_is_outlier_zscore'] = 0.0
        features['cpu_skewness'] = 0.0
        features['memory_skewness'] = 0.0
        features['cpu_kurtosis'] = 0.0
        features['memory_kurtosis'] = 0.0
        features['cpu_cv'] = 0.0
        features['memory_cv'] = 0.0
        features['disk_cv'] = 0.0
        features['cpu_range'] = 0.0
        features['memory_range'] = 0.0
        features['cpu_iqr'] = 0.0
        features['memory_iqr'] = 0.0
        features['cpu_memory_correlation'] = 0.0
        features['memory_disk_correlation'] = 0.0
    
    # ========== NETWORK FEATURES ==========
    features['network_errors_total'] = network_errors
    features['network_error_rate'] = network_errors / max(network_total, 1)
    features['network_rx_error_rate'] = float(network_rx_err or 0) / max(network_rx_val, 1)
    features['network_tx_error_rate'] = float(network_tx_err or 0) / max(network_tx_val, 1)
    features['network_throughput'] = network_total / max(age_minutes_val * 60, 1)
    features['network_bandwidth_utilization'] = network_total / max(cpu_usage, 1)
    
    # ========== INTERACTION FEATURES ==========
    features['cpu_memory_interaction'] = cpu_utilization * memory_utilization
    features['cpu_disk_interaction'] = cpu_utilization * disk_utilization
    features['memory_disk_interaction'] = memory_utilization * disk_utilization
    features['cpu_restart_interaction'] = cpu_utilization * restart_count
    features['memory_restart_interaction'] = memory_utilization * restart_count
    features['network_cpu_interaction'] = network_total * cpu_utilization
    features['network_memory_interaction'] = network_total * memory_utilization
    features['age_cpu_interaction'] = age_minutes_val * cpu_utilization
    features['age_memory_interaction'] = age_minutes_val * memory_utilization
    features['trend_cpu_interaction'] = cpu_trend_val * cpu_utilization
    features['trend_memory_interaction'] = memory_trend_val * memory_utilization
    
    # ========== POLYNOMIAL FEATURES ==========
    features['cpu_utilization_squared'] = cpu_utilization ** 2
    features['memory_utilization_squared'] = memory_utilization ** 2
    features['disk_utilization_squared'] = disk_utilization ** 2
    features['cpu_utilization_cubed'] = cpu_utilization ** 3
    features['memory_utilization_cubed'] = memory_utilization ** 3
    features['restart_count_squared'] = restart_count ** 2
    features['age_minutes_squared'] = age_minutes_val ** 2
    features['resource_pressure_squared'] = features['resource_pressure'] ** 2
    
    # ========== FFT FEATURES (simplified) ==========
    if historical and len(historical) > 10:
        try:
            cpu_fft_vals = [float(h[0] or 0) for h in historical[:100]]
            cpu_fft = np.abs(np.fft.fft(cpu_fft_vals))
            features['cpu_fft_max'] = float(np.max(cpu_fft)) if len(cpu_fft) > 0 else 0.0
            features['cpu_fft_std'] = float(np.std(cpu_fft)) if len(cpu_fft) > 1 else 0.0
            if len(cpu_fft) > 2:
                dominant_idx = np.argmax(cpu_fft[1:len(cpu_fft)//2]) + 1
                features['cpu_dominant_frequency'] = float(dominant_idx)
            else:
                features['cpu_dominant_frequency'] = 0.0
            
            mem_fft_vals = [float(h[1] or 0) for h in historical[:100]]
            mem_fft = np.abs(np.fft.fft(mem_fft_vals))
            features['memory_fft_mean'] = float(np.mean(mem_fft)) if len(mem_fft) > 0 else 0.0
        except:
            features['cpu_fft_max'] = 0.0
            features['cpu_fft_std'] = 0.0
            features['cpu_dominant_frequency'] = 0.0
            features['memory_fft_mean'] = 0.0
    else:
        features['cpu_fft_max'] = 0.0
        features['cpu_fft_std'] = 0.0
        features['cpu_dominant_frequency'] = 0.0
        features['memory_fft_mean'] = 0.0
    
    # ========== MOVING AVERAGES ==========
    if historical and len(historical) > 0:
        cpu_hist = [float(h[0] or 0) for h in historical]
        mem_hist = [float(h[1] or 0) for h in historical]
        for window in [3, 5, 10]:
            if len(cpu_hist) >= window:
                features[f'cpu_sma_{window}'] = float(np.mean(cpu_hist[:window]))
                features[f'memory_sma_{window}'] = float(np.mean(mem_hist[:window]))
            else:
                features[f'cpu_sma_{window}'] = cpu_utilization
                features[f'memory_sma_{window}'] = memory_utilization
    else:
        for window in [3, 5, 10]:
            features[f'cpu_sma_{window}'] = cpu_utilization
            features[f'memory_sma_{window}'] = memory_utilization
    
    # EMA and ROC (simplified)
    features['cpu_ema'] = cpu_utilization  # Simplified - would need historical for real EMA
    features['memory_ema'] = memory_utilization
    features['cpu_roc'] = cpu_trend_val / max(cpu_utilization, 1)  # Simplified ROC
    features['memory_roc'] = memory_trend_val / max(memory_utilization, 1)
    
    # ========== CATEGORICAL ENCODINGS ==========
    features['phase_encoded'] = 1.0  # Running
    features['container_state_encoded'] = 1.0  # Running
    
    # Ensure all values are finite
    for key in list(features.keys()):
        if not math.isfinite(features[key]):
            features[key] = 0.0
    
    return features


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
        # Query with all fields needed for 150-feature beast_train.py engineering
        cur.execute("""
            SELECT DISTINCT ON (pm.pod_name, pm.namespace)
                pm.pod_name, pm.namespace, pm.timestamp,
                pm.cpu_utilization, pm.memory_utilization,
                COALESCE(pm.disk_usage_bytes::float / NULLIF(pm.disk_limit_bytes, 0) * 100, 0) as disk_usage_percent,
                pm.cpu_usage_millicores, pm.memory_usage_bytes, pm.disk_usage_bytes,
                pm.network_rx_bytes, pm.network_tx_bytes,
                COALESCE(pm.network_rx_bytes + pm.network_tx_bytes, 0) as network_bytes,
                pm.network_rx_errors, pm.network_tx_errors,
                COALESCE(pm.network_rx_errors + pm.network_tx_errors, 0) as error_rate,
                pm.restarts,
                EXTRACT(EPOCH FROM (NOW() - pm.timestamp)) / 60.0 as age_minutes,
                pm.cpu_trend, pm.memory_trend, pm.restart_trend,
                pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues,
                pm.cpu_limit_millicores, pm.memory_limit_bytes, pm.disk_limit_bytes,
                0 as cpu_request_millicores, 0 as memory_request_bytes
            FROM pod_metrics pm
            LEFT JOIN ml_predictions mp ON 
                pm.pod_name = mp.pod_name 
                AND pm.namespace = mp.namespace 
                AND pm.timestamp = mp.timestamp
            WHERE pm.timestamp > NOW() - INTERVAL '1 hour'
                AND mp.timestamp IS NULL
                AND pm.namespace NOT IN ('kube-system', 'kube-public', 'kube-node-lease', 'local-path-storage')
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
                    # Validate metric tuple - handle variable field count
                    if len(metric) < 24:
                        logger.warning(f"Metric tuple has {len(metric)} fields, expected at least 24. Skipping metric.")
                        continue
                    
                    # Unpack with proper field count handling
                    try:
                        pod_name = metric[0]
                        namespace = metric[1]
                        timestamp = metric[2]
                        cpu_util = metric[3]
                        mem_util = metric[4]
                        disk_pct = metric[5]
                        cpu_usage_mc = metric[6]
                        mem_usage_bytes = metric[7]
                        disk_usage_bytes = metric[8]
                        network_rx = metric[9]
                        network_tx = metric[10]
                        network_bytes = metric[11]
                        network_rx_err = metric[12]
                        network_tx_err = metric[13]
                        err_rate = metric[14]
                        restarts = metric[15]
                        age_minutes = metric[16]
                        cpu_trend = metric[17]
                        mem_trend = metric[18]
                        restart_trend = metric[19]
                        has_oom = metric[20]
                        has_crash = metric[21]
                        has_high_cpu = metric[22]
                        has_network = metric[23]
                        cpu_limit_mc = metric[24] if len(metric) > 24 else None
                        mem_limit_bytes = metric[25] if len(metric) > 25 else None
                        disk_limit_bytes = metric[26] if len(metric) > 26 else None
                        cpu_request_mc = metric[27] if len(metric) > 27 else 0
                        mem_request_bytes = metric[28] if len(metric) > 28 else 0
                    except (IndexError, TypeError) as e:
                        logger.warning(f"Failed to unpack metric fields: {e}. Skipping metric.")
                        continue

                try:
                    # Get historical metrics for rolling features (needed for beast_train.py)
                    cur.execute("""
                        SELECT cpu_utilization, memory_utilization, disk_usage_bytes, 
                               network_rx_bytes, network_tx_bytes, restarts, timestamp
                        FROM pod_metrics
                        WHERE pod_name = %s AND namespace = %s
                        ORDER BY timestamp DESC
                        LIMIT 60
                    """, (pod_name, namespace))
                    historical = cur.fetchall()
                    
                    # Engineer features matching the 13 features used in model training
                    features = engineer_simple_features(
                        cpu_util, mem_util, disk_pct,
                        cpu_usage_mc, mem_usage_bytes, disk_usage_bytes,
                        network_rx, network_tx, network_bytes,
                        network_rx_err, network_tx_err, err_rate,
                        restarts, age_minutes, cpu_trend, mem_trend, restart_trend,
                        has_oom, has_crash, has_high_cpu, has_network,
                        cpu_limit_mc, mem_limit_bytes, disk_limit_bytes,
                        cpu_request_mc, mem_request_bytes,
                        timestamp, historical
                    )
                    
                    # Ensure all required features are present (fill missing with 0)
                    for feat_name in FEATURE_NAMES:
                        if feat_name not in features:
                            features[feat_name] = 0.0
                    
                    # Build feature dict in exact order of FEATURE_NAMES
                    ordered_features = {name: features.get(name, 0.0) for name in FEATURE_NAMES}
                    
                    # Validate feature count matches
                    if len(ordered_features) != len(FEATURE_NAMES):
                        logger.error(f"Feature count mismatch: {len(ordered_features)} vs {len(FEATURE_NAMES)}")
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
                            json={"features": ordered_features},
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
                        if has_crash or (restarts and restarts > 3):
                            predicted_issue = 'crash_loop'
                            confidence = 0.95  # High confidence for direct metric detection
                            logger.info(f"      Overriding ML prediction for {pod_name}: crash loop detected (restarts: {restarts})")
                        elif has_oom:
                            predicted_issue = 'OOMKilled'
                            confidence = 0.95
                            logger.info(f"      Overriding ML prediction for {pod_name}: OOM kill detected in metrics")
                        elif mem_util and mem_util > 70:
                            predicted_issue = 'high_memory'
                            confidence = max(confidence, 0.90)
                            logger.info(f"      Overriding ML prediction for {pod_name}: high memory detected ({mem_util:.1f}%)")
                        elif has_high_cpu or (cpu_util and cpu_util > 50):
                            predicted_issue = 'high_cpu'
                            confidence = max(confidence, 0.85)
                            logger.info(f"      Overriding ML prediction for {pod_name}: high CPU detected ({cpu_util:.1f}%)")
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
                            json.dumps(list(ordered_features.keys())[:10]), explanation,  # Top 10 features
                            'pod', pod_name, predicted_issue,
                            1.0 if predicted_issue != 'healthy' else 0.0,
                            prediction.get('model_used', 'ensemble'),
                            json.dumps(ordered_features),
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
                AND (
                    (mp.predicted_issue != 'healthy' AND mp.predicted_issue IS NOT NULL) 
                    OR mp.is_anomaly = 1 
                    OR (mp.predicted_issue = 'healthy' AND mp.confidence > 0.6 AND (pm.has_oom_kill = true OR pm.has_crash_loop = true OR pm.has_high_cpu = true OR pm.cpu_utilization > 50 OR pm.memory_utilization > 70))
                )
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

            # Override issue_type if it's 'healthy' but we have metric flags indicating an anomaly
            # This handles cases where ML model returns 'healthy' but is_anomaly = 1
            if issue_type == 'healthy':
                if has_crash:
                    issue_type = 'crash_loop'
                    confidence = max(confidence, 0.95)
                    logger.info(f"      Overriding 'healthy' issue type for {pod_name}: crash loop detected")
                elif has_oom:
                    issue_type = 'OOMKilled'
                    confidence = max(confidence, 0.95)
                    logger.info(f"      Overriding 'healthy' issue type for {pod_name}: OOM kill detected")
                elif mem_util and mem_util > 70:
                    issue_type = 'high_memory'
                    confidence = max(confidence, 0.90)
                    logger.info(f"      Overriding 'healthy' issue type for {pod_name}: high memory ({mem_util:.1f}%)")
                elif has_high_cpu or (cpu_util and cpu_util > 50):
                    issue_type = 'high_cpu'
                    confidence = max(confidence, 0.85)
                    logger.info(f"      Overriding 'healthy' issue type for {pod_name}: high CPU ({cpu_util:.1f}%)")
                elif has_network:
                    issue_type = 'NetworkErrors'
                    confidence = max(confidence, 0.80)
                    logger.info(f"      Overriding 'healthy' issue type for {pod_name}: network issues detected")
                else:
                    # If still 'healthy' with no metric flags, skip creating issue
                    logger.debug(f"      Skipping issue creation for {pod_name}: predicted 'healthy' with no metric anomalies")
                    continue

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
                
                # Link early warnings to issues after creation
                for (issue_id, pod_name, namespace, issue_type, severity, _, _, _, _, _) in issue_values:
                    logger.info(f"      🔴 Issue created: {namespace}/{pod_name} - {issue_type} ({severity})")
                    # Link any active warnings for this pod/namespace to the new issue
                    try:
                        cur.execute("""
                            UPDATE early_warnings
                            SET issue_id = %s
                            WHERE pod_name = %s
                                AND namespace = %s
                                AND (expires_at IS NULL OR expires_at > NOW())
                                AND (acknowledged IS NULL OR acknowledged = FALSE)
                                AND issue_id IS NULL
                        """, (issue_id, pod_name, namespace))
                        warnings_linked = cur.rowcount
                        if warnings_linked > 0:
                            logger.debug(f"      Linked {warnings_linked} warning(s) to issue {issue_id}")
                        conn.commit()
                    except psycopg2.Error as link_err:
                        logger.warning(f"Failed to link warnings to issue {issue_id}: {link_err}")
                        conn.rollback()
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
                AND pm.namespace NOT IN ('kube-system', 'kube-public', 'kube-node-lease', 'local-path-storage')
                AND (pm.namespace != 'default' OR (pm.namespace = 'default' AND pm.pod_name LIKE 'test-%'))
                AND (
                    pm.cpu_utilization > 80 OR
                    pm.memory_utilization > 70 OR
                    pm.has_oom_kill = true OR
                    pm.has_crash_loop = true OR
                    pm.restarts > 2
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
            elif mem_util and mem_util > 70:
                issue_type = "high_memory"
                severity = "high"
                description_parts.append(f"Memory utilization: {mem_util:.1f}%")
            elif cpu_util and cpu_util > 50:
                issue_type = "high_cpu"
                severity = "high"
                description_parts.append(f"CPU utilization: {cpu_util:.1f}%")
            elif has_crash:
                issue_type = "crash_loop"
                severity = "critical"
                description_parts.append("Pod is in crash loop")
            elif restarts and restarts > 2:
                issue_type = "frequent_restarts"
                severity = "critical" if restarts > 5 else "medium"
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
                
                # Link early warnings to issues after creation
                for (issue_id, pod_name, namespace, issue_type, severity, _, _, _, _, _) in issue_values:
                    logger.info(f"      🔴 Threshold issue created: {namespace}/{pod_name} - {issue_type} ({severity})")
                    # Link any active warnings for this pod/namespace to the new issue
                    try:
                        cur.execute("""
                            UPDATE early_warnings
                            SET issue_id = %s
                            WHERE pod_name = %s
                                AND namespace = %s
                                AND (expires_at IS NULL OR expires_at > NOW())
                                AND (acknowledged IS NULL OR acknowledged = FALSE)
                                AND issue_id IS NULL
                        """, (issue_id, pod_name, namespace))
                        warnings_linked = cur.rowcount
                        if warnings_linked > 0:
                            logger.debug(f"      Linked {warnings_linked} warning(s) to issue {issue_id}")
                        conn.commit()
                    except psycopg2.Error as link_err:
                        logger.warning(f"Failed to link warnings to issue {issue_id}: {link_err}")
                        conn.rollback()
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

                # Step 2: Create issues from predictions (always check existing predictions)
                # This ensures issues are created even if no new predictions were generated this cycle
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
