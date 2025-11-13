# AURA K8s - Complete Setup & Testing Guide

## 🎯 Overview

This guide provides complete end-to-end setup, testing, and troubleshooting for the AURA K8s monitoring and auto-remediation system.

## 📋 System Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  Collector  │────▶│  TimescaleDB │────▶│  ML Service │────▶│  Predictions │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
                                                                    │
                                                                    ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│ Remediator  │◀────│  MCP Server  │◀────│   Issues   │◀────│ Orchestrator │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
```

## 🚀 Quick Start

### 1. Start All Services

```bash
# Start all services with Docker Compose
docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f
```

### 2. Verify Services

```bash
# Check database
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "SELECT COUNT(*) FROM pod_metrics;"

# Check ML service
curl http://localhost:8001/health

# Check MCP server
curl http://localhost:8000/health

# Check Grafana
curl http://localhost:3000/api/health
```

### 3. Access Dashboards

- **Grafana**: http://localhost:3000
  - Username: `admin`
  - Password: `admin`
- **Dashboards**: Navigate to "Dashboards" → "AURA K8s"

## 📊 Data Flow

### Step 1: Metrics Collection
- **Service**: `data-generator` (or `collector` in K8s)
- **Frequency**: Every 10 seconds
- **Storage**: `pod_metrics` table in TimescaleDB
- **Check**: `SELECT COUNT(*) FROM pod_metrics;`

### Step 2: ML Predictions
- **Service**: `orchestrator`
- **Frequency**: Every 30 seconds
- **Process**: 
  1. Reads recent metrics
  2. Calls ML service for predictions
  3. Stores in `ml_predictions` table
- **Check**: `SELECT COUNT(*) FROM ml_predictions;`

### Step 3: Issue Creation
- **Service**: `orchestrator`
- **Process**:
  1. Finds anomaly predictions (confidence > 0.7)
  2. Creates issues in `issues` table
- **Check**: `SELECT COUNT(*) FROM issues WHERE status = 'Open';`

### Step 4: Remediation
- **Service**: `remediator`
- **Frequency**: Every 30 seconds
- **Process**:
  1. Gets open issues
  2. Calls MCP server for AI recommendations
  3. Executes remediation actions
  4. Records in `remediations` table
- **Check**: `SELECT COUNT(*) FROM remediations;`

## 🔧 Troubleshooting

### Issue: "No data" in Grafana dashboards

**Solution 1: Check if data exists**
```bash
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "
SELECT 
    'pod_metrics' as table_name, COUNT(*) as count 
FROM pod_metrics
UNION ALL
SELECT 'ml_predictions', COUNT(*) FROM ml_predictions
UNION ALL
SELECT 'issues', COUNT(*) FROM issues
UNION ALL
SELECT 'remediations', COUNT(*) FROM remediations;
"
```

**Solution 2: Check time range**
- In Grafana, ensure time range includes recent data
- Try "Last 1 hour" or "Last 6 hours"

**Solution 3: Verify data generator is running**
```bash
docker logs aura-data-generator
docker logs aura-orchestrator
```

**Solution 4: Manually generate data**
```bash
# Generate test data
docker exec aura-data-generator python generate_test_data.py

# Generate predictions
docker exec aura-orchestrator python orchestrator.py
```

### Issue: ML Service not responding

**Check:**
```bash
# Check if service is running
docker ps | grep ml-service

# Check logs
docker logs aura-ml-service

# Test endpoint
curl http://localhost:8001/health
curl http://localhost:8001/models
```

**Fix:**
```bash
# Restart service
docker-compose restart ml-service

# Check if models are loaded
docker exec aura-ml-service ls -la /app/ml/train/models/
```

### Issue: MCP Server not working

**Check:**
```bash
# Check if service is running
docker ps | grep mcp-server

# Check logs
docker logs aura-mcp-server

# Test endpoint
curl http://localhost:8000/health
```

**Fix:**
```bash
# Restart service
docker-compose restart mcp-server

# Check if Anthropic API key is set (optional - uses Ollama by default)
docker exec aura-mcp-server env | grep ANTHROPIC
```

### Issue: Database connection errors

**Check:**
```bash
# Verify database is running
docker ps | grep timescaledb

# Test connection
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "SELECT 1;"

# Check credentials
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "\du"
```

**Fix:**
```bash
# Restart database
docker-compose restart timescaledb

# Reinitialize schema
docker exec -i aura-timescaledb psql -U aura -d aura_metrics < scripts/init-db.sql
```

## 🤖 Interacting with MCP Server

### Method 1: Via Remediator (Automatic)
The remediator automatically calls MCP server when issues are detected.

### Method 2: Direct API Call

```bash
# Analyze an issue
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "issue_id": "test-123",
    "pod_name": "web-frontend-1234-abc",
    "namespace": "production",
    "issue_type": "high_cpu",
    "severity": "high",
    "description": "CPU usage above 80%"
  }'
```

**Response:**
```json
{
  "action": "scale_deployment",
  "action_details": "Scale deployment to handle CPU load",
  "reasoning": "High CPU indicates need for more replicas",
  "confidence": 0.85
}
```

### Method 3: Python Client

```python
import requests

mcp_url = "http://localhost:8000"

# Health check
response = requests.get(f"{mcp_url}/health")
print(response.json())

# Analyze issue
issue = {
    "issue_id": "test-123",
    "pod_name": "web-frontend-1234-abc",
    "namespace": "production",
    "issue_type": "high_cpu",
    "severity": "high",
    "description": "CPU usage above 80%"
}

