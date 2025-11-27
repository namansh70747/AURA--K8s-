package k8s

import (
	"context"
	"sync"
	"time"

	"github.com/namansh70747/aura-k8s/pkg/utils"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	metricsv "k8s.io/metrics/pkg/client/clientset/versioned"
)

// ClientManager manages Kubernetes client connections with automatic reconnection
type ClientManager struct {
	config         *rest.Config
	client         kubernetes.Interface
	metricsClient  metricsv.Interface
	mu             sync.RWMutex
	lastHealth     time.Time
	healthInterval time.Duration
	reconnectCount int64
}

// NewClientManager creates a new client manager
func NewClientManager(config *rest.Config) *ClientManager {
	cm := &ClientManager{
		config:         config,
		healthInterval: 30 * time.Second,
	}
	cm.reconnect()
	return cm
}

// reconnect recreates the Kubernetes clients
func (cm *ClientManager) reconnect() error {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	client, err := kubernetes.NewForConfig(cm.config)
	if err != nil {
		return err
	}

	metricsClient, err := metricsv.NewForConfig(cm.config)
	if err != nil {
		utils.Log.WithError(err).Warn("Failed to create metrics client, continuing without it")
	}

	cm.client = client
	cm.metricsClient = metricsClient
	cm.lastHealth = time.Now()
	cm.reconnectCount++

	utils.Log.WithField("reconnect_count", cm.reconnectCount).Info("K8s client reconnected")
	return nil
}

// GetClient returns the Kubernetes client, reconnecting if necessary
func (cm *ClientManager) GetClient(ctx context.Context) (kubernetes.Interface, error) {
	// Check health
	if time.Since(cm.lastHealth) > cm.healthInterval {
		if err := cm.healthCheck(ctx); err != nil {
			utils.Log.WithError(err).Warn("Health check failed, reconnecting")
			if err := cm.reconnect(); err != nil {
				return nil, err
			}
		}
	}

	cm.mu.RLock()
	defer cm.mu.RUnlock()
	return cm.client, nil
}

// GetMetricsClient returns the metrics client
func (cm *ClientManager) GetMetricsClient() metricsv.Interface {
	cm.mu.RLock()
	defer cm.mu.RUnlock()
	return cm.metricsClient
}

// healthCheck performs a health check on the Kubernetes API
func (cm *ClientManager) healthCheck(ctx context.Context) error {
	cm.mu.RLock()
	client := cm.client
	cm.mu.RUnlock()

	if client == nil {
		return errors.NewServiceUnavailable("client is nil")
	}

	// Simple health check: list namespaces
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	_, err := client.CoreV1().Namespaces().List(ctx, metav1.ListOptions{Limit: 1})
	if err != nil {
		return err
	}

	cm.mu.Lock()
	cm.lastHealth = time.Now()
	cm.mu.Unlock()

	return nil
}

// StartHealthChecker starts a background health checker
func (cm *ClientManager) StartHealthChecker(ctx context.Context) {
	ticker := time.NewTicker(cm.healthInterval)
	go func() {
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := cm.healthCheck(ctx); err != nil {
					utils.Log.WithError(err).Warn("Health check failed, reconnecting")
					cm.reconnect()
				}
			}
		}
	}()
}
