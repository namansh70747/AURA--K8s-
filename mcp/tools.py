"""
Kubernetes Tools for MCP Server
Provides helper functions to interact with Kubernetes API
"""

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import logging
import threading
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# Global Kubernetes client cache to avoid recreating on each request
_k8s_client_cache = None
_k8s_client_lock = threading.Lock()

class KubernetesTools:
    """Helper class for Kubernetes operations"""

    def __init__(self):
        """Initialize Kubernetes client (cached)"""
        global _k8s_client_cache
        
        # Use cached client if available
        if _k8s_client_cache is not None:
            self.v1 = _k8s_client_cache['v1']
            self.apps_v1 = _k8s_client_cache['apps_v1']
            self.metrics_v1 = _k8s_client_cache.get('metrics_v1')
            logger.debug("Using cached Kubernetes client")
            return
        
        # Create new client
        with _k8s_client_lock:
            # Double-check after acquiring lock
            if _k8s_client_cache is not None:
                self.v1 = _k8s_client_cache['v1']
                self.apps_v1 = _k8s_client_cache['apps_v1']
                self.metrics_v1 = _k8s_client_cache.get('metrics_v1')
                return
            
            try:
                # Try in-cluster config first
                config.load_incluster_config()
                logger.info("Loaded in-cluster Kubernetes configuration")
            except:
                # Fall back to kubeconfig
                try:
                    config.load_kube_config()
                    logger.info("Loaded kubeconfig configuration")
                except Exception as e:
                    logger.error(f"Failed to load Kubernetes configuration: {e}")
                    raise

            v1_client = client.CoreV1Api()
            apps_v1_client = client.AppsV1Api()
            metrics_v1_client = None

            # Try to initialize metrics API
            try:
                from kubernetes.client import CustomObjectsApi
                metrics_v1_client = CustomObjectsApi()
            except:
                logger.warning("Metrics API not available")

            # Cache the clients
            _k8s_client_cache = {
                'v1': v1_client,
                'apps_v1': apps_v1_client,
                'metrics_v1': metrics_v1_client
            }
            
            self.v1 = v1_client
            self.apps_v1 = apps_v1_client
            self.metrics_v1 = metrics_v1_client

    def get_pod(self, namespace: str, pod_name: str) -> Dict[str, Any]:
        """Get pod information"""
        try:
            pod = self.v1.read_namespaced_pod(name=pod_name, namespace=namespace)

            # Extract useful information
            info = {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "ready": self._is_pod_ready(pod),
                "restart_count": self._get_restart_count(pod),
                "node": pod.spec.node_name,
                "created_at": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
                "containers": [],
            }

            # Container information
            for container_status in pod.status.container_statuses or []:
                container_info = {
                    "name": container_status.name,
                    "ready": container_status.ready,
                    "restart_count": container_status.restart_count,
                    "image": container_status.image,
                    "state": self._get_container_state(container_status),
                }
                info["containers"].append(container_info)

            return info

        except ApiException as e:
            logger.error(f"Failed to get pod {namespace}/{pod_name}: status={e.status}, reason={e.reason}, body={e.body}")
            return {
                "error": "Failed to get pod",
                "status": e.status,
                "reason": e.reason,
                "namespace": namespace,
                "pod_name": pod_name
            }

    def get_pod_logs(self, namespace: str, pod_name: str, lines: int = 20, container: Optional[str] = None) -> str:
        """Get pod logs"""
        try:
            logs = self.v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=lines,
            )
            return logs
        except ApiException as e:
            logger.error(f"Failed to get logs for {namespace}/{pod_name}: status={e.status}, reason={e.reason}")
            return ""

    def get_events(self, namespace: str, pod_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent events for a pod"""
        try:
            field_selector = f"involvedObject.name={pod_name}"
            events = self.v1.list_namespaced_event(
                namespace=namespace,
                field_selector=field_selector,
                limit=limit,
            )

            event_list = []
            for event in events.items:
                event_list.append({
                    "reason": event.reason,
                    "message": event.message,
                    "type": event.type,
                    "count": event.count,
                    "first_timestamp": event.first_timestamp.isoformat() if event.first_timestamp else None,
                    "last_timestamp": event.last_timestamp.isoformat() if event.last_timestamp else None,
                })

            return event_list

        except ApiException as e:
            logger.error(f"Failed to get events for {namespace}/{pod_name}: status={e.status}, reason={e.reason}")
            return []

    def get_deployment_for_pod(self, namespace: str, pod_name: str) -> Optional[Dict[str, Any]]:
        """Get deployment information for a pod"""
        try:
            # First, get the pod to find its owner
            pod = self.v1.read_namespaced_pod(name=pod_name, namespace=namespace)

            if not pod.metadata.owner_references:
                return None

            # Find deployment owner
            for owner in pod.metadata.owner_references:
                if owner.kind == "ReplicaSet":
                    # Get ReplicaSet to find Deployment
                    rs = self.apps_v1.read_namespaced_replica_set(
                        name=owner.name,
                        namespace=namespace,
                    )

                    if rs.metadata.owner_references:
                        for rs_owner in rs.metadata.owner_references:
                            if rs_owner.kind == "Deployment":
                                deployment = self.apps_v1.read_namespaced_deployment(
                                    name=rs_owner.name,
                                    namespace=namespace,
                                )

                                return {
                                    "name": deployment.metadata.name,
                                    "namespace": deployment.metadata.namespace,
                                    "replicas": deployment.spec.replicas,
                                    "ready_replicas": deployment.status.ready_replicas or 0,
                                    "image": deployment.spec.template.spec.containers[0].image if deployment.spec.template.spec.containers else None,
                                }

                elif owner.kind == "StatefulSet":
                    ss = self.apps_v1.read_namespaced_stateful_set(
                        name=owner.name,
                        namespace=namespace,
                    )

                    return {
                        "name": ss.metadata.name,
                        "namespace": ss.metadata.namespace,
                        "replicas": ss.spec.replicas,
                        "ready_replicas": ss.status.ready_replicas or 0,
                        "image": ss.spec.template.spec.containers[0].image if ss.spec.template.spec.containers else None,
                        "type": "StatefulSet",
                    }

            return None

        except ApiException as e:
            logger.error(f"Failed to get deployment for {namespace}/{pod_name}: status={e.status}, reason={e.reason}")
            return None

    def get_node_info(self, node_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a node
        """
        try:
            node = self.v1.read_node(node_name)
            return {
                "name": node.metadata.name,
                "status": node.status.conditions[-1].type if node.status.conditions else "Unknown",
                "cpu": node.status.allocatable.get("cpu", "Unknown"),
                "memory": node.status.allocatable.get("memory", "Unknown"),
                "pod_capacity": node.status.allocatable.get("pods", "Unknown"),
            }
        except ApiException as e:
            logger.error(f"Failed to get node info for {node_name}: {e}")
            return None

    def get_pod_resource_usage(self, namespace: str, pod_name: str) -> Optional[Dict[str, Any]]:
        """Get pod resource usage from metrics API"""
        if not self.metrics_v1:
            logger.debug(f"Metrics API not available for {namespace}/{pod_name}")
            return {
                "error": "Metrics API not available",
                "cpu_millicores": None,
                "memory_bytes": None,
                "memory_mib": None
            }

        try:
            metrics = self.metrics_v1.get_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=namespace,
                plural="pods",
                name=pod_name,
            )

            if not metrics or "containers" not in metrics:
                return None

            total_cpu = 0
            total_memory = 0

            for container in metrics["containers"]:
                usage = container.get("usage", {})

                # Parse CPU - handle all Kubernetes resource formats
                cpu_str = usage.get("cpu", "0")
                try:
                    if cpu_str.endswith("n"):
                        # Nanocores (1/1000000 of a core)
                        cpu_value = int(cpu_str[:-1]) / 1000000
                    elif cpu_str.endswith("u"):
                        # Microcores (1/1000 of a core)
                        cpu_value = int(cpu_str[:-1]) / 1000
                    elif cpu_str.endswith("m"):
                        # Millicores (1/1000 of a core)
                        cpu_value = int(cpu_str[:-1])
                    elif cpu_str.endswith("K"):
                        # Kcores (1000 cores)
                        cpu_value = int(float(cpu_str[:-1])) * 1000000
                    else:
                        # Plain number (cores)
                        if '.' in cpu_str:
                            cpu_value = float(cpu_str) * 1000
                        else:
                            cpu_value = int(cpu_str) * 1000
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Failed to parse CPU value '{cpu_str}': {e}")
                    cpu_value = 0

                total_cpu += cpu_value

                # Parse memory - handle all Kubernetes resource formats
                mem_str = usage.get("memory", "0")
                try:
                    if mem_str.endswith("Ki"):
                        # Kibibytes
                        mem_value = int(mem_str[:-2]) * 1024
                    elif mem_str.endswith("Mi"):
                        # Mebibytes
                        mem_value = int(mem_str[:-2]) * 1024 * 1024
                    elif mem_str.endswith("Gi"):
                        # Gibibytes
                        mem_value = int(mem_str[:-2]) * 1024 * 1024 * 1024
                    elif mem_str.endswith("Ti"):
                        # Tebibytes
                        mem_value = int(mem_str[:-2]) * 1024 * 1024 * 1024 * 1024
                    elif mem_str.endswith("K"):
                        # Kilobytes
                        mem_value = int(mem_str[:-1]) * 1000
                    elif mem_str.endswith("M"):
                        # Megabytes
                        mem_value = int(mem_str[:-1]) * 1000 * 1000
                    elif mem_str.endswith("G"):
                        # Gigabytes
                        mem_value = int(mem_str[:-1]) * 1000 * 1000 * 1000
                    else:
                        # Plain bytes
                        mem_value = int(mem_str)
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Failed to parse memory value '{mem_str}': {e}")
                    mem_value = 0

                total_memory += mem_value

            return {
                "cpu_millicores": total_cpu,
                "memory_bytes": total_memory,
                "memory_mib": total_memory / (1024 * 1024),
            }

        except ApiException as e:
            logger.error(f"Metrics API error for {namespace}/{pod_name}: status={e.status}, reason={e.reason}, body={e.body}")
            return {
                "error": f"Metrics API error: {e.reason}",
                "cpu_millicores": None,
                "memory_bytes": None,
                "memory_mib": None
            }
        except Exception as e:
            logger.error(f"Failed to get metrics for {namespace}/{pod_name}: {type(e).__name__}: {e}")
            return {
                "error": f"Metrics retrieval failed: {str(e)}",
                "cpu_millicores": None,
                "memory_bytes": None,
                "memory_mib": None
            }

    def describe_pod(self, namespace: str, pod_name: str) -> str:
        """Get detailed pod description"""
        try:
            pod = self.v1.read_namespaced_pod(name=pod_name, namespace=namespace)

            description = f"Name: {pod.metadata.name}\n"
            description += f"Namespace: {pod.metadata.namespace}\n"
            description += f"Status: {pod.status.phase}\n"
            description += f"Node: {pod.spec.node_name}\n"
            description += f"\nContainers:\n"

            for container in pod.spec.containers:
                description += f"  - {container.name}:\n"
                description += f"      Image: {container.image}\n"
                if container.resources.requests:
                    description += f"      Requests: {dict(container.resources.requests)}\n"
                if container.resources.limits:
                    description += f"      Limits: {dict(container.resources.limits)}\n"

            description += f"\nConditions:\n"
            for condition in pod.status.conditions or []:
                description += f"  - {condition.type}: {condition.status}\n"

            return description

        except ApiException as e:
            logger.error(f"Failed to describe pod {namespace}/{pod_name}: status={e.status}, reason={e.reason}")
            return f"Error describing pod: {e.reason}"

    def get_namespace_resources(self, namespace: str) -> Dict[str, Any]:
        """Get overview of resources in a namespace"""
        try:
            pods = self.v1.list_namespaced_pod(namespace=namespace)
            deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
            services = self.v1.list_namespaced_service(namespace=namespace)

            return {
                "namespace": namespace,
                "pods": {
                    "total": len(pods.items),
                    "running": sum(1 for p in pods.items if p.status.phase == "Running"),
                    "pending": sum(1 for p in pods.items if p.status.phase == "Pending"),
                    "failed": sum(1 for p in pods.items if p.status.phase == "Failed"),
                },
                "deployments": {
                    "total": len(deployments.items),
                },
                "services": {
                    "total": len(services.items),
                },
            }

        except ApiException as e:
            logger.error(f"Failed to get namespace resources for {namespace}: status={e.status}, reason={e.reason}")
            return {
                "error": "Failed to get namespace resources",
                "namespace": namespace,
                "status": e.status,
                "reason": e.reason
            }

    def _is_pod_ready(self, pod) -> bool:
        """Check if pod is ready"""
        if not pod.status.conditions:
            return False

        for condition in pod.status.conditions:
            if condition.type == "Ready":
                return condition.status == "True"

        return False

    def _get_restart_count(self, pod) -> int:
        """Get total restart count for all containers"""
        total = 0
        if pod.status.container_statuses:
            for container_status in pod.status.container_statuses:
                total += container_status.restart_count
        return total

    def _get_container_state(self, container_status) -> Dict[str, Any]:
        """Get container state information"""
        state = {}

        if container_status.state.waiting:
            state["status"] = "waiting"
            state["reason"] = container_status.state.waiting.reason
            state["message"] = container_status.state.waiting.message
        elif container_status.state.running:
            state["status"] = "running"
            state["started_at"] = container_status.state.running.started_at.isoformat() if container_status.state.running.started_at else None
        elif container_status.state.terminated:
            state["status"] = "terminated"
            state["reason"] = container_status.state.terminated.reason
            state["exit_code"] = container_status.state.terminated.exit_code
            state["message"] = container_status.state.terminated.message

        return state
