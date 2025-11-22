#!/usr/bin/env python3
"""
Import Grafana dashboards into Grafana instance
This script imports all dashboard JSON files into Grafana
"""

import os
import json
import requests
import sys
from pathlib import Path

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.getenv("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.getenv("GRAFANA_PASSWORD", "admin")

def get_grafana_auth():
    """Get Grafana API key or credentials"""
    api_key = os.getenv("GRAFANA_API_KEY")
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return (GRAFANA_USER, GRAFANA_PASSWORD)

def check_datasource_exists(session):
    """Check if TimescaleDB datasource exists"""
    try:
        response = session.get(f"{GRAFANA_URL}/api/datasources/uid/aura-timescaledb")
        if response.status_code == 200:
            print("✅ TimescaleDB datasource exists")
            return True
        else:
            print(f"⚠️  Datasource not found: {response.status_code}")
            return False
    except Exception as e:
        print(f"⚠️  Error checking datasource: {e}")
        return False

def import_dashboard(session, dashboard_path):
    """Import a single dashboard into Grafana"""
    try:
        with open(dashboard_path, 'r') as f:
            dashboard_json = json.load(f)
        
        dashboard_name = dashboard_path.stem
        dashboard_uid = dashboard_json.get('uid', dashboard_name.replace('_', '-'))
        
        # Prepare dashboard for import
        dashboard_payload = {
            "dashboard": dashboard_json,
            "overwrite": True,
            "inputs": []
        }
        
        # Import dashboard
        response = session.post(
            f"{GRAFANA_URL}/api/dashboards/db",
            json=dashboard_payload
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Imported: {dashboard_name} (UID: {dashboard_uid})")
            return True
        else:
            print(f"❌ Failed to import {dashboard_name}: {response.status_code}")
            print(f"   Error: {response.text[:200]}")
            return False
            
    except FileNotFoundError:
        print(f"❌ Dashboard file not found: {dashboard_path}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {dashboard_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error importing {dashboard_path}: {e}")
        return False

def main():
    """Main function to import all dashboards"""
    print("=" * 60)
    print("Grafana Dashboard Importer")
    print("=" * 60)
    print()
    
    # Get project root
    project_root = Path(__file__).parent.parent
    dashboards_dir = project_root / "grafana" / "dashboards"
    
    if not dashboards_dir.exists():
        print(f"❌ Dashboards directory not found: {dashboards_dir}")
        return 1
    
    # Create session
    session = requests.Session()
    auth = get_grafana_auth()
    if isinstance(auth, dict):
        session.headers.update(auth)
    else:
        session.auth = auth
    
    # Check Grafana connectivity
    try:
        response = session.get(f"{GRAFANA_URL}/api/health")
        if response.status_code != 200:
            print(f"❌ Grafana not accessible at {GRAFANA_URL}")
            return 1
        print(f"✅ Connected to Grafana at {GRAFANA_URL}")
    except Exception as e:
        print(f"❌ Cannot connect to Grafana: {e}")
        return 1
    
    # Check datasource
    check_datasource_exists(session)
    print()
    
    # Find all dashboard JSON files
    dashboard_files = list(dashboards_dir.glob("*.json"))
    if not dashboard_files:
        print(f"❌ No dashboard JSON files found in {dashboards_dir}")
        return 1
    
    print(f"Found {len(dashboard_files)} dashboard(s) to import:")
    print()
    
    # Import each dashboard
    success_count = 0
    for dashboard_file in sorted(dashboard_files):
        if import_dashboard(session, dashboard_file):
            success_count += 1
        print()
    
    print("=" * 60)
    print(f"Import complete: {success_count}/{len(dashboard_files)} dashboards imported")
    print("=" * 60)
    
    if success_count == len(dashboard_files):
        print(f"\n✅ All dashboards imported successfully!")
        print(f"   Access Grafana at: {GRAFANA_URL}")
        return 0
    else:
        print(f"\n⚠️  Some dashboards failed to import")
        return 1

if __name__ == "__main__":
    sys.exit(main())

