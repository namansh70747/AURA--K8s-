#!/usr/bin/env python3
"""
AURA K8s Test Data Generator
Generates synthetic pod metrics data for testing the system
"""

import os
import time
import psycopg2
import psycopg2.extensions
import random
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from psycopg2.extensions import connection

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@localhost:5432/aura_metrics")

# Test pod configurations
TEST_PODS: List[Dict[str, str]] = [
    {"name": "nginx-deployment-12345-abcde", "namespace": "default", "node": "kind-worker"},
    {"name": "redis-master-67890-fghij", "namespace": "default", "node": "kind-worker"},
    {"name": "app-backend-54321-klmno", "namespace": "production", "node": "kind-worker2"},
    {"name": "app-frontend-98765-pqrst", "namespace": "production", "node": "kind-worker2"},
    {"name": "monitoring-agent-11111-uvwxy", "namespace": "kube-system", "node": "kind-control-plane"},
]

def get_db_connection() -> "connection":
    """Get database connection with retry logic.
    
    Returns:
        psycopg2 connection object
        
    Raises:
        psycopg2.OperationalError: If connection fails after max retries
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(2)
            else:
                raise e

def generate_pod_metrics() -> Dict[str, Any]:
    """Generate synthetic pod metrics.
    
    Returns:
        Dictionary containing pod metrics with all required fields
    """
    pod = random.choice(TEST_PODS)

    # Base metrics
    cpu_usage = random.uniform(10, 500)  # millicores
    memory_usage = random.randint(50 * 1024 * 1024, 2 * 1024 * 1024 * 1024)  # bytes
    memory_limit = 2 * 1024 * 1024 * 1024  # 2GB
    cpu_limit = 1000  # millicores

    # Network metrics
    network_rx = random.randint(1000, 100000)
    network_tx = random.randint(1000, 50000)

    # Disk metrics
    disk_usage = random.randint(100 * 1024 * 1024, 5 * 1024 * 1024 * 1024)  # bytes

    # Container state
    phases = ["Running", "Pending", "Succeeded", "Failed"]
    phase = random.choices(phases, weights=[0.9, 0.05, 0.03, 0.02])[0]

    ready = phase == "Running"
    restarts = random.randint(0, 5) if phase == "Running" else random.randint(0, 10)
    age = random.randint(60, 86400)  # seconds

    # Anomalies (introduce some issues)
    has_oom_kill = random.random() < 0.02  # 2% chance
    has_crash_loop = restarts > 3 and random.random() < 0.3
    has_high_cpu = cpu_usage > 800
    has_network_issues = random.random() < 0.05

    # If OOM kill, set memory high
    if has_oom_kill:
        memory_usage = int(memory_limit * 1.1)
        phase = "Failed"
        ready = False

    # If crash loop, set restarts high
    if has_crash_loop:
        restarts = random.randint(5, 15)
        if random.random() < 0.5:
            phase = "CrashLoopBackOff"
            ready = False

    return {
        "pod_name": pod["name"],
        "namespace": pod["namespace"],
        "node_name": pod["node"],
        "container_name": f"{pod['name']}-container",
        "timestamp": datetime.now(),
        "cpu_usage_millicores": cpu_usage,
        "memory_usage_bytes": memory_usage,
        "memory_limit_bytes": memory_limit,
        "cpu_limit_millicores": cpu_limit,
        "cpu_utilization": (cpu_usage / cpu_limit) * 100,
        "memory_utilization": (memory_usage / memory_limit) * 100,
        "network_rx_bytes": network_rx,
        "network_tx_bytes": network_tx,
        "network_rx_errors": random.randint(0, 10),
        "network_tx_errors": random.randint(0, 5),
        "disk_usage_bytes": disk_usage,
        "disk_limit_bytes": 10 * 1024 * 1024 * 1024,  # 10GB
        "phase": phase,
        "ready": ready,
        "restarts": restarts,
        "age": age,
        "container_ready": ready,
        "container_state": "running" if ready else "waiting",
        "last_state_reason": "Completed" if phase == "Succeeded" else ("Error" if phase == "Failed" else ""),
        "cpu_trend": random.uniform(-10, 10),
        "memory_trend": random.uniform(-10, 10),
        "restart_trend": random.uniform(-1, 1),
        "has_oom_kill": has_oom_kill,
        "has_crash_loop": has_crash_loop,
        "has_high_cpu": has_high_cpu,
        "has_network_issues": has_network_issues,
    }

def insert_pod_metrics(conn: "connection", metrics: Dict[str, Any]) -> None:
    """Insert pod metrics into database.
    
    Args:
        conn: Database connection object
        metrics: Dictionary containing pod metrics to insert
        
    Raises:
        psycopg2.Error: If database insertion fails
    """
    query = """
    INSERT INTO pod_metrics (
        pod_name, namespace, node_name, container_name, timestamp,
        cpu_usage_millicores, memory_usage_bytes, memory_limit_bytes, cpu_limit_millicores,
        cpu_utilization, memory_utilization, network_rx_bytes, network_tx_bytes,
        network_rx_errors, network_tx_errors, disk_usage_bytes, disk_limit_bytes,
        phase, ready, restarts, age, container_ready, container_state, last_state_reason,
        cpu_trend, memory_trend, restart_trend,
        has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
    """

    values = (
        metrics["pod_name"], metrics["namespace"], metrics["node_name"], metrics["container_name"], metrics["timestamp"],
        metrics["cpu_usage_millicores"], metrics["memory_usage_bytes"], metrics["memory_limit_bytes"], metrics["cpu_limit_millicores"],
        metrics["cpu_utilization"], metrics["memory_utilization"], metrics["network_rx_bytes"], metrics["network_tx_bytes"],
        metrics["network_rx_errors"], metrics["network_tx_errors"], metrics["disk_usage_bytes"], metrics["disk_limit_bytes"],
        metrics["phase"], metrics["ready"], metrics["restarts"], metrics["age"], metrics["container_ready"], metrics["container_state"], metrics["last_state_reason"],
        metrics["cpu_trend"], metrics["memory_trend"], metrics["restart_trend"],
        metrics["has_oom_kill"], metrics["has_crash_loop"], metrics["has_high_cpu"], metrics["has_network_issues"]
    )

    with conn.cursor() as cursor:
        cursor.execute(query, values)
    conn.commit()

def generate_single_batch() -> int:
    """Generate ONE batch of current metrics for all pods.
    
    Returns:
        Number of metrics generated
        
    Raises:
        Exception: If database operation fails
    """
    try:
        conn = get_db_connection()
        
        # Generate current metrics for each pod
        for _ in range(len(TEST_PODS)):
            metrics = generate_pod_metrics()
            metrics["timestamp"] = datetime.now()
            insert_pod_metrics(conn, metrics)
        
        conn.close()
        return len(TEST_PODS)
        
    except Exception as e:
        logger.error(f"❌ Error generating batch: {e}")
        if 'conn' in locals():
            conn.close()
        raise

def generate_and_store_data() -> None:
    """Generate historical data for the last 2 hours (ONE-TIME ONLY).
    
    Raises:
        Exception: If data generation fails
    """
    logger.info("🎯 Starting AURA K8s Historical Data Generator")
    logger.info("=" * 60)
    logger.info("⚠️  This generates 2 hours of historical data - RUN ONCE!")

    try:
        conn = get_db_connection()
        logger.info("✅ Connected to database")

        # Generate data for the last 2 hours with 15-second intervals
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=2)
        interval = timedelta(seconds=15)

        current_time = start_time
        total_records = 0

        logger.info(f"📊 Generating data from {start_time} to {end_time}")

        while current_time <= end_time:
            # Generate metrics for multiple pods at each timestamp
            for _ in range(len(TEST_PODS)):
                metrics = generate_pod_metrics()
                metrics["timestamp"] = current_time
                insert_pod_metrics(conn, metrics)
                total_records += 1

            current_time += interval

            # Progress update every 100 records
            if total_records % 100 == 0:
                logger.info(f"📝 Generated {total_records} records...")

        logger.info(f"✅ Successfully generated {total_records} test records")
        logger.info("🎉 Historical data generation complete!")

    except Exception as e:
        logger.error(f"❌ Error generating test data: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()

def main() -> None:
    """Main loop for continuous data generation.
    
    Runs indefinitely, generating test data every 15 seconds.
    """
    logger.info("🔄 Starting continuous test data generation")
    logger.info("📡 Generating current metrics every 15 seconds...")
    logger.info("💡 This generates CURRENT data only, not historical batches")

    iteration = 0
    while True:
        try:
            iteration += 1
            records = generate_single_batch()
            logger.info(f"✅ Iteration {iteration}: Generated {records} current metrics")
            
            # Sleep for 15 seconds (matches collection interval)
            time.sleep(15)
            
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}")
            time.sleep(10)  # Wait before retrying

if __name__ == "__main__":
    main()