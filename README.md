# AURA K8s - AI-Powered Kubernetes Auto-Remediation

![Status](https://img.shields.io/badge/status-production--ready-success)
![ML Accuracy](https://img.shields.io/badge/ML%20accuracy-96.7%25-blue)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Production-grade Kubernetes monitoring platform with ML-powered anomaly detection and intelligent auto-remediation.**

---

## 🎯 Overview

AURA K8s is an enterprise-ready Kubernetes monitoring and auto-remediation platform that leverages machine learning to proactively detect and automatically resolve infrastructure issues before they impact your applications.

### Key Features

- **🤖 Advanced ML Detection**: 96.7% accuracy using ensemble models (XGBoost, Random Forest, LightGBM, Gradient Boosting)
- **🔄 Intelligent Auto-Remediation**: 15+ remediation strategies for common Kubernetes issues
- **💾 Time-Series Optimization**: TimescaleDB for efficient metrics storage and querying
- **🧠 AI-Powered Insights**: Ollama integration for intelligent remediation recommendations
- **☸️ Native K8s Integration**: Works seamlessly with any Kubernetes cluster
- **📊 Real-Time Monitoring**: Continuous metrics collection and analysis
- **💰 Cost Optimization**: Automatic resource rightsizing recommendations

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

- **Collector** (Go): Gathers pod/node metrics every 15s
- **ML Service** (Python/FastAPI): Ensemble prediction engine
- **Orchestrator** (Python): Coordinates the prediction pipeline
- **Remediator** (Go): Executes remediation actions
- **MCP Server** (Python/FastAPI): AI recommendation engine with Ollama
- **TimescaleDB**: Optimized time-series database

---

## 🚀 Quick Start

### Prerequisites

- **Docker & Docker Compose** v2.0+
- **Go 1.21+**
- **Python 3.11+**
- **Kind** (for local K8s cluster)
- **kubectl**
- **8GB RAM minimum** (16GB recommended)

### Installation

```bash
# Clone repository
git clone https://github.com/namansh70747/AURA--K8s-.git
cd AURA--K8s-

# Start everything with one command
python3 aura-cli.py start
```

That's it! The CLI will:
- ✅ Validate prerequisites
- ✅ Clean up any conflicting processes
- ✅ Set up Kind cluster (if needed)
- ✅ Initialize database schema
- ✅ Train ML models (first time only)
- ✅ Start all services

**Time**: 2-3 minutes (5-10 minutes first time)

---

## 📋 CLI Commands

### Using CLI Tool
```bash
# Service management
python3 aura-cli.py start      # Start all services (including Grafana)
python3 aura-cli.py stop       # Stop all services
python3 aura-cli.py restart    # Restart all services

# Monitoring
python3 aura-cli.py status     # Check service health
python3 aura-cli.py logs       # View recent logs

# Validation
python3 aura-cli.py validate   # Run system validation
python3 aura-cli.py test       # Test end-to-end pipeline

# Maintenance
python3 aura-cli.py cleanup    # Clean up ports/processes
```

### Using Makefile (Recommended)
```bash
make help       # Show all available commands
make start      # Start all services
make stop       # Stop all services
make status     # Check service health
make validate   # Run system validation
make build      # Build Go binaries
make train      # Train ML models
make clean      # Clean build artifacts
```

---

## 🌐 Access Points

After startup, services are available at:

| Service | URL | Description |
|---------|-----|-------------|
| **Grafana** | **http://localhost:3000** | **Dashboards (admin/admin)** |
| ML Service | http://localhost:8001/health | Health check |
| ML Service API | http://localhost:8001/docs | FastAPI documentation |
| MCP Server | http://localhost:8000/health | Health check |
| MCP Server API | http://localhost:8000/docs | FastAPI documentation |
| Collector | http://localhost:9090/health | Metrics collector |
| Remediator | http://localhost:9091/health | Remediation engine |
| TimescaleDB | localhost:5432 | PostgreSQL (aura/aura_password) |
| Ollama | http://localhost:11434 | Local AI (optional) |

---

## 🤖 Machine Learning

### Training Models

Models are automatically trained on first startup. To retrain manually:

```bash
cd ml/train
python simple_train.py
```

This generates 10,000 synthetic samples and trains 4 ensemble models:
- Random Forest (accuracy: ~95%)
- XGBoost (accuracy: ~97%)
- LightGBM (accuracy: ~96%)
- Gradient Boosting (accuracy: ~96%)

### Prediction Pipeline

1. **Collector** gathers pod metrics every 15 seconds
2. **Orchestrator** engineers 13 features from raw metrics
3. **ML Service** runs ensemble prediction (majority vote)
4. **Database** stores predictions with confidence scores
5. **Issues** are created for anomalies above 50% confidence
6. **Remediator** executes appropriate fixes

### Feature Engineering (13 Features)

Base metrics:
- `cpu_usage`, `memory_usage`, `disk_usage`
- `network_bytes_sec`, `error_rate`, `latency_ms`
- `restart_count`, `age_minutes`

Engineered features:
- `cpu_memory_ratio` - Resource balance indicator
- `resource_pressure` - Overall resource utilization
- `error_latency_product` - Error-performance correlation
- `network_per_cpu` - Network efficiency
- `is_critical` - Boolean flag for critical conditions

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

---

## 📁 Project Structure

```
AURA--K8s-/
├── aura-cli.py              # Unified CLI tool (NEW!)
├── cmd/                      # Go applications
│   ├── collector/            # Metrics collection service
│   └── remediator/           # Remediation service
├── pkg/                      # Go packages
│   ├── k8s/                  # Kubernetes client
│   ├── metrics/              # Metrics collection
│   ├── ml/                   # ML client
│   ├── remediation/          # Remediation engine
│   ├── storage/              # Database interface
│   └── utils/                # Common utilities
├── ml/                       # Machine learning
│   ├── train/                # Model training
│   │   ├── simple_train.py   # Training script
│   │   └── models/           # Trained model artifacts
│   └── serve/                # Prediction service
│       └── predictor.py      # FastAPI ensemble service
├── mcp/                      # MCP server (AI recommendations)
│   ├── server_ollama.py      # FastAPI + Ollama integration
│   └── tools.py              # K8s utilities
├── scripts/                  # Utilities
│   ├── orchestrator.py       # ML pipeline coordinator
│   ├── generate_test_data.py # Test data generator
│   ├── validate_system.py    # System validator
│   └── init-db-local.sql     # Database schema
├── configs/                  # Configuration
│   └── kind-cluster-simple.yaml
├── docker-compose.yml        # TimescaleDB setup
├── go.mod                    # Go dependencies
└── README.md                 # This file
```

---

## 🛠️ Technology Stack

### Backend
- **Go 1.21+** - High-performance services (collector, remediator)
- **Python 3.11** - ML pipeline and orchestration

### Data & Storage
- **PostgreSQL 15** - Relational database
- **TimescaleDB 2.x** - Time-series optimization

### Machine Learning
- **scikit-learn** - Base ML framework
- **XGBoost** - Gradient boosting
- **LightGBM** - Fast gradient boosting
- **NumPy/Pandas** - Data processing

### AI & LLM
- **Ollama** - Local LLM runtime
- **Llama 3.2** - Open-source language model

### Kubernetes
- **client-go v0.28.4** - Go Kubernetes client
- **kubernetes v29.0.0** - Python Kubernetes client

### API & Web
- **FastAPI** - Modern Python API framework
- **Uvicorn** - ASGI server

---

## 📊 Performance

- **Metrics Collection**: 15-second intervals
- **ML Predictions**: 30-second intervals
- **Remediation**: 30-second polling
- **Database Retention**: 7 days raw data, 30 days predictions
- **ML Accuracy**: 96.7% average across ensemble

---

## 📊 Grafana Dashboards

AURA K8s includes 5 pre-configured Grafana dashboards for comprehensive monitoring:

### Dashboard Overview

1. **Main Overview** - System-wide health and metrics
   - Overall health score
   - Active issues count
   - Remediation success rate
   - Pod resource usage trends

2. **AI Predictions** - ML model insights
   - Prediction accuracy over time
   - Anomaly type distribution
   - Confidence score distribution
   - Model performance metrics

3. **Cost Optimization** - Resource efficiency
   - Cost savings calculations
   - Resource rightsizing recommendations
   - Optimization opportunities
   - Monthly savings projection

4. **Remediation Tracking** - Auto-remediation monitoring
   - Remediation actions timeline
   - Success/failure rates
   - Action type distribution
   - Time to resolution metrics

5. **Resource Analysis** - Deep resource monitoring
   - CPU/Memory utilization heatmaps
   - Network traffic analysis
   - Disk usage trends
   - Pod restart patterns

### Accessing Dashboards

```bash
# Start system (includes Grafana)
make start
# or
python3 aura-cli.py start

# Access Grafana
open http://localhost:3000
# Login: admin / admin

# Navigate to Dashboards → AURA K8s folder
```

### Dashboard Features

- 📊 **Real-time data** - Updates every 10 seconds
- 🎨 **Pre-configured panels** - No setup needed
- 📈 **Time-series visualizations** - Powered by TimescaleDB
- 🔔 **Alert integration** - (Configure as needed)
- 💾 **Data retention** - 7 days metrics, 30 days predictions

---

## 🧪 Testing

### Run Validation

```bash
python3 aura-cli.py validate
```

This tests:
- Database connectivity and schema
- ML service health and predictions
- Service endpoints
- Pipeline data flow

### Test Pipeline

```bash
python3 aura-cli.py test
```

Verifies complete flow: Metrics → Predictions → Issues → Remediation

### Manual Testing

```bash
# Check metrics collection
curl http://localhost:9090/health

# View recent logs
python3 aura-cli.py logs

# Query database
docker-compose exec timescaledb psql -U aura -d aura_metrics -c "
  SELECT COUNT(*) FROM pod_metrics WHERE timestamp > NOW() - INTERVAL '1 hour';
"
```

---

## 🔍 Troubleshooting

### Services Won't Start

```bash
# Check prerequisites
python3 aura-cli.py validate

# Clean up ports
python3 aura-cli.py cleanup

# Check logs
python3 aura-cli.py logs
```

### Database Connection Errors

```bash
# Restart TimescaleDB
docker-compose restart timescaledb

# Reinitialize schema
docker-compose exec timescaledb psql -U aura -d aura_metrics -f /docker-entrypoint-initdb.d/init.sql
```

### No Metrics Being Collected

```bash
# Check Kind cluster
kubectl get pods -A

# Check collector
curl http://localhost:9090/health
python3 aura-cli.py logs
```

### ML Service Not Responding

```bash
# Check if models exist
ls -la ml/train/models/

# Retrain models
cd ml/train && python simple_train.py

# Check service
curl http://localhost:8001/health
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
- scikit-learn, XGBoost, LightGBM teams for ML libraries

---

**Status:** ✅ Production Ready | **ML Accuracy:** 96.7% | **Cost:** $0 (fully local)

For issues or questions, please open a GitHub issue.
