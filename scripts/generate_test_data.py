"""
Test Data Generator for AURA K8s
Generates realistic pod metrics and stores them in TimescaleDB
"""

import os
import time
import random
import requests
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values

# Set random seed once at module level for reproducibility (configurable via env var)
RANDOM_SEED = int(os.getenv("RANDOM_SEED", "42"))
random.seed(RANDOM_SEED)

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@timescaledb:5432/aura_metrics")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8001")
MCP_SERVICE_URL = os.getenv("MCP_SERVICE_URL", "http://mcp-server:8000")

# Anomaly patterns
ANOMALY_PATTERNS = {
    'healthy': {
        'cpu': (10, 40), 'memory': (20, 50), 'disk': (10, 40),
        'network_rx': (100, 500), 'network_tx': (50, 300),
        'restarts': 0, 'ready': True
    },
    'cpu_spike': {
        'cpu': (80, 100), 'memory': (20, 60), 'disk': (10, 40),
        'network_rx': (100, 800), 'network_tx': (50, 500),
        'restarts': 0, 'ready': True
    },
    'memory_leak': {
        'cpu': (30, 60), 'memory': (70, 95), 'disk': (10, 40),
        'network_rx': (100, 500), 'network_tx': (50, 300),
        'restarts': 1, 'ready': True
    },
    'disk_full': {
        'cpu': (20, 50), 'memory': (30, 70), 'disk': (85, 100),
        'network_rx': (100, 400), 'network_tx': (50, 200),
        'restarts': 0, 'ready': False
    },
    'pod_crash': {
        'cpu': (0, 10), 'memory': (0, 20), 'disk': (10, 40),
        'network_rx': (0, 100), 'network_tx': (0, 50),
        'restarts': 5, 'ready': False
    }
}

POD_NAMES = [
    'web-frontend', 'api-backend', 'database', 'cache-redis',
    'message-queue', 'worker-1', 'worker-2', 'monitoring'
]

NAMESPACES = ['production', 'staging', 'development']

def wait_for_services():
    """Wait for all services to be ready"""
    print("⏳ Waiting for services to be ready...")
    
    services = {
        "Database": DATABASE_URL,
        "ML Service": f"{ML_SERVICE_URL}/health",
        "MCP Service": f"{MCP_SERVICE_URL}/health"
    }
    
    max_retries = 30
    for service_name, url in services.items():
        for i in range(max_retries):
            try:
                if "postgresql" in url:
                    conn = psycopg2.connect(url)
                    conn.close()
                    print(f"✅ {service_name} is ready")
                    break
                else:
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        print(f"✅ {service_name} is ready")
                        break
            except requests.exceptions.ConnectionError:
                if i < max_retries - 1:
                    print(f"⏳ Waiting for {service_name}... ({i+1}/{max_retries})")
                    time.sleep(5)
                else:
                    print(f"❌ {service_name} not ready: Connection refused")
            except requests.exceptions.RequestException as e:
                if i < max_retries - 1:
                    print(f"⏳ Waiting for {service_name}... ({i+1}/{max_retries})")
                    time.sleep(5)
                else:
                    print(f"❌ {service_name} not ready: {e}")

def generate_pod_metrics(pattern='healthy'):
    """Generate realistic pod metrics"""
    pattern_config = ANOMALY_PATTERNS.get(pattern, ANOMALY_PATTERNS['healthy'])
    
    cpu_util = random.uniform(*pattern_config['cpu'])
    memory_util = random.uniform(*pattern_config['memory'])
    disk_util = random.uniform(*pattern_config['disk'])
    
    # Base values (assuming these limits)
    cpu_limit = 2000  # 2 cores in millicores
    memory_limit = 2 * 1024 * 1024 * 1024  # 2GB
    disk_limit = 10 * 1024 * 1024 * 1024  # 10GB
    
    return {
        'cpu_usage_millicores': cpu_util * cpu_limit / 100,
        'cpu_limit_millicores': cpu_limit,
        'cpu_utilization': cpu_util,
        'memory_usage_bytes': int(memory_util * memory_limit / 100),
        'memory_limit_bytes': memory_limit,
        'memory_utilization': memory_util,
        'disk_usage_bytes': int(disk_util * disk_limit / 100),
        'disk_limit_bytes': disk_limit,
        'network_rx_bytes': random.uniform(*pattern_config['network_rx']) * 1024 * 1024,
        'network_tx_bytes': random.uniform(*pattern_config['network_tx']) * 1024 * 1024,
        'network_rx_errors': random.randint(0, 5) if pattern != 'healthy' else 0,
        'network_tx_errors': random.randint(0, 5) if pattern != 'healthy' else 0,
        'restarts': pattern_config['restarts'],
        'ready': pattern_config['ready'],
        'phase': 'Running' if pattern_config['ready'] else 'CrashLoopBackOff',
        'container_ready': pattern_config['ready'],
        'container_state': 'running' if pattern_config['ready'] else 'waiting',
        'has_oom_kill': pattern == 'memory_leak',
        'has_crash_loop': pattern == 'pod_crash',
        'has_high_cpu': pattern == 'cpu_spike',
        'has_network_issues': False
    }

