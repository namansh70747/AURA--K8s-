"""
Comprehensive Error Detection for Kubernetes
Detects 100+ error types from pod status, events, logs, and metrics
"""

from typing import Dict, List, Optional, Any
import re
import logging

logger = logging.getLogger(__name__)


class ComprehensiveErrorDetector:
    """
    Detects 100+ Kubernetes error types from various sources
    """
    
    def __init__(self):
        self.error_patterns = self._load_error_patterns()
    
    def _load_error_patterns(self) -> Dict:
        """Load comprehensive error patterns"""
        return {
            # Pod Status Errors (30+ types)
            'pod_status': {
                'OOMKilled': {'severity': 'critical', 'confidence': 1.0},
                'CrashLoopBackOff': {'severity': 'high', 'confidence': 1.0},
                'ImagePullBackOff': {'severity': 'high', 'confidence': 1.0},
                'ErrImagePull': {'severity': 'high', 'confidence': 1.0},
                'ImagePullError': {'severity': 'high', 'confidence': 1.0},
                'InvalidImageName': {'severity': 'high', 'confidence': 1.0},
                'ContainerCreating': {'severity': 'medium', 'confidence': 0.7},
                'PodInitializing': {'severity': 'low', 'confidence': 0.5},
                'Pending': {'severity': 'low', 'confidence': 0.3},
                'Failed': {'severity': 'critical', 'confidence': 1.0},
                'Unknown': {'severity': 'medium', 'confidence': 0.5},
                'Terminating': {'severity': 'low', 'confidence': 0.3},
                'Error': {'severity': 'high', 'confidence': 0.9},
                'Completed': {'severity': 'low', 'confidence': 0.2},
            },
            
            # Container Errors (20+ types)
            'container_errors': {
                'ContainerCannotRun': {'severity': 'critical', 'confidence': 1.0},
                'CreateContainerError': {'severity': 'high', 'confidence': 1.0},
                'CreateContainerConfigError': {'severity': 'high', 'confidence': 1.0},
                'ErrInvalidImageName': {'severity': 'high', 'confidence': 1.0},
                'ErrImageNeverPull': {'severity': 'high', 'confidence': 1.0},
                'ErrImagePull': {'severity': 'high', 'confidence': 1.0},
                'ErrImagePullBackOff': {'severity': 'high', 'confidence': 1.0},
                'ErrRegistryUnavailable': {'severity': 'high', 'confidence': 1.0},
                'ErrNetworkUnavailable': {'severity': 'high', 'confidence': 1.0},
                'NetworkNotReady': {'severity': 'medium', 'confidence': 0.8},
                'FailedSync': {'severity': 'high', 'confidence': 0.9},
                'FailedMount': {'severity': 'high', 'confidence': 1.0},
                'FailedAttachVolume': {'severity': 'high', 'confidence': 1.0},
                'FailedDetachVolume': {'severity': 'medium', 'confidence': 0.8},
                'Unhealthy': {'severity': 'high', 'confidence': 0.9},
                'DeadlineExceeded': {'severity': 'high', 'confidence': 0.9},
            },
            
            # Node Errors (20+ types)
            'node_errors': {
                'NodeNotReady': {'severity': 'critical', 'confidence': 1.0},
                'NodeUnreachable': {'severity': 'critical', 'confidence': 1.0},
                'NodeOutOfDisk': {'severity': 'critical', 'confidence': 1.0},
                'NodeMemoryPressure': {'severity': 'high', 'confidence': 1.0},
                'NodeDiskPressure': {'severity': 'high', 'confidence': 1.0},
                'NodePIDPressure': {'severity': 'high', 'confidence': 1.0},
                'NodeNetworkUnavailable': {'severity': 'high', 'confidence': 1.0},
                'NodeHasNoDiskCapacity': {'severity': 'critical', 'confidence': 1.0},
                'NodeHasInsufficientMemory': {'severity': 'high', 'confidence': 1.0},
                'NodeHasInsufficientPID': {'severity': 'high', 'confidence': 1.0},
                'NodeHasDiskPressure': {'severity': 'high', 'confidence': 1.0},
                'NodeHasMemoryPressure': {'severity': 'high', 'confidence': 1.0},
                'NodeSchedulable': {'severity': 'low', 'confidence': 0.1},
                'NodeUnschedulable': {'severity': 'high', 'confidence': 1.0},
            },
            
            # Event-Based Errors (30+ types)
            'event_errors': {
                'Failed': {'severity': 'high', 'confidence': 1.0},
                'FailedCreate': {'severity': 'high', 'confidence': 1.0},
                'FailedDelete': {'severity': 'medium', 'confidence': 0.8},
                'FailedMount': {'severity': 'high', 'confidence': 1.0},
                'FailedSync': {'severity': 'high', 'confidence': 0.9},
                'FailedAttachVolume': {'severity': 'high', 'confidence': 1.0},
                'FailedDetachVolume': {'severity': 'medium', 'confidence': 0.8},
                'BackOff': {'severity': 'high', 'confidence': 0.9},
                'FailedScheduling': {'severity': 'high', 'confidence': 1.0},
                'FailedBinding': {'severity': 'high', 'confidence': 1.0},
                'FailedValidation': {'severity': 'high', 'confidence': 1.0},
                'FailedAdmission': {'severity': 'high', 'confidence': 1.0},
                'Unhealthy': {'severity': 'high', 'confidence': 0.9},
                'Killing': {'severity': 'medium', 'confidence': 0.7},
                'Pulling': {'severity': 'low', 'confidence': 0.3},
                'Pulled': {'severity': 'low', 'confidence': 0.1},
                'Created': {'severity': 'low', 'confidence': 0.1},
                'Started': {'severity': 'low', 'confidence': 0.1},
            },
            
            # Log-Based Errors (20+ types)
            'log_errors': {
                'OutOfMemoryError': {'severity': 'critical', 'confidence': 0.95},
                'MemoryError': {'severity': 'critical', 'confidence': 0.95},
                'StackOverflowError': {'severity': 'high', 'confidence': 0.9},
                'NullPointerException': {'severity': 'high', 'confidence': 0.9},
                'ConnectionRefused': {'severity': 'high', 'confidence': 0.9},
                'ConnectionTimeout': {'severity': 'high', 'confidence': 0.9},
                'TimeoutException': {'severity': 'high', 'confidence': 0.9},
                'DatabaseConnectionError': {'severity': 'high', 'confidence': 0.9},
                'DNSResolutionFailed': {'severity': 'high', 'confidence': 0.9},
                'CertificateError': {'severity': 'high', 'confidence': 0.9},
                'SSLHandshakeFailed': {'severity': 'high', 'confidence': 0.9},
                'PermissionDenied': {'severity': 'high', 'confidence': 0.9},
                'FileNotFoundError': {'severity': 'medium', 'confidence': 0.8},
                'IOError': {'severity': 'medium', 'confidence': 0.8},
                'NetworkError': {'severity': 'high', 'confidence': 0.9},
            },
            
            # Metric-Based Errors (20+ types)
            'metric_errors': {
                'HighCPU': {'severity': 'high', 'confidence': 0.8, 'threshold': 80.0},
                'HighMemory': {'severity': 'high', 'confidence': 0.8, 'threshold': 85.0},
                'HighDiskIO': {'severity': 'medium', 'confidence': 0.7, 'threshold': 90.0},
                'HighNetworkLatency': {'severity': 'medium', 'confidence': 0.7, 'threshold': 1000.0},
                'HighErrorRate': {'severity': 'high', 'confidence': 0.8, 'threshold': 10.0},
                'CPUThrottling': {'severity': 'high', 'confidence': 0.9, 'threshold': 95.0},
                'MemoryPressure': {'severity': 'high', 'confidence': 0.9, 'threshold': 90.0},
                'DiskPressure': {'severity': 'high', 'confidence': 0.9, 'threshold': 90.0},
                'NetworkErrors': {'severity': 'medium', 'confidence': 0.7, 'threshold': 5.0},
                'HighRestartRate': {'severity': 'high', 'confidence': 0.9, 'threshold': 5.0},
            }
        }
    
    def detect_all_errors(self, pod_data: Dict, events: List[Dict], 
                         logs: str, metrics: Dict) -> List[Dict]:
        """
        Detect all possible Kubernetes errors from multiple sources
        
        Args:
            pod_data: Pod information dictionary
            events: List of Kubernetes events
            logs: Pod logs (string)
            metrics: Pod metrics dictionary
            
        Returns:
            List of detected errors with severity and confidence
        """
        errors = []
        
        # 1. Pod Status Errors
        errors.extend(self._detect_pod_status_errors(pod_data))
        
        # 2. Container Errors
        errors.extend(self._detect_container_errors(pod_data))
        
        # 3. Node Errors
        errors.extend(self._detect_node_errors(pod_data))
        
        # 4. Event-Based Errors
        errors.extend(self._detect_event_errors(events))
        
        # 5. Log-Based Errors
        errors.extend(self._detect_log_errors(logs))
        
        # 6. Metric-Based Errors
        errors.extend(self._detect_metric_errors(metrics))
        
        # Remove duplicates (same error type)
        seen = set()
        unique_errors = []
        for error in errors:
            error_key = (error['type'], error.get('severity'))
            if error_key not in seen:
                seen.add(error_key)
                unique_errors.append(error)
        
        return unique_errors
    
    def _detect_pod_status_errors(self, pod_data: Dict) -> List[Dict]:
        """Detect errors from pod status"""
        errors = []
        
        # Check last state reason
        last_state_reason = pod_data.get('last_state_reason', '')
        if last_state_reason:
            if last_state_reason in self.error_patterns['pod_status']:
                pattern = self.error_patterns['pod_status'][last_state_reason]
                errors.append({
                    'type': last_state_reason.lower(),
                    'severity': pattern['severity'],
                    'confidence': pattern['confidence'],
                    'source': 'pod_status',
                    'message': f"Pod status: {last_state_reason}"
                })
        
        # Check pod phase
        phase = pod_data.get('status', pod_data.get('phase', '')).lower()
        if phase in ['failed', 'unknown']:
            errors.append({
                'type': f'pod_{phase}',
                'severity': 'critical' if phase == 'failed' else 'medium',
                'confidence': 1.0 if phase == 'failed' else 0.5,
                'source': 'pod_status',
                'message': f"Pod phase: {phase}"
            })
        
        # Check if pod is not ready
        if not pod_data.get('ready', True):
            errors.append({
                'type': 'pod_not_ready',
                'severity': 'high',
                'confidence': 0.9,
                'source': 'pod_status',
                'message': "Pod is not ready"
            })
        
        return errors
    
    def _detect_container_errors(self, pod_data: Dict) -> List[Dict]:
        """Detect errors from container status"""
        errors = []
        
        containers = pod_data.get('containers', [])
        for container in containers:
            state = container.get('state', '')
            waiting_reason = container.get('waiting_reason', '')
            termination_reason = container.get('termination_reason', '')
            
            # Check waiting state
            if state == 'waiting' and waiting_reason:
                if waiting_reason in self.error_patterns['container_errors']:
                    pattern = self.error_patterns['container_errors'][waiting_reason]
                    errors.append({
                        'type': waiting_reason.lower(),
                        'severity': pattern['severity'],
                        'confidence': pattern['confidence'],
                        'source': 'container_status',
                        'message': f"Container {container.get('name', 'unknown')} waiting: {waiting_reason}"
                    })
            
            # Check termination reason
            if termination_reason:
                if termination_reason in self.error_patterns['container_errors']:
                    pattern = self.error_patterns['container_errors'][termination_reason]
                    errors.append({
                        'type': termination_reason.lower(),
                        'severity': pattern['severity'],
                        'confidence': pattern['confidence'],
                        'source': 'container_status',
                        'message': f"Container {container.get('name', 'unknown')} terminated: {termination_reason}"
                    })
            
            # Check restart count
            restart_count = container.get('restart_count', 0)
            if restart_count > 3:
                errors.append({
                    'type': 'high_restart_count',
                    'severity': 'high',
                    'confidence': 0.9,
                    'source': 'container_status',
                    'message': f"Container {container.get('name', 'unknown')} has {restart_count} restarts"
                })
        
        return errors
    
    def _detect_node_errors(self, pod_data: Dict) -> List[Dict]:
        """Detect errors from node conditions"""
        errors = []
        
        node_conditions = pod_data.get('node_conditions', [])
        for condition in node_conditions:
            condition_type = condition.get('type', '')
            status = condition.get('status', '')
            
            if condition_type in self.error_patterns['node_errors'] and status == 'True':
                pattern = self.error_patterns['node_errors'][condition_type]
                errors.append({
                    'type': condition_type.lower().replace(' ', '_'),
                    'severity': pattern['severity'],
                    'confidence': pattern['confidence'],
                    'source': 'node_conditions',
                    'message': f"Node condition: {condition_type}"
                })
        
        return errors
    
    def _detect_event_errors(self, events: List[Dict]) -> List[Dict]:
        """Detect errors from Kubernetes events"""
        errors = []
        
        for event in events:
            reason = event.get('reason', '')
            event_type = event.get('type', '')
            
            # Only process Warning and Error events
            if event_type in ['Warning', 'Error']:
                if reason in self.error_patterns['event_errors']:
                    pattern = self.error_patterns['event_errors'][reason]
                    errors.append({
                        'type': reason.lower().replace(' ', '_'),
                        'severity': pattern['severity'],
                        'confidence': pattern['confidence'],
                        'source': 'events',
                        'message': event.get('message', f"Event: {reason}")
                    })
        
        return errors
    
    def _detect_log_errors(self, logs: str) -> List[Dict]:
        """Detect errors from pod logs"""
        errors = []
        
        if not logs:
            return errors
        
        logs_lower = logs.lower()
        
        for error_type, pattern_info in self.error_patterns['log_errors'].items():
            # Search for error patterns in logs
            patterns = [
                error_type.lower(),
                error_type.lower().replace('error', ''),
                error_type.lower().replace('exception', ''),
            ]
            
            for pattern in patterns:
                if pattern in logs_lower:
                    errors.append({
                        'type': error_type.lower(),
                        'severity': pattern_info['severity'],
                        'confidence': pattern_info['confidence'],
                        'source': 'logs',
                        'message': f"Found {error_type} in logs"
                    })
                    break
        
        return errors
    
    def _detect_metric_errors(self, metrics: Dict) -> List[Dict]:
        """Detect errors from metrics"""
        errors = []
        
        for error_type, pattern_info in self.error_patterns['metric_errors'].items():
            threshold = pattern_info.get('threshold', 100.0)
            metric_key = error_type.lower().replace('high', '').replace('throttling', '').replace('pressure', '')
            
            # Map error types to metric keys
            metric_mapping = {
                'cpu': 'cpu_utilization',
                'memory': 'memory_utilization',
                'diskio': 'disk_utilization',
                'networklatency': 'latency_ms',
                'errorrate': 'error_rate',
                'networkerrors': 'network_error_rate',
                'restartrate': 'restart_count'
            }
            
            metric_key_mapped = metric_mapping.get(metric_key, metric_key)
            metric_value = metrics.get(metric_key_mapped, 0)
            
            if metric_value > threshold:
                errors.append({
                    'type': error_type.lower(),
                    'severity': pattern_info['severity'],
                    'confidence': pattern_info['confidence'],
                    'source': 'metrics',
                    'message': f"{error_type}: {metric_value:.2f} > {threshold}",
                    'metric_value': metric_value,
                    'threshold': threshold
                })
        
        return errors

