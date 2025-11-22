package k8s

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"time"

	configpkg "github.com/namansh70747/aura-k8s/pkg/config"
	"github.com/namansh70747/aura-k8s/pkg/utils"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	metricsapi "k8s.io/metrics/pkg/apis/metrics/v1beta1"
	metricsv "k8s.io/metrics/pkg/client/clientset/versioned"
)

type Client struct {
	clientset     *kubernetes.Clientset
	metricsClient *metricsv.Clientset
}

// NewClient creates a new Kubernetes client with proper timeouts and rate limiting
func NewClient() (*Client, error) {
	config, err := rest.InClusterConfig()
	if err != nil {
		// Fallback to kubeconfig with better error handling
		utils.Log.Info("Not running in cluster, trying kubeconfig")

		kubeconfigPath := clientcmd.RecommendedHomeFile
		if path := os.Getenv("KUBECONFIG"); path != "" {
			kubeconfigPath = path
		}

		config, err = clientcmd.BuildConfigFromFlags("", kubeconfigPath)
		if err != nil {
			return nil, fmt.Errorf("failed to build config from kubeconfig at %s: %w", kubeconfigPath, err)
		}
	}

	// Configure timeouts and rate limiting (externalized via config package)
	config.Timeout = configpkg.GetK8sTimeout()
	config.QPS = configpkg.GetK8sQPS()
	config.Burst = configpkg.GetK8sBurst()

	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("failed to create clientset: %w", err)
	}

	metricsClient, err := metricsv.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("failed to create metrics client: %w", err)
	}

	utils.Log.Info("Kubernetes client initialized with timeout=30s, QPS=50, Burst=100")

	return &Client{
		clientset:     clientset,
		metricsClient: metricsClient,
	}, nil
}

// Clientset returns the Kubernetes clientset
func (c *Client) Clientset() *kubernetes.Clientset {
	return c.clientset
}

// ListPods returns all pods in a namespace (or all namespaces if namespace is empty)
func (c *Client) ListPods(ctx context.Context, namespace string) (*corev1.PodList, error) {
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}
	return c.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
}

// GetPod returns a specific pod
func (c *Client) GetPod(ctx context.Context, namespace, name string) (*corev1.Pod, error) {
	return c.clientset.CoreV1().Pods(namespace).Get(ctx, name, metav1.GetOptions{})
}

// DeletePod deletes a pod
func (c *Client) DeletePod(ctx context.Context, namespace, name string) error {
	utils.Log.WithField("pod", name).WithField("namespace", namespace).Info("Deleting pod")
	return c.clientset.CoreV1().Pods(namespace).Delete(ctx, name, metav1.DeleteOptions{})
}

// ListNodes returns all nodes
func (c *Client) ListNodes(ctx context.Context) (*corev1.NodeList, error) {
	return c.clientset.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
}

// GetNode returns a specific node
func (c *Client) GetNode(ctx context.Context, name string) (*corev1.Node, error) {
	return c.clientset.CoreV1().Nodes().Get(ctx, name, metav1.GetOptions{})
}

// GetPodMetrics returns metrics for a pod
func (c *Client) GetPodMetrics(ctx context.Context, namespace, name string) (*metricsapi.PodMetrics, error) {
	return c.metricsClient.MetricsV1beta1().PodMetricses(namespace).Get(ctx, name, metav1.GetOptions{})
}

// GetNodeMetrics returns metrics for a node
func (c *Client) GetNodeMetrics(ctx context.Context, name string) (*metricsapi.NodeMetrics, error) {
	return c.metricsClient.MetricsV1beta1().NodeMetricses().Get(ctx, name, metav1.GetOptions{})
}

// GetDeployment returns a deployment
func (c *Client) GetDeployment(ctx context.Context, namespace, name string) (*appsv1.Deployment, error) {
	return c.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
}

// ScaleDeployment scales a deployment to the specified replica count
func (c *Client) ScaleDeployment(ctx context.Context, namespace, name string, replicas int32) error {
	utils.Log.WithField("deployment", name).WithField("replicas", replicas).Info("Scaling deployment")

	deployment, err := c.GetDeployment(ctx, namespace, name)
	if err != nil {
		return fmt.Errorf("failed to get deployment: %w", err)
	}

	deployment.Spec.Replicas = &replicas
	_, err = c.clientset.AppsV1().Deployments(namespace).Update(ctx, deployment, metav1.UpdateOptions{})
	return err
}

