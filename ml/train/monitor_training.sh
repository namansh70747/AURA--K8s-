#!/bin/bash
# Monitor ML Training Progress

echo "🔍 Monitoring BEAST LEVEL ML Training..."
echo "=" | head -c 70 && echo ""

while true; do
    clear
    echo "🚀 BEAST LEVEL ML TRAINING - Live Monitor"
    echo "=" | head -c 70 && echo ""
    echo ""
    
    # Check if training is running
    TRAINING_PID=$(ps aux | grep "python3.*beast_train" | grep -v grep | awk '{print $2}' | head -1)
    
    if [ -z "$TRAINING_PID" ]; then
        echo "❌ Training process not found"
        echo ""
        echo "Checking for completed models..."
        ls -lh models/*.joblib 2>/dev/null | tail -5
        break
    fi
    
    echo "✅ Training is RUNNING (PID: $TRAINING_PID)"
    echo ""
    
    # Process stats
    ps aux | grep "python3.*beast_train" | grep -v grep | awk '{
        printf "📊 Process Stats:\n"
        printf "   CPU Usage: %s%%\n", $3
        printf "   Memory Usage: %s%% (%s MB)\n", $4, $6/1024
        printf "   Runtime: %s\n", $10
    }'
    echo ""
    
    # Model files
    echo "📁 Trained Models:"
    ls -lth models/*.joblib 2>/dev/null | head -8 | awk '{printf "   %s (%s)\n", $9, $5}'
    echo ""
    
    # Check training metadata
    if [ -f "models/training_metadata.json" ]; then
        echo "📈 Training Progress:"
        python3 -c "
import json
import os
from datetime import datetime

try:
    with open('models/training_metadata.json', 'r') as f:
        meta = json.load(f)
    
    print(f\"   Version: {meta.get('version', 'unknown')}\")
    print(f\"   Samples: {meta.get('total_samples', 0):,}\")
    print(f\"   Features: {meta.get('num_features', 0)}\")
    print(f\"   Anomaly Types: {meta.get('num_anomaly_types', 0)}\")
    
    if 'model_performance' in meta:
        print(f\"   Models Trained: {len(meta['model_performance'])}\")
        for model, perf in meta['model_performance'].items():
            print(f\"      {model}: Accuracy={perf.get('accuracy', 0):.4f}, F1={perf.get('f1_score', 0):.4f}\")
except Exception as e:
    print(f\"   Metadata not yet available\")
" 2>/dev/null
    else
        echo "   ⏳ Training in progress (metadata not yet created)..."
    fi
    
    echo ""
    echo "Press Ctrl+C to stop monitoring"
    echo ""
    sleep 5
done

echo ""
echo "✅ Monitoring complete!"

