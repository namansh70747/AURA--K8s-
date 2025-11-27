"""
Kubernetes Tools for MCP Server
Provides helper functions to interact with Kubernetes API
"""

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import logging
import os
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
            
            kubeconfig_loaded = False
            try:
                # Priority 1: KUBECONFIG environment variable (explicit)
                kubeconfig_path = os.getenv("KUBECONFIG")
                if kubeconfig_path and os.path.exists(kubeconfig_path):
                    try:
                        config.load_kube_config(config_file=kubeconfig_path)
                        logger.info(f"✅ Loaded Kubernetes configuration from KUBECONFIG: {kubeconfig_path}")
                        kubeconfig_loaded = True
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to load KUBECONFIG from {kubeconfig_path}: {e}")
                
                # Priority 1.5: Try to get kind cluster kubeconfig (if KUBECONFIG not loaded)
                if not kubeconfig_loaded:
                    try:
                        import subprocess
                        import shutil
                        # Check if kind command exists
                        if shutil.which("kind"):
                            kind_cluster = os.getenv("KIND_CLUSTER_NAME", "aura-k8s-local")
                            result = subprocess.run(
                                ["kind", "get", "kubeconfig", "--name", kind_cluster],
                                capture_output=True,
                                text=True,
                                timeout=10
                            )
                            if result.returncode == 0 and result.stdout and "apiVersion" in result.stdout:
                                # Write to temp file and load
                                import tempfile
                                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                                    f.write(result.stdout)
                                    temp_kubeconfig = f.name
                                try:
                                    config.load_kube_config(config_file=temp_kubeconfig)
                                    logger.info(f"✅ Loaded Kubernetes configuration from kind cluster: {kind_cluster}")
                                    kubeconfig_loaded = True
                                    # Store temp file path for cleanup later if needed
                                    os.environ["KUBECONFIG"] = temp_kubeconfig
                                except Exception as load_err:
                                    logger.warning(f"⚠️  Failed to load kind kubeconfig: {load_err}")
                                    try:
                                        os.unlink(temp_kubeconfig)
                                    except:
                                        pass
                    except Exception as kind_err:
                        logger.debug(f"Kind cluster kubeconfig not available: {kind_err}")
                
                # Priority 2: In-cluster config (if running in pod and kubeconfig not loaded)
                if not kubeconfig_loaded:
                    try:
                        config.load_incluster_config()
                        logger.info("✅ Loaded in-cluster Kubernetes configuration")
                        kubeconfig_loaded = True
                    except config.ConfigException:
                        pass  # Not running in cluster, continue to next option
                
                # Priority 3: Try kubectl context (if kubeconfig not loaded)
                if not kubeconfig_loaded:
                    try:
                        # Try to use kubectl's current context
                        import subprocess
                        result = subprocess.run(
                            ["kubectl", "config", "view", "--raw"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if result.returncode == 0 and result.stdout and "apiVersion" in result.stdout:
                            import tempfile
                            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                                f.write(result.stdout)
                                temp_kubeconfig = f.name
                            try:
                                config.load_kube_config(config_file=temp_kubeconfig)
                                logger.info("✅ Loaded Kubernetes configuration from kubectl context")
                                kubeconfig_loaded = True
                                os.environ["KUBECONFIG"] = temp_kubeconfig
                            except Exception as load_err:
                                logger.warning(f"⚠️  Failed to load kubectl kubeconfig: {load_err}")
                                try:
                                    os.unlink(temp_kubeconfig)
                                except:
                                    pass
                    except Exception:
                        pass  # kubectl not available or failed
                
                # Priority 4: Default kubeconfig locations (if still not loaded)
                if not kubeconfig_loaded:
                    try:
                        # Try default location first
                        default_kubeconfig = os.path.expanduser("~/.kube/config")
                        if os.path.exists(default_kubeconfig):
                            # Validate the kubeconfig file before loading
                            try:
                                with open(default_kubeconfig, 'r') as f:
                                    content = f.read()
                                    if "apiVersion" in content and "clusters" in content:
                                        config.load_kube_config(config_file=default_kubeconfig)
                                        logger.info(f"✅ Loaded kubeconfig from default location: {default_kubeconfig}")
                                        kubeconfig_loaded = True
                                    else:
                                        logger.warning(f"⚠️  Default kubeconfig file exists but appears invalid")
                            except Exception as validate_err:
                                logger.warning(f"⚠️  Failed to validate default kubeconfig: {validate_err}")
                        else:
                            # Try loading without explicit path (uses KUBECONFIG env or default)
                            try:
                                config.load_kube_config()
                                logger.info("✅ Loaded kubeconfig configuration (auto-detected)")
                                kubeconfig_loaded = True
                            except Exception:
                                pass  # Will raise error below
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to load from default kubeconfig: {e}")
                
                # If still not loaded, raise error
                if not kubeconfig_loaded:
                    error_msg = "Failed to load Kubernetes configuration from any source"
                    logger.error(f"❌ {error_msg}")
                    logger.error("   Tried: KUBECONFIG env, kind cluster, in-cluster config, kubectl context, ~/.kube/config, auto-detect")
                    raise RuntimeError(f"{error_msg} - MCP server will continue without Kubernetes client")
                    
            except config.ConfigException as e:
                logger.error(f"❌ Kubernetes config exception: {e}")
                # Don't raise - allow server to continue without K8s client
                logger.warning("⚠️  MCP server will continue without Kubernetes client (using intelligent fallback)")
                raise RuntimeError(f"Kubernetes configuration error: {e}") from e
            except RuntimeError as re:
                # Check if it's our custom error about kubeconfig loading
                if "Failed to load Kubernetes configuration" in str(re) or "Kubernetes configuration error" in str(re):
                    logger.warning("⚠️  Kubernetes client initialization failed, but server will continue")
                    logger.warning("   MCP server will use intelligent fallback for remediation plans")
                    # Re-raise to prevent client creation, but server can still start
                    raise
                # Re-raise other RuntimeErrors
                raise
            except Exception as e:
                logger.error(f"❌ Failed to initialize Kubernetes client: {type(e).__name__}: {e}")
                logger.warning("⚠️  MCP server will continue without Kubernetes client (using intelligent fallback)")
                raise RuntimeError(f"Kubernetes client initialization failed: {e}") from e

            # Only create clients if kubeconfig was successfully loaded
            try:
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
                
                logger.info("✅ Kubernetes client fully initialized and ready")
            except Exception as client_err:
                logger.error(f"❌ Failed to create Kubernetes API clients: {client_err}")
                logger.warning("⚠️  MCP server will continue without Kubernetes client")
                raise RuntimeError(f"Kubernetes API client creation failed: {client_err}") from client_err

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