// UpdatePodResourceLimits updates the resource limits for a pod's container
func (c *Client) UpdatePodResourceLimits(ctx context.Context, namespace, podName, containerName string, cpuLimit, memoryLimit string) error {
	utils.Log.WithField("pod", podName).WithField("cpu", cpuLimit).WithField("memory", memoryLimit).Info("Updating resource limits")

	// Get the pod's owner (deployment/statefulset/etc)
	pod, err := c.GetPod(ctx, namespace, podName)
	if err != nil {
		return fmt.Errorf("failed to get pod: %w", err)
	}

	// Check if pod has owner references
	if len(pod.OwnerReferences) == 0 {
		return fmt.Errorf("pod has no owner references - cannot update standalone pod resources")
	}

	owner := pod.OwnerReferences[0]
	if owner.Kind != "ReplicaSet" {
		return fmt.Errorf("pod owner is not a ReplicaSet")
	}

	// Get ReplicaSet to find Deployment
	rs, err := c.clientset.AppsV1().ReplicaSets(namespace).Get(ctx, owner.Name, metav1.GetOptions{})
	if err != nil {
		return fmt.Errorf("failed to get replicaset: %w", err)
	}

	if len(rs.OwnerReferences) == 0 {
		return fmt.Errorf("replicaset has no owner references")
	}

	deploymentName := rs.OwnerReferences[0].Name
	deployment, err := c.GetDeployment(ctx, namespace, deploymentName)
	if err != nil {
		return fmt.Errorf("failed to get deployment: %w", err)
	}

	// Update container resources with safe parsing
	for i, container := range deployment.Spec.Template.Spec.Containers {
		if container.Name == containerName {
			// Initialize limits map if nil
			if deployment.Spec.Template.Spec.Containers[i].Resources.Limits == nil {
				deployment.Spec.Template.Spec.Containers[i].Resources.Limits = corev1.ResourceList{}
			}

			if cpuLimit != "" {
				cpuQuantity, err := resource.ParseQuantity(cpuLimit)
				if err != nil {
					return fmt.Errorf("invalid CPU limit '%s': %w", cpuLimit, err)
				}
				deployment.Spec.Template.Spec.Containers[i].Resources.Limits[corev1.ResourceCPU] = cpuQuantity
			}
			if memoryLimit != "" {
				memQuantity, err := resource.ParseQuantity(memoryLimit)
				if err != nil {
					return fmt.Errorf("invalid memory limit '%s': %w", memoryLimit, err)
				}
				deployment.Spec.Template.Spec.Containers[i].Resources.Limits[corev1.ResourceMemory] = memQuantity
			}
			break
		}
	}

	_, err = c.clientset.AppsV1().Deployments(namespace).Update(ctx, deployment, metav1.UpdateOptions{})
	return err
}

// GetPodLogs returns the logs for a pod's container
func (c *Client) GetPodLogs(ctx context.Context, namespace, podName, containerName string, lines int64) (string, error) {
	req := c.clientset.CoreV1().Pods(namespace).GetLogs(podName, &corev1.PodLogOptions{
		Container: containerName,
		TailLines: &lines,
	})

	logs, err := req.Stream(ctx)
	if err != nil {
		return "", fmt.Errorf("failed to stream logs: %w", err)
	}
	defer logs.Close()

	// Read all logs properly
	var buf bytes.Buffer
	_, err = io.Copy(&buf, logs)
	if err != nil {
		return "", fmt.Errorf("failed to read logs: %w", err)
	}

	return buf.String(), nil
}

// GetEvents returns events for a pod
func (c *Client) GetEvents(ctx context.Context, namespace, podName string) (*corev1.EventList, error) {
	fieldSelector := fmt.Sprintf("involvedObject.name=%s,involvedObject.kind=Pod", podName)
	return c.clientset.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{
		FieldSelector: fieldSelector,
	})
}

// GetDeploymentForPod finds the deployment that owns a pod
func (c *Client) GetDeploymentForPod(ctx context.Context, namespace, podName string) (*appsv1.Deployment, error) {
	pod, err := c.GetPod(ctx, namespace, podName)
	if err != nil {
		return nil, err
	}

	if len(pod.OwnerReferences) == 0 {
		return nil, fmt.Errorf("pod has no owner references")
	}

	owner := pod.OwnerReferences[0]
	if owner.Kind != "ReplicaSet" {
		return nil, fmt.Errorf("pod owner is not a ReplicaSet")
	}

	rs, err := c.clientset.AppsV1().ReplicaSets(namespace).Get(ctx, owner.Name, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}

	if len(rs.OwnerReferences) == 0 {
		return nil, fmt.Errorf("replicaset has no owner references")
	}

	deploymentName := rs.OwnerReferences[0].Name
	return c.GetDeployment(ctx, namespace, deploymentName)
}

// WaitForPodReady waits for a pod to become ready
func (c *Client) WaitForPodReady(ctx context.Context, namespace, podName string, timeout time.Duration) error {
	utils.Log.WithField("pod", podName).Info("Waiting for pod to become ready")

	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		// Check for context cancellation FIRST
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		pod, err := c.GetPod(ctx, namespace, podName)
		if err != nil {
			// Check context before sleeping
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(2 * time.Second):
			}
			continue
		}

		for _, cond := range pod.Status.Conditions {
			if cond.Type == corev1.PodReady && cond.Status == corev1.ConditionTrue {
				utils.Log.WithField("pod", podName).Info("Pod is ready")
				return nil
			}
		}

		// Sleep with context check
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}

	return fmt.Errorf("timeout waiting for pod %s/%s to be ready", namespace, podName)
}

// RestartPod restarts a pod by deleting it (controller will recreate)
func (c *Client) RestartPod(ctx context.Context, namespace, podName string) error {
	utils.Log.WithField("pod", podName).Info("Restarting pod")
	return c.DeletePod(ctx, namespace, podName)
}
