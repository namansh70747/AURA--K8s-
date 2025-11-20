#!/bin/bash
# AURA K8s - Complete Local Environment Startup Script

set -e

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/venv"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# PID file locations
PID_DIR="$PROJECT_ROOT/.pids"
mkdir -p "$PID_DIR"

# Log file locations
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}🛑 Stopping all services...${NC}"
    
    # Kill all background processes
    if [ -f "$PID_DIR/ml_service.pid" ]; then
        kill $(cat "$PID_DIR/ml_service.pid") 2>/dev/null || true
        rm "$PID_DIR/ml_service.pid"
    fi
    
    if [ -f "$PID_DIR/mcp_server.pid" ]; then
        kill $(cat "$PID_DIR/mcp_server.pid") 2>/dev/null || true
        rm "$PID_DIR/mcp_server.pid"
    fi
    
    if [ -f "$PID_DIR/collector.pid" ]; then
        kill $(cat "$PID_DIR/collector.pid") 2>/dev/null || true
        rm "$PID_DIR/collector.pid"
    fi
    
    if [ -f "$PID_DIR/remediator.pid" ]; then
        kill $(cat "$PID_DIR/remediator.pid") 2>/dev/null || true
        rm "$PID_DIR/remediator.pid"
    fi
    
    if [ -f "$PID_DIR/data_generator.pid" ]; then
        kill $(cat "$PID_DIR/data_generator.pid") 2>/dev/null || true
        rm "$PID_DIR/data_generator.pid"
    fi
    
    if [ -f "$PID_DIR/orchestrator.pid" ]; then
        kill $(cat "$PID_DIR/orchestrator.pid") 2>/dev/null || true
        rm "$PID_DIR/orchestrator.pid"
    fi
    
    if [ -f "$PID_DIR/grafana.pid" ]; then
        kill $(cat "$PID_DIR/grafana.pid") 2>/dev/null || true
        rm "$PID_DIR/grafana.pid"
    fi
    
    echo -e "${GREEN}✓ All services stopped${NC}"
    exit 0
}

trap cleanup INT TERM

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         🚀 AURA K8s - Local Environment Startup 🚀         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"

# Detect Kind cluster kubeconfig
CLUSTER_NAME="aura-k8s-local"
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo -e "${GREEN}✓ Found Kind cluster: ${CLUSTER_NAME}${NC}"
    # Get kubeconfig path for Kind cluster
    export KUBECONFIG="$HOME/.kube/config"
    # Set Kind context
    kubectl config use-context kind-${CLUSTER_NAME} >/dev/null 2>&1
else
    echo -e "${YELLOW}⚠️  Kind cluster '${CLUSTER_NAME}' not found. Using default kubeconfig${NC}"
    echo -e "${YELLOW}   Run './scripts/setup_complete_local.sh' first to create the cluster${NC}"
    export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
fi

echo -e "${BLUE}Using kubeconfig: ${KUBECONFIG}${NC}"

# Load environment variables
if [ -f "$PROJECT_ROOT/.env.local" ]; then
    echo -e "${GREEN}✓ Loading local environment variables${NC}"
    set -a
    source "$PROJECT_ROOT/.env.local"
    set +a
else
    echo -e "${RED}❌ .env.local not found. Please create it from .env.local template${NC}"
    exit 1
fi

# 1. Check Prerequisites
echo -e "\n${BLUE}[1/10] Checking Prerequisites...${NC}"

# Check Python
if ! command -v python3.11 &> /dev/null; then
    echo -e "${RED}❌ Python 3.11 is not installed${NC}"
    echo -e "${YELLOW}Installing Python 3.11...${NC}"
    brew install python@3.11 || exit 1
fi
echo -e "${GREEN}✓ Python 3.11 installed${NC}"

# Check Go
if ! command -v go &> /dev/null; then
    echo -e "${RED}❌ Go is not installed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Go installed: $(go version)${NC}"