response = requests.post(f"{mcp_url}/analyze", json=issue)
recommendation = response.json()
print(f"Action: {recommendation['action']}")
print(f"Confidence: {recommendation['confidence']}")
```

## 📈 Monitoring Data Generation

### Check Metrics Count
```sql
SELECT 
    COUNT(*) as total_metrics,
    COUNT(DISTINCT pod_name) as unique_pods,
    MIN(timestamp) as earliest,
    MAX(timestamp) as latest
FROM pod_metrics;
```

### Check Predictions
```sql
SELECT 
    COALESCE(predicted_issue, 'unknown') as issue_type,
    COUNT(*) as count,
    AVG(confidence) as avg_confidence
FROM ml_predictions
WHERE timestamp > NOW() - INTERVAL '1 hour'
GROUP BY predicted_issue
ORDER BY count DESC;
```

### Check Issues
```sql
SELECT 
    issue_type,
    severity,
    status,
    COUNT(*) as count
FROM issues
GROUP BY issue_type, severity, status
ORDER BY count DESC;
```

### Check Remediations
```sql
SELECT 
    strategy,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE success = true) as successful,
    AVG(time_to_resolve) as avg_duration
FROM remediations
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY strategy
ORDER BY total DESC;
```

## 🧪 Testing End-to-End Flow

### Test 1: Generate Metrics
```bash
# Watch data generator
docker logs -f aura-data-generator

# Should see: "✅ Stored X metrics"
```

### Test 2: Generate Predictions
```bash
# Watch orchestrator
docker logs -f aura-orchestrator

# Should see: "✅ Generated X predictions"
```

### Test 3: Verify in Database
```bash
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "
SELECT 
    (SELECT COUNT(*) FROM pod_metrics) as metrics,
    (SELECT COUNT(*) FROM ml_predictions) as predictions,
    (SELECT COUNT(*) FROM issues WHERE status = 'Open') as open_issues,
    (SELECT COUNT(*) FROM remediations) as remediations;
"
```

### Test 4: Check Grafana
1. Open http://localhost:3000
2. Navigate to "Main Overview" dashboard
3. Set time range to "Last 1 hour"
4. All panels should show data

## 🔄 Automatic Remediation

### How It Works

1. **Issue Detection**: Orchestrator creates issues from high-confidence predictions
2. **AI Analysis**: Remediator calls MCP server for recommendations
3. **Action Execution**: Remediator executes the recommended action
4. **Tracking**: All actions are logged in `remediations` table

### Supported Actions

- `restart_pod` - Restart pod to recover from issues
- `increase_memory` - Increase memory limits by 50%
- `increase_cpu` - Increase CPU limits by 50%
- `scale_deployment` - Scale deployment up by 1 replica
- `clean_logs` - Clean logs (restarts pod)

### Check Remediation Status

```sql
SELECT 
    pod_name,
    namespace,
    strategy,
    success,
    executed_at,
    error_message
FROM remediations
ORDER BY executed_at DESC
LIMIT 10;
```

## 📝 Configuration

### Environment Variables

**Database:**
- `DATABASE_URL`: PostgreSQL connection string
- Default: `postgresql://aura:aura_password@timescaledb:5432/aura_metrics`

**ML Service:**
- `ML_SERVICE_URL`: ML prediction service URL
- Default: `http://ml-service:8001`

**MCP Server:**
- `MCP_SERVICE_URL`: MCP server URL
- Default: `http://mcp-server:8000`
- `ANTHROPIC_API_KEY`: (Optional) Anthropic API key for Claude

### Service Intervals

- **Data Generator**: 10 seconds
- **Orchestrator**: 30 seconds
- **Remediator**: 30 seconds
- **Collector**: 15 seconds (in K8s)

## 🐛 Common Issues & Fixes

### Issue: Services not starting

```bash
# Check Docker Compose logs
docker-compose logs

# Check individual service
docker logs <service-name>

# Restart specific service
docker-compose restart <service-name>
```

### Issue: Database schema errors

```bash
# Reinitialize schema
docker exec -i aura-timescaledb psql -U aura -d aura_metrics < scripts/init-db.sql

# Check if views exist
docker exec aura-timescaledb psql -U aura -d aura_metrics -c "\dv"
```

### Issue: Grafana dashboards not loading

```bash
# Check Grafana logs
docker logs aura-grafana

# Verify datasource
# Go to Grafana → Configuration → Data Sources → TimescaleDB → Test

# Check dashboard files
docker exec aura-grafana ls -la /etc/grafana/provisioning/dashboards/
```

## ✅ Verification Checklist

- [ ] All Docker containers are running
- [ ] Database has data (`pod_metrics` table has rows)
- [ ] ML service is responding (`/health` endpoint)
- [ ] MCP server is responding (`/health` endpoint)
- [ ] Grafana is accessible (http://localhost:3000)
- [ ] Dashboards show data (not "No data")
- [ ] Orchestrator is generating predictions
- [ ] Issues are being created from predictions
- [ ] Remediator is processing issues

## 📚 Additional Resources

- **Grafana Dashboards**: `grafana/dashboards/`
- **Database Schema**: `scripts/init-db.sql`
- **API Documentation**: Check service `/docs` endpoints (FastAPI)
- **Logs**: `docker-compose logs -f <service-name>`

## 🎉 Success Indicators

When everything is working, you should see:

1. ✅ Metrics being generated every 10 seconds
2. ✅ Predictions being generated every 30 seconds
3. ✅ Issues being created for anomalies
4. ✅ Remediations being executed
5. ✅ All Grafana dashboards showing data
6. ✅ No "No data" messages in panels

---

**Need Help?** Check logs: `docker-compose logs -f`

