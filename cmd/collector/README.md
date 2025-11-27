# AURA K8s Collector

The **AURA K8s Collector** is the core metrics collection service that continuously monitors your Kubernetes cluster, gathering comprehensive metrics from all pods, containers, and nodes. It ensures **100% metric coverage** with intelligent fallback mechanisms, making it resilient and reliable.

## 🎯 What It Does

The collector continuously:
- **Scans** all pods and containers in your cluster (excluding system namespaces)
- **Collects** comprehensive metrics: CPU, memory, network, disk, pod state, container health
- **Saves** all metrics to TimescaleDB for time-series analysis
- **Tracks** every container individually, even if metrics aren't immediately available (uses zero values as fallback)
- **Streams** recent metrics via circular buffer for fast access by predictive systems
- **Monitors** cluster health and provides Prometheus metrics

## 🚀 How It Works

### Collection Workflow

```
1. List all pods in cluster
   ↓
2. For each pod:
   ├─ For each container in pod:
   │  ├─ Try Metrics API (metrics-server)
   │  ├─ Fallback to Kubelet API (direct)
   │  ├─ Fallback to cAdvisor API
   │  ├─ Fallback to historical data
   │  └─ Use zero values (ensures ALL containers tracked)
   │
   └─ Collect pod state, conditions, restarts, trends
   ↓
3. Collect node metrics comprehensively
   ↓
4. Save all metrics to TimescaleDB (batch + individual fallback)
   ↓
5. Push to circular buffer for streaming access
```

### Key Features

- **🔄 Multi-Tier Fallback System**: Never fails to collect metrics
  1. Metrics API (metrics-server) - Primary source
  2. Kubelet API - Direct node access
  3. cAdvisor API - Container-level metrics
  4. Historical Data - Recent metrics from database
  5. Zero Values - Ensures every container is tracked

- **📊 Comprehensive Collection**: Collects **ALL** metrics for **ALL** containers
  - CPU usage, limits, utilization
  - Memory usage, limits, utilization
  - Network I/O (rx/tx bytes, errors)
  - Disk usage and limits
  - Pod state (phase, ready, restarts, age)
  - Container state (ready, state, last reason)
  - Health indicators (OOM kills, crash loops, high CPU, network issues)
  - Trends (CPU, memory, restart trends from historical data)

- **🛡️ Resilient Design**: 
  - Continues collecting even if some metrics fail
  - Saves metrics individually if batch save fails
  - Creates zero-value metrics for containers without data
  - Never skips a container

- **⚡ Performance Optimized**:
  - Batch processing for efficient database writes
  - Circular buffer for fast streaming access
  - Parallel collection mode (optional)
  - Metrics caching (in parallel mode)

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COLLECTION_INTERVAL` | `500ms` | How often to collect metrics (minimum: 100ms) |
| `USE_PARALLEL_COLLECTION` | `true` | Enable parallel collection mode |
| `METRICS_COLLECTOR_WORKERS` | `20` | Number of parallel workers (parallel mode only) |
| `METRICS_COLLECTOR_BATCH_SIZE` | `100` | Batch size for database writes |
| `METRICS_CACHE_TTL_SECONDS` | `10` | Cache TTL for metrics (parallel mode) |
| `METRICS_PORT` | `9090` | Port for health/metrics HTTP server |
| `DATABASE_URL` | *required* | PostgreSQL/TimescaleDB connection string |
| `ML_SERVICE_URL` | `http://localhost:8001` | ML service URL for predictions |
| `LOG_DIR` | - | Directory for log files (enables file logging) |
| `LOG_MAX_SIZE_MB` | `100` | Maximum log file size in MB |
| `LOG_MAX_AGE_DAYS` | `7` | Maximum age of log files |
| `LOG_MAX_BACKUPS` | `5` | Maximum number of log backups |
| `LOG_COMPRESS` | `true` | Compress rotated log files |
| `ENVIRONMENT` | `development` | Environment mode (development/production) |

### Collection Modes