# Check PostgreSQL
if ! command -v psql &> /dev/null; then
    echo -e "${YELLOW}⚠️  PostgreSQL not found. Installing...${NC}"
    brew install postgresql@14
fi
echo -e "${GREEN}✓ PostgreSQL installed${NC}"

# 2. Setup PostgreSQL
echo -e "\n${BLUE}[2/10] Setting up PostgreSQL...${NC}"

# Start PostgreSQL
brew services start postgresql@14 2>/dev/null || true
sleep 2

# Check if database exists
if psql -lqt -U $(whoami) 2>/dev/null | cut -d \| -f 1 | grep -qw aura_metrics; then
    echo -e "${GREEN}✓ Database already exists${NC}"
else
    echo -e "${YELLOW}Creating database...${NC}"
    
    # Create user and database
    psql postgres -c "DROP DATABASE IF EXISTS aura_metrics;" 2>/dev/null || true
    psql postgres -c "DROP USER IF EXISTS aura;" 2>/dev/null || true
    psql postgres -c "CREATE USER aura WITH PASSWORD 'aura_password';" || true
    psql postgres -c "CREATE DATABASE aura_metrics OWNER aura;"
    psql postgres -c "GRANT ALL PRIVILEGES ON DATABASE aura_metrics TO aura;"
    
    # Initialize schema
    psql -d aura_metrics -U aura -f "$SCRIPT_DIR/init-db-local.sql"
    
    echo -e "${GREEN}✓ Database created and initialized${NC}"
fi

# 3. Setup Python Virtual Environment
echo -e "\n${BLUE}[3/10] Setting up Python Virtual Environment...${NC}"

if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3.11 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo -e "${GREEN}✓ Virtual environment activated${NC}"

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies (this may take a few minutes)...${NC}"
pip install -q --upgrade pip setuptools wheel
pip install -q -r "$PROJECT_ROOT/mcp/requirements.txt"
pip install -q -r "$PROJECT_ROOT/ml/train/requirements.txt"
echo -e "${GREEN}✓ Python dependencies installed${NC}"

# 4. Build Go Services
echo -e "\n${BLUE}[4/10] Building Go Services...${NC}"

cd "$PROJECT_ROOT"

if [ ! -f "$PROJECT_ROOT/bin/collector" ]; then
    echo -e "${YELLOW}Building collector...${NC}"
    go build -o bin/collector ./cmd/collector
    echo -e "${GREEN}✓ Collector built${NC}"
else
    echo -e "${GREEN}✓ Collector already built${NC}"
fi

if [ ! -f "$PROJECT_ROOT/bin/remediator" ]; then
    echo -e "${YELLOW}Building remediator...${NC}"
    go build -o bin/remediator ./cmd/remediator
    echo -e "${GREEN}✓ Remediator built${NC}"
else
    echo -e "${GREEN}✓ Remediator already built${NC}"
fi

# 5. Train ML Models (if not already trained)
echo -e "\n${BLUE}[5/10] Checking ML Models...${NC}"

if [ ! -f "$PROJECT_ROOT/ml/train/models/random_forest_model.joblib" ]; then
    echo -e "${YELLOW}Training ML models...${NC}"
    cd "$PROJECT_ROOT/ml/train"
    python3 simple_train.py
    echo -e "${GREEN}✓ ML models trained${NC}"
else
    echo -e "${GREEN}✓ ML models already exist${NC}"
fi

cd "$PROJECT_ROOT"

# 6. Start ML Service
echo -e "\n${BLUE}[6/10] Starting ML Service...${NC}"

# Kill any existing process on port 8001
lsof -ti :8001 | xargs kill -9 2>/dev/null || true
sleep 1

export PYTHONPATH="$PROJECT_ROOT"
export MODEL_PATH="$PROJECT_ROOT/ml/train/models"
export PORT=8001

