#!/usr/bin/env python3
"""
AURA K8s System Validator
Tests all critical components to ensure they're working correctly
"""

import psycopg2
import psycopg2.extensions
import requests
import time
import sys
import os
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg2.extensions import connection

# Configuration - read from environment with fallback to localhost
DB_URL: str = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@localhost:5432/aura_metrics")
ML_SERVICE_URL: str = os.getenv("ML_SERVICE_URL", "http://localhost:8001")
GRAFANA_URL: str = os.getenv("GRAFANA_URL", "http://localhost:3000")
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://localhost:8000")

print(f"\n📡 Using endpoints:")
print(f"   DB: {DB_URL.split('@')[1] if '@' in DB_URL else DB_URL}")
print(f"   ML: {ML_SERVICE_URL}")
print(f"   Grafana: {GRAFANA_URL}")
print(f"   MCP: {MCP_SERVER_URL}")

class Colors:
    """ANSI color codes for terminal output."""
    GREEN: str = '\033[92m'
    RED: str = '\033[91m'
    YELLOW: str = '\033[93m'
    BLUE: str = '\033[94m'
    END: str = '\033[0m'

def print_header(text: str) -> None:
    """Print a formatted header.
    
    Args:
        text: Header text to display
    """
    print(f"\n{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}{text:^60}{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}\n")

def print_success(text: str) -> None:
    """Print a success message.
    
    Args:
        text: Success message to display
    """
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")

def print_error(text: str) -> None:
    """Print an error message.
    
    Args:
        text: Error message to display
    """
    print(f"{Colors.RED}✗ {text}{Colors.END}")

def print_warning(text: str) -> None:
    """Print a warning message.
    
    Args:
        text: Warning message to display
    """
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")