#### Standard Mode (Default)
- Sequential collection
- Uses comprehensive collector
- Simpler, more predictable

#### Parallel Mode (`USE_PARALLEL_COLLECTION=true`)
- Concurrent collection with worker pool
- Metrics caching for performance
- Batch processing with background workers
- Better for large clusters

## 📡 API Endpoints

The collector exposes HTTP endpoints on `METRICS_PORT` (default: 9090):

- **`GET /health`**: Health check endpoint
  - Returns `200 OK` if healthy
  - Returns `503 Service Unavailable` if critical dependencies down
  - Shows degraded status if non-critical services unavailable

- **`GET /metrics`**: Prometheus metrics endpoint
  - Exposes collection statistics, errors, durations

- **`GET /api/v1/buffer/metrics?limit=1000`**: Get recent metrics from circular buffer
  - Used by predictive orchestrator for fast access
  - Returns JSON array of recent pod metrics

- **`GET /api/v1/buffer/stats`**: Get circular buffer statistics
  - Returns buffer size, count, fill status

## 🔍 Special Features

### Zero-Value Metrics
If a container exists but Metrics API has no data yet, the collector creates a metric record with zero values. This ensures:
- **100% container coverage** - Every container is tracked from the moment it's created
- **No gaps in data** - Even new containers appear in dashboards immediately
- **Complete visibility** - You can see all containers, even if they're not consuming resources yet

### Circular Buffer Streaming
Recent metrics are stored in an in-memory circular buffer (10,000 entries by default):
- **Ultra-fast access** for predictive systems
- **Reduces database load** for real-time queries
- **Enables streaming analytics** without querying TimescaleDB

### Comprehensive Metrics
The collector tracks **32+ fields** per container:
- Resource usage (CPU, memory, network, disk)
- Resource limits and requests
- Utilization percentages
- Pod and container state
- Health indicators (OOM, crash loops, high CPU, network issues)
- Trends calculated from historical data
- Age, restarts, and lifecycle information

### Intelligent Fallback
If Metrics API is unavailable, the collector:
1. Tries Kubelet API directly
2. Falls back to cAdvisor API
3. Uses recent historical data from database
4. Creates zero-value metrics as last resort
5. **Never stops collecting** - always saves something

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Collector Service                     │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐      ┌──────────────────┐            │
│  │   Main Loop  │─────▶│ Comprehensive    │            │
│  │  (Ticker)    │      │   Collector       │            │
│  └──────────────┘      └──────────────────┘            │
│                              │                           │
│                              ├─▶ Pod Metrics            │
│                              ├─▶ Node Metrics           │
│                              ├─▶ All Containers         │
│                              └─▶ Zero-Value Fallback    │
│                                                          │
│  ┌──────────────┐      ┌──────────────────┐            │
│  │  Multi-Tier  │─────▶│  Metrics API     │            │
│  │   Fallback   │      │  Kubelet API     │            │
│  │   System     │      │  cAdvisor API    │            │
│  └──────────────┘      │  Historical Data │            │
│                        │  Zero Values     │            │
│                        └──────────────────┘            │
│                                                          │
│  ┌──────────────┐      ┌──────────────────┐            │
│  │   Batch      │─────▶│   TimescaleDB    │            │
│  │  Processor   │      │   (PostgreSQL)  │            │
│  └──────────────┘      └──────────────────┘            │
│         │                                                │
│         └─▶ Individual Save (if batch fails)            │
│                                                          │
│  ┌──────────────┐      ┌──────────────────┐            │
│  │  Circular    │─────▶│  HTTP API        │            │
│  │   Buffer     │      │  /api/v1/buffer  │            │
│  └──────────────┘      └──────────────────┘            │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## 🎨 Collection Strategies

### Comprehensive Collector (Primary)
- **Always active** - This is the default and primary method
- Collects metrics for **every container** in **every pod**
- Uses multi-tier fallback system
- Ensures zero-value metrics for containers without data
- **Guaranteed coverage** - Never skips a container

