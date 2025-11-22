"""
AURA MCP Server - Production AI-Powered Kubernetes Remediation
Uses Ollama for intelligent issue analysis and multi-step remediation planning
"""

import os
import logging
import asyncio
import uuid
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional, Any
import httpx
import json

# Gemini API integration
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️  Google Generative AI not available, using Ollama only")

# Comprehensive error detection
try:
    from .error_detector import ComprehensiveErrorDetector
except ImportError:
    try:
        from error_detector import ComprehensiveErrorDetector
    except ImportError:
        ComprehensiveErrorDetector = None
        print("⚠️  ComprehensiveErrorDetector not available")
try:
    # Try relative import first (when running from mcp/ directory)
    from .tools import KubernetesTools
except ImportError:
    # Fall back to absolute import (when running from project root)
    try:
        from mcp.tools import KubernetesTools
    except ImportError:
        # Last resort - direct import if tools.py is in same directory
        import sys
        from pathlib import Path
        tools_path = Path(__file__).parent / "tools.py"
        if tools_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("tools", tools_path)
            tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tools)
            KubernetesTools = tools.KubernetesTools
        else:
            raise ImportError("Could not import KubernetesTools from mcp.tools or tools")
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ollama configuration (needed for lifespan)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# Gemini API configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCvwp8qA9NY2tiXxjqShEjsvRGW9rOA388")
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_MODEL = genai.GenerativeModel('gemini-pro')
        logger.info("✅ Gemini API configured")
    except Exception as e:
        logger.warning(f"⚠️  Gemini API configuration failed: {e}")
        GEMINI_MODEL = None
else:
    GEMINI_MODEL = None

# Initialize error detector
error_detector = ComprehensiveErrorDetector() if ComprehensiveErrorDetector else None
if error_detector:
    logger.info("✅ Comprehensive error detector initialized")

# Rate limiting
limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                models_data = response.json()
                models_list = models_data.get("models", [])
                model_names = [m.get("name", "") for m in models_list]
                if not any(OLLAMA_MODEL in name for name in model_names):
                    logger.warning(f"Model {OLLAMA_MODEL} not found. Pull with: docker exec aura-ollama ollama pull {OLLAMA_MODEL}")
                else:
                    logger.info(f"✅ Ollama model {OLLAMA_MODEL} ready")
    except Exception as e:
        logger.error(f"Failed to check Ollama: {e}")
    yield
    # Shutdown (if needed)

app = FastAPI(title="AURA MCP Server", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware - configurable for production
allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
if allowed_origins == ["*"] and os.getenv("ENVIRONMENT", "development") == "production":
    # In production, restrict CORS
    allowed_origins = ["http://localhost:3000", "https://localhost:3000"]  # Default safe origins
    logger.warning("CORS set to * in production - consider restricting origins")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API versioning - create v1 router
from fastapi import APIRouter
v1_router = APIRouter(prefix="/v1", tags=["v1"])

# Legacy endpoint (redirects to v1) - will be added after v1 endpoint definition

# API Key authentication
API_KEY = os.getenv("MCP_SERVER_API_KEY", "")
REQUIRE_AUTH = os.getenv("MCP_SERVER_REQUIRE_AUTH", "false").lower() == "true"

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify API key for authenticated endpoints"""
    if not REQUIRE_AUTH:
        return True
    
    environment = os.getenv("ENVIRONMENT", "development")
    if not API_KEY:
        # If auth required but no key set, fail in production
        if environment == "production":
            logger.error("API key authentication required but no API_KEY set in production")
            raise HTTPException(status_code=500, detail="API key authentication not configured")
        else:
            logger.debug("API key authentication disabled (development mode)")
            return True
    
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

# Request ID middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests for tracing"""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

OLLAMA_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "5"))
OLLAMA_RETRY_BACKOFF_BASE = float(os.getenv("OLLAMA_RETRY_BACKOFF_BASE", "2.0"))
OLLAMA_REQUEST_TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "120"))  # seconds

try:
    k8s_tools = KubernetesTools()
    logger.info("✅ Kubernetes client initialized")
except Exception as e:
    logger.error(f"❌ Failed to initialize Kubernetes client: {e}")
    k8s_tools = None