nohup python3 -m ml.serve.predictor > "$LOG_DIR/ml_service.log" 2>&1 &
echo $! > "$PID_DIR/ml_service.pid"

# Wait and check if ML service started successfully
sleep 3
if ps -p $(cat "$PID_DIR/ml_service.pid") > /dev/null 2>&1; then
    # Health check
    for i in {1..10}; do
        if curl -s http://localhost:8001/health > /dev/null 2>&1; then
            echo -e "${GREEN}✓ ML Service started (PID: $(cat "$PID_DIR/ml_service.pid"))${NC}"
            break
        fi
        sleep 1
    done
else
    echo -e "${RED}❌ ML Service failed to start${NC}"
    cat "$LOG_DIR/ml_service.log"
    exit 1
fi

# 7. Start MCP Server
echo -e "\n${BLUE}[7/10] Starting MCP Server...${NC}"

# Kill any existing process on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
sleep 1

# Check if Ollama is available (optional)
if command -v ollama &> /dev/null; then
    if ! pgrep -x ollama > /dev/null; then
        echo -e "${YELLOW}Starting Ollama...${NC}"
        nohup ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
        sleep 3
        echo -e "${GREEN}✓ Ollama started${NC}"
    else
        echo -e "${GREEN}✓ Ollama already running${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Ollama not installed (optional). MCP will work without it.${NC}"
fi

cd "$PROJECT_ROOT/mcp"
nohup uvicorn server_ollama:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/mcp_server.log" 2>&1 &
echo $! > "$PID_DIR/mcp_server.pid"

sleep 3
if ps -p $(cat "$PID_DIR/mcp_server.pid") > /dev/null 2>&1; then
    # Health check
    for i in {1..10}; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            echo -e "${GREEN}✓ MCP Server started (PID: $(cat "$PID_DIR/mcp_server.pid"))${NC}"
            break
        fi
        sleep 1
    done
else
    echo -e "${RED}❌ MCP Server failed to start${NC}"
    cat "$LOG_DIR/mcp_server.log"
    exit 1
fi

# 8. Start Collector
echo -e "\n${BLUE}[8/10] Starting Collector...${NC}"

cd "$PROJECT_ROOT"
export KUBECONFIG="${KUBECONFIG}"
nohup env KUBECONFIG="${KUBECONFIG}" ./bin/collector > "$LOG_DIR/collector.log" 2>&1 &
echo $! > "$PID_DIR/collector.pid"

sleep 3
if ps -p $(cat "$PID_DIR/collector.pid") > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Collector started (PID: $(cat "$PID_DIR/collector.pid"))${NC}"
else
    echo -e "${YELLOW}⚠️  Collector may have issues. Check logs: $LOG_DIR/collector.log${NC}"
    tail -10 "$LOG_DIR/collector.log"
fi

# 9. Start Remediator
echo -e "\n${BLUE}[9/10] Starting Remediator...${NC}"

nohup env KUBECONFIG="${KUBECONFIG}" ./bin/remediator > "$LOG_DIR/remediator.log" 2>&1 &
echo $! > "$PID_DIR/remediator.pid"

sleep 3
if ps -p $(cat "$PID_DIR/remediator.pid") > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Remediator started (PID: $(cat "$PID_DIR/remediator.pid"))${NC}"
else
    echo -e "${RED}❌ Remediator failed to start${NC}"
    cat "$LOG_DIR/remediator.log"
    exit 1
fi

# 10. Start Data Generator and Orchestrator
echo -e "\n${BLUE}[10/10] Starting Data Pipeline...${NC}"

cd "$PROJECT_ROOT/scripts"

# Start Data Generator
nohup python3 generate_test_data.py > "$LOG_DIR/data_generator.log" 2>&1 &
echo $! > "$PID_DIR/data_generator.pid"
sleep 2
echo -e "${GREEN}✓ Data Generator started (PID: $(cat "$PID_DIR/data_generator.pid"))${NC}"

