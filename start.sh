#!/bin/bash
# AURA K8s - Single Unified Startup Script
# Starts all services and verifies everything works

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AURA K8s - Starting System${NC}"
echo -e "${GREEN}========================================${NC}"

# Load environment
if [ ! -f .env.local ]; then
    cat > .env.local << 'EOF'
POSTGRES_USER=aura
POSTGRES_PASSWORD=aura_password
POSTGRES_DB=aura_metrics
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
DATABASE_URL=postgresql://aura:aura_password@localhost:5432/aura_metrics?sslmode=disable
ML_SERVICE_URL=http://localhost:8001
MCP_SERVER_URL=http://localhost:8000
GRAFANA_PORT=3000
GRAFANA_USER=admin
GRAFANA_PASSWORD=admin
COLLECTION_INTERVAL=500ms
USE_PARALLEL_COLLECTION=true
REMEDIATION_INTERVAL=30s
PREVENTIVE_REMEDIATION_INTERVAL=10s
ENABLE_PREVENTIVE_REMEDIATION=true
DRY_RUN=false
ENVIRONMENT=development
EOF
fi
export $(cat .env.local | grep -v '^#' | xargs)

# Prerequisites
echo -e "${BLUE}Checking prerequisites...${NC}"
for cmd in docker go python3 kind kubectl; do
    if ! command -v $cmd >/dev/null 2>&1; then
        echo -e "${RED}✗ $cmd not found${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ Prerequisites OK${NC}"

# Kubernetes cluster
echo -e "${BLUE}Setting up Kubernetes cluster...${NC}"
if ! kind get clusters | grep -q aura-k8s-local 2>/dev/null; then
    echo -e "${BLUE}Creating Kubernetes cluster...${NC}"
    kind create cluster --name aura-k8s-local --config configs/kind-cluster-simple.yaml 2>&1 || {
        echo -e "${RED}✗ Failed to create cluster${NC}"
        exit 1
    }
fi

