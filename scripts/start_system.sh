#!/bin/bash
# AURA K8s Complete System Startup Script

set -e

# Configuration
WAIT_SLEEP="${WAIT_SLEEP:-2}"  # Configurable sleep duration (default 2s)
INITIAL_DATA_WAIT="${INITIAL_DATA_WAIT:-5}"  # Wait for initial data (default 5s)

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Cleanup trap for interruption
cleanup() {
    echo -e "\n${YELLOW}Caught interrupt signal. Cleaning up...${NC}"
    docker-compose down
    exit 1
}
trap cleanup INT TERM

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║              🚀 AURA K8s System Startup 🚀                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}✗ Docker is not running. Please start Docker first.${NC}"
    exit 1
fi

echo -e "\n${GREEN}✓ Docker is running${NC}"

# Stop any existing containers
echo -e "\n${BLUE}Stopping existing containers...${NC}"
docker-compose down 2>/dev/null || true

# Clean up old data (optional - comment out if you want to keep data)
# echo -e "\n${YELLOW}Cleaning old data...${NC}"
# docker volume rm aura-timescaledb-data 2>/dev/null || true
# docker volume rm aura-grafana-data 2>/dev/null || true
# docker volume rm aura-ollama-data 2>/dev/null || true

# Build images
echo -e "\n${BLUE}Building Docker images...${NC}"
docker-compose build --no-cache

# Start core services first
echo -e "\n${BLUE}Starting TimescaleDB...${NC}"
docker-compose up -d timescaledb

# Wait for database
echo -e "${YELLOW}Waiting for database to be ready...${NC}"
for i in {1..30}; do
    if docker-compose exec -T timescaledb pg_isready -U aura -d aura_metrics > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Database is ready${NC}"
        break
    fi
    echo -n "."
    sleep "$WAIT_SLEEP"
done

# Start ML service
echo -e "\n${BLUE}Starting ML Service...${NC}"
docker-compose up -d ml-service

# Wait for ML service
echo -e "${YELLOW}Waiting for ML service to be ready...${NC}"
for i in {1..30}; do
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ ML Service is ready${NC}"
        break
    fi
    echo -n "."
    sleep "$WAIT_SLEEP"
done

# Start Ollama
echo -e "\n${BLUE}Starting Ollama (Local AI)...${NC}"
docker-compose up -d ollama

# Wait for Ollama
echo -e "${YELLOW}Waiting for Ollama to be ready...${NC}"
for i in {1..30}; do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Ollama is ready${NC}"
        break
    fi
    echo -n "."
    sleep "$WAIT_SLEEP"
done

# Pull Ollama model (in background)
echo -e "\n${BLUE}Pulling Ollama model (llama2)...${NC}"
docker-compose exec -T ollama ollama pull llama2 &

# Start MCP server
echo -e "\n${BLUE}Starting MCP Server...${NC}"
docker-compose up -d mcp-server

# Start Grafana
echo -e "\n${BLUE}Starting Grafana...${NC}"
docker-compose up -d grafana

# Wait for Grafana
echo -e "${YELLOW}Waiting for Grafana to be ready...${NC}"
for i in {1..30}; do
    if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Grafana is ready${NC}"
        break
    fi
    echo -n "."
    sleep "$WAIT_SLEEP"
done

# Start data generator
echo -e "\n${BLUE}Starting Data Generator...${NC}"
docker-compose up -d data-generator

# Wait a bit for initial data
sleep "$INITIAL_DATA_WAIT"

# Start orchestrator
echo -e "\n${BLUE}Starting Orchestrator...${NC}"
docker-compose up -d orchestrator

# Show status
echo -e "\n${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    System Status                           ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"

docker-compose ps

# Show access URLs
echo -e "\n${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                  🎉 System is Running! 🎉                  ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo -e "\n${BLUE}Access Points:${NC}"
echo -e "  ${GREEN}📊 Grafana:${NC}      http://localhost:3000 (admin/admin)"
echo -e "  ${GREEN}🤖 ML Service:${NC}   http://localhost:8001"
echo -e "  ${GREEN}🧠 MCP Server:${NC}   http://localhost:8000"
echo -e "  ${GREEN}🦙 Ollama:${NC}       http://localhost:11434"
echo -e "  ${GREEN}💾 TimescaleDB:${NC}  localhost:5432 (aura/aura_password)"

echo -e "\n${YELLOW}Useful Commands:${NC}"
echo -e "  ${BLUE}View logs:${NC}       docker-compose logs -f [service]"
echo -e "  ${BLUE}Stop all:${NC}        docker-compose down"
echo -e "  ${BLUE}Restart:${NC}         docker-compose restart [service]"
echo -e "  ${BLUE}Validate:${NC}        python3 scripts/validate_system.py"

echo -e "\n${GREEN}✓ Startup complete!${NC}"
echo -e "${YELLOW}💡 Tip: Run 'python3 scripts/validate_system.py' to test everything${NC}\n"
