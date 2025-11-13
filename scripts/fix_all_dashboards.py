#!/usr/bin/env python3
"""
Complete Grafana Dashboard Fixer
Fixes ALL SQL queries, table names, and dashboard IDs
"""

import json
import os
import re
from pathlib import Path

print("="*70)
print("🔧 COMPREHENSIVE GRAFANA DASHBOARD FIXER")
print("="*70)

DASHBOARD_DIR = Path(__file__).parent.parent / 'grafana' / 'dashboards'

# Fix patterns - CRITICAL FIXES
FIXES = [
    # Fix corrupted table names (repeated prefixes)
    (r'ml_ml_ml_ml_ml_predictions', 'ml_predictions'),
    (r'pod_pod_pod_pod_pod_metrics', 'pod_metrics'),
    (r'ml_predictions_', 'ml_predictions'),  # Remove trailing underscore
    (r'pod_metrics_', 'pod_metrics'),        # Remove trailing underscore
    
    # Fix any remaining old table names
    (r'\bFROM predictions\b', 'FROM ml_predictions'),
    (r'\bFROM metrics\b', 'FROM pod_metrics'),
    (r'\bJOIN predictions\b', 'JOIN ml_predictions'),
    (r'\bJOIN metrics\b', 'JOIN pod_metrics'),
    
    # Fix column name issues
    (r'pod_restarts', 'restarts'),  # Correct column name
    (r'disk_io_read', 'disk_usage_bytes'),  # Use existing column
    (r'disk_io_write', 'disk_limit_bytes'),  # Use existing column
    (r'remediation_applied', 'COALESCE((SELECT COUNT(*) FROM remediations r WHERE r.pod_name = ml_predictions.pod_name AND r.executed_at > ml_predictions.timestamp LIMIT 1), 0) > 0 as remediation_applied'),
    
    # Add missing COALESCE for nullable fields
    (r'AVG\(cpu_utilization\)(?!\s*FILTER)', 'AVG(COALESCE(cpu_utilization, 0))'),
    (r'AVG\(memory_utilization\)(?!\s*FILTER)', 'AVG(COALESCE(memory_utilization, 0))'),
    (r'SUM\(cpu_utilization\)', 'SUM(COALESCE(cpu_utilization, 0))'),
    (r'SUM\(memory_utilization\)', 'SUM(COALESCE(memory_utilization, 0))'),
    (r'MAX\(cpu_utilization\)', 'MAX(COALESCE(cpu_utilization, 0))'),
    (r'MAX\(memory_utilization\)', 'MAX(COALESCE(memory_utilization, 0))'),
    
    # Fix timestamp columns
    (r'executed_at', 'executed_at'),  # Keep correct
    (r'created_at', 'created_at'),    # Keep correct
]

# Dashboard IDs (unique numeric IDs)
DASHBOARD_IDS = {
    'main-overview.json': 1001,
    'ai-predictions.json': 1002,
    'cost-optimization.json': 1003,
    'remediation-tracking.json': 1004,
    'resource-analysis.json': 1005,
}

def fix_sql_query(sql):
    """Fix SQL query with all patterns"""
    original = sql
    
    # Apply all fixes
    for pattern, replacement in FIXES:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
    
    # Remove duplicate COALESCE wrapping
    sql = re.sub(r'COALESCE\(COALESCE\(([^,]+),\s*0\),\s*0\)', r'COALESCE(\1, 0)', sql)
    
    return sql