def test_database() -> Tuple[bool, Optional[str]]:
    """Test database connectivity and schema.
    
    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    print_header("DATABASE TESTS")
    
    try:
        # Use context manager for automatic cleanup
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                print_success("Connected to TimescaleDB")
                
                # Test tables exist - include all tables used by orchestrator
                tables = ['pod_metrics', 'ml_predictions', 'remediations', 'issues', 'node_metrics', 'cost_savings', 'remediation_actions']
                cur.execute("""
                    SELECT tablename FROM pg_tables 
                    WHERE schemaname = 'public' 
                    AND tablename IN ('pod_metrics', 'ml_predictions', 'remediations', 'issues', 'node_metrics', 'cost_savings', 'remediation_actions')
                """)
                existing_tables = [row[0] for row in cur.fetchall()]
                
                for table in tables:
                    if table in existing_tables:
                        print_success(f"Table '{table}' exists")
                    else:
                        print_error(f"Table '{table}' missing")
                
                # Test views exist
                cur.execute("""
                    SELECT viewname FROM pg_views 
                    WHERE schemaname = 'public' 
                    AND viewname IN ('metrics', 'predictions')
                """)
                views = [row[0] for row in cur.fetchall()]
                
                if 'metrics' in views:
                    print_success("View 'metrics' exists (aliases pod_metrics)")
                else:
                    print_error("View 'metrics' missing")
                
                if 'predictions' in views:
                    print_success("View 'predictions' exists (aliases ml_predictions)")
                else:
                    print_error("View 'predictions' missing")
                
                # Test data exists
                cur.execute("SELECT COUNT(*) FROM pod_metrics")
                count = cur.fetchone()[0]
                if count > 0:
                    print_success(f"pod_metrics has {count} rows")
                else:
                    print_warning("pod_metrics is empty - run data generator")
                
                cur.execute("SELECT COUNT(*) FROM ml_predictions")
                count = cur.fetchone()[0]
                if count > 0:
                    print_success(f"ml_predictions has {count} rows")
                else:
                    print_warning("ml_predictions is empty - run orchestrator")
                
                return True
        
    except psycopg2.OperationalError as e:
        print_error(f"Database connection failed: {e}")
        return False
    except psycopg2.Error as e:
        print_error(f"Database test failed: {e}")
        return False

def test_ml_service():
    """Test ML service availability"""
    print_header("ML SERVICE TESTS")
    
    try:
        response = requests.get(f"{ML_SERVICE_URL}/health", timeout=5)
        if response.status_code == 200:
            print_success("ML service is healthy")
            
            # Test prediction endpoint with correct features format
            # Test prediction endpoint with correct features format
            test_data = {
                "features": {
                    "cpu_usage": 85.5,
                    "memory_usage": 75.0,
                    "disk_usage": 50.0,
                    "network_bytes_sec": 1000.0,
                    "error_rate": 0.05,
                    "latency_ms": 100.0,
                    "restart_count": 2.0,
                    "age_minutes": 120.0,
                    "cpu_memory_ratio": 1.14,
                    "resource_pressure": 0.8,
                    "error_latency_product": 5.0,
                    "network_per_cpu": 11.7,
                    "is_critical": 1.0
                }
            }
            
            response = requests.post(f"{ML_SERVICE_URL}/predict", json=test_data, timeout=10)
            if response.status_code == 200:
                result = response.json()
                print_success(f"Prediction API works: {result.get('anomaly_type', 'N/A')} (confidence: {result.get('confidence', 0):.2%})")
                return True
            else:
                print_error(f"Prediction API failed: {response.status_code}")
                try:
                    print_error(f"  Error: {response.json()}")
                except:
                    pass
                return False
        else:
            print_error(f"ML service unhealthy: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print_error("ML service not reachable - is it running?")
        return False
    except requests.exceptions.RequestException as e:
        print_error(f"ML service request failed: {e}")
        return False
    except (ValueError, KeyError) as e:
        print_error(f"ML service response invalid: {e}")
        return False

def test_grafana():
    """Test Grafana availability and datasource"""
    print_header("GRAFANA TESTS")
    
    try:
        response = requests.get(f"{GRAFANA_URL}/api/health", timeout=5)
        if response.status_code == 200:
            print_success("Grafana is healthy")
            
            # Test datasource
            response = requests.get(
                f"{GRAFANA_URL}/api/datasources",
                auth=('admin', 'admin'),
                timeout=5
            )
            if response.status_code == 200:
                datasources = response.json()
                if datasources:
                    print_success(f"Found {len(datasources)} datasource(s)")
                    for ds in datasources:
                        print_success(f"  - {ds['name']} ({ds['type']})")
                else:
                    print_warning("No datasources configured")
                return True
            else:
                print_error(f"Could not fetch datasources: {response.status_code}")
                return False
        else:
            print_error(f"Grafana unhealthy: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print_error("Grafana not reachable - is it running?")
        return False
    except requests.exceptions.RequestException as e:
        print_error(f"Grafana request failed: {e}")
        return False

def test_dashboards():
    """Test that dashboards can query data"""
    print_header("DASHBOARD QUERY TESTS")
    
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # Test queries from dashboards
        test_queries = [
            ("Health Score Query", """
                SELECT
                  NOW() as time,
                  100.0 - (COUNT(*) FILTER (WHERE is_anomaly = 1) * 100.0 / NULLIF(COUNT(*), 0)) as health_score
                FROM ml_predictions
                WHERE timestamp > NOW() - INTERVAL '1 hour'
            """),
            ("CPU Usage Query", """
                SELECT
                  date_trunc('minute', timestamp) as time,
                  AVG(COALESCE(cpu_utilization, 0)) as cpu_usage,
                  AVG(COALESCE(memory_utilization, 0)) as memory_usage
                FROM pod_metrics
                WHERE timestamp > NOW() - INTERVAL '1 hour'
                GROUP BY time
                ORDER BY time
                LIMIT 10
            """),
            ("Anomaly Count Query", """
                SELECT
                  COUNT(*) as anomaly_count
                FROM ml_predictions
                WHERE timestamp > NOW() - INTERVAL '24 hours'
                  AND is_anomaly = 1
            """),
        ]
        
        for name, query in test_queries:
            try:
                cur.execute(query)
                result = cur.fetchall()
                print_success(f"{name}: OK ({len(result)} rows)")
            except psycopg2.Error as e:
                print_error(f"{name}: FAILED - {str(e)[:50]}")
        
        cur.close()
        conn.close()
        return True
        
    except psycopg2.OperationalError as e:
        print_error(f"Database connection failed: {e}")
        return False
    except psycopg2.Error as e:
        print_error(f"Dashboard query test failed: {e}")
        return False

def main():
    """Run all tests"""
    print(f"\n{Colors.BLUE}╔{'═'*58}╗{Colors.END}")
    print(f"{Colors.BLUE}║{Colors.END}  🚀 AURA K8s System Validation  {Colors.END}{Colors.BLUE}{'║':>25}{Colors.END}")
    print(f"{Colors.BLUE}║{Colors.END}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.END}{Colors.BLUE}{'║':>37}{Colors.END}")
    print(f"{Colors.BLUE}╚{'═'*58}╝{Colors.END}")
    
    results = {}
    
    # Run all tests
    results['database'] = test_database()
    time.sleep(1)
    
    results['ml_service'] = test_ml_service()
    time.sleep(1)
    
    results['grafana'] = test_grafana()
    time.sleep(1)
    
    results['dashboards'] = test_dashboards()
    
    # Summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test, result in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  {test.upper():.<40} {status}")
    
    print(f"\n{Colors.BLUE}{'─'*60}{Colors.END}")
    
    if passed == total:
        print(f"{Colors.GREEN}✓ All tests passed! ({passed}/{total}){Colors.END}")
        print(f"\n{Colors.GREEN}🎉 System is ready!{Colors.END}")
        print(f"\n{Colors.BLUE}Access Grafana:{Colors.END} http://localhost:3000 (admin/admin)")
        return 0
    else:
        print(f"{Colors.RED}✗ Some tests failed ({passed}/{total} passed){Colors.END}")
        print(f"\n{Colors.YELLOW}Fix the issues above and run again{Colors.END}")
        return 1

if __name__ == '__main__':
    sys.exit(main())
