#!/usr/bin/env python3
"""
AURA K8s Orchestrator
Continuously processes metrics → ML predictions → issues → remediation
"""

import os
import time
import psycopg2
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@timescaledb:5432/aura_metrics")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8001")
MCP_SERVICE_URL = os.getenv("MCP_SERVICE_URL", "http://mcp-server:8000")

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(DATABASE_URL)

def generate_predictions(conn, ml_service_url: str):
    """Generate ML predictions for recent metrics"""
    print("🤖 Generating ML predictions...")
    
    cur = conn.cursor()
    
    # Get recent metrics without predictions
    cur.execute("""
        SELECT DISTINCT ON (pm.pod_name, pm.namespace)
            pm.pod_name, pm.namespace, pm.timestamp,
            pm.cpu_utilization, pm.memory_utilization,
            COALESCE(pm.disk_usage_bytes::float / NULLIF(pm.disk_limit_bytes, 0) * 100, 0) as disk_usage_percent,
            COALESCE(pm.network_rx_bytes / 1024.0 / 1024.0, 0) as network_rx_mb,
            COALESCE(pm.network_tx_bytes / 1024.0 / 1024.0, 0) as network_tx_mb,
            COALESCE(pm.network_rx_errors + pm.network_tx_errors, 0) as error_rate,
            pm.restarts, COALESCE(pm.age / 3600.0, 0) as age_hours,
            pm.cpu_trend, pm.memory_trend, pm.restart_trend,
            pm.has_oom_kill, pm.has_crash_loop, pm.has_high_cpu, pm.has_network_issues
        FROM pod_metrics pm
        LEFT JOIN ml_predictions mp ON 
            pm.pod_name = mp.pod_name 
            AND pm.namespace = mp.namespace 
            AND pm.timestamp = mp.timestamp
        WHERE pm.timestamp > NOW() - INTERVAL '1 hour'
            AND mp.timestamp IS NULL
        ORDER BY pm.pod_name, pm.namespace, pm.timestamp DESC
        LIMIT 50
    """)
    
    metrics = cur.fetchall()
    print(f"   Found {len(metrics)} metrics to analyze")
    
    predictions_made = 0
    
    for metric in metrics:
        (pod_name, namespace, timestamp, cpu_util, mem_util, disk_pct, 
         net_rx, net_tx, err_rate, restarts, age_hrs, cpu_trend, 
         mem_trend, restart_trend, has_oom, has_crash, has_high_cpu, has_network) = metric
        
        # Build feature vector
        features = {
            "cpu_utilization": float(cpu_util or 0),
            "memory_utilization": float(mem_util or 0),
            "disk_usage_percent": float(disk_pct or 0),
            "network_rx_mb": float(net_rx or 0),
            "network_tx_mb": float(net_tx or 0),
            "error_rate": float(err_rate or 0),
            "restarts": int(restarts or 0),
            "age_hours": float(age_hrs or 0),
            "cpu_trend": float(cpu_trend or 0),
            "memory_trend": float(mem_trend or 0),
            "restart_trend": float(restart_trend or 0),
            "has_oom_kill": 1.0 if has_oom else 0.0,
            "has_crash_loop": 1.0 if has_crash else 0.0,
            "has_high_cpu": 1.0 if has_high_cpu else 0.0,
            "has_network_issues": 1.0 if has_network else 0.0
        }
        
        try:
            response = requests.post(
                f"{ml_service_url}/predict",
                json={"features": features},
                timeout=5
            )
            
            if response.status_code == 200:
                prediction = response.json()
                
                # Determine predicted issue type
                predicted_issue = prediction.get('anomaly_type', 'healthy')
                confidence = prediction.get('confidence', 0.5)
                
                # Insert prediction
                cur.execute("""
                    INSERT INTO ml_predictions (
                        pod_name, namespace, timestamp,
                        predicted_issue, confidence, time_horizon_seconds,
                        top_features, explanation,
                        resource_type, resource_name, prediction_type, 
                        prediction_value, model_version, features
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (timestamp, pod_name, namespace) DO NOTHING
                """, (
                    pod_name, namespace, timestamp,
                    predicted_issue, confidence, 3600,
                    json.dumps(features), prediction.get('reasoning', ''),
                    'pod', pod_name, predicted_issue,
                    1.0 if predicted_issue != 'healthy' else 0.0,
                    prediction.get('model_used', 'ensemble'),
                    json.dumps(features)
                ))
                
                predictions_made += 1
                
                if predicted_issue != 'healthy':
                    print(f"      🔴 {namespace}/{pod_name}: {predicted_issue} ({confidence:.1%})")
                    
        except requests.exceptions.RequestException as e:
            print(f"      ❌ ML service request failed for {pod_name}: {e}")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"      ❌ Invalid prediction response for {pod_name}: {e}")
        except psycopg2.Error as e:
            print(f"      ❌ Database error for {pod_name}: {e}")
    
    conn.commit()
    print(f"   ✅ Generated {predictions_made} predictions")
    return predictions_made