# Ensure cluster is running
if ! kubectl cluster-info >/dev/null 2>&1; then
    echo -e "${BLUE}Starting Kubernetes cluster...${NC}"
    docker start aura-k8s-local-control-plane 2>/dev/null || true
    echo -e "${BLUE}Waiting for cluster to be ready (this may take up to 60 seconds)...${NC}"
    sleep 10  # Initial wait for container to start
    for i in {1..30}; do
        if kubectl cluster-info >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Kubernetes cluster running${NC}"
            # Verify nodes are ready
            if kubectl get nodes >/dev/null 2>&1; then
                NODE_STATUS=$(kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
                if [ "$NODE_STATUS" = "True" ]; then
                    echo -e "${GREEN}✓ Kubernetes node is ready${NC}"
                    break
                fi
            fi
        fi
        if [ $((i % 5)) -eq 0 ]; then
            echo -e "${BLUE}  Still waiting... ($i/30)${NC}"
        fi
        sleep 2
    done
    if ! kubectl cluster-info >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠ Kubernetes cluster may still be starting, continuing anyway...${NC}"
        echo -e "${YELLOW}  (Services will retry connection)${NC}"
    fi
fi
kubectl config use-context kind-aura-k8s-local 2>/dev/null || true

# Export KUBECONFIG for all services to use
# Try to get kind kubeconfig first, then fall back to default
if command -v kind >/dev/null 2>&1; then
    KIND_KUBECONFIG=$(kind get kubeconfig-path --name=aura-k8s-local 2>/dev/null || kind get kubeconfig --name=aura-k8s-local 2>/dev/null | head -1)
    if [ -n "$KIND_KUBECONFIG" ] && [ -f "$KIND_KUBECONFIG" ]; then
        export KUBECONFIG="$KIND_KUBECONFIG"
        echo -e "${GREEN}✓ KUBECONFIG exported: $KUBECONFIG${NC}"
    elif kind get kubeconfig --name=aura-k8s-local >/dev/null 2>&1; then
        # Create temp kubeconfig file from kind output
        TEMP_KUBECONFIG=$(mktemp /tmp/aura-kubeconfig-XXXXXX.yaml)
        kind get kubeconfig --name=aura-k8s-local > "$TEMP_KUBECONFIG" 2>/dev/null
        if [ -f "$TEMP_KUBECONFIG" ] && grep -q "apiVersion" "$TEMP_KUBECONFIG" 2>/dev/null; then
            export KUBECONFIG="$TEMP_KUBECONFIG"
            echo -e "${GREEN}✓ KUBECONFIG exported from kind: $KUBECONFIG${NC}"
        else
            rm -f "$TEMP_KUBECONFIG" 2>/dev/null
        fi
    fi
fi

# If KUBECONFIG still not set, try default location
if [ -z "$KUBECONFIG" ] || [ ! -f "$KUBECONFIG" ]; then
    DEFAULT_KUBECONFIG="$HOME/.kube/config"
    if [ -f "$DEFAULT_KUBECONFIG" ] && grep -q "apiVersion" "$DEFAULT_KUBECONFIG" 2>/dev/null; then
        export KUBECONFIG="$DEFAULT_KUBECONFIG"
        echo -e "${GREEN}✓ KUBECONFIG exported from default: $KUBECONFIG${NC}"
    fi
fi

# Export KIND_CLUSTER_NAME for MCP server
export KIND_CLUSTER_NAME="aura-k8s-local"

# Install metrics-server with complete configuration (RBAC, APIService, Health Checks)
# This ensures metrics-server is ALWAYS working every day and every time
if ! kubectl get deployment metrics-server -n kube-system >/dev/null 2>&1; then
    echo -e "${BLUE}Installing metrics-server with complete configuration...${NC}"
    kubectl apply -f configs/metrics-server.yaml
    echo -e "${BLUE}Waiting for metrics-server to be ready...${NC}"
    kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s || true
    sleep 15
    
    # Verify metrics-server is working
    echo -e "${BLUE}Verifying metrics-server health...${NC}"
    for i in {1..30}; do
        if kubectl top nodes >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Metrics-server ready and working${NC}"
            break
        fi
        if [ $i -eq 30 ]; then
            echo -e "${YELLOW}⚠ Metrics-server installed but not ready yet (will continue in background)${NC}"
        fi
        sleep 2
    done
else
    # Ensure metrics-server is always healthy - check and restart if needed
    echo -e "${BLUE}Checking metrics-server health...${NC}"
    if ! kubectl get deployment metrics-server -n kube-system -o jsonpath='{.status.readyReplicas}' | grep -q "1"; then
        echo -e "${YELLOW}Metrics-server not ready, ensuring it's running...${NC}"
        kubectl rollout restart deployment/metrics-server -n kube-system
        kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s || true
    fi
    
    # Verify APIService is registered
    if ! kubectl get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1; then
        echo -e "${YELLOW}APIService not found, applying complete configuration...${NC}"
        kubectl apply -f configs/metrics-server.yaml
    fi
    
    # Test metrics-server
    if kubectl top nodes >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Metrics-server is working${NC}"
    else
        echo -e "${YELLOW}⚠ Metrics-server may need a moment to become ready${NC}"
    fi
fi

# Ensure metrics-server is always working (health check)
echo -e "${BLUE}Running metrics-server health check...${NC}"
bash scripts/ensure-metrics-server.sh || echo -e "${YELLOW}Metrics-server health check completed with warnings${NC}"

# Note: User should deploy their own pods for metrics collection

# Docker services
echo -e "${BLUE}Starting TimescaleDB...${NC}"
docker-compose up -d timescaledb
for i in {1..30}; do
    if docker exec aura-timescaledb pg_isready -U aura >/dev/null 2>&1; then
        echo -e "${GREEN}✓ TimescaleDB ready${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}Starting Grafana...${NC}"
# Check if port 3000 is already in use
if lsof -ti:3000 >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Port 3000 already in use - checking if Grafana is running...${NC}"
    if docker ps | grep -q aura-grafana; then
        echo -e "${GREEN}✓ Grafana container already running${NC}"
    elif curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Grafana already running on port 3000 (external instance)${NC}"
    else
        echo -e "${YELLOW}⚠ Port 3000 in use but Grafana not detected - stopping conflicting process...${NC}"
        lsof -ti:3000 | xargs kill -9 2>/dev/null || true
        sleep 2
        docker-compose up -d grafana
    fi
elif docker ps | grep -q aura-grafana; then
    echo -e "${GREEN}✓ Grafana container running${NC}"
elif docker ps -a | grep -q aura-grafana; then
    echo -e "${BLUE}Starting existing Grafana container...${NC}"
    docker start aura-grafana 2>/dev/null || docker-compose up -d grafana
else
    echo -e "${BLUE}Creating Grafana container...${NC}"
    docker-compose up -d grafana
fi

# Wait for Grafana to be ready
for i in {1..30}; do
    if curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Grafana ready${NC}"
            break
        fi
    if [ $i -eq 30 ]; then
        echo -e "${YELLOW}⚠ Grafana taking longer than expected to start${NC}"
    fi
        sleep 2
    done

# Database schema
echo -e "${BLUE}Initializing database schema...${NC}"
docker exec aura-timescaledb psql -U aura -d aura_metrics -f /docker-entrypoint-initdb.d/init.sql >/dev/null 2>&1 || true

# Python environment
if [ ! -d venv ]; then
    echo -e "${BLUE}Creating Python environment...${NC}"
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r ml/train/requirements.txt -r mcp/requirements.txt -r ml/serve/requirements.txt

# Train models if needed
if [ ! -f ml/train/models/random_forest_model.joblib ]; then
    echo -e "${BLUE}Training ML models...${NC}"
    cd ml/train && python simple_train.py && cd ../..
fi

# Import Grafana dashboards
echo -e "${BLUE}Importing Grafana dashboards...${NC}"
for i in {1..10}; do
    if curl -s http://localhost:3000/api/health >/dev/null 2>&1; then
        python3 scripts/import_grafana_dashboards.py >/dev/null 2>&1 && break
    fi
    sleep 2
done

# Create directories
mkdir -p logs .pids

# Start services
echo -e "${BLUE}Starting services...${NC}"

start_service() {
    local name=$1
    local cmd=$2
    local pid_file=$3
    local port=$4
    local max_retries=${5:-3}
    
    # Kill existing process if running
    if [ -f .pids/$pid_file ]; then
        local old_pid=$(cat .pids/$pid_file 2>/dev/null)
        if [ -n "$old_pid" ] && ps -p $old_pid >/dev/null 2>&1; then
            kill $old_pid 2>/dev/null || true
            sleep 1
        fi
        rm .pids/$pid_file 2>/dev/null || true
    fi
    
    # Convert name to lowercase for log file
    local log_name=$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
    
    # Special handling for collector with auto-restart and KUBECONFIG refresh
    if [ "$name" = "Collector" ]; then
        # Create wrapper script with auto-restart and KUBECONFIG refresh
        cat > /tmp/collector-wrapper.sh << WRAPPER_EOF
#!/bin/bash
KUBECONFIG_PATH="$1"
BINARY_PATH="$2"
LOG_FILE="$3"

# Function to refresh KUBECONFIG from kind
refresh_kubeconfig() {
    if kind get clusters | grep -q aura-k8s-local 2>/dev/null; then
        kind get kubeconfig --name aura-k8s-local > "$KUBECONFIG_PATH" 2>/dev/null
        export KUBECONFIG="$KUBECONFIG_PATH"
        return 0
    fi
    return 1
}

# Initial KUBECONFIG refresh
refresh_kubeconfig

while true; do
    # Refresh KUBECONFIG every 30 seconds to handle kind cluster port changes
    refresh_kubeconfig
    
    export KUBECONFIG="$KUBECONFIG_PATH"
    "$BINARY_PATH" 2>&1
    EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo "$(date): Collector exited with code $EXIT_CODE, restarting in 5 seconds..." >> "$LOG_FILE"
        sleep 5
    else
        echo "$(date): Collector exited normally, restarting in 5 seconds..." >> "$LOG_FILE"
        sleep 5
    fi
done
WRAPPER_EOF
        chmod +x /tmp/collector-wrapper.sh
        nohup /tmp/collector-wrapper.sh "$KUBECONFIG" "./bin/collector" "logs/${log_name}.log" > logs/${log_name}.log 2>&1 &
        local pid=$!
    else
        # Start service with proper environment
    nohup bash -c "$cmd" > logs/${log_name}.log 2>&1 &
    local pid=$!
    fi
    
    echo $pid > .pids/$pid_file
    
    # Wait for service to start with retries
    local started=0
    if [ -n "$port" ]; then
        # Service with port - check port is listening
        for i in {1..30}; do
            if lsof -ti:$port >/dev/null 2>&1; then
                # Additional check: try to connect to health endpoint if available
                if [ "$port" = "8001" ] || [ "$port" = "8000" ] || [ "$port" = "9090" ] || [ "$port" = "9091" ]; then
                    if curl -sf http://localhost:$port/health >/dev/null 2>&1; then
                        started=1
                        break
                    fi
                else
                    started=1
                    break
                fi
            fi
            sleep 1
        done
        
        if [ $started -eq 1 ]; then
            echo -e "${GREEN}✓ $name started${NC}"
            return 0
        else
            # Check if process is still running
            if ps -p $pid >/dev/null 2>&1; then
                echo -e "${YELLOW}⚠ $name process running but port $port not ready (check logs/${log_name}.log)${NC}"
            else
                echo -e "${RED}✗ $name failed to start (check logs/${log_name}.log)${NC}"
                # Show last few lines of log
                if [ -f "logs/${log_name}.log" ]; then
                    echo -e "${YELLOW}  Last log lines:${NC}"
                    tail -5 "logs/${log_name}.log" | sed 's/^/  /'
                fi
                return 1
            fi
        fi
    else
        # Service without port - check process is running
        sleep 3
        for i in {1..10}; do
        if ps -p $pid >/dev/null 2>&1; then
                started=1
                break
            fi
            sleep 1
        done
        
        if [ $started -eq 1 ]; then
            echo -e "${GREEN}✓ $name started${NC}"
            return 0
        else
            echo -e "${RED}✗ $name failed to start (check logs/${log_name}.log)${NC}"
            if [ -f "logs/${log_name}.log" ]; then
                echo -e "${YELLOW}  Last log lines:${NC}"
                tail -5 "logs/${log_name}.log" | sed 's/^/  /'
            fi
            return 1
        fi
    fi
}

export PYTHONPATH=$(pwd)
cd $(dirname $0)

# Ensure kubeconfig is set correctly
KUBE_FILE="/tmp/aura-kubeconfig"
if kind get clusters | grep -q aura-k8s-local 2>/dev/null; then
    kind get kubeconfig --name aura-k8s-local > "$KUBE_FILE" 2>/dev/null
    export KUBECONFIG="$KUBE_FILE"
elif [ -f "$HOME/.kube/config" ]; then
    export KUBECONFIG="$HOME/.kube/config"
else
    kind get kubeconfig --name aura-k8s-local > "$KUBE_FILE" 2>/dev/null || true
    export KUBECONFIG="$KUBE_FILE"
fi

# Verify KUBECONFIG file exists and is valid
if [ ! -f "$KUBECONFIG" ]; then
    echo -e "${RED}✗ KUBECONFIG file not found: $KUBECONFIG${NC}"
    exit 1
fi
echo -e "${BLUE}Using KUBECONFIG: $KUBECONFIG${NC}"

# Build Go binaries first to catch compilation errors early
echo -e "${BLUE}Building Go services...${NC}"
if go build -o bin/collector ./cmd/collector 2>&1 | tee logs/build-collector.log; then
    echo -e "${GREEN}✓ Collector built${NC}"
else
    echo -e "${RED}✗ Collector build failed${NC}"
    exit 1
fi

if go build -o bin/remediator ./cmd/remediator 2>&1 | tee logs/build-remediator.log; then
    echo -e "${GREEN}✓ Remediator built${NC}"
else
    echo -e "${RED}✗ Remediator build failed${NC}"
    exit 1
fi

# Start services in order with dependencies
echo -e "${BLUE}Starting ML Service...${NC}"
if ! start_service "ML Service" "cd $(pwd) && source venv/bin/activate && python ml/serve/predictor.py" "ml-service.pid" "8001"; then
    echo -e "${YELLOW}⚠ ML Service failed, will retry after other services${NC}"
fi

echo -e "${BLUE}Starting MCP Server...${NC}"
# Ensure MCP server gets KUBECONFIG and KIND_CLUSTER_NAME
MCP_ENV="KUBECONFIG=$KUBECONFIG KIND_CLUSTER_NAME=aura-k8s-local"
# Export AI API keys from .env.local
if [ -n "$GEMINI_API_KEY" ]; then
    MCP_ENV="$MCP_ENV GEMINI_API_KEY=$GEMINI_API_KEY"
fi
if [ -n "$GROQ_API_KEY" ]; then
    MCP_ENV="$MCP_ENV GROQ_API_KEY=$GROQ_API_KEY"
fi
if [ -n "$GROQ_MODEL" ]; then
    MCP_ENV="$MCP_ENV GROQ_MODEL=$GROQ_MODEL"
fi
# Also export Ollama settings
if [ -n "$OLLAMA_URL" ]; then
    MCP_ENV="$MCP_ENV OLLAMA_URL=$OLLAMA_URL"
fi
if [ -n "$OLLAMA_MODEL" ]; then
    MCP_ENV="$MCP_ENV OLLAMA_MODEL=$OLLAMA_MODEL"
fi
if [ -n "$OLLAMA_REQUEST_TIMEOUT" ]; then
    MCP_ENV="$MCP_ENV OLLAMA_REQUEST_TIMEOUT=$OLLAMA_REQUEST_TIMEOUT"
fi
if ! start_service "MCP Server" "cd $(pwd) && source venv/bin/activate && $MCP_ENV python mcp/server_ollama.py" "mcp-server.pid" "8000"; then
    echo -e "${YELLOW}⚠ MCP Server failed, will retry after other services${NC}"
fi

echo -e "${BLUE}Starting Collector...${NC}"
if ! start_service "Collector" "cd $(pwd) && env KUBECONFIG=$KUBECONFIG ./bin/collector" "collector.pid" "9090"; then
    echo -e "${RED}✗ Collector failed to start${NC}"
    exit 1
fi

echo -e "${BLUE}Starting Remediator...${NC}"
if ! start_service "Remediator" "cd $(pwd) && env KUBECONFIG=$KUBECONFIG METRICS_PORT=9091 ./bin/remediator" "remediator.pid" "9091"; then
    echo -e "${RED}✗ Remediator failed to start${NC}"
    exit 1
fi

echo -e "${BLUE}Starting Orchestrator...${NC}"
if ! start_service "Orchestrator" "cd $(pwd) && source venv/bin/activate && python scripts/orchestrator.py" "orchestrator.pid" ""; then
    echo -e "${YELLOW}⚠ Orchestrator failed, will retry${NC}"
fi

echo -e "${BLUE}Starting Predictive Orchestrator...${NC}"
if ! start_service "Predictive Orchestrator" "cd $(pwd) && source venv/bin/activate && python scripts/predictive_orchestrator.py" "predictive-orchestrator.pid" ""; then
    echo -e "${YELLOW}⚠ Predictive Orchestrator failed, will retry${NC}"
fi

# Retry failed services
echo -e "${BLUE}Retrying failed services...${NC}"
sleep 5

if ! curl -sf http://localhost:8001/health >/dev/null 2>&1; then
    echo -e "${BLUE}Retrying ML Service...${NC}"
    start_service "ML Service" "cd $(pwd) && source venv/bin/activate && python ml/serve/predictor.py" "ml-service.pid" "8001" || true
fi

if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo -e "${BLUE}Retrying MCP Server...${NC}"
    start_service "MCP Server" "cd $(pwd) && source venv/bin/activate && $MCP_ENV python mcp/server_ollama.py" "mcp-server.pid" "8000" || true
fi

# Wait for services to initialize
echo -e "${BLUE}Waiting for services to initialize...${NC}"
sleep 10

# Verify services with retries
echo -e "${BLUE}Verifying services...${NC}"

verify_service() {
    local name=$1
    local url=$2
    local max_attempts=15
    
    for i in $(seq 1 $max_attempts); do
        if curl -sf "$url" >/dev/null 2>&1; then
            echo -e "${GREEN}✓ $name healthy${NC}"
            return 0
        fi
        if [ $i -lt $max_attempts ]; then
            sleep 2
        fi
    done
    echo -e "${YELLOW}⚠ $name not responding (may still be starting)${NC}"
    return 1
}

verify_service "ML Service" "http://localhost:8001/health"
verify_service "MCP Server" "http://localhost:8000/health"
verify_service "Collector" "http://localhost:9090/health"
verify_service "Remediator" "http://localhost:9091/health"

# Verify processes are running
echo -e "${BLUE}Verifying processes...${NC}"
for pid_file in ml-service.pid mcp-server.pid collector.pid remediator.pid orchestrator.pid predictive-orchestrator.pid; do
    if [ -f .pids/$pid_file ]; then
        pid=$(cat .pids/$pid_file 2>/dev/null)
        if [ -n "$pid" ] && ps -p $pid >/dev/null 2>&1; then
            name=$(echo $pid_file | sed 's/.pid$//' | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')
            echo -e "${GREEN}✓ $name process running (PID: $pid)${NC}"
        else
            name=$(echo $pid_file | sed 's/.pid$//' | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')
            echo -e "${YELLOW}⚠ $name process not found${NC}"
        fi
    else
        name=$(echo $pid_file | sed 's/.pid$//' | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')
        echo -e "${YELLOW}⚠ $name PID file not found${NC}"
    fi
done

# Verify metrics collection from Kubernetes pods
echo -e "${BLUE}Verifying pod metrics collection...${NC}"
sleep 30
RECENT_METRICS=$(docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM pod_metrics WHERE timestamp > NOW() - INTERVAL '2 minutes';" 2>/dev/null | tr -d ' ')
if [ -n "$RECENT_METRICS" ] && [ "$RECENT_METRICS" -gt 0 ]; then
    echo -e "${GREEN}✓ Collecting Kubernetes pod metrics: $RECENT_METRICS recent records${NC}"
    
    # Show sample of pod data
    SAMPLE=$(docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT pod_name, namespace, cpu_utilization, memory_utilization FROM pod_metrics ORDER BY timestamp DESC LIMIT 1;" 2>/dev/null)
    if [ -n "$SAMPLE" ]; then
        echo -e "${GREEN}  Sample: $SAMPLE${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Waiting for metrics collection. Ensure you have pods running in your cluster.${NC}"
fi

# Verify predictions
PREDICTIONS_COUNT=$(docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM ml_predictions WHERE timestamp > NOW() - INTERVAL '5 minutes';" 2>/dev/null | tr -d ' ')
if [ -n "$PREDICTIONS_COUNT" ] && [ "$PREDICTIONS_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ ML predictions active: $PREDICTIONS_COUNT recent predictions${NC}"
    
    # Get model accuracy from training metadata
    if [ -f ml/train/models/training_metadata.json ]; then
        ACCURACY=$(python3 -c "import json; data=json.load(open('ml/train/models/training_metadata.json')); perf=data.get('model_performance', {}); ens=perf.get('ensemble', {}); print('{:.2%}'.format(ens.get('accuracy', 0)))" 2>/dev/null || echo "N/A")
        if [ "$ACCURACY" != "N/A" ]; then
            echo -e "${GREEN}✓ Model accuracy: ${ACCURACY}${NC}"
        fi
    fi
else
    echo -e "${YELLOW}⚠ No predictions yet (orchestrator will process metrics once collected)${NC}"
fi

# Verify pod metrics collection (from any namespace)
ALL_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep -v kube-system | grep Running | wc -l | tr -d ' ')
if [ "$ALL_PODS" -gt 0 ]; then
    echo -e "${GREEN}✓ Kubernetes pods detected: $ALL_PODS pods${NC}"
    
    # Check if we're collecting metrics from any pods
    ANY_METRICS=$(docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(DISTINCT pod_name) FROM pod_metrics WHERE timestamp > NOW() - INTERVAL '5 minutes';" 2>/dev/null | tr -d ' ')
    if [ -n "$ANY_METRICS" ] && [ "$ANY_METRICS" -gt 0 ]; then
        echo -e "${GREEN}✓ Collecting metrics from Kubernetes pods: $ANY_METRICS pods${NC}"
    else
        echo -e "${YELLOW}⚠ Waiting for first metrics collection cycle...${NC}"
    fi
else
    echo -e "${YELLOW}⚠ No pods detected. Deploy your applications to start collecting metrics.${NC}"
fi

# Verify remediation
REMEDIATIONS_COUNT=$(docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM remediations WHERE success=true AND executed_at > NOW() - INTERVAL '5 minutes';" 2>/dev/null | tr -d ' ')
if [ -n "$REMEDIATIONS_COUNT" ] && [ "$REMEDIATIONS_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Remediations active: $REMEDIATIONS_COUNT recent successful${NC}"
fi

# Verify Grafana
if curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
    DASHBOARD_COUNT=$(curl -s -u admin:admin http://localhost:3000/api/search?type=dash-db 2>/dev/null | python3 -c "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    if [ "$DASHBOARD_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓ Grafana dashboards loaded: $DASHBOARD_COUNT dashboards${NC}"
    else
        echo -e "${YELLOW}⚠ Dashboards may not be loaded yet${NC}"
    fi
fi

echo ""
# Start metrics-server monitor in background (ensures metrics-server is always working)
echo -e "${BLUE}Starting metrics-server monitor (ensures metrics-server is always working)...${NC}"
nohup bash scripts/metrics-server-monitor.sh > logs/metrics-server-monitor.log 2>&1 &
echo $! > .metrics-server-monitor.pid
echo -e "${GREEN}✓ Metrics-server monitor started (PID: $(cat .metrics-server-monitor.pid))${NC}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}System Started Successfully${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Access Points:"
echo "  Grafana:     http://localhost:3000 (admin/admin)"
echo "  Collector:   http://localhost:9090"
echo "  Remediator:  http://localhost:9091"
echo "  ML Service:  http://localhost:8001"
echo "  MCP Server:  http://localhost:8000"
echo ""
echo "Logs: ./logs/"
echo "Stop: ./stop.sh"
echo ""
