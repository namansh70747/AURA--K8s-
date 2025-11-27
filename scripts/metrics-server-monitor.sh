#!/bin/bash
# Background monitor script to ensure metrics-server is ALWAYS working
# This runs continuously and checks metrics-server health every 60 seconds

KUBECONFIG=${KUBECONFIG:-/tmp/aura-kubeconfig}
export KUBECONFIG

LOG_FILE="${LOG_FILE:-logs/metrics-server-monitor.log}"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "🚀 Starting metrics-server monitor (checks every 60 seconds)"

while true; do
    # Check if metrics-server is working
    if ! kubectl top nodes >/dev/null 2>&1; then
        log "⚠️  Metrics-server not responding, attempting to fix..."
        
        # Check deployment
        READY=$(kubectl get deployment metrics-server -n kube-system -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        DESIRED=$(kubectl get deployment metrics-server -n kube-system -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
        
        if [ "$READY" != "$DESIRED" ]; then
            log "Restarting metrics-server deployment..."
            kubectl rollout restart deployment/metrics-server -n kube-system >/dev/null 2>&1
            sleep 30
        fi
        
        # Check APIService
        if ! kubectl get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1; then
            log "Re-applying metrics-server configuration..."
            kubectl apply -f configs/metrics-server.yaml >/dev/null 2>&1
            sleep 15
        fi
        
        # Test again
        if kubectl top nodes >/dev/null 2>&1; then
            log "✅ Metrics-server fixed and working"
        else
            log "❌ Metrics-server still not working, will retry in next cycle"
        fi
    else
        log "✅ Metrics-server is working correctly"
    fi
    
    # Sleep for 60 seconds before next check
    sleep 60
done