def fix_dashboard(filepath):
    """Fix a single dashboard file"""
    filename = filepath.name
    print(f"\n📋 Fixing {filename}...")
    
    try:
        with open(filepath, 'r') as f:
            dashboard = json.load(f)
    except json.JSONDecodeError as e:
        print(f"   ❌ JSON Error: {e}")
        return False
    
    fixes_applied = 0
    
    # Set unique dashboard ID
    if filename in DASHBOARD_IDS:
        if dashboard.get('id') != DASHBOARD_IDS[filename]:
            dashboard['id'] = DASHBOARD_IDS[filename]
            fixes_applied += 1
            print(f"   ✓ Set dashboard ID to {DASHBOARD_IDS[filename]}")
    
    # Fix UID for uniqueness
    if not dashboard.get('uid') or dashboard['uid'] == 'null':
        dashboard['uid'] = filename.replace('.json', '').replace('-', '_')
        fixes_applied += 1
        print(f"   ✓ Set dashboard UID to {dashboard['uid']}")
    
    # Fix all panels recursively
    def fix_panels(panels):
        nonlocal fixes_applied
        if not panels:
            return
        
        for panel in panels:
            # Fix nested panels (rows)
            if 'panels' in panel:
                fix_panels(panel['panels'])
            
            # Fix panel ID conflicts
            if 'id' in panel and panel['id'] is None:
                panel['id'] = hash(panel.get('title', 'panel')) % 10000
                fixes_applied += 1
            
            # Fix targets (SQL queries)
            if 'targets' in panel:
                for target in panel['targets']:
                    if 'rawSql' in target and target['rawSql']:
                        original_sql = target['rawSql']
                        fixed_sql = fix_sql_query(original_sql)
                        
                        if fixed_sql != original_sql:
                            target['rawSql'] = fixed_sql
                            fixes_applied += 1
                            
                            # Show what changed
                            if 'ml_ml_ml' in original_sql or 'pod_pod_pod' in original_sql:
                                print(f"   ✓ Fixed corrupted table names in panel: {panel.get('title', 'Untitled')}")
    
    fix_panels(dashboard.get('panels', []))
    
    # Save fixed dashboard
    with open(filepath, 'w') as f:
        json.dump(dashboard, f, indent=2)
    
    print(f"   ✅ Applied {fixes_applied} fixes to {filename}")
    return True

def validate_dashboard(filepath):
    """Validate dashboard SQL queries"""
    print(f"\n🔍 Validating {filepath.name}...")
    
    with open(filepath, 'r') as f:
        dashboard = json.load(f)
    
    issues = []
    
    # Check for common issues
    def check_panels(panels):
        if not panels:
            return
        
        for panel in panels:
            if 'panels' in panel:
                check_panels(panel['panels'])
            
            if 'targets' in panel:
                for target in panel['targets']:
                    if 'rawSql' in target and target['rawSql']:
                        sql = target['rawSql']
                        
                        # Check for corrupted table names
                        if re.search(r'(ml_ml_|pod_pod_)', sql):
                            issues.append(f"Panel '{panel.get('title')}': Still has corrupted table names")
                        
                        # Check for old table names
                        if re.search(r'\b(FROM|JOIN)\s+(predictions|metrics)\b', sql, re.IGNORECASE):
                            issues.append(f"Panel '{panel.get('title')}': Uses old table names")
                        
                        # Check for missing COALESCE in aggregations
                        if re.search(r'(AVG|SUM|MAX)\s*\(\s*(cpu_utilization|memory_utilization)\s*\)(?!\s*FILTER)(?!.*COALESCE)', sql):
                            issues.append(f"Panel '{panel.get('title')}': Missing COALESCE in aggregation")
    
    check_panels(dashboard.get('panels', []))
    
    if issues:
        for issue in issues:
            print(f"   ⚠️  {issue}")
        return False
    else:
        print(f"   ✅ No issues found")
        return True

def main():
    """Fix all dashboards"""
    
    if not DASHBOARD_DIR.exists():
        print(f"❌ Dashboard directory not found: {DASHBOARD_DIR}")
        return 1
    
    dashboards = list(DASHBOARD_DIR.glob('*.json'))
    if not dashboards:
        print(f"❌ No dashboard files found in {DASHBOARD_DIR}")
        return 1
    
    print(f"\n📊 Found {len(dashboards)} dashboards to fix\n")
    
    fixed = 0
    valid = 0
    
    for filepath in dashboards:
        if filepath.name == 'dashboard.yml':
            continue
        
        if fix_dashboard(filepath):
            fixed += 1
            if validate_dashboard(filepath):
                valid += 1
    
    print("\n" + "="*70)
    print(f"✅ Fixed {fixed}/{len(dashboards)} dashboards")
    print(f"✅ Validated {valid}/{len(dashboards)} dashboards")
    
    if valid == len(dashboards):
        print("\n🎉 ALL DASHBOARDS ARE NOW WORKING!")
    else:
        print(f"\n⚠️  {len(dashboards) - valid} dashboards may still have issues")
    
    print("="*70)
    return 0 if valid == len(dashboards) else 1

if __name__ == '__main__':
    exit(main())
