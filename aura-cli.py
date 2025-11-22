#!/usr/bin/env python3
"""
AURA K8s - Unified CLI Management Tool
Replaces all scattered scripts with a single professional interface
"""

import sys
import os
import subprocess
import argparse
import time
import requests
import signal
import psycopg2
from pathlib import Path
from datetime import datetime

# Colors for output
class Color:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'

PROJECT_ROOT = Path(__file__).parent.absolute()
PID_DIR = PROJECT_ROOT / '.pids'
LOG_DIR = PROJECT_ROOT / 'logs'

# Configuration - read from environment variables or config
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aura:aura_password@localhost:5432/aura_metrics")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8001")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")

# Required ports - read from environment or use defaults
REQUIRED_PORTS_STR = os.getenv("REQUIRED_PORTS", "5432,8000,8001,9090,9091,3000,11434")
try:
    REQUIRED_PORTS = [int(p.strip()) for p in REQUIRED_PORTS_STR.split(",")]
except ValueError:
    # Fallback to defaults if parsing fails
    REQUIRED_PORTS = [5432, 8000, 8001, 9090, 9091, 3000, 11434]


def print_banner():
    """Print AURA banner"""
    print(f"{Color.BLUE}")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║        █████╗ ██╗   ██╗██████╗  █████╗     ██╗  ██╗███████╗ ║")
    print("║       ██╔══██╗██║   ██║██╔══██╗██╔══██╗    ██║ ██╔╝██╔════╝ ║")
    print("║       ███████║██║   ██║██████╔╝███████║    █████╔╝ ███████╗ ║")
    print("║       ██╔══██║██║   ██║██╔══██╗██╔══██║    ██╔═██╗ ╚════██║ ║")
    print("║       ██║  ██║╚██████╔╝██║  ██║██║  ██║    ██║  ██╗███████║ ║")
    print("║       ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝    ╚═╝  ╚═╝╚══════╝ ║")
    print("║                                                              ║")
    print("║           AI-Powered Kubernetes Auto-Remediation            ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"{Color.NC}\n")


def run_command(cmd, capture=False):
    """Run shell command"""
    if capture:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.returncode == 0, result.stdout
    else:
        return subprocess.run(cmd, shell=True).returncode == 0


def kill_port(port):
    """Kill process on specific port"""
    success, pids = run_command(f"lsof -ti :{port}", capture=True)
    if success and pids.strip():
        print(f"{Color.YELLOW}   → Killing processes on port {port}{Color.NC}")
        run_command(f"kill -9 {pids.strip()}")
        time.sleep(0.5)
        return True
    return False


def cleanup_ports():
    """Clean up all required ports"""
    print(f"\n{Color.BLUE}[Cleanup] Freeing up required ports...{Color.NC}")
    for port in REQUIRED_PORTS:
        if kill_port(port):
            print(f"{Color.GREEN}   ✓ Port {port} freed{Color.NC}")
    
    # Kill known process names
    for proc in ['ml.serve.predictor', 'server_ollama', 'orchestrator.py', 'generate_test_data.py']:
        run_command(f"pkill -9 -f '{proc}'")
    
    print(f"{Color.GREEN}✓ Port cleanup complete{Color.NC}")


def validate_prerequisites():
    """Validate system prerequisites"""
    print(f"\n{Color.BLUE}[Validation] Checking prerequisites...{Color.NC}")
    
    checks = {
        "Docker": "docker --version",
        "Go": "go version",
        "Python3": "python3 --version",
        "Kind": "kind --version",
        "kubectl": "kubectl version --client"
    }
    
    all_ok = True
    for name, cmd in checks.items():
        if run_command(cmd):
            print(f"{Color.GREEN}   ✓ {name} installed{Color.NC}")
        else:
            print(f"{Color.RED}   ✗ {name} missing{Color.NC}")
            all_ok = False
    
    return all_ok


def start_services():
    """Start all AURA services"""
    print(f"\n{Color.BLUE}[Startup] Starting AURA K8s services...{Color.NC}")
    
    # Create directories
    PID_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    
    # Validate and cleanup
    if not validate_prerequisites():
        print(f"{Color.RED}✗ Prerequisites missing. Please install required tools.{Color.NC}")
        return False
    
    cleanup_ports()
    
    # Setup Kind cluster if needed
    cluster_name = "aura-k8s-local"
    success, clusters = run_command(f"kind get clusters", capture=True)
    if cluster_name not in clusters:
        print(f"\n{Color.YELLOW}Creating Kind cluster...{Color.NC}")
        run_command(f"kind create cluster --name {cluster_name} --config configs/kind-cluster-simple.yaml")
    else:
        print(f"{Color.GREEN}✓ Kind cluster exists{Color.NC}")
    
    # Start Docker services (TimescaleDB only - Grafana runs locally)
    print(f"\n{Color.BLUE}[Docker] Starting TimescaleDB...{Color.NC}")
    run_command("docker-compose up -d timescaledb")
    
    # Wait for database to be ready with health check
    print(f"{Color.BLUE}[Database] Waiting for TimescaleDB to be ready...{Color.NC}")
    max_retries = 30
    db_ready = False
    for attempt in range(max_retries):
        success, output = run_command("docker-compose exec -T timescaledb pg_isready -U aura -d aura_metrics 2>&1", capture=True)
        if success and ("accepting connections" in output.lower() or "accepting" in output.lower()):
            print(f"{Color.GREEN}   ✓ TimescaleDB is ready{Color.NC}")
            db_ready = True
            break
        if attempt < max_retries - 1:
            time.sleep(1)
        else:
            print(f"{Color.YELLOW}   ⚠ TimescaleDB may not be ready, continuing anyway...{Color.NC}")
    
    # Start Grafana locally (not in Docker)
    print(f"\n{Color.BLUE}[Grafana] Checking Grafana...{Color.NC}")
    # Check if Grafana is already running
    success, _ = run_command("lsof -ti :3000", capture=True)
    if success:
        print(f"{Color.GREEN}   ✓ Grafana already running on port 3000{Color.NC}")
    else:
        # Try to find and start Grafana
        success, grafana_path = run_command("which grafana-server", capture=True)
        if not success:
            # Try common installation paths
            for path in ["/usr/local/bin/grafana-server", "/opt/homebrew/bin/grafana-server", "/usr/bin/grafana-server"]:
                if Path(path).exists():
                    grafana_path = path
                    success = True
                    break
        
        if success and grafana_path:
            grafana_log = LOG_DIR / "grafana.log"
            # Use default config paths or create a simple one
            config_paths = [
                "/usr/local/etc/grafana/grafana.ini",
                "/opt/homebrew/etc/grafana/grafana.ini",
                "/etc/grafana/grafana.ini"
            ]
            config_arg = ""
            for cfg_path in config_paths:
                if Path(cfg_path).exists():
                    config_arg = f"--config={cfg_path}"
                    break
            
            homepath_paths = [
                "/usr/local/share/grafana",
                "/opt/homebrew/share/grafana",
                "/usr/share/grafana"
            ]
            homepath_arg = ""
            for hp_path in homepath_paths:
                if Path(hp_path).exists():
                    homepath_arg = f"--homepath={hp_path}"
                    break
            
            cmd_parts = [grafana_path.strip(), "web"]
            if config_arg:
                cmd_parts.insert(1, config_arg)
            if homepath_arg:
                cmd_parts.insert(-1, homepath_arg)
            
            full_cmd = " ".join(cmd_parts)
            full_cmd = f"nohup {full_cmd} > {grafana_log} 2>&1 & echo $! > {PID_DIR / 'grafana.pid'}"
            run_command(full_cmd)
            time.sleep(3)
            
            # Verify Grafana started with retry logic
            grafana_started = False
            for retry in range(5):
                time.sleep(1)
                success, _ = run_command("lsof -ti :3000", capture=True)
                if success:
                    grafana_started = True
                    break
            
            if grafana_started:
                print(f"{Color.GREEN}   ✓ Grafana started locally (port 3000){Color.NC}")
            else:
                print(f"{Color.YELLOW}   ⚠ Grafana may not have started. Check {grafana_log}{Color.NC}")
                print(f"{Color.YELLOW}   You may need to configure Grafana manually. See README.{Color.NC}")
                # Check if Grafana is required - if so, fail fast
                if os.getenv("GRAFANA_REQUIRED", "false").lower() == "true":
                    print(f"{Color.RED}   ✗ Grafana is required but failed to start. Exiting.{Color.NC}")
                    return False
        else:
            print(f"{Color.YELLOW}   ⚠ Grafana not found. Please install Grafana:")
            print(f"      macOS: brew install grafana")
            print(f"      Linux: Follow https://grafana.com/docs/grafana/latest/setup-grafana/installation/{Color.NC}")
            print(f"{Color.YELLOW}   Or start Grafana manually and ensure it's on port 3000{Color.NC}")
            print(f"{Color.YELLOW}   Configure datasource at {PROJECT_ROOT / 'grafana/datasources/datasource-local.yml'}{Color.NC}")
    
    # Initialize database schema (idempotent, safe to run multiple times)
    if db_ready:
        print(f"{Color.BLUE}[Database] Initializing schema...{Color.NC}")
        success, output = run_command("docker-compose exec -T timescaledb psql -U aura -d aura_metrics -f /docker-entrypoint-initdb.d/init.sql 2>&1", capture=True)
        if success:
            print(f"{Color.GREEN}   ✓ Database schema initialized{Color.NC}")
        else:
            # Schema might already be initialized
            if "already exists" in output.lower() or "duplicate" in output.lower() or "already a hypertable" in output.lower():
                print(f"{Color.GREEN}   ✓ Database schema already initialized{Color.NC}")
            else:
                print(f"{Color.YELLOW}   ⚠ Schema initialization: {output[:100] if output else 'check logs'}{Color.NC}")
    else:
        print(f"{Color.YELLOW}   ⚠ Skipping schema initialization (database not ready){Color.NC}")
    
    # Build Go services
    print(f"\n{Color.BLUE}[Build] Compiling Go services...{Color.NC}")
    run_command("go build -o bin/collector ./cmd/collector")
    run_command("go build -o bin/remediator ./cmd/remediator")
    
    # Setup Python environment
    if not (PROJECT_ROOT / 'venv').exists():
        print(f"{Color.BLUE}[Python] Creating virtual environment...{Color.NC}")
        run_command("python3 -m venv venv")
    
    # Install Python dependencies
    print(f"{Color.BLUE}[Python] Installing dependencies...{Color.NC}")
    run_command("./venv/bin/pip install -q -r ml/train/requirements.txt")
    run_command("./venv/bin/pip install -q -r mcp/requirements.txt")
    
    # Train ML models if needed
    if not (PROJECT_ROOT / 'ml/train/models/random_forest_model.joblib').exists():
        print(f"\n{Color.BLUE}[ML] Training models (first time only)...{Color.NC}")
        run_command("cd ml/train && ../../venv/bin/python simple_train.py")
    
    # Import Grafana dashboards
    print(f"\n{Color.BLUE}[Grafana] Importing dashboards...{Color.NC}")
    success, output = run_command("python3 scripts/import_grafana_dashboards.py", capture=True)
    if success:
        print(f"{Color.GREEN}   ✓ Dashboards imported{Color.NC}")
    else:
        print(f"{Color.YELLOW}   ⚠ Dashboard import may have failed (check manually){Color.NC}")
    
    # Start services in background
    print(f"\n{Color.BLUE}[Services] Starting all services...{Color.NC}")
    
    # Set PYTHONPATH to include project root for proper imports
    pythonpath = f"PYTHONPATH={PROJECT_ROOT}"
    
    # Check if we should use real data only (disable test data generator)
    use_real_data_only = os.getenv("USE_REAL_DATA_ONLY", "true").lower() == "true"
    
    services = [
        ("ML Service", "./venv/bin/python ml/serve/predictor.py", "ml_service.pid", 8001),
        ("MCP Server", "./venv/bin/uvicorn mcp.server_ollama:app --host 0.0.0.0 --port 8000", "mcp_server.pid", 8000),
        ("Collector", "./bin/collector", "collector.pid", 9090),
        ("Remediator", "./bin/remediator", "remediator.pid", 9091),
        ("Orchestrator", "./venv/bin/python scripts/orchestrator.py", "orchestrator.pid", None),
    ]
    
    # Only add test data generator if explicitly enabled
    if not use_real_data_only:
        services.append(("Data Generator", "./venv/bin/python scripts/generate_test_data.py", "data_generator.pid", None))
        print(f"{Color.YELLOW}   ⚠ Test data generator enabled (set USE_REAL_DATA_ONLY=false to disable){Color.NC}")
    else:
        print(f"{Color.GREEN}   ✓ Using real pod data only (test data generator disabled){Color.NC}")
    
    for name, cmd, pid_file, port in services:
        log_file = LOG_DIR / f"{name.lower().replace(' ', '_')}.log"
        # Set PYTHONPATH environment variable for Python services
        if cmd.startswith("./venv/bin/python") or cmd.startswith("./venv/bin/uvicorn"):
            full_cmd = f"cd {PROJECT_ROOT} && env PYTHONPATH={PROJECT_ROOT} nohup {cmd} > {log_file} 2>&1 & echo $! > {PID_DIR / pid_file}"
        else:
            full_cmd = f"cd {PROJECT_ROOT} && nohup {cmd} > {log_file} 2>&1 & echo $! > {PID_DIR / pid_file}"
        run_command(full_cmd)
        time.sleep(2)
        
        if port:
            # Verify service started
            success, _ = run_command(f"lsof -ti :{port}", capture=True)
            if success:
                print(f"{Color.GREEN}   ✓ {name} started (port {port}){Color.NC}")
            else:
                print(f"{Color.YELLOW}   ⚠ {name} may have failed{Color.NC}")
        else:
            print(f"{Color.GREEN}   ✓ {name} started{Color.NC}")
    
    print(f"\n{Color.GREEN}╔════════════════════════════════════════════════════════════╗{Color.NC}")
    print(f"{Color.GREEN}║                 ✅ AURA K8s Started! 🚀                      ║{Color.NC}")
    print(f"{Color.GREEN}╚════════════════════════════════════════════════════════════╝{Color.NC}\n")
    
    print(f"{Color.CYAN}Access Points:{Color.NC}")
    print(f"  • ML Service:    http://localhost:8001/health")
    print(f"  • MCP Server:    http://localhost:8000/health")
    print(f"  • Collector:     http://localhost:9090/health")
    print(f"  • Remediator:    http://localhost:9091/health")
    print(f"  • Grafana:       http://localhost:3000 (admin/admin)\n")
    print(f"{Color.YELLOW}💡 Grafana dashboards available at: http://localhost:3000/dashboards{Color.NC}\n")
    
    return True


def stop_services():
    """Stop all AURA services"""
    print(f"\n{Color.YELLOW}[Shutdown] Stopping all services...{Color.NC}")
    
    # Stop services by PID
    if PID_DIR.exists():
        for pid_file in PID_DIR.glob('*.pid'):
            try:
                with open(pid_file) as f:
                    pid = f.read().strip()
                if pid:
                    run_command(f"kill -9 {pid}")
                    print(f"{Color.GREEN}   ✓ Stopped {pid_file.stem}{Color.NC}")
            except:
                pass
            pid_file.unlink()
    
    # Kill any remaining processes
    cleanup_ports()
    
    # Stop Docker
    print(f"{Color.BLUE}[Docker] Stopping containers...{Color.NC}")
    run_command("docker-compose down")
    
    print(f"\n{Color.GREEN}✓ All services stopped{Color.NC}\n")


def status_check():
    """Check status of all services"""
    print(f"\n{Color.BLUE}╔══════════════════════════════════════════════════════════════╗{Color.NC}")
    print(f"{Color.BLUE}║                    AURA K8s Status Check                     ║{Color.NC}")
    print(f"{Color.BLUE}╚══════════════════════════════════════════════════════════════╝{Color.NC}\n")
    
    # Check services
    services = [
        ("ML Service", ML_SERVICE_URL),
        ("MCP Server", MCP_SERVER_URL),
        ("Collector", "http://localhost:9090"),
        ("Remediator", "http://localhost:9091"),
        ("Grafana", "http://localhost:3000"),
    ]
    
    print(f"{Color.CYAN}Service Health:{Color.NC}")
    for name, url in services:
        try:
            resp = requests.get(f"{url}/health", timeout=2)
            if resp.status_code == 200:
                print(f"  {Color.GREEN}✓{Color.NC} {name:20} - Online")
            else:
                print(f"  {Color.RED}✗{Color.NC} {name:20} - Error")
        except:
            print(f"  {Color.RED}✗{Color.NC} {name:20} - Offline")
    
    # Check database
    print(f"\n{Color.CYAN}Database Status:{Color.NC}")
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Check database connectivity
        cur.execute("SELECT 1")
        cur.fetchone()
        print(f"  {Color.GREEN}✓{Color.NC} Database connection - OK")
        
        # Check tables and row counts
        tables = ['pod_metrics', 'ml_predictions', 'issues', 'remediation_actions']
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                print(f"  • {table:25} {count:>8} rows")
            except Exception as e:
                print(f"  {Color.YELLOW}⚠{Color.NC} {table:25} - Error: {str(e)[:50]}")
        
        conn.close()
    except Exception as e:
        print(f"  {Color.RED}✗ Database connection failed: {e}{Color.NC}")
    
    # Check Kubernetes cluster
    print(f"\n{Color.CYAN}Kubernetes Cluster:{Color.NC}")
    try:
        success, output = run_command("kubectl cluster-info", capture=True)
        if success:
            print(f"  {Color.GREEN}✓{Color.NC} Kubernetes cluster - Connected")
        else:
            print(f"  {Color.RED}✗{Color.NC} Kubernetes cluster - Not accessible")
    except Exception as e:
        print(f"  {Color.YELLOW}⚠{Color.NC} Kubernetes check failed: {e}")
    
    print()


def validate_system():
    """Run complete system validation"""
    print(f"\n{Color.BLUE}[Validation] Running system tests...{Color.NC}\n")
    
    # Use the existing validation script
    result = subprocess.run(["./venv/bin/python", "scripts/validate_system.py"])
    return result.returncode == 0


def test_pipeline():
    """Test end-to-end pipeline"""
    print(f"\n{Color.BLUE}[Testing] Running pipeline test...{Color.NC}\n")
    
    # Simple pipeline test
    tests = [
        ("Database connectivity", lambda: __import__('psycopg2').connect(DATABASE_URL)),
        ("ML Service health", lambda: requests.get(f"{ML_SERVICE_URL}/health", timeout=5).status_code == 200),
        ("MCP Server health", lambda: requests.get(f"{MCP_SERVER_URL}/health", timeout=5).status_code == 200),
    ]
    
    passed = 0
    for name, test_func in tests:
        try:
            test_func()
            print(f"{Color.GREEN}✓{Color.NC} {name}")
            passed += 1
        except Exception as e:
            print(f"{Color.RED}✗{Color.NC} {name}: {str(e)[:50]}")
    
    print(f"\n{Color.CYAN}Results: {passed}/{len(tests)} tests passed{Color.NC}\n")
    return passed == len(tests)


def show_logs():
    """Show recent logs from all services"""
    print(f"\n{Color.BLUE}[Logs] Recent activity:{Color.NC}\n")
    
    if LOG_DIR.exists():
        for log_file in sorted(LOG_DIR.glob('*.log')):
            print(f"{Color.CYAN}─── {log_file.name} {'─' * (50 - len(log_file.name))}{Color.NC}")
            run_command(f"tail -n 5 {log_file}")
            print()
    else:
        print(f"{Color.YELLOW}No logs found{Color.NC}")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='AURA K8s - Unified Management CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  start       Start all AURA services
  stop        Stop all services
  restart     Restart all services
  status      Check service status
  validate    Run system validation
  test        Test end-to-end pipeline
  logs        Show recent logs
  cleanup     Clean up ports and processes
  
Examples:
  python aura-cli.py start
  python aura-cli.py status
  python aura-cli.py restart
        """
    )
    
    parser.add_argument('command', 
                       choices=['start', 'stop', 'restart', 'status', 'validate', 'test', 'logs', 'cleanup'],
                       help='Command to execute')
    
    args = parser.parse_args()
    
    print_banner()
    
    try:
        if args.command == 'start':
            return 0 if start_services() else 1
        elif args.command == 'stop':
            stop_services()
            return 0
        elif args.command == 'restart':
            stop_services()
            time.sleep(2)
            return 0 if start_services() else 1
        elif args.command == 'status':
            status_check()
            return 0
        elif args.command == 'validate':
            return 0 if validate_system() else 1
        elif args.command == 'test':
            return 0 if test_pipeline() else 1
        elif args.command == 'logs':
            show_logs()
            return 0
        elif args.command == 'cleanup':
            cleanup_ports()
            return 0
    except KeyboardInterrupt:
        print(f"\n{Color.YELLOW}⏹️  Interrupted{Color.NC}")
        return 1
    except Exception as e:
        print(f"\n{Color.RED}❌ Error: {e}{Color.NC}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