# Start Orchestrator
nohup python3 orchestrator.py > "$LOG_DIR/orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/orchestrator.pid"
sleep 2
echo -e "${GREEN}✓ Orchestrator started (PID: $(cat "$PID_DIR/orchestrator.pid"))${NC}"

# Optional: Start Grafana
if command -v grafana-server &> /dev/null || command -v grafana &> /dev/null; then
    if ! pgrep -x grafana-server > /dev/null && ! pgrep -x grafana > /dev/null; then
        echo -e "${YELLOW}Starting Grafana...${NC}"

        GRAFANA_PREFIX=$(brew --prefix grafana 2>/dev/null || true)
        if [ -z "$GRAFANA_PREFIX" ]; then
            if [ -d "/opt/homebrew/opt/grafana" ]; then
                GRAFANA_PREFIX="/opt/homebrew/opt/grafana"
            else
                GRAFANA_PREFIX="/usr/local/opt/grafana"
            fi
        fi
        GRAFANA_HOME="$GRAFANA_PREFIX/share/grafana"

        if [ ! -d "$GRAFANA_HOME" ]; then
            echo -e "${RED}❌ Unable to locate Grafana home at $GRAFANA_HOME${NC}"
        else
            GRAFANA_STATE_DIR="$PROJECT_ROOT/.grafana"
            GRAFANA_PROVISIONING_DIR="$GRAFANA_STATE_DIR/provisioning"
            GRAFANA_DASHBOARD_DIR="$GRAFANA_STATE_DIR/dashboards"

            if [ -d "$GRAFANA_STATE_DIR" ] && [ "${PRESERVE_GRAFANA_STATE:-false}" != "true" ]; then
                echo -e "${YELLOW}   Resetting Grafana state at $GRAFANA_STATE_DIR (set PRESERVE_GRAFANA_STATE=true to keep it)${NC}"
                rm -rf "$GRAFANA_STATE_DIR"
            elif [ -d "$GRAFANA_STATE_DIR" ]; then
                echo -e "${YELLOW}   PRESERVE_GRAFANA_STATE=true -> keeping existing Grafana data${NC}"
            fi

            mkdir -p "$GRAFANA_STATE_DIR/data" \
                     "$GRAFANA_STATE_DIR/logs" \
                     "$GRAFANA_STATE_DIR/plugins" \
                     "$GRAFANA_PROVISIONING_DIR/datasources" \
                     "$GRAFANA_PROVISIONING_DIR/dashboards" \
                     "$GRAFANA_DASHBOARD_DIR"

            cp -f "$PROJECT_ROOT"/grafana/dashboards/*.json "$GRAFANA_DASHBOARD_DIR"/ 2>/dev/null || true
            cp -f "$PROJECT_ROOT/grafana/datasources/datasource-local.yml" "$GRAFANA_PROVISIONING_DIR/datasources/aura.yml" 2>/dev/null || true
            cp -f "$PROJECT_ROOT/grafana/dashboards/dashboard.yml" "$GRAFANA_PROVISIONING_DIR/dashboards/aura.yml" 2>/dev/null || true
            if [ -f "$GRAFANA_PROVISIONING_DIR/dashboards/aura.yml" ]; then
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    sed -i '' "s|/var/lib/grafana/dashboards|$GRAFANA_DASHBOARD_DIR|g" "$GRAFANA_PROVISIONING_DIR/dashboards/aura.yml"
                else
                    sed -i "s|/var/lib/grafana/dashboards|$GRAFANA_DASHBOARD_DIR|g" "$GRAFANA_PROVISIONING_DIR/dashboards/aura.yml"
                fi
            fi

            export GF_SECURITY_ADMIN_USER="${GF_SECURITY_ADMIN_USER:-admin}"
            export GF_SECURITY_ADMIN_PASSWORD="${GF_SECURITY_ADMIN_PASSWORD:-admin}"
            export GF_PATHS_PROVISIONING="$GRAFANA_PROVISIONING_DIR"
            export GF_PATHS_DATA="$GRAFANA_STATE_DIR/data"
            export GF_PATHS_LOGS="$GRAFANA_STATE_DIR/logs"
            export GF_PATHS_PLUGINS="$GRAFANA_STATE_DIR/plugins"

            GRAFANA_CMD=("grafana-server")
            if command -v grafana &> /dev/null; then
                GRAFANA_CMD=("grafana" "server")
            fi

            nohup "${GRAFANA_CMD[@]}" --homepath="$GRAFANA_HOME" > "$LOG_DIR/grafana.log" 2>&1 &
            echo $! > "$PID_DIR/grafana.pid"
            sleep 3
            echo -e "${GREEN}✓ Grafana started on http://localhost:3000${NC}"
            echo -e "${YELLOW}   Default credentials: ${GF_SECURITY_ADMIN_USER}/${GF_SECURITY_ADMIN_PASSWORD}${NC}"
        fi
    else
        echo -e "${GREEN}✓ Grafana already running${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Grafana not installed. Install with: brew install grafana${NC}"
fi

# Summary
echo -e "\n${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✅ AURA K8s Local Environment Started! ✅           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"

echo -e "\n${BLUE}📊 Service Status:${NC}"
echo -e "  ${GREEN}✓${NC} ML Service:      http://localhost:8001/health"
echo -e "  ${GREEN}✓${NC} MCP Server:      http://localhost:8000/health"
echo -e "  ${GREEN}✓${NC} Collector:       Running (logs: logs/collector.log)"
echo -e "  ${GREEN}✓${NC} Remediator:      http://localhost:9091/metrics"
echo -e "  ${GREEN}✓${NC} Data Generator:  Running (background)"
echo -e "  ${GREEN}✓${NC} Orchestrator:    Running (background)"
echo -e "  ${GREEN}✓${NC} Database:        localhost:5432/aura_metrics"
if command -v grafana-server &> /dev/null && pgrep -x grafana-server > /dev/null; then
    echo -e "  ${GREEN}✓${NC} Grafana:         http://localhost:3000"
fi

echo -e "\n${BLUE}📂 Log Files:${NC}"
echo -e "  ${YELLOW}→${NC} $LOG_DIR/"
echo -e "    - ml_service.log"
echo -e "    - mcp_server.log"
echo -e "    - collector.log"
echo -e "    - remediator.log"
echo -e "    - data_generator.log"
echo -e "    - orchestrator.log"

echo -e "\n${BLUE}🎯 Quick Commands:${NC}"
echo -e "  ${YELLOW}→${NC} View all logs:       tail -f $LOG_DIR/*.log"
echo -e "  ${YELLOW}→${NC} Stop all services:   ./scripts/stop_local.sh"
echo -e "  ${YELLOW}→${NC} Validate system:     cd scripts && python3 validate_system.py"
echo -e "  ${YELLOW}→${NC} Check database:      psql -d aura_metrics -U aura"

echo -e "\n${YELLOW}⏳ Services are warming up. Running system validation in 10 seconds...${NC}"
sleep 10

# Import Grafana dashboards
echo -e "\n${BLUE}📊 Importing Grafana Dashboards...${NC}"
"$SCRIPT_DIR/import_grafana_dashboards.sh" > /dev/null 2>&1 && \
    echo -e "${GREEN}✓ Grafana dashboards imported successfully${NC}" || \
    echo -e "${YELLOW}⚠ Dashboard import failed (Grafana may still be starting)${NC}"

# Validate system
echo -e "\n${BLUE}🔍 Running System Validation...${NC}"
cd "$PROJECT_ROOT/scripts"
python3 validate_system.py || true

echo -e "\n${GREEN}✅ System is ready! Press Ctrl+C to stop all services${NC}"
echo -e "${BLUE}Logs are being written to: $LOG_DIR/${NC}\n"

# Keep script running
wait
