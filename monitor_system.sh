#!/bin/bash
# System monitoring script for 1 hour
START_TIME=$(date +%s)
END_TIME=$((START_TIME + 3600))  # 1 hour
INTERVAL=60  # Check every 60 seconds
ERROR_LOG="SYSTEM_TEST_ERRORS.md"

echo "=== Starting System Monitoring ===" >> "$ERROR_LOG"
echo "Start Time: $(date)" >> "$ERROR_LOG"
echo "" >> "$ERROR_LOG"

while [ $(date +%s) -lt $END_TIME ]; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    MINUTES=$((ELAPSED / 60))
    
    echo "" >> "$ERROR_LOG"
    echo "## Check at $(date) (Minute $MINUTES)" >> "$ERROR_LOG"
    echo "" >> "$ERROR_LOG"
    
    # Check service health
    echo "### Service Health Checks" >> "$ERROR_LOG"
    curl -s http://localhost:8001/health >/dev/null 2>&1 && echo "✓ ML Service: Healthy" >> "$ERROR_LOG" || echo "✗ ML Service: Unhealthy" >> "$ERROR_LOG"
    curl -s http://localhost:8000/health >/dev/null 2>&1 && echo "✓ MCP Server: Healthy" >> "$ERROR_LOG" || echo "✗ MCP Server: Unhealthy" >> "$ERROR_LOG"
    curl -s http://localhost:9090/health >/dev/null 2>&1 && echo "✓ Collector: Healthy" >> "$ERROR_LOG" || echo "✗ Collector: Unhealthy" >> "$ERROR_LOG"
    curl -s http://localhost:9091/health >/dev/null 2>&1 && echo "✓ Remediator: Healthy" >> "$ERROR_LOG" || echo "✗ Remediator: Unhealthy" >> "$ERROR_LOG"
    
    # Check for errors in logs
    echo "" >> "$ERROR_LOG"
    echo "### Recent Errors in Logs" >> "$ERROR_LOG"
    tail -100 logs/collector.log | grep -i "error\|fatal\|panic" | tail -3 >> "$ERROR_LOG" 2>&1 || echo "No collector errors" >> "$ERROR_LOG"
    tail -100 logs/remediator.log | grep -i "error\|fatal\|panic" | tail -3 >> "$ERROR_LOG" 2>&1 || echo "No remediator errors" >> "$ERROR_LOG"
    tail -100 logs/mcp-server.log | grep -i "error\|fatal\|panic" | tail -3 >> "$ERROR_LOG" 2>&1 || echo "No MCP server errors" >> "$ERROR_LOG"
    
    # Check database metrics
    echo "" >> "$ERROR_LOG"
    echo "### Database Metrics" >> "$ERROR_LOG"
    docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM pod_metrics WHERE timestamp > NOW() - INTERVAL '5 minutes';" >> "$ERROR_LOG" 2>&1
    docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM early_warnings WHERE created_at > NOW() - INTERVAL '5 minutes';" >> "$ERROR_LOG" 2>&1
    docker exec aura-timescaledb psql -U aura -d aura_metrics -t -c "SELECT COUNT(*) FROM issues WHERE status IN ('Open', 'InProgress');" >> "$ERROR_LOG" 2>&1
    
    sleep $INTERVAL
done

echo "" >> "$ERROR_LOG"
echo "=== Monitoring Complete ===" >> "$ERROR_LOG"
echo "End Time: $(date)" >> "$ERROR_LOG"
