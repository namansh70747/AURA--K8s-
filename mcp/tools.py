import os
import requests

class KubernetesTools:
    """Tools for interacting with Kubernetes API"""
    
    def __init__(self):
        self.api_url = os.getenv("KUBERNETES_SERVICE_HOST", "localhost")
        self.api_port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
        self.token_file = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        
    def _get_headers(self):
        """Get auth headers for K8s API"""
        if os.path.exists(self.token_file):
            with open(self.token_file) as f:
                token = f.read()
            return {"Authorization": f"Bearer {token}"}
        return {}
    
    def get_pod(self, namespace: str, name: str) -> str:
        """Get pod details"""
        try:
            url = f"https://{self.api_url}:{self.api_port}/api/v1/namespaces/{namespace}/pods/{name}"
            resp = requests.get(url, headers=self._get_headers(), verify=False, timeout=10)
            return str(resp.json())
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            return f"Pod details not available: {e}"
    
    def get_pod_logs(self, namespace: str, name: str, lines: int = 100) -> str:
        """Get pod logs"""
        try:
            url = f"https://{self.api_url}:{self.api_port}/api/v1/namespaces/{namespace}/pods/{name}/log?tailLines={lines}"
            resp = requests.get(url, headers=self._get_headers(), verify=False, timeout=10)
            return resp.text
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            return f"Logs not available: {e}"
    
    def get_events(self, namespace: str, pod_name: str) -> str:
        """Get events for a pod"""
        try:
            url = f"https://{self.api_url}:{self.api_port}/api/v1/namespaces/{namespace}/events"
            resp = requests.get(url, headers=self._get_headers(), verify=False, timeout=10)
            events = resp.json().get("items", [])
            pod_events = [e for e in events if e.get("involvedObject", {}).get("name") == pod_name]
            return str(pod_events[-10:])  # Last 10 events
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            return f"Events not available: {e}"
    
    def get_deployment_for_pod(self, namespace: str, pod_name: str) -> str:
        """Get deployment that owns the pod"""
        try:
            # This is simplified - in reality you'd trace through ReplicaSet
            url = f"https://{self.api_url}:{self.api_port}/apis/apps/v1/namespaces/{namespace}/deployments"
            resp = requests.get(url, headers=self._get_headers(), verify=False, timeout=10)
            return str(resp.json())
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            return f"Deployment details not available: {e}"
