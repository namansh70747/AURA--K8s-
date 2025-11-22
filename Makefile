.PHONY: help start stop restart status validate test clean build install

# AURA K8s - Unified Makefile
# Professional build and deployment automation

PYTHON := python3
GO := go
DOCKER_COMPOSE := docker-compose

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

help: ## Show this help message
	@echo ""
	@echo "$(BLUE)╔══════════════════════════════════════════════════════════════╗$(NC)"
	@echo "$(BLUE)║        █████╗ ██╗   ██╗██████╗  █████╗     ██╗  ██╗███████╗ ║$(NC)"
	@echo "$(BLUE)║       ██╔══██╗██║   ██║██╔══██╗██╔══██╗    ██║ ██╔╝██╔════╝ ║$(NC)"
	@echo "$(BLUE)║       ███████║██║   ██║██████╔╝███████║    █████╔╝ ███████╗ ║$(NC)"
	@echo "$(BLUE)║       ██╔══██║██║   ██║██╔══██╗██╔══██║    ██╔═██╗ ╚════██║ ║$(NC)"
	@echo "$(BLUE)║       ██║  ██║╚██████╔╝██║  ██║██║  ██║    ██║  ██╗███████║ ║$(NC)"
	@echo "$(BLUE)║       ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝    ╚═╝  ╚═╝╚══════╝ ║$(NC)"
	@echo "$(BLUE)╚══════════════════════════════════════════════════════════════╝$(NC)"
	@echo ""
	@echo "$(GREEN)Available targets:$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-15s$(NC) %s\n", $$1, $$2}'
	@echo ""

install: ## Install dependencies
	@echo "$(BLUE)Installing dependencies...$(NC)"
	@$(PYTHON) -m venv venv
	@./venv/bin/pip install --upgrade pip
	@./venv/bin/pip install -r ml/train/requirements.txt
	@./venv/bin/pip install -r mcp/requirements.txt
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

build: ## Build Go binaries
	@echo "$(BLUE)Building Go services...$(NC)"
	@mkdir -p bin
	@$(GO) build -o bin/collector ./cmd/collector
	@$(GO) build -o bin/remediator ./cmd/remediator
	@echo "$(GREEN)✓ Binaries built: bin/collector, bin/remediator$(NC)"

train: ## Train ML models
	@echo "$(BLUE)Training ML models...$(NC)"
	@cd ml/train && ../../venv/bin/$(PYTHON) simple_train.py
	@echo "$(GREEN)✓ Models trained$(NC)"

start: ## Start all services
	@echo "$(GREEN)Starting AURA K8s...$(NC)"
	@$(PYTHON) aura-cli.py start

stop: ## Stop all services
	@echo "$(YELLOW)Stopping AURA K8s...$(NC)"
	@$(PYTHON) aura-cli.py stop

restart: stop start ## Restart all services

status: ## Check service status
	@$(PYTHON) aura-cli.py status

validate: ## Run system validation
	@$(PYTHON) aura-cli.py validate

test: ## Run end-to-end tests
	@$(PYTHON) aura-cli.py test

logs: ## View recent logs
	@$(PYTHON) aura-cli.py logs

cleanup: ## Clean up ports and processes
	@$(PYTHON) aura-cli.py cleanup

clean: ## Clean build artifacts and caches
	@echo "$(BLUE)Cleaning build artifacts...$(NC)"
	@rm -rf bin/
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .pids/
	@echo "$(GREEN)✓ Cleaned$(NC)"

deep-clean: clean ## Deep clean including venv and models
	@echo "$(RED)Deep cleaning (including venv and models)...$(NC)"
	@rm -rf venv/
	@rm -rf ml/train/models/*.joblib
	@$(DOCKER_COMPOSE) down -v
	@echo "$(GREEN)✓ Deep cleaned$(NC)"

docker-up: ## Start only Docker services (TimescaleDB only - Grafana runs locally)
	@echo "$(BLUE)Starting Docker services...$(NC)"
	@$(DOCKER_COMPOSE) up -d timescaledb
	@echo "$(GREEN)✓ Docker services started (TimescaleDB)$(NC)"
	@echo "$(YELLOW)Note: Grafana should be started locally$(NC)"

docker-down: ## Stop Docker services
	@echo "$(YELLOW)Stopping Docker services...$(NC)"
	@$(DOCKER_COMPOSE) down
	@echo "$(GREEN)✓ Docker services stopped$(NC)"

docker-logs: ## View Docker logs
	@$(DOCKER_COMPOSE) logs -f

db-shell: ## Open PostgreSQL shell
	@$(DOCKER_COMPOSE) exec timescaledb psql -U aura -d aura_metrics

db-reset: ## Reset database (WARNING: Deletes all data)
	@echo "$(RED)Resetting database...$(NC)"
	@$(DOCKER_COMPOSE) down -v
	@$(DOCKER_COMPOSE) up -d timescaledb
	@sleep 5
	@echo "$(GREEN)✓ Database reset$(NC)"

grafana-restart: ## Restart Grafana (local)
	@echo "$(YELLOW)Grafana runs locally - restart manually or use:$(NC)"
	@echo "$(YELLOW)  macOS: brew services restart grafana$(NC)"
	@echo "$(YELLOW)  Linux: systemctl restart grafana-server$(NC)"
	@echo "$(YELLOW)  Or: pkill -9 grafana-server && grafana-server web$(NC)"

dev: ## Start in development mode (with hot reload where possible)
	@echo "$(BLUE)Starting in development mode...$(NC)"
	@$(MAKE) build
	@$(MAKE) start

check: ## Run all checks (lint, test, validate)
	@echo "$(BLUE)Running checks...$(NC)"
	@$(GO) vet ./...
	@$(GO) fmt ./...
	@$(MAKE) validate
	@echo "$(GREEN)✓ All checks passed$(NC)"

update-deps: ## Update dependencies
	@echo "$(BLUE)Updating Go dependencies...$(NC)"
	@$(GO) get -u ./...
	@$(GO) mod tidy
	@echo "$(BLUE)Updating Python dependencies...$(NC)"
	@./venv/bin/pip install --upgrade pip
	@./venv/bin/pip install --upgrade -r ml/train/requirements.txt
	@./venv/bin/pip install --upgrade -r mcp/requirements.txt
	@echo "$(GREEN)✓ Dependencies updated$(NC)"

fmt: ## Format code
	@echo "$(BLUE)Formatting Go code...$(NC)"
	@$(GO) fmt ./...
	@echo "$(BLUE)Formatting Python code...$(NC)"
	@./venv/bin/black ml/ mcp/ scripts/ *.py 2>/dev/null || echo "Install black: pip install black"
	@echo "$(GREEN)✓ Code formatted$(NC)"

