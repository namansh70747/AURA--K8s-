#!/bin/bash
# AURA K8s - Stop Script
# Stops all running services

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Stopping AURA K8s services...${NC}"

# Stop metrics-server monitor if running
if [ -f .metrics-server-monitor.pid ]; then
    MONITOR_PID=$(cat .metrics-server-monitor.pid)
    if ps -p $MONITOR_PID > /dev/null 2>&1; then
        echo -e "${GREEN}Stopping metrics-server monitor (PID: $MONITOR_PID)...${NC}"
        kill $MONITOR_PID 2>/dev/null || true
        rm -f .metrics-server-monitor.pid
    fi
fi

# Create .pids directory if it doesn't exist
mkdir -p .pids

# Stop services by PID files
for pidfile in .pids/*.pid; do
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        name=$(basename "$pidfile" .pid)
        if ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}Stopping $name (PID: $pid)...${NC}"
            kill "$pid" 2>/dev/null || true
            rm "$pidfile"
        fi
    fi
done

# Also kill any remaining processes
pkill -f "go run cmd/collector" 2>/dev/null || true
pkill -f "go run cmd/remediator" 2>/dev/null || true
pkill -f "mcp/server_ollama.py" 2>/dev/null || true
pkill -f "scripts/orchestrator.py" 2>/dev/null || true

echo -e "${GREEN}All services stopped${NC}"

