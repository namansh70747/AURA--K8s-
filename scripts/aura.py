#!/usr/bin/env python3
"""
AURA K8s Management Tool
Consolidated utility for all system operations
"""

import sys
import argparse
from pathlib import Path

# Add scripts to path
sys.path.append(str(Path(__file__).parent))

from validate_system import main as validate
from remediation_service import main as remediate
from generate_test_data import generate_and_store_data as generate_data


def show_menu():
    """Interactive menu"""
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║                   AURA K8s Management Tool                           ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

Available Commands:

  1. validate   - Run complete system validation
  2. remediate  - Run remediation service (continuous)
  3. generate   - Generate test data
  4. status     - Quick system status check
  5. help       - Show this help message

Usage:
  python aura.py <command>
  python aura.py          # Interactive mode
""")


def quick_status():
    """Show quick system status"""
    import requests
    import psycopg2
    
    print("\n🔍 Quick System Status")
    print("="*70)
    
    # Check ML Service
    try:
        resp = requests.get("http://localhost:8001/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ ML Service: {data['models_loaded']} models loaded")
        else:
            print("❌ ML Service: Not responding")
    except:
        print("❌ ML Service: Offline")
    
    # Check Grafana
    try:
        resp = requests.get("http://localhost:3000/api/health", timeout=3)
        if resp.status_code == 200:
            print("✅ Grafana: Accessible")
        else:
            print("❌ Grafana: Not responding")
    except:
        print("❌ Grafana: Offline")
    
    # Check Database
    try:
        conn = psycopg2.connect("postgresql://aura:aura_password@localhost:5432/aura_metrics")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pod_metrics")
        count = cur.fetchone()[0]
        print(f"✅ Database: {count} metrics stored")
        conn.close()
    except Exception as e:
        print(f"❌ Database: Error - {e}")
    
    print("="*70)
    print("\nRun 'python aura.py validate' for full validation\n")


def main():
    parser = argparse.ArgumentParser(
        description='AURA K8s Management Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python aura.py validate    # Run system validation
  python aura.py status      # Quick status check
        """
    )
    
    parser.add_argument(
        'command',
        nargs='?',
        choices=['validate', 'remediate', 'generate', 'status', 'help'],
        help='Command to execute'
    )
    
    args = parser.parse_args()
    
    if not args.command or args.command == 'help':
        show_menu()
        return 0
    
    try:
        if args.command == 'validate':
            return validate()
        elif args.command == 'remediate':
            remediate()
            return 0
        elif args.command == 'generate':
            generate_data()
            return 0
        elif args.command == 'status':
            quick_status()
            return 0
    except KeyboardInterrupt:
        print("\n\n⏹️  Interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