### Parallel Collector (Optional)
- Enabled with `USE_PARALLEL_COLLECTION=true`
- Uses worker pool for concurrent collection
- Includes metrics caching
- Better performance for large clusters
- Still uses comprehensive collector internally

## 📊 Metrics Collected

### Per Container
- CPU usage (millicores), limits, utilization (%)
- Memory usage (bytes), limits, utilization (%)
- Network RX/TX bytes and errors
- Disk usage and limits
- Container state (Running/Waiting/Terminated)
- Container ready status
- Restart count
- Last termination reason

### Per Pod
- Pod phase (Pending/Running/Succeeded/Failed)
- Pod ready status
- Pod age (seconds)
- CPU/Memory/Restart trends (calculated from history)
- Health flags: OOM kills, crash loops, high CPU, network issues

### Per Node
- CPU usage and capacity
- Memory usage and capacity
- Pod count and capacity
- Node conditions (disk pressure, memory pressure, network unavailable, ready)

## 🔗 Integration Points

- **TimescaleDB**: All metrics saved for time-series analysis
- **ML Service**: Optional ML predictions for anomaly detection
- **Predictive Orchestrator**: Accesses circular buffer for fast forecasting
- **Grafana**: Queries TimescaleDB for visualization
- **Prometheus**: Exposes collection metrics for monitoring

## 🚦 Health Checks

The collector performs tiered health checks:
- **Critical**: Database connection, Kubernetes API access
- **Non-Critical**: ML service, MCP server (degraded if unavailable)

Health endpoint returns:
- `200 OK` - Fully healthy
- `200 OK` with "DEGRADED" - Operational but some services unavailable
- `503 Service Unavailable` - Critical dependencies down

## 💡 Best Practices

1. **Metrics-Server**: Ensure metrics-server is running for best performance
   ```bash
   kubectl get deployment metrics-server -n kube-system
   ```

2. **Collection Interval**: 
   - Default 500ms is good for most clusters
   - Lower intervals (100-200ms) for high-frequency monitoring
   - Higher intervals (1-5s) for large clusters to reduce load

3. **Parallel Mode**: 
   - Enable for clusters with 50+ pods
   - Adjust worker count based on cluster size
   - Monitor cache hit rate for optimal TTL

4. **Database**: 
   - Use TimescaleDB for time-series optimization
   - Configure retention policies to manage data growth
   - Monitor database connection pool

## 🐛 Troubleshooting

### No Metrics Collected
- Check metrics-server: `kubectl top nodes`
- Verify Kubernetes API access: `kubectl get pods`
- Check collector logs: `tail -f logs/collector.log`

### Missing Containers
- Collector creates zero-value metrics for all containers
- Check database: `SELECT DISTINCT container_name FROM pod_metrics`
- Verify pod has containers: `kubectl get pod <pod-name> -o jsonpath='{.spec.containers[*].name}'`

### High CPU Usage
- Increase `COLLECTION_INTERVAL` to reduce frequency
- Disable parallel mode if not needed
- Reduce worker count in parallel mode

### Database Connection Issues
- Verify `DATABASE_URL` is correct
- Check TimescaleDB is running: `docker ps | grep timescaledb`
- Review connection pool settings

## 📝 Logs

Logs are written to:
- **Console**: Structured JSON logs
- **File**: `logs/collector.log` (if `LOG_DIR` is set)

Log levels:
- `INFO`: Normal operation, collection cycles
- `DEBUG`: Detailed metrics collection, fallback usage
- `WARN`: Non-critical errors, fallback activations
- `ERROR`: Critical failures, collection errors

## 🎯 Summary

The AURA K8s Collector is designed to be:
- **Reliable**: Multi-tier fallback ensures metrics are always collected
- **Comprehensive**: Tracks every container with 32+ metrics
- **Resilient**: Continues working even when some APIs fail
- **Performant**: Batch processing, caching, and parallel collection
- **Observable**: Health checks, Prometheus metrics, detailed logs

It's the foundation of the AURA K8s monitoring system, ensuring you have complete visibility into your Kubernetes cluster's health and performance.