def create_issues_from_predictions(conn):
    """Create issues from anomaly predictions"""
    print("📋 Creating issues from predictions...")
    
    cur = conn.cursor()
    
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
            AND mp.confidence > 0.7
            AND i.id IS NULL
        ORDER BY mp.pod_name, mp.namespace, mp.predicted_issue, mp.timestamp DESC
        LIMIT 20
    """)
    
    predictions = cur.fetchall()
    print(f"   Found {len(predictions)} anomaly predictions")
    
    issues_created = 0
    
    for pred in predictions:
        (pod_name, namespace, issue_type, confidence, timestamp,
         cpu_util, mem_util, has_oom, has_crash, has_high_cpu, has_network) = pred
        
        # Determine severity
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
        
        try:
            cur.execute("""
                INSERT INTO issues (
                    id, pod_name, namespace, issue_type, severity,
                    description, created_at, status, confidence, predicted_time_horizon
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                issue_id, pod_name, namespace, issue_type, severity,
                description, timestamp, 'Open', confidence, 3600
            ))
            
            issues_created += 1
            print(f"      🔴 Issue created: {namespace}/{pod_name} - {issue_type} ({severity})")
            
        except psycopg2.Error as e:
            print(f"      ❌ Database error creating issue: {e}")
    
    conn.commit()
    print(f"   ✅ Created {issues_created} issues")
    return issues_created

def main():
    """Main orchestrator loop"""
    print("="*70)
    print("   AURA K8s Orchestrator")
    print("   Processing: Metrics → Predictions → Issues → Remediation")
    print("="*70)
    
    iteration = 0
    
    while True:
        iteration += 1
        print(f"\n🔄 Iteration {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            conn = get_db_connection()
            
            # Step 1: Generate predictions
            predictions = generate_predictions(conn, ML_SERVICE_URL)
            
            # Step 2: Create issues from predictions
            if predictions > 0:
                issues = create_issues_from_predictions(conn)
            else:
                print("   ⏭️  Skipping issue creation (no new predictions)")
            
            conn.close()
            
            # Wait before next iteration
            print(f"   ⏳ Waiting 30 seconds before next iteration...")
            time.sleep(30)
            
        except KeyboardInterrupt:
            print("\n⏹️  Stopping orchestrator...")
            break
        except psycopg2.OperationalError as e:
            print(f"   ❌ Database connection error: {e}")
            print("   ⏳ Retrying in 30 seconds...")
            time.sleep(30)
        except requests.exceptions.RequestException as e:
            print(f"   ❌ Service request error: {e}")
            print("   ⏳ Retrying in 30 seconds...")
            time.sleep(30)
        except (psycopg2.Error, ValueError, KeyError) as e:
            print(f"   ❌ Error in iteration: {e}")
            print("   ⏳ Retrying in 30 seconds...")
            time.sleep(30)

if __name__ == "__main__":
    main()

