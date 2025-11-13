#!/usr/bin/env python3
"""
Simple Remediation Service for AURA K8s
Detects anomalies from ML predictions and creates remediation actions
"""

import os
import psycopg2
import time
import json
from datetime import datetime
import uuid

DB_URL = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@localhost:5432/aura_metrics")

# Remediation strategies
REMEDIATION_STRATEGIES = {
    'cpu_spike': {
        'action': 'scale_horizontal',
        'details': 'Increase replica count to distribute CPU load',
        'auto_apply': False
    },
    'memory_leak': {
        'action': 'restart_pod',
        'details': 'Restart pod to clear memory leak',
        'auto_apply': True
    },
    'disk_full': {
        'action': 'clean_logs',
        'details': 'Clean old logs and temporary files',
        'auto_apply': True
    },
    'pod_crash': {
        'action': 'restart_pod',
        'details': 'Restart crashed pod',
        'auto_apply': True
    },
    'oom_kill': {
        'action': 'increase_memory',
        'details': 'Increase memory limits to prevent OOM',
        'auto_apply': False
    },
    'network_latency': {
        'action': 'check_network',
        'details': 'Investigate network connectivity issues',
        'auto_apply': False
    }
}

def get_recent_anomalies(conn):
    """Get anomalies from last 5 minutes"""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (resource_name, namespace)
            resource_name,
            namespace,
            prediction_type,
            confidence,
            timestamp
        FROM ml_predictions
        WHERE 
            prediction_type != 'healthy'
            AND timestamp > NOW() - INTERVAL '5 minutes'
            AND confidence > 0.7
        ORDER BY resource_name, namespace, timestamp DESC
    """)
    return cur.fetchall()

def check_existing_issue(conn, pod_name, namespace, issue_type):
    """Check if issue already exists"""
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM issues
        WHERE resource_name = %s
            AND namespace = %s
            AND issue_type = %s
            AND status = 'open'
        LIMIT 1
    """, (pod_name, namespace, issue_type))
    result = cur.fetchone()
    return result[0] if result else None

def create_issue(conn, pod_name, namespace, issue_type, severity, description):
    """Create a new issue"""
    issue_id = str(uuid.uuid4())
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO issues (
            id, resource_type, resource_name, namespace,
            issue_type, severity, description, detected_at, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        issue_id, 'pod', pod_name, namespace,
        issue_type, severity, description, datetime.now(), 'open'
    ))
    conn.commit()
    return issue_id

def create_remediation_action(conn, issue_id, action_type, action_details, auto_applied):
    """Create a remediation action"""
    action_id = str(uuid.uuid4())
    cur = conn.cursor()
    
    action_data = {
        'strategy': action_type,
        'details': action_details,
        'auto_applied': auto_applied
    }
    
    status = 'completed' if auto_applied else 'pending'
    
    cur.execute("""
        INSERT INTO remediation_actions (
            id, issue_id, action_type, action_details,
            executed_at, status
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        action_id, issue_id, action_type,
        json.dumps(action_data), datetime.now(), status
    ))
    conn.commit()
    return action_id

def remediate_anomaly(conn, pod_name, namespace, anomaly_type, confidence):
    """Process a single anomaly"""
    
    # Check if issue already exists
    existing_issue = check_existing_issue(conn, pod_name, namespace, anomaly_type)
    
    if existing_issue:
        print(f"   ℹ️  Issue already exists for {namespace}/{pod_name} ({anomaly_type})")
        return None
    
    # Determine severity
    if confidence > 0.9:
        severity = 'critical'
    elif confidence > 0.7:
        severity = 'high'
    else:
        severity = 'medium'
    
    # Create issue
    description = f"ML model detected {anomaly_type} with {confidence:.1%} confidence"
    issue_id = create_issue(conn, pod_name, namespace, anomaly_type, severity, description)
    
    print(f"   🔴 Created issue {issue_id[:8]}... for {namespace}/{pod_name}")
    
    # Get remediation strategy
    strategy = REMEDIATION_STRATEGIES.get(anomaly_type, {
        'action': 'investigate',
        'details': 'Manual investigation required',
        'auto_apply': False
    })
    
    # Create remediation action
    action_id = create_remediation_action(
        conn,
        issue_id,
        strategy['action'],
        strategy['details'],
        strategy['auto_apply']
    )
    
    if strategy['auto_apply']:
        print(f"      ✅ Auto-remediation: {strategy['action']}")
    else:
        print(f"      ⏸️  Manual action required: {strategy['action']}")
    
    return issue_id

def run_remediation_cycle():
    """Run one remediation cycle"""
    print(f"\n{'='*70}")
    print(f"🔄 Remediation Cycle - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    
    conn = psycopg2.connect(DB_URL)
    
    try:
        # Get recent anomalies
        anomalies = get_recent_anomalies(conn)
        
        if not anomalies:
            print("✅ No anomalies detected")
            return
        
        print(f"📊 Found {len(anomalies)} anomalies to process:")
        
        remediated = 0
        for pod_name, namespace, anomaly_type, confidence, timestamp in anomalies:
            result = remediate_anomaly(conn, pod_name, namespace, anomaly_type, confidence)
            if result:
                remediated += 1
        
        print(f"\n✅ Processed {remediated} new issues")
        
        # Show statistics
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                status,
                COUNT(*) as count
            FROM issues
            GROUP BY status
        """)
        print("\n📈 Issue Statistics:")
        for status, count in cur.fetchall():
            print(f"   • {status:15} {count:5} issues")
        
        cur.execute("""
            SELECT 
                action_type,
                status,
                COUNT(*) as count
            FROM remediation_actions
            GROUP BY action_type, status
            ORDER BY count DESC
        """)
        print("\n🔧 Remediation Actions:")
        for action_type, status, count in cur.fetchall():
            print(f"   • {action_type:20} {status:15} {count:5}")
        
    finally:
        conn.close()

def main():
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║              🏥 AURA K8s - Remediation Service 🏥                  ║
║                                                                      ║
║           Automated Issue Detection & Remediation                    ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
    """)
    
    print("🚀 Starting remediation service...")
    print(f"📊 Checking for anomalies every 30 seconds")
    print(f"💾 Database: {DB_URL.split('@')[1]}")
    print()
    
    try:
        while True:
            try:
                run_remediation_cycle()
            except psycopg2.OperationalError as e:
                print(f"❌ Database connection error: {e}")
            except psycopg2.Error as e:
                print(f"❌ Database error in remediation cycle: {e}")
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                print(f"❌ Data validation error: {e}")
            
            print(f"\n⏸️  Sleeping for 30 seconds...")
            time.sleep(30)
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Remediation service stopped")

if __name__ == "__main__":
    main()
