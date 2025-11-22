"""
Automated Data Labeling from Kubernetes Events
Labels anomalies automatically from Kubernetes events and pod status
"""

import os
import sys
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import psycopg
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.error_detector import ComprehensiveErrorDetector


def get_db_connection() -> psycopg.Connection:
    """Get database connection"""
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://aura:aura@localhost:5432/aura_metrics"
    )
    return psycopg.connect(db_url)


def label_anomaly_from_events(pod_metrics: pd.DataFrame, events_data: pd.DataFrame) -> pd.DataFrame:
    """
    Automatically label anomalies from Kubernetes events
    
    Args:
        pod_metrics: DataFrame with pod metrics
        events_data: DataFrame with Kubernetes events
        
    Returns:
        DataFrame with labels added
    """
    print("🏷️  Labeling anomalies from Kubernetes events...")
    
    # Initialize error detector
    error_detector = ComprehensiveErrorDetector()
    
    # Create labels column
    pod_metrics['anomaly_type'] = 'healthy'
    pod_metrics['anomaly_severity'] = 'low'
    pod_metrics['anomaly_confidence'] = 0.0
    
    # Process each pod
    for idx, row in pod_metrics.iterrows():
        pod_name = row.get('pod_name', '')
        namespace = row.get('namespace', '')
        timestamp = row.get('timestamp', datetime.now())
        
        # Get events for this pod
        pod_events = events_data[
            (events_data['pod_name'] == pod_name) &
            (events_data['namespace'] == namespace) &
            (pd.to_datetime(events_data['timestamp']) <= pd.to_datetime(timestamp) + timedelta(minutes=5))
        ]
        
        # Convert to list of dicts for error detector
        events_list = []
        for _, event_row in pod_events.iterrows():
            events_list.append({
                'reason': event_row.get('reason', ''),
                'type': event_row.get('type', ''),
                'message': event_row.get('message', '')
            })
        
        # Detect errors
        pod_data = {
            'last_state_reason': row.get('last_state_reason', ''),
            'status': row.get('phase', ''),
            'ready': row.get('ready', True),
            'containers': [{
                'name': row.get('container_name', ''),
                'state': row.get('container_state', ''),
                'waiting_reason': row.get('last_state_reason', ''),
                'termination_reason': row.get('last_state_reason', ''),
                'restart_count': int(row.get('restarts', 0))
            }]
        }
        
        metrics_dict = {
            'cpu_utilization': row.get('cpu_utilization', 0),
            'memory_utilization': row.get('memory_utilization', 0),
            'disk_utilization': row.get('disk_utilization', 0),
            'error_rate': row.get('error_rate', 0),
            'latency_ms': row.get('latency_ms', 0),
            'network_error_rate': row.get('network_rx_errors', 0) + row.get('network_tx_errors', 0),
            'restart_count': int(row.get('restarts', 0))
        }
        
        detected_errors = error_detector.detect_all_errors(
            pod_data=pod_data,
            events=events_list,
            logs="",  # Logs not available in metrics table
            metrics=metrics_dict
        )
        
        # Label based on detected errors
        if detected_errors:
            # Get highest severity error
            highest_severity_error = max(detected_errors, key=lambda x: {
                'critical': 4, 'high': 3, 'medium': 2, 'low': 1
            }.get(x['severity'], 0))
            
            pod_metrics.at[idx, 'anomaly_type'] = highest_severity_error['type']
            pod_metrics.at[idx, 'anomaly_severity'] = highest_severity_error['severity']
            pod_metrics.at[idx, 'anomaly_confidence'] = highest_severity_error['confidence']
        else:
            # Check for metric-based anomalies
            if row.get('has_oom_kill', False):
                pod_metrics.at[idx, 'anomaly_type'] = 'oom_kill'
                pod_metrics.at[idx, 'anomaly_severity'] = 'critical'
                pod_metrics.at[idx, 'anomaly_confidence'] = 1.0
            elif row.get('has_crash_loop', False):
                pod_metrics.at[idx, 'anomaly_type'] = 'crash_loop'
                pod_metrics.at[idx, 'anomaly_severity'] = 'high'
                pod_metrics.at[idx, 'anomaly_confidence'] = 1.0
            elif row.get('has_high_cpu', False):
                pod_metrics.at[idx, 'anomaly_type'] = 'cpu_spike'
                pod_metrics.at[idx, 'anomaly_severity'] = 'high'
                pod_metrics.at[idx, 'anomaly_confidence'] = 0.8
            elif row.get('has_network_issues', False):
                pod_metrics.at[idx, 'anomaly_type'] = 'network_latency'
                pod_metrics.at[idx, 'anomaly_severity'] = 'medium'
                pod_metrics.at[idx, 'anomaly_confidence'] = 0.7
    
    # Print label distribution
    print("\n📊 Label Distribution:")
    print(pod_metrics['anomaly_type'].value_counts())
    print(f"\n   Total labeled: {len(pod_metrics)}")
    print(f"   Healthy: {len(pod_metrics[pod_metrics['anomaly_type'] == 'healthy'])}")
    print(f"   Anomalies: {len(pod_metrics[pod_metrics['anomaly_type'] != 'healthy'])}")
    
    return pod_metrics


