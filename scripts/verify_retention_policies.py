#!/usr/bin/env python3
"""
Script to verify TimescaleDB retention policies are active
Usage: python verify_retention_policies.py [--db-url postgresql://user:pass@host:port/db]
"""

import os
import sys
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor


def check_retention_policies(db_url: str):
    """Check if retention policies are active for TimescaleDB hypertables"""
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if TimescaleDB is available
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'timescaledb');")
        timescale_exists = cur.fetchone()['exists']
        
        if not timescale_exists:
            print("⚠️  TimescaleDB extension not found. Retention policies require TimescaleDB.")
            return False
        
        print("✅ TimescaleDB extension found\n")
        
        # Query retention policies
        query = """
        SELECT 
            h.table_name AS hypertable,
            j.config->>'drop_after' AS interval,
            j.job_id,
            j.config->>'schedule_interval' AS schedule_interval,
            j.last_run_success AS last_run_success,
            js.last_run_status AS last_run_status
        FROM timescaledb_information.jobs j
        LEFT JOIN timescaledb_information.job_stats js ON j.job_id = js.job_id
        JOIN timescaledb_information.hypertables h ON h.hypertable_name = (
            SELECT (j.config->>'hypertable_id')::regclass::text
        )
        WHERE j.proc_name = 'policy_retention'
        ORDER BY h.table_name;
        """
        
        cur.execute(query)
        policies = cur.fetchall()
        
        if not policies:
            print("⚠️  No retention policies found!")
            print("\nExpected policies:")
            print("  - pod_metrics: 7 days")
            print("  - node_metrics: 7 days")
            print("  - ml_predictions: 30 days")
            print("\nRetention policies should be created automatically during schema initialization.")
            return False
        
        print(f"✅ Found {len(policies)} retention policies:\n")
        
        all_active = True
        for policy in policies:
            status = "✅ ACTIVE" if policy['last_run_success'] else "⚠️  INACTIVE"
            if not policy['last_run_success']:
                all_active = False
            
            print(f"  {status} - {policy['hypertable']}")
            print(f"    Interval: {policy['interval']}")
            print(f"    Job ID: {policy['job_id']}")
            print(f"    Schedule: {policy['schedule_interval']}")
            if policy['last_run_status']:
                print(f"    Last run: {policy['last_run_status']}")
            print()
        
        # Check for expected hypertables
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY hypertable_name;")
        hypertables = [row['hypertable_name'] for row in cur.fetchall()]
        
        expected_tables = ['pod_metrics', 'node_metrics', 'ml_predictions']
        missing_policies = [t for t in expected_tables if t in hypertables and not any(p['hypertable'] == t for p in policies)]
        
        if missing_policies:
            print(f"⚠️  Missing retention policies for: {', '.join(missing_policies)}")
            all_active = False
        
        cur.close()
        conn.close()
        
        return all_active
        
    except psycopg2.Error as e:
        print(f"❌ Database error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Verify TimescaleDB retention policies')
    parser.add_argument('--db-url', 
                       default=os.getenv('DATABASE_URL', 'postgresql://aura:aura_password@localhost:5432/aura_metrics'),
                       help='Database connection URL')
    
    args = parser.parse_args()
    
    print("🔍 Checking TimescaleDB retention policies...\n")
    print(f"Database: {args.db_url.split('@')[1] if '@' in args.db_url else args.db_url}\n")
    
    success = check_retention_policies(args.db_url)
    
    if success:
        print("✅ All retention policies are active!")
        sys.exit(0)
    else:
        print("❌ Some retention policies are missing or inactive.")
        print("\nTo fix this, ensure retention policies are created during schema initialization.")
        sys.exit(1)


if __name__ == "__main__":
    main()

