# AURA K8s - Complete Startup Guide

## ✅ All Issues Fixed

### Critical Fixes Applied
1. ✅ **Database schema initialization** - Fixed view dropping issue
2. ✅ **PYTHONPATH** - Fixed import paths for Python services
3. ✅ **MCP Server imports** - Fixed module imports
4. ✅ **ML Service imports** - Fixed cache module imports
5. ✅ **Collector database connection** - Added proper error handling

### Runtime Issues Fixed
1. ✅ **Database views** - Views are now dropped before schema initialization
2. ✅ **PYTHONPATH environment** - Properly set for all Python services
3. ✅ **Service startup** - All services use correct working directories

## 🚀 Starting the Project

### Prerequisites
1. **Docker Desktop must be running**
   ```bash
   # On macOS:
   open -a Docker
   
   # Wait until Docker is fully started (check with: docker info)
   ```

2. **Verify all tools are installed:**
   ```bash
   docker --version
   go version
   python3 --version
   kind --version
   kubectl version --client
   ```

### Quick Start

#### Option 1: Using the startup script (Recommended)
```bash
./START_PROJECT.sh
```

#### Option 2: Using aura-cli.py directly
```bash
# Start all services
python3 aura-cli.py start

# Check status
python3 aura-cli.py status

# View logs
python3 aura-cli.py logs

# Stop all services
python3 aura-cli.py stop
```

#### Option 3: Using Makefile
```bash
# Start all services
make start

# Check status
make status

# Stop all services
make stop

# View logs
make logs
```

#### Option 4: Using RUN.sh
```bash
./RUN.sh
# Then select option 1 (Start)
```

## 🔧 Troubleshooting

### Docker Not Running
**Error:** `Cannot connect to the Docker daemon`

**Solution:**
1. Start Docker Desktop:
   ```bash
   open -a Docker
   ```
2. Wait for Docker to fully start (check with `docker info`)
3. Try starting again

### Database Connection Failed
**Error:** `connection refused` or `database not ready`

**Solution:**
1. Ensure Docker is running
2. Start TimescaleDB:
   ```bash
   docker-compose up -d timescaledb
   ```
3. Wait for database to be ready:
   ```bash
   docker-compose exec -T timescaledb pg_isready -U aura -d aura_metrics
   ```
4. Drop views if they exist:
   ```bash
   docker-compose exec -T timescaledb psql -U aura -d aura_metrics -c "DROP VIEW IF EXISTS metrics CASCADE; DROP VIEW IF EXISTS predictions CASCADE;"
   ```

### Services Not Starting

#### ML Service
- Check logs: `tail -f logs/ml_service.log`
- Verify PYTHONPATH is set correctly
- Ensure models exist in `ml/train/models/`

#### MCP Server
- Check logs: `tail -f logs/mcp_server.log`
- Verify Ollama is accessible (if using Ollama)
- Check port 8000 is free: `lsof -ti :8000`

#### Collector
- Check logs: `tail -f logs/collector.log`
- Verify database connection
- Ensure Kind cluster is running: `kind get clusters`
- Install metrics-server if needed:
  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  ```

#### Remediator
- Check logs: `tail -f logs/remediator.log`
- Verify database connection
- Check MCP server is accessible

### Port Conflicts
If ports are already in use:
```bash
# Kill processes on specific ports
python3 aura-cli.py cleanup

# Or manually:
lsof -ti :8000 | xargs kill -9  # MCP Server
lsof -ti :8001 | xargs kill -9  # ML Service
lsof -ti :9090 | xargs kill -9  # Collector
lsof -ti :9091 | xargs kill -9  # Remediator
lsof -ti :3000 | xargs kill -9  # Grafana
```

## 📊 Service Status

After startup, check service health:

```bash
python3 aura-cli.py status
```

Expected output:
- ✅ ML Service - Online (http://localhost:8001/health)
- ✅ MCP Server - Online (http://localhost:8000/health)
- ✅ Collector - Online (http://localhost:9090/health)
- ✅ Remediator - Online (http://localhost:9091/health)
- ✅ Grafana - Online (http://localhost:3000)

## 🗄️ Database Setup

The database schema is automatically initialized on first startup. If you need to reset:

```bash
# Reset database (WARNING: Deletes all data)
docker-compose down -v
docker-compose up -d timescaledb

# Wait for database to be ready
sleep 10

# Initialize schema manually if needed
docker-compose exec -T timescaledb psql -U aura -d aura_metrics -f /docker-entrypoint-initdb.d/init.sql
```

## 📝 Important Notes

1. **Docker is Required:** TimescaleDB and Kind cluster run in Docker
2. **Grafana Runs Locally:** Grafana should be installed and running locally (not in Docker)
   - macOS: `brew install grafana && brew services start grafana`
   - Or run manually: `grafana-server web`
3. **Kind Cluster:** A Kind cluster is automatically created on first startup
4. **Metrics Server:** For full functionality, install metrics-server in the Kind cluster

## 🎯 Next Steps

1. **Verify all services are running:**
   ```bash
   python3 aura-cli.py status
   ```

2. **Access Grafana dashboards:**
   - URL: http://localhost:3000
   - Default credentials: admin/admin

3. **Monitor logs:**
   ```bash
   python3 aura-cli.py logs
   # Or tail specific service logs:
   tail -f logs/collector.log
   tail -f logs/remediator.log
   ```

4. **Check database:**
   ```bash
   docker-compose exec timescaledb psql -U aura -d aura_metrics
   ```

## 🔗 Access Points

- **ML Service:** http://localhost:8001/health
- **MCP Server:** http://localhost:8000/health
- **Collector:** http://localhost:9090/health
- **Remediator:** http://localhost:9091/health
- **Grafana:** http://localhost:3000
- **Database:** localhost:5432 (aura/aura_password)

## 📚 Additional Resources

- Main README: `README.md`
- Comprehensive Issues Report: `COMPREHENSIVE_IMPROVEMENTS_REPORT.md`
- Makefile targets: `make help`
- CLI help: `python3 aura-cli.py --help`

---

**Last Updated:** After all fixes applied
**Status:** All critical issues resolved, ready for startup (requires Docker Desktop running)