def load_data_from_database(conn: psycopg.Connection, days_back: int = 30) -> tuple:
    """Load pod metrics and events from database"""
    print(f"📊 Loading data from database (last {days_back} days)...")
    
    # Load pod metrics
    metrics_query = f"""
        SELECT 
            pod_name, namespace, node_name, container_name, timestamp,
            cpu_usage_millicores as cpu_usage,
            memory_usage_bytes as memory_usage,
            disk_usage_bytes as disk_usage,
            network_rx_bytes, network_tx_bytes,
            network_rx_errors, network_tx_errors,
            cpu_utilization, memory_utilization,
            restarts, age,
            cpu_limit_millicores as cpu_limit,
            memory_limit_bytes as memory_limit,
            disk_limit_bytes as disk_limit,
            cpu_trend, memory_trend, restart_trend,
            has_oom_kill, has_crash_loop, has_high_cpu, has_network_issues,
            ready, phase, container_state, last_state_reason
        FROM pod_metrics
        WHERE timestamp > NOW() - INTERVAL '{days_back} days'
        ORDER BY timestamp DESC
        LIMIT 100000
    """
    
    pod_metrics = pd.read_sql(metrics_query, conn)
    print(f"   Loaded {len(pod_metrics)} pod metrics")
    
    # Load events (if events table exists)
    events_query = f"""
        SELECT 
            pod_name, namespace, timestamp, reason, type, message
        FROM events
        WHERE timestamp > NOW() - INTERVAL '{days_back} days'
        ORDER BY timestamp DESC
        LIMIT 50000
    """
    
    try:
        events = pd.read_sql(events_query, conn)
        print(f"   Loaded {len(events)} events")
    except Exception as e:
        print(f"   ⚠️  Events table not available: {e}")
        events = pd.DataFrame(columns=['pod_name', 'namespace', 'timestamp', 'reason', 'type', 'message'])
    
    return pod_metrics, events


def save_labeled_data(conn: psycopg.Connection, labeled_data: pd.DataFrame):
    """Save labeled data back to database or CSV"""
    output_path = os.getenv("LABELED_DATA_PATH", "ml/train/labeled_data.csv")
    
    print(f"💾 Saving labeled data to {output_path}...")
    
    # Create directory if needed
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Save to CSV
    labeled_data.to_csv(output_path, index=False)
    print(f"   ✅ Saved {len(labeled_data)} labeled samples to {output_path}")
    
    # Optionally save to database (create labeled_metrics table)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS labeled_metrics (
                    id SERIAL PRIMARY KEY,
                    pod_name TEXT,
                    namespace TEXT,
                    timestamp TIMESTAMPTZ,
                    anomaly_type TEXT,
                    anomaly_severity TEXT,
                    anomaly_confidence DOUBLE PRECISION,
                    features JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
            print("   ✅ Created labeled_metrics table")
    except Exception as e:
        print(f"   ⚠️  Failed to create table: {e}")


def main():
    """Main labeling function"""
    print("="*70)
    print("🏷️  AUTOMATED DATA LABELING FROM KUBERNETES EVENTS")
    print("="*70)
    
    # Get database connection
    try:
        conn = get_db_connection()
        print("✅ Connected to database")
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        return
    
    # Load data
    days_back = int(os.getenv("LABELING_DAYS_BACK", "30"))
    pod_metrics, events = load_data_from_database(conn, days_back)
    
    if len(pod_metrics) == 0:
        print("❌ No data found in database")
        return
    
    # Label data
    labeled_data = label_anomaly_from_events(pod_metrics, events)
    
    # Save labeled data
    save_labeled_data(conn, labeled_data)
    
    conn.close()
    
    print("\n✅ Labeling complete!")
    print("="*70)


if __name__ == "__main__":
    main()

