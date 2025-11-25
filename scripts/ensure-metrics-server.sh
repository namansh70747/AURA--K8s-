#!/bin/bash
# Script to ensure metrics-server is ALWAYS working
# This script can be run periodically to check and fix metrics-server

set -e

KUBECONFIG=${KUBECONFIG:-/tmp/aura-kubeconfig}
export KUBECONFIG

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔍 Checking metrics-server health...${NC}"

# Check if metrics-server deployment exists
if ! kubectl get deployment metrics-server -n kube-system >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Metrics-server not found, installing...${NC}"
    kubectl apply -f configs/metrics-server.yaml
    sleep 10
fi

# Check deployment status
READY_REPLICAS=$(kubectl get deployment metrics-server -n kube-system -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
DESIRED_REPLICAS=$(kubectl get deployment metrics-server -n kube-system -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")

if [ "$READY_REPLICAS" != "$DESIRED_REPLICAS" ]; then
    echo -e "${YELLOW}⚠ Metrics-server not ready ($READY_REPLICAS/$DESIRED_REPLICAS), restarting...${NC}"
    kubectl rollout restart deployment/metrics-server -n kube-system
    kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s || true
    sleep 10
fi

# Check APIService
if ! kubectl get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠ APIService not found, applying configuration...${NC}"
    kubectl apply -f configs/metrics-server.yaml
    sleep 10
fi

# Check APIService status
APISERVICE_STATUS=$(kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null || echo "Unknown")
if [ "$APISERVICE_STATUS" != "True" ]; then
    echo -e "${YELLOW}⚠ APIService not available, checking metrics-server pods...${NC}"
    kubectl get pods -n kube-system -l k8s-app=metrics-server
    kubectl logs -n kube-system -l k8s-app=metrics-server --tail=20 || true
fi

# Test metrics API
if kubectl top nodes >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Metrics-server is working correctly${NC}"
    exit 0
else
    echo -e "${RED}❌ Metrics-server is not responding${NC}"
    echo -e "${BLUE}Checking metrics-server pods...${NC}"
    kubectl get pods -n kube-system -l k8s-app=metrics-server
    echo -e "${BLUE}Checking metrics-server logs...${NC}"
    kubectl logs -n kube-system -l k8s-app=metrics-server --tail=30 || true
    echo -e "${YELLOW}Attempting to fix metrics-server...${NC}"
    kubectl delete pod -n kube-system -l k8s-app=metrics-server --force --grace-period=0 2>/dev/null || true
    sleep 15
    if kubectl top nodes >/dev/null 2>&1; then
        echo -e "${GREEN}✅ Metrics-server fixed and working${NC}"
        exit 0
    else
        echo -e "${RED}❌ Metrics-server still not working, manual intervention may be needed${NC}"
        exit 1
    fi
fi