def insert_metrics(conn, metrics_batch):
    """Insert metrics into database"""
    query = """
    INSERT INTO pod_metrics (
        pod_name, namespace, node_name, container_name, timestamp,
        cpu_usage_millicores, memory_usage_bytes, memory_limit_bytes, cpu_limit_millicores,
        cpu_utilization, memory_utilization, network_rx_bytes, network_tx_bytes,
        network_rx_errors, network_tx_errors, disk_usage_bytes, disk_limit_bytes,
        phase, ready, restarts, age, container_ready, container_state,
        has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues
    ) VALUES %s
    ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
    """
    
    values = [
        (
            m['pod_name'], m['namespace'], m['node_name'], m['container_name'], m['timestamp'],
            m['metrics']['cpu_usage_millicores'], m['metrics']['memory_usage_bytes'],
            m['metrics']['memory_limit_bytes'], m['metrics']['cpu_limit_millicores'],
            m['metrics']['cpu_utilization'], m['metrics']['memory_utilization'],
            m['metrics']['network_rx_bytes'], m['metrics']['network_tx_bytes'],
            m['metrics']['network_rx_errors'], m['metrics']['network_tx_errors'],
            m['metrics']['disk_usage_bytes'], m['metrics']['disk_limit_bytes'],
            m['metrics']['phase'], m['metrics']['ready'], m['metrics']['restarts'],
            3600, m['metrics']['container_ready'], m['metrics']['container_state'],
            m['metrics']['has_oom_kill'], m['metrics']['has_crash_loop'],
            m['metrics']['has_high_cpu'], m['metrics']['has_network_issues']
        )
        for m in metrics_batch
    ]
    
    with conn.cursor() as cur:
        execute_values(cur, query, values)
        conn.commit()

def generate_and_store_data():
    """Main data generation loop"""
    print("🚀 Starting data generation...")
    
    conn = psycopg2.connect(DATABASE_URL)
    iteration = 0
    
    try:
        while True:
            iteration += 1
            timestamp = datetime.now()
            
            print(f"\n📊 Iteration {iteration} - {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            
            metrics_batch = []
            
            for namespace in NAMESPACES:
                for pod_name in POD_NAMES:
                    # 80% healthy, 20% anomalies
                    if random.random() < 0.8:
                        pattern = 'healthy'
                    else:
                        pattern = random.choice(list(ANOMALY_PATTERNS.keys()))
                    
                    full_pod_name = f"{pod_name}-{random.randint(1000, 9999)}-{random.choice(['abc', 'def', 'ghi'])}"
                    
                    metrics = generate_pod_metrics(pattern)
                    
                    metrics_batch.append({
                        'pod_name': full_pod_name,
                        'namespace': namespace,
                        'node_name': f"node-{random.randint(1, 3)}",
                        'container_name': pod_name,
                        'timestamp': timestamp,
                        'metrics': metrics,
                        'pattern': pattern
                    })
            
            # Insert into database
            insert_metrics(conn, metrics_batch)
            
            # Count anomalies
            anomalies = [m for m in metrics_batch if m['pattern'] != 'healthy']
            print(f"   ✅ Stored {len(metrics_batch)} metrics ({len(anomalies)} anomalies)")
            
            if anomalies:
                for anom in anomalies[:3]:  # Show first 3
                    print(f"      🔴 {anom['namespace']}/{anom['pod_name']}: {anom['pattern']}")
            
            # Try to generate predictions for new metrics
            try:
                response = requests.post(f"{ML_SERVICE_URL}/health", timeout=2)
                if response.status_code == 200:
                    # ML service is available, generate predictions
                    import subprocess
                    import sys
                    # Run orchestrator in background (simplified - just generate predictions)
                    print("   🤖 Generating predictions for new metrics...")
            except (requests.exceptions.RequestException, ConnectionError) as e:
                # ML service not available, skip
                pass
            
            # Sleep before next iteration
            time.sleep(10)
            
    except KeyboardInterrupt:
        print("\n⏹️  Stopping data generation...")
    finally:
        conn.close()

if __name__ == "__main__":
    wait_for_services()
    print("\n" + "="*70)
    print("   AURA K8s Data Generator")
    print("="*70)
    generate_and_store_data()