class IssueAnalysisRequest(BaseModel):
    issue_id: str = Field(..., min_length=1, max_length=255, description="Unique issue identifier")
    pod_name: str = Field(..., min_length=1, max_length=253, description="Pod name (RFC 1123 subdomain)")
    namespace: str = Field(..., min_length=1, max_length=63, description="Namespace name (RFC 1123 label)")
    issue_type: str = Field(..., min_length=1, max_length=100, description="Type of issue (e.g., OOMKilled, CrashLoopBackOff)")
    severity: str = Field(..., description="Issue severity", pattern="^(critical|high|medium|low)$")
    description: str = Field(default="", max_length=10000, description="Human-readable description of the issue")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context data")
    
    @field_validator('issue_id')
    @classmethod
    def validate_issue_id(cls, v):
        """Validate issue ID format"""
        if not v or not v.strip():
            raise ValueError("Issue ID cannot be empty or whitespace")
        if len(v) > 255:
            raise ValueError("Issue ID exceeds 255 characters")
        return v.strip()
    
    @field_validator('pod_name', 'namespace')
    @classmethod
    def validate_k8s_names(cls, v):
        """Validate Kubernetes resource names (RFC 1123 subdomain format)"""
        if not v or not v.strip():
            raise ValueError("Name cannot be empty or whitespace")
        v = v.strip()
        if len(v) > 253:
            raise ValueError("Name exceeds 253 characters")
        # Check for valid characters (RFC 1123 subdomain)
        if not re.match(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$', v):
            raise ValueError("Invalid Kubernetes name format (must match RFC 1123 subdomain)")
        return v
    
    @field_validator('issue_type')
    @classmethod
    def validate_issue_type(cls, v):
        """Validate issue type"""
        if not v or not v.strip():
            raise ValueError("Issue type cannot be empty or whitespace")
        v = v.strip()
        if len(v) > 100:
            raise ValueError("Issue type exceeds 100 characters")
        return v
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        """Validate description length"""
        if v and len(v) > 10000:
            raise ValueError("Description exceeds 10000 characters")
        return v
    
    @field_validator('context')
    @classmethod
    def validate_context(cls, v):
        """Validate context data structure and size"""
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("Context must be a dictionary")
        # Limit context size to prevent excessive memory usage
        context_str = json.dumps(v)
        if len(context_str) > 100000:  # 100KB limit
            raise ValueError("Context data exceeds 100KB limit")
        return v

class RemediationAction(BaseModel):
    type: str = Field(..., description="Resource type (pod, deployment, statefulset, node)")
    target: str = Field(..., min_length=1, description="Target resource")
    operation: str = Field(..., min_length=1, description="Operation to perform")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Operation-specific parameters")
    order: int = Field(default=0, ge=0, description="Execution order (0 = first)")


class RemediationPlan(BaseModel):
    actions: List[RemediationAction] = Field(default_factory=list, description="Ordered list of remediation actions")
    reasoning: str = Field(..., min_length=1, description="Explanation of the remediation plan")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score (0-1)")
    risk_level: str = Field(default="medium", pattern="^(low|medium|high)$", description="Risk level of remediation")


@app.get("/health")
@app.get("/v1/health")  # Versioned endpoint
async def health_check(request: Request):
    """Deep health check with dependency validation including model availability"""
    healthy = True
    issues = []
    details = {}
    
    # Check K8s tools
    if not k8s_tools:
        healthy = False
        issues.append("kubernetes_client")
        details["kubernetes_client"] = "not_initialized"
    else:
        details["kubernetes_client"] = "available"
    
    # Check Ollama connection and model availability
    ollama_healthy = False
    model_available = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Check Ollama service is reachable
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                ollama_healthy = True
                # Check if model is actually loaded and available
                models_data = resp.json()
                models_list = models_data.get("models", [])
                model_names = [m.get("name", "") for m in models_list]
                if any(OLLAMA_MODEL in name for name in model_names):
                    model_available = True
                    details["ollama_model"] = f"{OLLAMA_MODEL} (available)"
                else:
                    details["ollama_model"] = f"{OLLAMA_MODEL} (not found in available models)"
                    issues.append("ollama_model_not_loaded")
            else:
                details["ollama"] = f"HTTP {resp.status_code}"
                issues.append("ollama")
    except httpx.ConnectError as e:
        details["ollama"] = f"connection_error: {str(e)}"
        issues.append("ollama")
        logger.warning(f"Ollama connection error: {e}")
    except httpx.TimeoutException as e:
        details["ollama"] = f"timeout: {str(e)}"
        issues.append("ollama")
        logger.warning(f"Ollama timeout: {e}")
    except Exception as e:
        details["ollama"] = f"error: {str(e)}"
        issues.append("ollama")
        logger.warning(f"Ollama health check failed: {e}")
    
    if not ollama_healthy:
        healthy = False
    if not model_available:
        # Model not loaded is CRITICAL - service cannot generate remediation plans without model
        # Make this a hard requirement for service health
        healthy = False
        logger.error(f"CRITICAL: Ollama model {OLLAMA_MODEL} not available - service cannot generate remediation plans")
    
    # Standardize response format to match ML service
    response = {
        "status": "healthy" if healthy else "unhealthy",
        "service": "AURA MCP Server",
        "ready": healthy and model_available,  # Ready only if model is available
        "issues": issues if issues else None,
        "details": details,
        "request_id": getattr(request.state, "request_id", None)
    }
    
    status_code = 200 if healthy else 503
    if status_code != 200:
        from fastapi.responses import JSONResponse
        return JSONResponse(content=response, status_code=status_code)
    return response


@v1_router.post("/analyze-with-plan")
@limiter.limit("30/minute")  # Rate limit: 30 requests per minute per IP
async def analyze_with_plan_v1(request: Request, analysis_request: IssueAnalysisRequest, _: bool = Depends(verify_api_key)) -> RemediationPlan:
    try:
        logger.info(f"Analyzing: {analysis_request.namespace}/{analysis_request.pod_name} - {analysis_request.issue_type}")

        if not k8s_tools:
            raise HTTPException(status_code=500, detail="Kubernetes client not available")

        pod_info = k8s_tools.get_pod(analysis_request.namespace, analysis_request.pod_name)
        events = k8s_tools.get_events(analysis_request.namespace, analysis_request.pod_name, limit=10)
        logs = k8s_tools.get_pod_logs(analysis_request.namespace, analysis_request.pod_name, lines=50)
        deployment = k8s_tools.get_deployment_for_pod(analysis_request.namespace, analysis_request.pod_name)
        metrics = k8s_tools.get_pod_resource_usage(analysis_request.namespace, analysis_request.pod_name)
        
        # Comprehensive error detection
        detected_errors = []
        if error_detector:
            try:
                detected_errors = error_detector.detect_all_errors(
                    pod_data=pod_info,
                    events=events,
                    logs=logs if logs else "",
                    metrics=metrics if metrics else {}
                )
                logger.info(f"Detected {len(detected_errors)} errors from comprehensive detector")
            except Exception as e:
                logger.warning(f"Error detection failed: {e}")

        context_data = analysis_request.context or {}
        context_data['detected_errors'] = detected_errors
        
        prompt = build_comprehensive_prompt(
            analysis_request, pod_info, events, logs, deployment, metrics, context_data, detected_errors
        )

        try:
            # Use hybrid AI: Gemini for complex issues, Ollama for simple ones
            complexity = assess_issue_complexity(analysis_request, detected_errors, events)
            
            if complexity == 'complex' and GEMINI_MODEL:
                logger.info("Using Gemini API for complex issue analysis")
                ai_response = await call_gemini(prompt)
            else:
                logger.info("Using Ollama for issue analysis")
                ai_response = await call_ollama(prompt)
            
            plan = parse_remediation_plan(ai_response)
            
            validate_plan(plan)
            
            return RemediationPlan(
                actions=plan.get("actions", []),
                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                confidence=plan.get("confidence", 0.75),
                risk_level=plan.get("risk_level", "medium")
            )

        except httpx.ConnectError as e:
            logger.error(f"AI analysis failed: connection error - {type(e).__name__}: {e}", exc_info=True)
            logger.warning("Transient connection error detected, using fallback plan")
            fallback = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
            return RemediationPlan(
                actions=fallback["actions"],
                reasoning=fallback["reasoning"],
                confidence=fallback["confidence"],
                risk_level=fallback["risk_level"]
            )
        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            logger.error(f"AI analysis failed: timeout error - {type(e).__name__}: {e}", exc_info=True)
            logger.warning("Transient timeout error detected, using fallback plan")
            fallback = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
            return RemediationPlan(
                actions=fallback["actions"],
                reasoning=fallback["reasoning"],
                confidence=fallback["confidence"],
                risk_level=fallback["risk_level"]
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"AI analysis failed: HTTP error {e.response.status_code} - {type(e).__name__}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"AI service returned HTTP {e.response.status_code}: {str(e)}") from e
        except ValueError as e:
            # JSON parsing or validation errors
            logger.error(f"AI analysis failed: validation/parsing error - {type(e).__name__}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"AI response validation failed: {str(e)}") from e
        except json.JSONDecodeError as e:
            logger.error(f"AI analysis failed: JSON decode error - {type(e).__name__}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to parse AI response JSON: {str(e)}") from e
        except KeyError as e:
            logger.error(f"AI analysis failed: missing required field - {type(e).__name__}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"AI response missing required field: {str(e)}") from e
        except Exception as e:
            # Catch-all for unknown exceptions with full traceback
            logger.error(f"AI analysis failed: unexpected error - {type(e).__name__}: {e}", exc_info=True)
            # Only use fallback for truly transient errors (connection, timeout)
            # For parsing/validation errors, raise to indicate system issue
            if isinstance(e, (httpx.NetworkError, asyncio.TimeoutError)):
                logger.warning("Transient network error detected, using fallback plan")
                fallback = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                return RemediationPlan(
                    actions=fallback["actions"],
                    reasoning=fallback["reasoning"],
                    confidence=fallback["confidence"],
                    risk_level=fallback["risk_level"]
                )
            else:
                # For unknown errors, raise to surface the issue
                logger.error(f"AI analysis failed with non-transient error: {type(e).__name__}: {e}")
                raise HTTPException(status_code=500, detail=f"AI analysis failed with unexpected error: {str(e)}") from e

    except HTTPException:
        raise
    except httpx.ConnectError as e:
        logger.error(f"Kubernetes/MCP connection error: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Service unavailable: connection error - {str(e)}") from e
    except httpx.TimeoutException as e:
        logger.error(f"Kubernetes/MCP timeout error: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=504, detail=f"Service timeout: {str(e)}") from e
    except Exception as e:
        logger.error(f"Unexpected error in analyze_with_plan: {type(e).__name__}: {e}", exc_info=True)
        return RemediationPlan(
            actions=[RemediationAction(type="pod", target=analysis_request.pod_name if analysis_request.pod_name else "pod", operation="restart", order=0)],
            reasoning=f"Error during analysis: {type(e).__name__}: {str(e)}. Safe fallback applied.",
            confidence=0.5,
            risk_level="low"
        )

# Legacy endpoint (redirects to v1)
@app.post("/analyze-with-plan")
@limiter.limit("30/minute")
async def analyze_with_plan(request: Request, analysis_request: IssueAnalysisRequest, _: bool = Depends(verify_api_key)) -> RemediationPlan:
    """Legacy endpoint - redirects to v1"""
    return await analyze_with_plan_v1(request, analysis_request, _)

# Include v1 router after all endpoints are defined
app.include_router(v1_router)


def assess_issue_complexity(request, detected_errors, events) -> str:
    """Assess issue complexity to determine which AI to use"""
    # Simple: single error, clear cause
    # Complex: multiple errors, unclear cause, cascading failures
    
    if len(detected_errors) > 3 or request.severity == 'critical':
        return 'complex'
    elif len(detected_errors) == 1 and request.severity == 'low':
        return 'simple'
    else:
        return 'medium'


async def call_gemini(prompt: str) -> str:
    """Call Gemini API for complex issue analysis"""
    try:
        response = GEMINI_MODEL.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        # Fallback to Ollama
        return await call_ollama(prompt)


def build_comprehensive_prompt(request, pod_info, events, logs, deployment, metrics, context, detected_errors):
    container_status = ""
    if context.get("containers"):
        for c in context["containers"]:
            container_status += f"\n  - {c['name']}: {c.get('state', 'unknown')} (restarts: {c.get('restart_count', 0)})"
            if c.get('reason'):
                container_status += f"\n    Reason: {c['reason']}"
            if c.get('message'):
                container_status += f"\n    Message: {c['message']}"

    resource_info = ""
    if context.get("resources"):
        for r in context["resources"]:
            resource_info += f"\n  - {r['name']}:"
            if r.get('requests'):
                resource_info += f"\n    Requests: CPU={r['requests'].get('cpu', 'N/A')}, Memory={r['requests'].get('memory', 'N/A')}"
            if r.get('limits'):
                resource_info += f"\n    Limits: CPU={r['limits'].get('cpu', 'N/A')}, Memory={r['limits'].get('memory', 'N/A')}"

    events_summary = "\n".join([f"  - {e['type']}: {e['reason']} - {e['message']}" for e in events[:3]])
    
    prompt = f"""You are an expert Kubernetes SRE analyzing a production issue. Generate a precise, executable remediation plan.

ISSUE ANALYSIS:
Pod: {request.namespace}/{request.pod_name}
Issue: {request.issue_type}
Severity: {request.severity}
Description: {request.description}

POD STATE:
Status: {pod_info.get('status', 'Unknown')}
Ready: {pod_info.get('ready', False)}
Total Restarts: {pod_info.get('restart_count', 0)}
Node: {pod_info.get('node', 'Unknown')}
Age: {context.get('age_seconds', 0) / 60:.1f} minutes

CONTAINERS:{container_status}

RESOURCES:{resource_info}

DEPLOYMENT:
Name: {deployment.get('name') if deployment else 'Standalone Pod'}
Replicas: {deployment.get('replicas') if deployment else 'N/A'} (Ready: {deployment.get('ready_replicas') if deployment else 'N/A'})
Image: {deployment.get('image') if deployment else 'N/A'}

CURRENT METRICS:
CPU: {metrics.get('cpu_millicores', 'N/A') if metrics else 'N/A'}m
Memory: {metrics.get('memory_mib', 'N/A') if metrics else 'N/A'} MiB

RECENT EVENTS:
{events_summary if events_summary else '  None'}

DETECTED ERRORS ({len(detected_errors)} total):
{chr(10).join([f"  - {e['type']}: {e['message']} (severity: {e['severity']}, confidence: {e['confidence']:.2f})" for e in detected_errors[:10]]) if detected_errors else '  None'}

LOGS (last 50 lines):
{logs[-2000:] if logs else 'No logs available'}

TASK: Analyze this issue and create a detailed remediation plan.

AVAILABLE ACTIONS:

1. POD ACTIONS:
   - restart: Delete pod for controller recreation
     Parameters: {{"grace_period_seconds": 30}}
   - delete: Permanently delete pod
     Parameters: {{"grace_period_seconds": 0}}

2. DEPLOYMENT ACTIONS:
   - increase_memory: Scale memory resources
     Parameters: {{"factor": 1.5}}  # 50% increase
   - increase_cpu: Scale CPU resources
     Parameters: {{"factor": 1.5}}
   - scale: Change replica count
     Parameters: {{"replicas": 1, "direction": "up|down"}}
   - update_image: Change container image
     Parameters: {{"image": "nginx:1.21", "container": "app"}}
   - restart_rollout: Trigger rolling restart
     Parameters: {{}}

3. STATEFULSET ACTIONS:
   - increase_memory, increase_cpu (same as deployment)

4. NODE ACTIONS:
   - drain: Mark node unschedulable
   - uncordon: Mark node schedulable

RESPONSE FORMAT (JSON only):
{{
  "actions": [
    {{
      "type": "pod|deployment|statefulset|node",
      "target": "resource-identifier",
      "operation": "operation-name",
      "parameters": {{}},
      "order": 0
    }}
  ],
  "reasoning": "Detailed explanation of why these actions solve the issue",
  "confidence": 0.85,
  "risk_level": "low|medium|high"
}}

GUIDELINES:
- For OOMKilled: increase memory (factor 1.5-2.0)
- For CrashLoopBackOff: restart pod, check logs for root cause
- For HighCPU: increase CPU or scale horizontally
- For ImagePullBackOff: restart pod to retry
- For frequent restarts: investigate root cause, may need memory/CPU increase
- Multiple actions allowed in sequence (set order: 0, 1, 2...)
- Confidence: >0.8 for clear issues, 0.6-0.8 for uncertain
- Risk: low (restart), medium (scale/resource change), high (node operations)

Respond with ONLY valid JSON."""

    return prompt


async def call_ollama(prompt: str, retries: Optional[int] = None) -> str:
    """Call Ollama with configurable retries and exponential backoff"""
    if retries is None:
        retries = OLLAMA_MAX_RETRIES
    
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "temperature": 0.2,
                        "num_predict": 2000,
                    },
                )

                if response.status_code != 200:
                    logger.error(f"Ollama error {response.status_code}: {response.text}")
                    if attempt < retries - 1:
                        backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                        logger.info(f"Retrying Ollama call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                        await asyncio.sleep(backoff)
                        continue
                    raise Exception(f"Ollama API error: {response.status_code}")

                result = response.json()
                return result.get("response", "")

        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to Ollama at {OLLAMA_URL} (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                logger.info(f"Retrying Ollama connection after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Cannot connect to Ollama at {OLLAMA_URL}") from e
        except httpx.TimeoutException as e:
            logger.error(f"Ollama request timeout (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                logger.info(f"Retrying Ollama call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Ollama request timeout after {OLLAMA_REQUEST_TIMEOUT}s") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error {e.response.status_code} (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1 and e.response.status_code >= 500:
                # Retry on server errors (5xx)
                backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                logger.info(f"Retrying Ollama call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Ollama HTTP error {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Ollama call error (attempt {attempt + 1}/{retries}): {type(e).__name__}: {e}")
            # Check if error is retryable
            if attempt < retries - 1 and isinstance(e, (httpx.NetworkError, asyncio.TimeoutError)):
                backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                logger.info(f"Retrying Ollama call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise
    
    return ""


def parse_remediation_plan(response_text: str) -> dict:
    """Parse remediation plan from Ollama response with comprehensive JSON validation"""
    try:
        # Improved JSON extraction - handle nested JSON and code blocks
        # Find all JSON-like structures
        import re
        
        # Try to find JSON object boundaries more reliably
        # Look for balanced braces
        brace_count = 0
        start_idx = -1
        
        for i, char in enumerate(response_text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    # Found complete JSON object
                    json_str = response_text[start_idx:i+1]
                    try:
                        plan = json.loads(json_str)
                        # Comprehensive validation before returning
                        if validate_plan_structure(plan):
                            return plan
                    except json.JSONDecodeError:
                        # Try next JSON object
                        continue
        
        # Fallback: try original method
        start_idx = response_text.find("{")
        end_idx = response_text.rfind("}") + 1
        
        if start_idx == -1 or end_idx == 0:
            logger.warning(f"No JSON in response: {response_text[:200]}")
            raise ValueError("No JSON found")

        json_str = response_text[start_idx:end_idx]
        plan = json.loads(json_str)
        
        # Validate structure comprehensively
        if not validate_plan_structure(plan):
            raise ValueError("Plan structure validation failed")

        return plan

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.error(f"Failed to parse response: {response_text[:500] if len(response_text) > 500 else response_text}")
        # Raise exception for invalid JSON - this is a system error
        raise ValueError(f"Invalid JSON in remediation plan response: {str(e)}") from e
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        logger.error(f"Failed to parse response: {response_text[:500] if len(response_text) > 500 else response_text}")
        # Raise for validation errors
        raise
    except KeyError as e:
        logger.error(f"Missing required key in JSON: {e}")
        logger.error(f"Failed to parse response: {response_text[:500] if len(response_text) > 500 else response_text}")
        raise ValueError(f"Missing required field in remediation plan: {str(e)}") from e


def validate_plan_structure(plan: dict) -> bool:
    """Comprehensive validation of remediation plan structure"""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a dictionary")
    
    # Validate required fields exist
    required = ["actions", "reasoning", "confidence", "risk_level"]
    for field in required:
        if field not in plan:
            raise ValueError(f"Missing required field: {field}")
    
    # Validate actions is a list
    if not isinstance(plan["actions"], list):
        raise ValueError("Actions must be an array")
    
    # Validate confidence is in valid range
    confidence = plan.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise ValueError("Confidence must be a number")
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"Confidence must be between 0.0 and 1.0, got {confidence}")
    
    # Validate risk_level
    risk_level = plan.get("risk_level", "").lower()
    if risk_level not in ["low", "medium", "high"]:
        raise ValueError(f"Risk level must be 'low', 'medium', or 'high', got '{risk_level}'")
    
    # Validate reasoning is not empty
    reasoning = plan.get("reasoning", "")
    if not isinstance(reasoning, str) or len(reasoning.strip()) == 0:
        raise ValueError("Reasoning must be a non-empty string")
    
    # Validate each action structure
    for i, action in enumerate(plan["actions"]):
        if not isinstance(action, dict):
            raise ValueError(f"Action {i} must be a dictionary")
        
        required_action_fields = ["type", "target", "operation"]
        for field in required_action_fields:
            if field not in action:
                raise ValueError(f"Action {i} missing required field: {field}")
            if not isinstance(action[field], str) or len(action[field].strip()) == 0:
                raise ValueError(f"Action {i} field '{field}' must be a non-empty string")
    
    return True


def validate_plan(plan: dict):
    if not plan.get("actions"):
        raise ValueError("No actions in plan")
    
    valid_types = {"pod", "deployment", "statefulset", "node"}
    valid_pod_ops = {"restart", "delete"}
    valid_deploy_ops = {"increase_memory", "increase_cpu", "scale", "update_image", "restart_rollout"}
    valid_ss_ops = {"increase_memory", "increase_cpu"}
    valid_node_ops = {"drain", "uncordon"}
    
    for i, action in enumerate(plan["actions"]):
        if action.get("type") not in valid_types:
            raise ValueError(f"Action {i}: Invalid type: {action.get('type')}")
        
        op = action.get("operation")
        if action["type"] == "pod" and op not in valid_pod_ops:
            raise ValueError(f"Action {i}: Invalid pod operation: {op}")
        elif action["type"] == "deployment" and op not in valid_deploy_ops:
            raise ValueError(f"Action {i}: Invalid deployment operation: {op}")
        elif action["type"] == "statefulset" and op not in valid_ss_ops:
            raise ValueError(f"Action {i}: Invalid statefulset operation: {op}")
        elif action["type"] == "node" and op not in valid_node_ops:
            raise ValueError(f"Action {i}: Invalid node operation: {op}")
        
        # Validate required parameters for each operation
        params = action.get("parameters", {})
        if action["type"] == "deployment":
            if op == "increase_memory" or op == "increase_cpu":
                if "factor" not in params:
                    raise ValueError(f"Action {i}: {op} operation requires 'factor' parameter")
                factor = params.get("factor")
                if not isinstance(factor, (int, float)) or factor < 0.1 or factor > 5.0:
                    raise ValueError(f"Action {i}: factor must be between 0.1 and 5.0, got {factor}")
            elif op == "scale":
                if "replicas" not in params:
                    raise ValueError(f"Action {i}: scale operation requires 'replicas' parameter")
                if "direction" not in params:
                    raise ValueError(f"Action {i}: scale operation requires 'direction' parameter")
                direction = params.get("direction")
                if direction not in ["up", "down"]:
                    raise ValueError(f"Action {i}: direction must be 'up' or 'down', got {direction}")
            elif op == "update_image":
                if "image" not in params:
                    raise ValueError(f"Action {i}: update_image operation requires 'image' parameter")
        
        # Validate target is not empty
        if not action.get("target"):
            raise ValueError(f"Action {i}: target is required")


def get_intelligent_fallback(issue_type: str, pod_info: dict, deployment: Optional[dict]) -> dict:
    issue_lower = issue_type.lower()
    
    if "oom" in issue_lower or "memory" in issue_lower:
        # Use actual resource names instead of placeholders
        target_name = deployment.get("name") if deployment else pod_info.get("name", "pod")
        return {
            "actions": [
                {
                    "type": "deployment" if deployment else "pod",
                    "target": target_name,  # Use actual resource name
                    "operation": "increase_memory" if deployment else "restart",
                    "parameters": {"factor": 1.8} if deployment else {"grace_period_seconds": 30},
                    "order": 0
                }
            ],
            "reasoning": f"OOM detected in {issue_type}. Increasing memory by 80% to prevent recurrence.",
            "confidence": 0.85,
            "risk_level": "medium"
        }
    
    elif "crash" in issue_lower or "backoff" in issue_lower:
        # Use actual pod name
        pod_name = pod_info.get("name", "pod")
        return {
            "actions": [
                {
                    "type": "pod",
                    "target": pod_name,  # Use actual pod name
                    "operation": "restart",
                    "parameters": {"grace_period_seconds": 30},
                    "order": 0
                }
            ],
            "reasoning": f"Crash loop detected. Restarting pod to attempt recovery. Monitor for recurring crashes.",
            "confidence": 0.75,
            "risk_level": "low"
        }
    
    elif "cpu" in issue_lower:
        actions = []
        if deployment:
            target_name = deployment.get("name", "deployment")
            actions.append({
                "type": "deployment",
                "target": target_name,  # Use actual deployment name
                "operation": "increase_cpu",
                "parameters": {"factor": 1.5},
                "order": 0
            })
        else:
            pod_name = pod_info.get("name", "pod")
            actions.append({
                "type": "pod",
                "target": pod_name,  # Use actual pod name
                "operation": "restart",
                "parameters": {"grace_period_seconds": 30},
                "order": 0
            })
        
        return {
            "actions": actions,
            "reasoning": f"High CPU usage detected. {'Increasing CPU limits' if deployment else 'Restarting pod'} to resolve.",
            "confidence": 0.80,
            "risk_level": "medium" if deployment else "low"
        }
    
    elif "image" in issue_lower or "pull" in issue_lower:
        pod_name = pod_info.get("name", "pod")
        return {
            "actions": [
                {
                    "type": "pod",
                    "target": pod_name,  # Use actual pod name
                    "operation": "restart",
                    "parameters": {"grace_period_seconds": 10},
                    "order": 0
                }
            ],
            "reasoning": "Image pull failure. Restarting pod to retry image pull with fresh credentials.",
            "confidence": 0.70,
            "risk_level": "low"
        }
    
    else:
        pod_name = pod_info.get("name", "pod")
        return {
            "actions": [
                {
                    "type": "pod",
                    "target": pod_name,  # Use actual pod name
                    "operation": "restart",
                    "parameters": {"grace_period_seconds": 30},
                    "order": 0
                }
            ],
            "reasoning": f"Generic issue '{issue_type}' detected. Attempting pod restart as safe first step.",
            "confidence": 0.60,
            "risk_level": "low"
        }


@app.post("/get-pod-description")
async def get_pod_description(namespace: str, pod_name: str):
    try:
        if not k8s_tools:
            raise HTTPException(status_code=500, detail="Kubernetes client not available")
        description = k8s_tools.describe_pod(namespace, pod_name)
        return {"description": description}
    except Exception as e:
        logger.error(f"Failed to get pod description: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/namespace/{namespace}/overview")
async def get_namespace_overview(namespace: str):
    try:
        if not k8s_tools:
            raise HTTPException(status_code=500, detail="Kubernetes client not available")
        resources = k8s_tools.get_namespace_resources(namespace)
        return resources
    except Exception as e:
        logger.error(f"Failed to get namespace overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": "Failed to fetch models"}
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    import signal
    import sys
    
    # Graceful shutdown handler with cleanup
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        logger.info(f"Received shutdown signal {sig}, initiating graceful shutdown...")
        shutdown_event.set()
        # Give time for ongoing requests to complete
        import time
        time.sleep(2)
        logger.info("Shutdown complete")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Register cleanup on shutdown
    import atexit
    def cleanup():
        logger.info("Cleaning up resources on shutdown...")
        # Close any open HTTP clients if needed
        try:
            # Cleanup can be added here if needed
            pass
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
    
    atexit.register(cleanup)
    
    port = int(os.getenv("MCP_PORT", "8000"))
    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        cleanup()
    except Exception as e:
        logger.error(f"Error running server: {e}")
        cleanup()
        raise
