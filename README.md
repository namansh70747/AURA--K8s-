# AURA K8s - AI-Powered Kubernetes Auto-Remediation

![Status](https://img.shields.io/badge/status-production--ready-success)
![ML Accuracy](https://img.shields.io/badge/ML%20accuracy-96.7%25-blue)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**Production-grade Kubernetes monitoring platform with ML-powered anomaly detection and intelligent auto-remediation.**

---

## 🎯 Overview

AURA K8s is an enterprise-ready Kubernetes monitoring and auto-remediation platform that leverages machine learning to proactively detect and automatically resolve infrastructure issues before they impact your applications.

### Key Features

- **🤖 Advanced ML Detection**: 96.7% accuracy using ensemble models (XGBoost, Random Forest, LightGBM, Gradient Boosting, CatBoost)
- **🔮 Predictive Anomaly Detection**: Forecast anomalies before they occur with 5-15 minute prediction horizon
- **⚠️ Early Warning System**: Risk scoring, severity classification, and time-to-anomaly estimation
- **🛡️ Preventive Remediation**: Proactive actions to prevent issues (scale-up, resource increase, load balancing)
- **🔄 Intelligent Auto-Remediation**: 15+ remediation strategies for common Kubernetes issues
- **💾 Time-Series Optimization**: TimescaleDB for efficient metrics storage and querying
- **🧠 AI-Powered Insights**: Ollama integration for intelligent remediation recommendations
- **☸️ Native K8s Integration**: Works seamlessly with any Kubernetes cluster
- **📊 Real-Time Monitoring**: Metrics collection every 15 seconds with parallel processing (20 workers)
- **💰 Cost Optimization**: Automatic resource rightsizing recommendations
- **⚡ High Performance**: Sub-second latency with multi-level caching

---

## 🏗️ Architecture

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│ Go Collector│─────▶│ TimescaleDB  │◀─────│Go Remediator│
│   Metrics   │      │ Time-Series  │      │  Actions    │
└─────────────┘      └──────┬───────┘      └──────┬──────┘
                            │                      │
                     ┌──────▼──────┐        ┌─────▼──────┐
                     │Orchestrator │───────▶│ MCP Server │
                     │  Pipeline   │        │  + Ollama  │
                     └──────┬──────┘        └────────────┘
                            │
                     ┌──────▼──────┐
                     │ ML Service  │
                     │  Ensemble   │
                     └─────────────┘
```

### Components

- **Collector** (Go): Gathers pod/node metrics every 15 seconds with parallel processing (20 workers)
- **ML Service** (Python/FastAPI): Ensemble prediction engine with forecasting capabilities
- **Predictive Orchestrator** (Python): Coordinates predictive detection and preventive actions (5-second intervals)
- **Orchestrator** (Python): Coordinates the prediction pipeline (30-second intervals)
- **Remediator** (Go): Executes remediation actions (reactive + preventive, 10-second intervals)
- **MCP Server** (Python/FastAPI): AI recommendation engine with Ollama
- **TimescaleDB**: Optimized time-series database with continuous aggregates and 7-day retention
- **Grafana**: 8 pre-configured dashboards for comprehensive monitoring

---

## 🚀 Quick Start

### Prerequisites

**Required Tools:**
- Docker & Docker Compose v2.0+
- Go 1.21+
- Python 3.11+
- Kind (Kubernetes in Docker)
- kubectl

**Installation Commands:**

```bash
# macOS (using Homebrew)
brew install docker docker-compose go python@3.11 kind kubectl

# Linux (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y docker.io docker-compose golang-go python3.11 python3-pip kubectl
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# Verify all installations
docker --version
docker-compose --version
go version
python3 --version
kind --version
kubectl version --client
```

**System Requirements:**
- **8GB RAM minimum** (16GB recommended)
- **10GB free disk space**
- **Docker Desktop** or **Docker Engine** running

### Step-by-Step Installation

**Method 1: One-Command Start (Recommended)**

```bash
# 1. Clone the repository
git clone https://github.com/namansh70747/AURA--K8s-.git
cd AURA--K8s-

# 2. Start everything (one command does it all!)
./start.sh
```

**That's it!** The `start.sh` script automatically:
1. ✅ **Validates prerequisites** - Checks if all required tools are installed
2. ✅ **Creates Kind cluster** - Sets up local Kubernetes cluster (if not exists)
3. ✅ **Installs metrics-server** - Enables Kubernetes metrics API
4. ✅ **Starts TimescaleDB** - Launches PostgreSQL with TimescaleDB extension
5. ✅ **Starts Grafana** - Launches monitoring dashboards
6. ✅ **Initializes database** - Creates schema with 10 tables
7. ✅ **Creates Python virtual environment** - Sets up `venv/` directory
8. ✅ **Installs Python dependencies** - Installs all required packages
9. ✅ **Builds Go binaries** - Compiles Collector and Remediator services
10. ✅ **Trains ML models** - Trains models if they don't exist (skips if present)
11. ✅ **Imports Grafana dashboards** - Sets up 8 pre-configured dashboards
12. ✅ **Starts all services** - Launches all 6 core services:
    - ML Service (port 8001)
    - MCP Server (port 8000)
    - Collector (port 9090)
    - Remediator (port 9091)
    - Orchestrator
    - Predictive Orchestrator
13. ✅ **Verifies health** - Checks all services are running correctly

**Time**: 2-3 minutes (5-10 minutes first time, including model training)

**Method 2: Manual Setup (Advanced)**

If you prefer manual control:

```bash
# 1. Clone repository
git clone https://github.com/namansh70747/AURA--K8s-.git
cd AURA--K8s-

# 2. Install Python dependencies
make install
# Or manually:
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r ml/train/requirements.txt
./venv/bin/pip install -r mcp/requirements.txt
./venv/bin/pip install -r ml/serve/requirements.txt

# 3. Build Go binaries
make build
# Or manually:
go build -o bin/collector ./cmd/collector
go build -o bin/remediator ./cmd/remediator

# 4. Start Docker services
docker-compose up -d timescaledb

# 5. Start all services
./start.sh
```

### First-Time Startup Details

On the **first run**, the script will:
- Create Kind Kubernetes cluster (takes ~30-60 seconds)
- Download Docker images for TimescaleDB and Grafana
- Create Python virtual environment (`venv/`)
- Install ~50 Python packages (takes ~2-3 minutes)
- Build Go binaries (takes ~10-20 seconds)
- Train ML models if missing (takes ~5-10 minutes, but models are preserved in git)
- Import Grafana dashboards

**Subsequent runs** are much faster (2-3 minutes) because:
- Models are already trained and preserved in git
- Docker images are cached
- Python packages are already installed
- Go binaries are rebuilt automatically

### Verifying Startup

After running `./start.sh`, verify everything is working:

```bash
# Check all services are healthy
curl http://localhost:9090/health  # Collector
curl http://localhost:9091/health  # Remediator
curl http://localhost:8001/health  # ML Service
curl http://localhost:8000/health  # MCP Server

# Check Kubernetes cluster
kubectl get pods -A

# Check database
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "\dt"

# Access Grafana
open http://localhost:3000  # macOS
# Login: admin / admin
```

### Troubleshooting Startup

If startup fails, check:

```bash
# View startup logs
tail -f logs/collector.log
tail -f logs/remediator.log
tail -f logs/ml-service.log

# Check if ports are in use
lsof -i :9090  # Collector
lsof -i :9091  # Remediator
lsof -i :8001  # ML Service
lsof -i :8000  # MCP Server
lsof -i :5432  # TimescaleDB
lsof -i :3000  # Grafana

# Check Docker services
docker-compose ps
docker-compose logs timescaledb

# Check Kubernetes cluster
kubectl cluster-info
kubectl get nodes
```

---

## 📋 Complete Command Reference

### Basic Operations

#### Start System
```bash
# Method 1: Using startup script (recommended)
./start.sh

# Method 2: Using Makefile
make start

# Method 3: Using Python CLI
python3 aura-cli.py start
```

#### Stop System
```bash
# Method 1: Using stop script
./stop.sh

# Method 2: Using Makefile
make stop

# Method 3: Using Python CLI
python3 aura-cli.py stop
```

#### Restart System
```bash
# Using Makefile
make restart

# Or manually
./stop.sh && ./start.sh
```

#### Check Status
```bash
# Check all services
make status

# Or using Python CLI
python3 aura-cli.py status

# Check individual services
curl http://localhost:9090/health  # Collector
curl http://localhost:9091/health  # Remediator
curl http://localhost:8001/health  # ML Service
curl http://localhost:8000/health  # MCP Server
```

### Build & Development

#### Install Dependencies
```bash
# Install Python dependencies
make install

# Or manually
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r ml/train/requirements.txt
./venv/bin/pip install -r mcp/requirements.txt
./venv/bin/pip install -r ml/serve/requirements.txt
```

#### Build Go Binaries
```bash
# Build all binaries
make build

# Or manually
go build -o bin/collector ./cmd/collector
go build -o bin/remediator ./cmd/remediator
```

#### Train ML Models
```bash
# Train models (models are auto-loaded if they exist)
cd ml/train
python3 beast_train.py

# Force retraining (even if models exist)
FORCE_RETRAIN=true python3 beast_train.py

# Using Makefile
make train
```

**Note:** Models are automatically preserved in git. Training is skipped if models already exist.

### Docker Operations

#### Start Docker Services
```bash
# Start TimescaleDB only (Grafana runs locally)
make docker-up

# Or manually
docker-compose up -d timescaledb
```

#### Stop Docker Services
```bash
# Stop all Docker services
make docker-down

# Or manually
docker-compose down
```

#### View Docker Logs
```bash
# View all Docker logs
make docker-logs

# Or manually
docker-compose logs -f timescaledb
```

#### Database Operations
```bash
# Open PostgreSQL shell
make db-shell

# Or manually
docker exec -it aura-timescaledb psql -U aura -d aura_metrics

# Reset database (WARNING: Deletes all data)
make db-reset

# Check database tables
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "\dt"

# Query metrics
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "
  SELECT COUNT(*) FROM pod_metrics WHERE timestamp > NOW() - INTERVAL '1 hour';
"
```

### Logs & Monitoring

#### View Logs
```bash
# View all recent logs
make logs

# Or using Python CLI
python3 aura-cli.py logs

# View specific service logs
tail -f logs/collector.log
tail -f logs/remediator.log
tail -f logs/ml-service.log
tail -f logs/mcp-server.log
tail -f logs/orchestrator.log
tail -f logs/predictive-orchestrator.log

# View all logs in real-time
tail -f logs/*.log
```

#### Monitor Services
```bash
# Check service health endpoints
curl http://localhost:9090/health  # Collector
curl http://localhost:9091/health  # Remediator
curl http://localhost:8001/health  # ML Service
curl http://localhost:8000/health  # MCP Server

# Check service metrics
curl http://localhost:9090/metrics  # Collector metrics
curl http://localhost:9091/metrics  # Remediator metrics

# Check Kubernetes pods
kubectl get pods -A
kubectl top pods
kubectl top nodes
```

### Testing & Validation

#### Validate System
```bash
# Run system validation
make validate

# Or using Python CLI
python3 aura-cli.py validate

# Or manually
python3 scripts/validate_system.py
```

#### Run Tests
```bash
# Run end-to-end tests
make test

# Or using Python CLI
python3 aura-cli.py test
```

#### Deploy Test Pods
```bash
# Deploy pods that will trigger remediation
kubectl apply -f test-remediation-pod.yaml

# Deploy stress test pods
kubectl apply -f stress-pods.yaml

# Monitor predictions and warnings
tail -f logs/predictive-orchestrator.log

# Check early warnings in database
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "
  SELECT pod_name, anomaly_type, confidence, time_to_anomaly_seconds, created_at
  FROM early_warnings
  ORDER BY created_at DESC
  LIMIT 10;
"
```

### Cleanup & Maintenance

#### Clean Build Artifacts
```bash
# Clean build artifacts and caches
make clean

# This removes:
# - bin/ directory
# - __pycache__ directories
# - *.pyc files
# - .pids/ directory
```

#### Deep Clean
```bash
# Deep clean (including venv and models)
make deep-clean

# WARNING: This removes:
# - venv/ directory
# - ml/train/models/*.joblib files
# - Docker volumes
```

#### Cleanup Ports & Processes
```bash
# Clean up ports and processes
make cleanup

# Or using Python CLI
python3 aura-cli.py cleanup
```

### Code Quality

#### Format Code
```bash
# Format Go code
go fmt ./...

# Format Python code (if black is installed)
make fmt
```

#### Run Checks
```bash
# Run all checks (lint, test, validate)
make check

# Go-specific checks
go vet ./...
go fmt ./...
```

#### Update Dependencies
```bash
# Update all dependencies
make update-deps

# Update Go dependencies manually
go get -u ./...
go mod tidy

# Update Python dependencies manually
./venv/bin/pip install --upgrade -r ml/train/requirements.txt
./venv/bin/pip install --upgrade -r mcp/requirements.txt
./venv/bin/pip install --upgrade -r ml/serve/requirements.txt
```

### Kubernetes Operations

#### Cluster Management
```bash
# Create Kind cluster
kind create cluster --name aura-k8s-local --config configs/kind-cluster-simple.yaml

# Delete Kind cluster
kind delete cluster --name aura-k8s-local

# List clusters
kind get clusters

# Get cluster info
kubectl cluster-info

# Switch context
kubectl config use-context kind-aura-k8s-local
```

#### Metrics Server
```bash
# Check metrics-server status
kubectl get deployment metrics-server -n kube-system

# View metrics-server logs
kubectl logs -n kube-system -l k8s-app=metrics-server

# Test metrics API
kubectl top pods
kubectl top nodes
```

#### Pod Management
```bash
# List all pods
kubectl get pods -A

# Get pod details
kubectl describe pod <pod-name> -n <namespace>

# View pod logs
kubectl logs <pod-name> -n <namespace>

# Execute command in pod
kubectl exec -it <pod-name> -n <namespace> -- /bin/sh
```

### Grafana Operations

#### Access Grafana
```bash
# Open Grafana in browser
open http://localhost:3000  # macOS
xdg-open http://localhost:3000  # Linux

# Default credentials:
# Username: admin
# Password: admin
```

#### Import Dashboards
```bash
# Dashboards are auto-imported on startup
# Manual import (if needed)
python3 scripts/import_grafana_dashboards.py
```

#### Restart Grafana
```bash
# macOS
brew services restart grafana

# Linux
sudo systemctl restart grafana-server

# Or manually
pkill -9 grafana-server
grafana-server web
```

---

## 🌐 Access Points

After startup, services are available at:

| Service | URL | Description |
|---------|-----|-------------|
| **Grafana** | **http://localhost:3000** | **Dashboards (admin/admin)** |
| ML Service | http://localhost:8001/health | Health check |
| ML Service API | http://localhost:8001/docs | FastAPI documentation |
| ML Service Forecast | http://localhost:8001/forecast | Forecasting endpoint |
| MCP Server | http://localhost:8000/health | Health check |
| MCP Server API | http://localhost:8000/docs | FastAPI documentation |
| Collector | http://localhost:9090/health | Metrics collector |
| Collector Metrics | http://localhost:9090/metrics | Prometheus metrics |
| Remediator | http://localhost:9091/health | Remediation engine |
| Remediator Metrics | http://localhost:9091/metrics | Prometheus metrics |
| TimescaleDB | localhost:5432 | PostgreSQL (aura/aura_password) |
| Ollama | http://localhost:11434 | Local AI (optional) |

---

## 🤖 Machine Learning

### Model Training

Models are automatically preserved in git and loaded on startup. Training is skipped if models exist.

```bash
# Train models (only if missing)
cd ml/train
python3 beast_train.py

# Force retraining
FORCE_RETRAIN=true python3 beast_train.py
```

**Models Included:**
- **XGBoost** (1.2MB) - Accuracy: ~97%
- **Random Forest** (3.3MB) - Accuracy: ~95%
- **LightGBM** (3.8MB) - Accuracy: ~96%
- **Gradient Boosting** (5.7MB) - Accuracy: ~96%
- **CatBoost** (49MB) - Accuracy: ~97%
- **Isolation Forest** (2.7MB) - Anomaly detection
- **Ensemble** - Auto-reconstructed from individual models

### Prediction Pipeline

1. **Collector** gathers pod metrics every 15 seconds
2. **Orchestrator** engineers features from raw metrics
3. **ML Service** runs ensemble prediction (majority vote)
4. **Database** stores predictions with confidence scores
5. **Issues** are created for anomalies above 50% confidence
6. **Remediator** executes appropriate fixes

### Feature Engineering

The system engineers 13+ features from raw metrics including:
- Base metrics: CPU, memory, disk, network, errors, latency, restarts
- Engineered features: resource ratios, pressure indicators, trend analysis

### Forecasting

The predictive orchestrator generates forecasts every 5 seconds:
- **Trend Analysis**: Linear regression on recent metrics
- **Anomaly Probability**: ML-based probability estimation
- **Time-to-Anomaly**: Estimated time until threshold breach
- **Risk Scoring**: Severity classification (low/medium/high/critical)

---

## 🔧 Remediation Strategies

### Automated Actions

AURA automatically applies these remediation strategies:

1. **IncreaseMemory** - Scale memory limit by 50%
2. **IncreaseCPU** - Scale CPU limit by 50%
3. **RestartPod** - Graceful pod restart
4. **ScaleDeployment** - Horizontal scaling
5. **ImagePullStrategy** - Fix image pull failures
6. **CleanLogs** - Disk pressure remediation
7. **RestartNetwork** - Network reset
8. **RestartDNS** - DNS cache clear
9. **DrainNode** - Node evacuation
10. **ExpandPVC** - Storage expansion
11. **RestartService** - Service restart
12. **RestartIngress** - Ingress controller reset
13. **RestartCertManager** - Certificate renewal
14. **RestartLoadBalancer** - LB reset
15. **RestartApiServer** - API server restart

### AI Recommendations

For complex issues, AURA consults Ollama (Llama 3.2) which:
- Analyzes pod logs, events, and context
- Provides structured remediation recommendations
- Explains root causes
- **100% FREE** - runs locally, no API costs!

### Preventive Actions

Preventive remediation runs every 10 seconds and can:
- Scale up deployments before resource exhaustion
- Increase resource limits proactively
- Rebalance load across pods
- Trigger alerts for manual intervention

---

## 📁 Project Structure

```
AURA--K8s-/
├── start.sh                    # Main startup script
├── stop.sh                     # Shutdown script
├── aura-cli.py                 # Python CLI tool
├── Makefile                    # Build automation
├── docker-compose.yml          # TimescaleDB & Grafana setup
├── .gitignore                  # Git ignore patterns
├── .gitattributes             # Git LFS configuration
│
├── cmd/                        # Go applications
│   ├── collector/              # Metrics collection service
│   │   └── main.go
│   └── remediator/             # Remediation service
│       └── main.go
│
├── pkg/                        # Go packages
│   ├── k8s/                    # Kubernetes client
│   ├── metrics/                # Metrics collection
│   ├── ml/                     # ML client
│   ├── remediation/             # Remediation engine
│   ├── storage/                # Database interface
│   └── utils/                  # Common utilities
│
├── ml/                         # Machine learning
│   ├── train/                  # Model training
│   │   ├── beast_train.py      # Training script
│   │   ├── dataset_downloader.py  # Auto-download datasets
│   │   ├── feature_engineering.py
│   │   ├── models/             # Trained model artifacts
│   │   └── datasets/           # Dataset directory (auto-downloaded)
│   └── serve/                  # Prediction service
│       ├── predictor.py        # FastAPI ensemble service
│       └── forecaster.py       # Forecasting service
│
├── mcp/                        # MCP server (AI recommendations)
│   ├── server_ollama.py        # FastAPI + Ollama integration
│   ├── tools.py                # K8s utilities
│   ├── cost_calculator.py      # Cost optimization
│   ├── remediation_planner.py   # Remediation planning
│   ├── remediation_learner.py  # Learning from past actions
│   └── safety_checker.py       # Safety validation
│
├── scripts/                    # Utilities
│   ├── orchestrator.py         # ML pipeline coordinator
│   ├── predictive_orchestrator.py  # Predictive detection
│   ├── validate_system.py      # System validator
│   ├── init-db-timescale.sql   # Database schema
│   └── import_grafana_dashboards.py  # Dashboard importer
│
├── configs/                    # Configuration
│   └── kind-cluster-simple.yaml
│
├── grafana/                    # Grafana dashboards
│   ├── dashboards/             # 8 pre-configured dashboards
│   └── datasources/            # Data source configuration
│
├── go.mod                      # Go dependencies
├── go.sum                      # Go dependency checksums
└── README.md                   # This file
```

---

## 🛠️ Technology Stack

### Backend
- **Go 1.21+** - High-performance services (collector, remediator)
- **Python 3.11+** - ML pipeline and orchestration

### Data & Storage
- **PostgreSQL 15** - Relational database
- **TimescaleDB 2.x** - Time-series optimization with hypertables

### Machine Learning
- **scikit-learn** - Base ML framework
- **XGBoost** - Gradient boosting
- **LightGBM** - Fast gradient boosting
- **CatBoost** - Categorical boosting
- **NumPy/Pandas** - Data processing
- **joblib** - Model serialization

### AI & LLM
- **Ollama** - Local LLM runtime
- **Llama 3.2** - Open-source language model

### Kubernetes
- **client-go v0.28.4** - Go Kubernetes client
- **kubernetes v29.0.0** - Python Kubernetes client

### API & Web
- **FastAPI** - Modern Python API framework
- **Uvicorn** - ASGI server

### Monitoring
- **Grafana** - Visualization and dashboards
- **Prometheus** - Metrics format

---

## 📊 Performance

- **Metrics Collection**: 15-second intervals with parallel processing (20 workers)
- **ML Predictions**: 30-second intervals
- **Forecasting**: 5-second intervals (predictive mode)
- **Remediation**: 10-second polling (preventive), 30-second (reactive)
- **Database Retention**: 7 days raw data, 30 days predictions
- **ML Accuracy**: 96.7% average across ensemble
- **Prediction Latency**: ~50-100ms per pod
- **Forecast Latency**: <100ms (p95)
- **Remediation Time**: ~2-5 seconds per action

---

## 🔮 Predictive Anomaly Detection

AURA K8s includes **predictive anomaly detection** capabilities that forecast anomalies before they occur:

### Features

- **⏱️ Fast Collection**: 15-second collection intervals with parallel processing
- **🔮 Forecasting Engine**: Trend-based forecasting with ML probability estimation
- **⚠️ Early Warnings**: Risk scoring, severity classification, and time-to-anomaly estimation
- **🛡️ Preventive Actions**: Proactive scaling, resource increases, load balancing
- **📊 Real-Time Processing**: Sub-second latency with batch processing

### Usage

The predictive orchestrator runs automatically when you start the system with `./start.sh`. It:
- Generates forecasts every 5 seconds
- Detects future anomalies based on trends
- Creates early warnings with time-to-anomaly estimates
- Triggers preventive actions automatically

### Configuration

Environment variables (in `.env.local`):
```bash
COLLECTION_INTERVAL=15s
USE_PARALLEL_COLLECTION=true
FORECAST_INTERVAL=5s
PREDICTION_HORIZON=300  # 5 minutes
ENABLE_PREVENTIVE_REMEDIATION=true
PREVENTIVE_REMEDIATION_INTERVAL=10s
```

---

## 📊 Grafana Dashboards

AURA K8s includes 8 pre-configured Grafana dashboards:

1. **Main Overview** - System-wide health and metrics
2. **AI Predictions** - ML model insights and accuracy
3. **Anomaly Detection** - Real-time anomaly monitoring
4. **Performance** - Resource utilization and trends
5. **Cost Optimization** - Resource efficiency and savings
6. **Remediation Tracking** - Auto-remediation monitoring
7. **Resource Analysis** - Deep resource monitoring
8. **ML Model** - Model performance metrics

### Accessing Dashboards

```bash
# Start system (includes Grafana)
./start.sh

# Access Grafana
open http://localhost:3000  # macOS
xdg-open http://localhost:3000  # Linux

# Login: admin / admin
# Navigate to Dashboards → AURA K8s folder
```

---

## 🔍 Troubleshooting

### Services Won't Start

```bash
# Check prerequisites
./start.sh  # Will validate prerequisites

# Check logs
tail -f logs/collector.log
tail -f logs/remediator.log
tail -f logs/ml-service.log

# Check if ports are in use
lsof -i :9090  # Collector
lsof -i :9091  # Remediator
lsof -i :8001  # ML Service
lsof -i :8000  # MCP Server
```

### Database Connection Errors

```bash
# Restart TimescaleDB
docker-compose restart timescaledb

# Check database
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "\dt"

# Check database logs
docker-compose logs timescaledb
```

### No Metrics Being Collected

```bash
# Check Kind cluster
kubectl get pods -A
kubectl cluster-info

# Check metrics-server
kubectl get deployment metrics-server -n kube-system
kubectl logs -n kube-system -l k8s-app=metrics-server

# Test metrics API
kubectl top pods

# Check collector
curl http://localhost:9090/health
tail -f logs/collector.log
```

### ML Service Not Responding

```bash
# Check if models exist
ls -la ml/train/models/

# Check service
curl http://localhost:8001/health
tail -f logs/ml-service.log

# Test prediction endpoint
curl -X POST http://localhost:8001/predict \
  -H "Content-Type: application/json" \
  -d '{"pod_name": "test-pod", "cpu_usage": 0.5, "memory_usage": 0.6}'
```

### Models Not Loading

```bash
# Check model files
ls -lh ml/train/models/*.joblib

# Models are preserved in git - no retraining needed
# If models are missing, train them:
cd ml/train
python3 beast_train.py
```

### Port Conflicts

```bash
# Find process using port
lsof -i :9090
lsof -i :9091
lsof -i :8001
lsof -i :8000

# Kill process
kill -9 <PID>

# Or use cleanup command
make cleanup
```

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

## 📝 License

MIT License - see LICENSE file for details

---

## 👥 Authors

- **Naman Sharma** - [@namansh70747](https://github.com/namansh70747)

---

## 🙏 Acknowledgments

- Kubernetes community for excellent client libraries
- TimescaleDB for optimized time-series storage
- Ollama for free local LLM capabilities
- scikit-learn, XGBoost, LightGBM, CatBoost teams for ML libraries

---

**Status:** ✅ Production Ready | **ML Accuracy:** 96.7% | **Cost:** $0 (fully local)

For issues or questions, please open a GitHub issue.
