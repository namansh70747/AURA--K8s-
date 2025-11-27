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

# Use uvloop for faster event loop (production optimization)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    logger = logging.getLogger(__name__)
    logger.info("✅ Using uvloop for optimized async performance")
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("⚠️  uvloop not available, using default event loop (install with: pip install uvloop)")

# Connection pooling for aiohttp/httpx
# httpx already uses connection pooling, but we can optimize
httpx_connector_config = {
    "limits": httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    ),
    "timeout": httpx.Timeout(30.0, connect=5.0),
}

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
    from .remediation_planner import RemediationPlanner, MultiStrategyRemediationPlan
    from .cost_calculator import CostCalculator, CostOptimizedPlanner
    from .remediation_learner import RemediationLearningEngine, RemediationSuccessPredictor, BestActionRecommender
    from .safety_checker import RemediationSafetyChecker
except ImportError:
    # Fall back to absolute import (when running from project root)
    try:
        from mcp.tools import KubernetesTools
        from mcp.remediation_planner import RemediationPlanner, MultiStrategyRemediationPlan
        from mcp.cost_calculator import CostCalculator, CostOptimizedPlanner
        from mcp.remediation_learner import RemediationLearningEngine, RemediationSuccessPredictor, BestActionRecommender
        from mcp.safety_checker import RemediationSafetyChecker
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
        # Try to import new modules
        try:
            from mcp.remediation_planner import RemediationPlanner, MultiStrategyRemediationPlan
            from mcp.cost_calculator import CostCalculator, CostOptimizedPlanner
            from mcp.remediation_learner import RemediationLearningEngine, RemediationSuccessPredictor, BestActionRecommender
            from mcp.safety_checker import RemediationSafetyChecker
        except ImportError:
            RemediationPlanner = None
            CostCalculator = None
            RemediationLearningEngine = None
            RemediationSafetyChecker = None
            logger.warning("Advanced remediation modules not available, using basic mode")
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ollama configuration (needed for lifespan)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# Gemini API configuration - read from environment variable only (no hardcoded default)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_AVAILABLE:
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            # Use gemini-2.5-flash (best available, provides excellent quality)
            # Pro models require different API key (current key works with Flash)
            GEMINI_MODEL = genai.GenerativeModel('gemini-2.5-flash')
            logger.info("✅ Using Gemini 2.5 Flash model (enhanced prompts for high-quality responses)")
            logger.info("✅ Gemini API configured")
        except Exception as e:
            logger.warning(f"⚠️  Gemini API configuration failed: {e}")
            GEMINI_MODEL = None
    else:
        logger.info("ℹ️  GEMINI_API_KEY not set, using Ollama only")
        GEMINI_MODEL = None
else:
    if GEMINI_API_KEY:
        logger.warning("⚠️  Gemini package not installed (pip install google-generativeai), using Ollama only")
    GEMINI_MODEL = None

# Groq API configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")  # Updated to current model  # Fast and capable model
GROQ_AVAILABLE = bool(GROQ_API_KEY and GROQ_API_KEY.strip())
if GROQ_AVAILABLE:
    logger.info("✅ Groq API configured")
    logger.info(f"✅ Using Groq model: {GROQ_MODEL}")
else:
    logger.info("ℹ️  GROQ_API_KEY not set, Groq will not be available")

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

OLLAMA_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))  # Reduced retries, faster failure
OLLAMA_RETRY_BACKOFF_BASE = float(os.getenv("OLLAMA_RETRY_BACKOFF_BASE", "1.5"))  # Faster backoff
OLLAMA_REQUEST_TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "30"))  # 30 seconds for reliable AI responses
OLLAMA_STREAM_TIMEOUT = int(os.getenv("OLLAMA_STREAM_TIMEOUT", "60"))  # Timeout for streaming responses

try:
    k8s_tools = KubernetesTools()
    logger.info("✅ Kubernetes client initialized")
except RuntimeError as e:
    # RuntimeError means kubeconfig loading failed, but server can continue
    error_msg = str(e)
    if "Kubernetes" in error_msg or "kubeconfig" in error_msg.lower() or "configuration" in error_msg.lower():
        logger.warning(f"⚠️  Kubernetes client not available: {error_msg}")
        logger.info("ℹ️  MCP server will continue using intelligent fallback for remediation plans")
        k8s_tools = None
    else:
        # Unexpected RuntimeError - log and continue
        logger.error(f"❌ Unexpected error initializing Kubernetes client: {e}")
        k8s_tools = None
except Exception as e:
    logger.error(f"❌ Failed to initialize Kubernetes client: {type(e).__name__}: {e}")
    logger.info("ℹ️  MCP server will continue using intelligent fallback for remediation plans")
    k8s_tools = None

# Initialize advanced remediation components
cost_calculator = None
remediation_learner = None
remediation_planner = None
safety_checker = None

try:
    if CostCalculator:
        cost_calculator = CostCalculator()
        logger.info("✅ Cost calculator initialized")
    if RemediationLearningEngine:
        remediation_learner = RemediationLearningEngine()
        logger.info("✅ Remediation learner initialized")
    if RemediationPlanner:
        remediation_planner = RemediationPlanner(
            learner=remediation_learner,
            cost_calculator=cost_calculator
        )
        logger.info("✅ Remediation planner initialized")
    if RemediationSafetyChecker:
        safety_checker = RemediationSafetyChecker(k8s_tools=k8s_tools)
        logger.info("✅ Safety checker initialized")
except Exception as e:
    logger.warning(f"⚠️  Failed to initialize some advanced components: {e}")


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
    ai_source: Optional[str] = Field(default=None, description="AI source: Ollama, Gemini, or MultiStrategy")


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
        # Model not loaded - check if Gemini or Groq is available as fallback
        if GEMINI_MODEL:
            logger.warning(f"Ollama model {OLLAMA_MODEL} not available, but Gemini is configured as fallback")
            details["fallback"] = "Gemini available"
            # Service can still work with Gemini fallback
        elif GROQ_AVAILABLE:
            logger.warning(f"Ollama model {OLLAMA_MODEL} not available, but Groq is configured as fallback")
            details["fallback"] = "Groq available"
            # Service can still work with Groq fallback
        else:
            # No fallback available - this is critical
            healthy = False
            logger.error(f"CRITICAL: Ollama model {OLLAMA_MODEL} not available and no AI fallback (Gemini/Groq) - service cannot generate remediation plans")
    
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

        # Handle case where Kubernetes client is not available - use minimal context
        pod_info = {}
        events = []
        logs = ""
        deployment = None
        metrics = {}
        
        if k8s_tools:
            try:
                pod_info = k8s_tools.get_pod(analysis_request.namespace, analysis_request.pod_name)
                events = k8s_tools.get_events(analysis_request.namespace, analysis_request.pod_name, limit=10)
                logs = k8s_tools.get_pod_logs(analysis_request.namespace, analysis_request.pod_name, lines=50)
                deployment = k8s_tools.get_deployment_for_pod(analysis_request.namespace, analysis_request.pod_name)
                metrics = k8s_tools.get_pod_resource_usage(analysis_request.namespace, analysis_request.pod_name)
            except Exception as e:
                logger.warning(f"Failed to fetch Kubernetes context: {e}, using minimal context")
                # Use minimal pod_info with just the name
                pod_info = {"name": analysis_request.pod_name, "namespace": analysis_request.namespace}
        else:
            logger.warning("Kubernetes client not available, using minimal context for remediation")
            # Use minimal pod_info with just the name
            pod_info = {"name": analysis_request.pod_name, "namespace": analysis_request.namespace}
        
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
            # PRIORITY: Use AI (Ollama/Gemini/Groq) FIRST for intelligent remediation
            # Only fall back to MultiStrategy if AI fails
            ai_source = None
            ai_response = None
            
            # Try AI services in order: Ollama -> Gemini -> Groq
            logger.info("🤖 Attempting AI-powered remediation (Ollama/Gemini/Groq)")
            
            # Try Ollama first
            if OLLAMA_URL and OLLAMA_MODEL:
                try:
                    logger.info(f"🔄 Calling Ollama ({OLLAMA_MODEL}) for AI remediation plan")
                    ai_response = await call_ollama(prompt)
                    ai_source = "Ollama"
                    logger.info("✅ Ollama successfully generated remediation plan")
                except Exception as ollama_err:
                    logger.warning(f"⚠️  Ollama failed: {ollama_err}, trying Gemini")
                    # Try Gemini
                    if GEMINI_MODEL:
                        try:
                            logger.info("🔄 Calling Gemini for AI remediation plan")
                            ai_response = await call_gemini(prompt)
                            ai_source = "Gemini"
                            logger.info("✅ Gemini successfully generated remediation plan")
                        except Exception as gemini_err:
                            logger.warning(f"⚠️  Gemini failed: {gemini_err}, trying Groq")
                            # Try Groq
                            if GROQ_AVAILABLE:
                                try:
                                    logger.info("🔄 Calling Groq for AI remediation plan")
                                    ai_response = await call_groq(prompt)
                                    ai_source = "Groq"
                                    logger.info("✅ Groq successfully generated remediation plan")
                                except Exception as groq_err:
                                    logger.warning(f"⚠️  All AI services failed (Ollama: {ollama_err}, Gemini: {gemini_err}, Groq: {groq_err})")
                                    ai_response = None
                            else:
                                logger.warning("⚠️  Groq not available")
                    else:
                        logger.warning("⚠️  Gemini not available")
            else:
                logger.warning("⚠️  Ollama not configured")
            
            # If AI succeeded, parse and return AI plan
            if ai_response and ai_source:
                try:
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    
                    # Add AI source to reasoning for tracking
                    original_reasoning = plan.get("reasoning", "AI-generated remediation plan")
                    plan["reasoning"] = f"[{ai_source}] {original_reasoning}"
                    
                    logger.info(f"✅ AI remediation plan validated successfully (source: {ai_source})")
                    
                    # Convert to RemediationPlan format
                    actions = [
                        RemediationAction(
                            type=action.get("type", "pod"),
                            target=action.get("target", analysis_request.pod_name),
                            operation=action.get("operation", "restart"),
                            parameters=action.get("parameters", {}),
                            order=action.get("order", i)
                        )
                        for i, action in enumerate(plan.get("actions", []))
                    ]
                    
                    # Run safety checks on AI-generated actions
                    safety_warnings = []
                    if safety_checker:
                        for action in actions:
                            passed, warnings = safety_checker.pre_check(action.dict(), context_data)
                            if warnings:
                                safety_warnings.extend(warnings)
                            if not passed:
                                logger.warning(f"Safety check failed for AI action {action.operation}")
                    
                    reasoning = plan.get("reasoning", "AI-generated remediation plan")
                    if safety_warnings:
                        reasoning += f" Safety warnings: {', '.join(safety_warnings)}"
                    
                    return RemediationPlan(
                        actions=actions,
                        reasoning=reasoning,
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source=ai_source
                    )
                except (ValueError, KeyError) as parse_err:
                    logger.warning(f"⚠️  AI plan parsing/validation failed: {parse_err}, falling back to MultiStrategy")
                    # Fall through to MultiStrategy fallback
            
            # FALLBACK: Use multi-strategy planning if AI failed or not available
            use_multi_strategy = remediation_planner is not None and cost_calculator is not None
            
            if use_multi_strategy:
                logger.info("Using multi-strategy remediation planning (AI fallback)")
                
                # Generate multiple strategies
                multi_plan = remediation_planner.generate_strategies(
                    issue_type=analysis_request.issue_type,
                    severity=analysis_request.severity,
                    pod_context=pod_info,
                    deployment=deployment,
                    historical_data=context_data
                )
                
                # Select best strategy based on constraints
                constraints = {}
                if analysis_request.severity == "critical":
                    constraints["max_time"] = 300  # 5 minutes max for critical
                elif analysis_request.severity == "low":
                    constraints["max_cost"] = 50.0  # Lower cost for low severity
                
                best_strategy = multi_plan.select_best_strategy(constraints=constraints)
                
                # Convert strategy to plan format
                actions = [
                    {
                        "type": action.type,
                        "target": action.target,
                        "operation": action.operation,
                        "parameters": action.parameters,
                        "order": action.order
                    }
                    for action in best_strategy.actions
                ]
                
                # Run safety checks
                safety_warnings = []
                if safety_checker:
                    for action in actions:
                        passed, warnings = safety_checker.pre_check(action, context_data)
                        if warnings:
                            safety_warnings.extend(warnings)
                        if not passed:
                            logger.warning(f"Safety check failed for action {action.get('operation')}")
                
                reasoning = best_strategy.reasoning
                if safety_warnings:
                    reasoning += f" Safety warnings: {', '.join(safety_warnings)}"
                
                return RemediationPlan(
                    actions=actions,
                    reasoning=reasoning,
                    confidence=best_strategy.confidence,
                    risk_level=best_strategy.risk_level.value,
                    ai_source="MultiStrategy"
                )
            else:
                # Fallback to original AI-based planning
                # Try Ollama first (cost-effective), fallback to Gemini if Ollama fails
                ai_response = None
                ai_source = None
                
                # Attempt 1: Try Ollama
                try:
                    logger.info("🤖 Attempting AI remediation with Ollama (primary)")
                    ai_response = await call_ollama(prompt)
                    ai_source = "Ollama"
                    logger.info("✅ Ollama successfully generated remediation plan")
                except Exception as ollama_err:
                    logger.warning(f"⚠️  Ollama failed: {ollama_err}")
                    
                    # Attempt 2: Fallback to Gemini if Ollama fails
                    if GEMINI_MODEL:
                        logger.info("🔄 Falling back to Gemini API")
                        try:
                            ai_response = await call_gemini(prompt)
                            ai_source = "Gemini"
                            logger.info("✅ Gemini successfully generated remediation plan")
                        except Exception as gemini_err:
                            logger.warning(f"⚠️  Gemini also failed: {gemini_err}")
                            
                            # Attempt 3: Fallback to Groq if Gemini fails
                            if GROQ_AVAILABLE:
                                logger.info("🔄 Falling back to Groq API")
                                try:
                                    ai_response = await call_groq(prompt)
                                    ai_source = "Groq"
                                    logger.info("✅ Groq successfully generated remediation plan")
                                except Exception as groq_err:
                                    logger.warning(f"⚠️  Groq also failed: {groq_err}, using intelligent fallback")
                                    # Use intelligent fallback instead of failing
                                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                                    return RemediationPlan(
                                        actions=fallback_plan.get("actions", []),
                                        reasoning=f"[Intelligent-Fallback] {fallback_plan.get('reasoning', '')}",
                                        confidence=fallback_plan.get("confidence", 0.7),
                                        risk_level=fallback_plan.get("risk_level", "medium"),
                                        ai_source="Intelligent-Fallback"
                                    )
                            else:
                                logger.warning("⚠️  Groq not available, using intelligent fallback")
                                fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                                return RemediationPlan(
                                    actions=fallback_plan.get("actions", []),
                                    reasoning=f"[Intelligent-Fallback] {fallback_plan.get('reasoning', '')}",
                                    confidence=fallback_plan.get("confidence", 0.7),
                                    risk_level=fallback_plan.get("risk_level", "medium"),
                                    ai_source="Intelligent-Fallback"
                                )
                    elif GROQ_AVAILABLE:
                        # Ollama failed, try Groq directly
                        logger.info("🔄 Ollama failed, trying Groq API")
                        try:
                            ai_response = await call_groq(prompt)
                            ai_source = "Groq"
                            logger.info("✅ Groq successfully generated remediation plan")
                        except Exception as groq_err:
                            logger.warning(f"⚠️  Groq also failed: {groq_err}, using intelligent fallback")
                            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                            return RemediationPlan(
                                actions=fallback_plan.get("actions", []),
                                reasoning=f"[Intelligent-Fallback] {fallback_plan.get('reasoning', '')}",
                                confidence=fallback_plan.get("confidence", 0.7),
                                risk_level=fallback_plan.get("risk_level", "medium"),
                                ai_source="Intelligent-Fallback"
                            )
                    else:
                        logger.warning("⚠️  All AI services unavailable, using intelligent fallback")
                        # Use intelligent fallback instead of failing
                        fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                        return RemediationPlan(
                            actions=fallback_plan.get("actions", []),
                            reasoning=f"[Intelligent-Fallback] {fallback_plan.get('reasoning', '')}",
                            confidence=fallback_plan.get("confidence", 0.7),
                            risk_level=fallback_plan.get("risk_level", "medium"),
                            ai_source="Intelligent-Fallback"
                        )
                
                # Parse and validate the AI response
                if not ai_response:
                    raise Exception("No AI response received")
                
                try:
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    
                    # Add AI source to reasoning for tracking
                    original_reasoning = plan.get("reasoning", "AI-generated remediation plan")
                    if ai_source:
                        plan["reasoning"] = f"[{ai_source}] {original_reasoning}"
                    
                    logger.info(f"✅ AI remediation plan validated successfully (source: {ai_source})")
                    
                    return RemediationPlan(
                        actions=plan.get("actions", []),
                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source=ai_source
                    )
                except (ValueError, KeyError) as parse_err:
                    # If parsing/validation fails, try other AI services in order
                    if ai_source == "Ollama":
                        # Try Gemini first
                        if GEMINI_MODEL:
                            logger.warning(f"⚠️  Ollama plan validation failed: {parse_err}, retrying with Gemini")
                            try:
                                ai_response = await call_gemini(prompt)
                                plan = parse_remediation_plan(ai_response)
                                validate_plan(plan)
                                plan["reasoning"] = f"[Gemini-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                                logger.info("✅ Gemini retry successfully generated valid remediation plan")
                                return RemediationPlan(
                                    actions=plan.get("actions", []),
                                    reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                    confidence=plan.get("confidence", 0.75),
                                    risk_level=plan.get("risk_level", "medium"),
                                    ai_source="Gemini"
                                )
                            except Exception as gemini_retry_err:
                                logger.warning(f"⚠️  Gemini retry also failed: {gemini_retry_err}")
                                # Try Groq
                                if GROQ_AVAILABLE:
                                    logger.info("🔄 Trying Groq after Gemini validation failure")
                                    try:
                                        ai_response = await call_groq(prompt)
                                        plan = parse_remediation_plan(ai_response)
                                        validate_plan(plan)
                                        plan["reasoning"] = f"[Groq-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                                        logger.info("✅ Groq retry successfully generated valid remediation plan")
                                        return RemediationPlan(
                                            actions=plan.get("actions", []),
                                            reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                            confidence=plan.get("confidence", 0.75),
                                            risk_level=plan.get("risk_level", "medium"),
                                            ai_source="Groq"
                                        )
                                    except Exception as groq_retry_err:
                                        logger.error(f"❌ Groq retry also failed: {groq_retry_err}")
                                        logger.warning(f"All AI retries failed, using intelligent fallback")
                                        fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                                        return RemediationPlan(
                                            actions=fallback_plan.get("actions", []),
                                            reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                                            confidence=fallback_plan.get("confidence", 0.7),
                                            risk_level=fallback_plan.get("risk_level", "medium"),
                                            ai_source="Intelligent-Fallback"
                                        )
                                else:
                                    logger.warning(f"All AI retries failed, using intelligent fallback")
                                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                                    return RemediationPlan(
                                        actions=fallback_plan.get("actions", []),
                                        reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                                        confidence=fallback_plan.get("confidence", 0.7),
                                        risk_level=fallback_plan.get("risk_level", "medium"),
                                        ai_source="Intelligent-Fallback"
                                    )
                        elif GROQ_AVAILABLE:
                            # Try Groq directly if Gemini not available
                            logger.warning(f"⚠️  Ollama plan validation failed: {parse_err}, retrying with Groq")
                            try:
                                ai_response = await call_groq(prompt)
                                plan = parse_remediation_plan(ai_response)
                                validate_plan(plan)
                                plan["reasoning"] = f"[Groq-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                                logger.info("✅ Groq retry successfully generated valid remediation plan")
                                return RemediationPlan(
                                    actions=plan.get("actions", []),
                                    reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                    confidence=plan.get("confidence", 0.75),
                                    risk_level=plan.get("risk_level", "medium"),
                                    ai_source="Groq"
                                )
                            except Exception as groq_retry_err:
                                logger.error(f"❌ Groq retry also failed: {groq_retry_err}")
                                logger.warning(f"All AI retries failed, using intelligent fallback")
                                fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                                return RemediationPlan(
                                    actions=fallback_plan.get("actions", []),
                                    reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                                    confidence=fallback_plan.get("confidence", 0.7),
                                    risk_level=fallback_plan.get("risk_level", "medium"),
                                    ai_source="Intelligent-Fallback"
                                )
                        else:
                            logger.warning(f"AI plan validation failed: {parse_err}, using intelligent fallback")
                            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                            return RemediationPlan(
                                actions=fallback_plan.get("actions", []),
                                reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                                confidence=fallback_plan.get("confidence", 0.7),
                                risk_level=fallback_plan.get("risk_level", "medium"),
                                ai_source="Intelligent-Fallback"
                            )
                    elif ai_source == "Gemini" and GROQ_AVAILABLE:
                        # Gemini validation failed, try Groq
                        logger.warning(f"⚠️  Gemini plan validation failed: {parse_err}, retrying with Groq")
                        try:
                            ai_response = await call_groq(prompt)
                            plan = parse_remediation_plan(ai_response)
                            validate_plan(plan)
                            plan["reasoning"] = f"[Groq-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                            logger.info("✅ Groq retry successfully generated valid remediation plan")
                            return RemediationPlan(
                                actions=plan.get("actions", []),
                                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                confidence=plan.get("confidence", 0.75),
                                risk_level=plan.get("risk_level", "medium"),
                                ai_source="Groq"
                            )
                        except Exception as groq_retry_err:
                            logger.error(f"❌ Groq retry also failed: {groq_retry_err}")
                            logger.warning(f"All AI retries failed, using intelligent fallback")
                            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                            return RemediationPlan(
                                actions=fallback_plan.get("actions", []),
                                reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                                confidence=fallback_plan.get("confidence", 0.7),
                                risk_level=fallback_plan.get("risk_level", "medium"),
                                ai_source="Intelligent-Fallback"
                            )
                    else:
                        logger.warning(f"AI plan validation failed: {parse_err}, using intelligent fallback")
                        fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                        return RemediationPlan(
                            actions=fallback_plan.get("actions", []),
                            reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                            confidence=fallback_plan.get("confidence", 0.7),
                            risk_level=fallback_plan.get("risk_level", "medium"),
                            ai_source="Intelligent-Fallback"
                        )

        except httpx.ConnectError as e:
            logger.error(f"AI analysis failed: connection error - {type(e).__name__}: {e}", exc_info=True)
            # Retry with Gemini, then Groq, then intelligent fallback
            if GEMINI_MODEL:
                logger.warning("Ollama connection failed, retrying with Gemini")
                try:
                    ai_response = await call_gemini(prompt)
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    plan["reasoning"] = f"[Gemini-connection-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                    logger.info("✅ Gemini successfully generated remediation plan after Ollama connection failure")
                    return RemediationPlan(
                        actions=plan.get("actions", []),
                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source="Gemini"
                    )
                except Exception as gemini_err:
                    logger.warning(f"Gemini also failed after Ollama connection error: {gemini_err}")
                    # Try Groq
                    if GROQ_AVAILABLE:
                        logger.info("🔄 Trying Groq after Gemini connection failure")
                        try:
                            ai_response = await call_groq(prompt)
                            plan = parse_remediation_plan(ai_response)
                            validate_plan(plan)
                            plan["reasoning"] = f"[Groq-connection-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                            logger.info("✅ Groq successfully generated remediation plan after connection failures")
                            return RemediationPlan(
                                actions=plan.get("actions", []),
                                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                confidence=plan.get("confidence", 0.75),
                                risk_level=plan.get("risk_level", "medium"),
                                ai_source="Groq"
                            )
                        except Exception as groq_err:
                            logger.warning(f"Groq also failed: {groq_err}, using intelligent fallback")
                            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                            return RemediationPlan(
                                actions=fallback_plan.get("actions", []),
                                reasoning=f"[Intelligent-Fallback-Connection] {fallback_plan.get('reasoning', '')}",
                                confidence=fallback_plan.get("confidence", 0.7),
                                risk_level=fallback_plan.get("risk_level", "medium"),
                                ai_source="Intelligent-Fallback"
                            )
                    else:
                        logger.warning(f"All AI services failed, using intelligent fallback")
                        fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                        return RemediationPlan(
                            actions=fallback_plan.get("actions", []),
                            reasoning=f"[Intelligent-Fallback-Connection] {fallback_plan.get('reasoning', '')}",
                            confidence=fallback_plan.get("confidence", 0.7),
                            risk_level=fallback_plan.get("risk_level", "medium"),
                            ai_source="Intelligent-Fallback"
                        )
            elif GROQ_AVAILABLE:
                # Try Groq directly if Gemini not available
                logger.warning("Ollama connection failed, trying Groq")
                try:
                    ai_response = await call_groq(prompt)
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    plan["reasoning"] = f"[Groq-connection-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                    logger.info("✅ Groq successfully generated remediation plan after Ollama connection failure")
                    return RemediationPlan(
                        actions=plan.get("actions", []),
                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source="Groq"
                    )
                except Exception as groq_err:
                    logger.warning(f"Groq also failed: {groq_err}, using intelligent fallback")
                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                    return RemediationPlan(
                        actions=fallback_plan.get("actions", []),
                        reasoning=f"[Intelligent-Fallback-Connection] {fallback_plan.get('reasoning', '')}",
                        confidence=fallback_plan.get("confidence", 0.7),
                        risk_level=fallback_plan.get("risk_level", "medium"),
                        ai_source="Intelligent-Fallback"
                    )
            else:
                logger.warning("Ollama connection failed, using intelligent fallback")
                try:
                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                    return RemediationPlan(
                        actions=fallback_plan.get("actions", []),
                        reasoning=f"[Intelligent-Fallback-Connection] {fallback_plan.get('reasoning', '')}",
                        confidence=fallback_plan.get("confidence", 0.7),
                        risk_level=fallback_plan.get("risk_level", "medium"),
                        ai_source="Intelligent-Fallback"
                    )
                except Exception as fallback_err:
                    logger.error(f"Fallback failed: {fallback_err}")
                    return RemediationPlan(
                        actions=[{
                            "type": "pod",
                            "target": analysis_request.pod_name,
                            "operation": "restart",
                            "parameters": {"grace_period_seconds": 30},
                            "order": 0
                        }],
                        reasoning="Emergency fallback: Restarting pod",
                        confidence=0.5,
                        risk_level="low",
                        ai_source="Emergency-Fallback"
                    )
        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            logger.error(f"AI analysis failed: timeout error - {type(e).__name__}: {e}", exc_info=True)
            # Retry with Gemini, then Groq, then intelligent fallback
            if GEMINI_MODEL:
                logger.warning("Ollama timeout, retrying with Gemini")
                try:
                    ai_response = await call_gemini(prompt)
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    plan["reasoning"] = f"[Gemini-timeout-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                    logger.info("✅ Gemini successfully generated remediation plan after Ollama timeout")
                    return RemediationPlan(
                        actions=plan.get("actions", []),
                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source="Gemini"
                    )
                except Exception as gemini_err:
                    logger.warning(f"Gemini also failed after Ollama timeout: {gemini_err}")
                    # Try Groq
                    if GROQ_AVAILABLE:
                        logger.info("🔄 Trying Groq after Gemini timeout")
                        try:
                            ai_response = await call_groq(prompt)
                            plan = parse_remediation_plan(ai_response)
                            validate_plan(plan)
                            plan["reasoning"] = f"[Groq-timeout-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                            logger.info("✅ Groq successfully generated remediation plan after timeouts")
                            return RemediationPlan(
                                actions=plan.get("actions", []),
                                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                confidence=plan.get("confidence", 0.75),
                                risk_level=plan.get("risk_level", "medium"),
                                ai_source="Groq"
                            )
                        except Exception as groq_err:
                            logger.warning(f"Groq also failed: {groq_err}, using intelligent fallback")
                            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                            return RemediationPlan(
                                actions=fallback_plan.get("actions", []),
                                reasoning=f"[Intelligent-Fallback-Timeout] {fallback_plan.get('reasoning', '')}",
                                confidence=fallback_plan.get("confidence", 0.7),
                                risk_level=fallback_plan.get("risk_level", "medium"),
                                ai_source="Intelligent-Fallback"
                            )
                    else:
                        logger.warning(f"All AI services failed, using intelligent fallback")
                        fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                        return RemediationPlan(
                            actions=fallback_plan.get("actions", []),
                            reasoning=f"[Intelligent-Fallback-Timeout] {fallback_plan.get('reasoning', '')}",
                            confidence=fallback_plan.get("confidence", 0.7),
                            risk_level=fallback_plan.get("risk_level", "medium"),
                            ai_source="Intelligent-Fallback"
                        )
            elif GROQ_AVAILABLE:
                # Try Groq directly if Gemini not available
                logger.warning("Ollama timeout, trying Groq")
                try:
                    ai_response = await call_groq(prompt)
                    plan = parse_remediation_plan(ai_response)
                    validate_plan(plan)
                    plan["reasoning"] = f"[Groq-timeout-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                    logger.info("✅ Groq successfully generated remediation plan after Ollama timeout")
                    return RemediationPlan(
                        actions=plan.get("actions", []),
                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                        confidence=plan.get("confidence", 0.75),
                        risk_level=plan.get("risk_level", "medium"),
                        ai_source="Groq"
                    )
                except Exception as groq_err:
                    logger.warning(f"Groq also failed: {groq_err}, using intelligent fallback")
                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                    return RemediationPlan(
                        actions=fallback_plan.get("actions", []),
                        reasoning=f"[Intelligent-Fallback-Timeout] {fallback_plan.get('reasoning', '')}",
                        confidence=fallback_plan.get("confidence", 0.7),
                        risk_level=fallback_plan.get("risk_level", "medium"),
                        ai_source="Intelligent-Fallback"
                    )
            else:
                # No AI services, use intelligent fallback
                logger.warning(f"Ollama timeout, using intelligent fallback")
                fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                return RemediationPlan(
                    actions=fallback_plan.get("actions", []),
                    reasoning=f"[Intelligent-Fallback-Timeout] {fallback_plan.get('reasoning', '')}",
                    confidence=fallback_plan.get("confidence", 0.7),
                    risk_level=fallback_plan.get("risk_level", "medium"),
                    ai_source="Intelligent-Fallback"
                )
        except httpx.HTTPStatusError as e:
            logger.warning(f"AI analysis failed: HTTP error {e.response.status_code} - {type(e).__name__}: {e}, using intelligent fallback")
            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
            return RemediationPlan(
                actions=fallback_plan.get("actions", []),
                reasoning=f"[Intelligent-Fallback-HTTP] {fallback_plan.get('reasoning', '')}",
                confidence=fallback_plan.get("confidence", 0.7),
                risk_level=fallback_plan.get("risk_level", "medium"),
                ai_source="Intelligent-Fallback"
            )
        except ValueError as e:
            # JSON parsing or validation errors - try intelligent fallback if AI completely failed
            error_msg = str(e)
            if "AI plan validation failed" in error_msg and ("Ollama" in error_msg or "Gemini" in error_msg):
                # Both AI attempts failed validation - try one more time with simplified prompt
                logger.error(f"AI analysis failed: validation/parsing error - {type(e).__name__}: {e}", exc_info=True)
                logger.warning("Both Ollama and Gemini failed validation, retrying with simplified prompt")
                try:
                    # Create a simplified prompt for retry
                    simplified_prompt = f"""You are a Kubernetes expert. Generate a remediation plan in JSON format.

ISSUE: {analysis_request.issue_type} in pod {analysis_request.namespace}/{analysis_request.pod_name}
SEVERITY: {analysis_request.severity}
DESCRIPTION: {analysis_request.description}

POD INFO: {json.dumps(pod_info, default=str)[:500]}
DEPLOYMENT: {json.dumps(deployment, default=str)[:200] if deployment else 'None'}

Generate a JSON remediation plan with this exact format:
{{
  "actions": [
    {{
      "type": "pod|deployment",
      "target": "{analysis_request.pod_name if not deployment else (deployment.get('name', analysis_request.pod_name) if deployment else analysis_request.pod_name)}",
      "operation": "restart|increase_memory|increase_cpu|restart_rollout",
      "parameters": {{}},
      "order": 0
    }}
  ],
  "reasoning": "Brief explanation",
  "confidence": 0.85,
  "risk_level": "low|medium|high"
}}

CRITICAL: Use actual pod/deployment name for target. Return ONLY valid JSON."""
                    
                    # Try Gemini with simplified prompt
                    if GEMINI_MODEL:
                        try:
                            ai_response = await call_gemini(simplified_prompt)
                            plan = parse_remediation_plan(ai_response)
                            validate_plan(plan)
                            plan["reasoning"] = f"[Gemini-simplified-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                            logger.info("✅ Gemini successfully generated remediation plan with simplified prompt")
                            return RemediationPlan(
                                actions=plan.get("actions", []),
                                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                confidence=plan.get("confidence", 0.75),
                                risk_level=plan.get("risk_level", "medium"),
                                ai_source="Gemini"
                            )
                        except Exception as gemini_simplified_err:
                            logger.warning(f"Gemini simplified prompt also failed: {gemini_simplified_err}")
                            # Try Groq with simplified prompt
                            if GROQ_AVAILABLE:
                                try:
                                    ai_response = await call_groq(simplified_prompt)
                                    plan = parse_remediation_plan(ai_response)
                                    validate_plan(plan)
                                    plan["reasoning"] = f"[Groq-simplified-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                                    logger.info("✅ Groq successfully generated remediation plan with simplified prompt")
                                    return RemediationPlan(
                                        actions=plan.get("actions", []),
                                        reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                        confidence=plan.get("confidence", 0.75),
                                        risk_level=plan.get("risk_level", "medium"),
                                        ai_source="Groq"
                                    )
                                except Exception as groq_simplified_err:
                                    logger.warning(f"Groq simplified prompt also failed: {groq_simplified_err}")
                                    raise retry_err  # Will trigger intelligent fallback
                            else:
                                raise retry_err  # Will trigger intelligent fallback
                    elif GROQ_AVAILABLE:
                        # Try Groq with simplified prompt
                        try:
                            ai_response = await call_groq(simplified_prompt)
                            plan = parse_remediation_plan(ai_response)
                            validate_plan(plan)
                            plan["reasoning"] = f"[Groq-simplified-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                            logger.info("✅ Groq successfully generated remediation plan with simplified prompt")
                            return RemediationPlan(
                                actions=plan.get("actions", []),
                                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                                confidence=plan.get("confidence", 0.75),
                                risk_level=plan.get("risk_level", "medium"),
                                ai_source="Groq"
                            )
                        except Exception as groq_simplified_err:
                            logger.warning(f"Groq simplified prompt also failed: {groq_simplified_err}")
                            raise retry_err  # Will trigger intelligent fallback
                    else:
                        # Last resort: try Ollama with simplified prompt
                        ai_response = await call_ollama(simplified_prompt, retries=1)
                        plan = parse_remediation_plan(ai_response)
                        validate_plan(plan)
                        plan["reasoning"] = f"[Ollama-simplified-retry] {plan.get('reasoning', 'AI-generated remediation plan')}"
                        logger.info("✅ Ollama successfully generated remediation plan with simplified prompt")
                        return RemediationPlan(
                            actions=plan.get("actions", []),
                            reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                            confidence=plan.get("confidence", 0.75),
                            risk_level=plan.get("risk_level", "medium"),
                            ai_source="Ollama"
                        )
                except Exception as retry_err:
                    logger.error(f"All AI attempts failed including simplified prompt retry: {retry_err}")
                    logger.warning(f"All AI attempts failed including simplified prompt retry: {retry_err}, using intelligent fallback")
                    fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                    return RemediationPlan(
                        actions=fallback_plan.get("actions", []),
                        reasoning=f"[Intelligent-Fallback-AllFailed] {fallback_plan.get('reasoning', '')}",
                        confidence=fallback_plan.get("confidence", 0.7),
                        risk_level=fallback_plan.get("risk_level", "medium"),
                        ai_source="Intelligent-Fallback"
                    )
            else:
                logger.warning(f"AI analysis failed: validation/parsing error - {type(e).__name__}: {e}, using intelligent fallback")
                fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                return RemediationPlan(
                    actions=fallback_plan.get("actions", []),
                    reasoning=f"[Intelligent-Fallback-Validation] {fallback_plan.get('reasoning', '')}",
                    confidence=fallback_plan.get("confidence", 0.7),
                    risk_level=fallback_plan.get("risk_level", "medium"),
                    ai_source="Intelligent-Fallback"
                )
        except json.JSONDecodeError as e:
            logger.warning(f"AI analysis failed: JSON decode error - {type(e).__name__}: {e}, using intelligent fallback")
            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
            return RemediationPlan(
                actions=fallback_plan.get("actions", []),
                reasoning=f"[Intelligent-Fallback-JSON] {fallback_plan.get('reasoning', '')}",
                confidence=fallback_plan.get("confidence", 0.7),
                risk_level=fallback_plan.get("risk_level", "medium"),
                ai_source="Intelligent-Fallback"
            )
        except KeyError as e:
            logger.warning(f"AI analysis failed: missing required field - {type(e).__name__}: {e}, using intelligent fallback")
            fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
            return RemediationPlan(
                actions=fallback_plan.get("actions", []),
                reasoning=f"[Intelligent-Fallback-KeyError] {fallback_plan.get('reasoning', '')}",
                confidence=fallback_plan.get("confidence", 0.7),
                risk_level=fallback_plan.get("risk_level", "medium"),
                ai_source="Intelligent-Fallback"
            )
        except Exception as e:
            # Catch-all for unknown exceptions - use intelligent fallback
            logger.warning(f"AI analysis failed: unexpected error - {type(e).__name__}: {e}, using intelligent fallback")
            try:
                fallback_plan = get_intelligent_fallback(analysis_request.issue_type, pod_info, deployment)
                return RemediationPlan(
                    actions=fallback_plan.get("actions", []),
                    reasoning=f"[Intelligent-Fallback-Exception] {fallback_plan.get('reasoning', '')}",
                    confidence=fallback_plan.get("confidence", 0.7),
                    risk_level=fallback_plan.get("risk_level", "medium"),
                    ai_source="Intelligent-Fallback"
                )
            except Exception as fallback_err:
                # Last resort - return minimal plan
                logger.error(f"Even fallback failed: {fallback_err}")
                return RemediationPlan(
                    actions=[{
                        "type": "pod",
                        "target": analysis_request.pod_name,
                        "operation": "restart",
                        "parameters": {"grace_period_seconds": 30},
                        "order": 0
                    }],
                    reasoning="Emergency fallback: Restarting pod as last resort",
                    confidence=0.5,
                    risk_level="low",
                    ai_source="Emergency-Fallback"
                )

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
        # Last attempt: try AI with minimal prompt
        try:
            minimal_prompt = f"""Generate JSON remediation plan for {analysis_request.issue_type} in pod {analysis_request.pod_name}.

{{
  "actions": [{{"type": "pod", "target": "{analysis_request.pod_name}", "operation": "restart", "parameters": {{}}, "order": 0}}],
  "reasoning": "Restart pod to resolve {analysis_request.issue_type}",
  "confidence": 0.7,
  "risk_level": "low"
}}"""
            ai_source_final = None
            if GEMINI_MODEL:
                try:
                    ai_response = await call_gemini(minimal_prompt)
                    ai_source_final = "Gemini"
                except Exception as gemini_final_err:
                    logger.warning(f"Gemini final attempt failed: {gemini_final_err}")
                    if GROQ_AVAILABLE:
                        try:
                            ai_response = await call_groq(minimal_prompt)
                            ai_source_final = "Groq"
                        except Exception as groq_final_err:
                            logger.warning(f"Groq final attempt failed: {groq_final_err}")
                            ai_response = await call_ollama(minimal_prompt, retries=1)
                            ai_source_final = "Ollama"
                    else:
                        ai_response = await call_ollama(minimal_prompt, retries=1)
                        ai_source_final = "Ollama"
            elif GROQ_AVAILABLE:
                try:
                    ai_response = await call_groq(minimal_prompt)
                    ai_source_final = "Groq"
                except Exception as groq_final_err:
                    logger.warning(f"Groq final attempt failed: {groq_final_err}")
                    ai_response = await call_ollama(minimal_prompt, retries=1)
                    ai_source_final = "Ollama"
            else:
                ai_response = await call_ollama(minimal_prompt, retries=1)
                ai_source_final = "Ollama"
            
            plan = parse_remediation_plan(ai_response)
            validate_plan(plan)
            return RemediationPlan(
                actions=plan.get("actions", []),
                reasoning=plan.get("reasoning", "AI-generated remediation plan"),
                confidence=plan.get("confidence", 0.7),
                risk_level=plan.get("risk_level", "low"),
                ai_source=ai_source_final or "Unknown"
            )
        except Exception as final_err:
            logger.error(f"Final AI attempt failed: {final_err}")
            raise HTTPException(status_code=500, detail=f"All remediation attempts failed: {str(e)}. Final error: {str(final_err)}") from final_err

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
    """Call Gemini API for complex issue analysis (fallback when Ollama fails)"""
    if not GEMINI_MODEL:
        raise Exception("Gemini model not available")
    try:
        # Use generate_content with proper configuration
        generation_config = {
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 1000,
        }
        response = GEMINI_MODEL.generate_content(
            prompt,
            generation_config=generation_config
        )
        if response and response.text:
            return response.text
        else:
            raise Exception("Gemini returned empty response")
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        raise Exception(f"Gemini API call failed: {e}")


async def call_groq(prompt: str, retries: Optional[int] = None) -> str:
    """Call Groq API for fast AI responses (fallback when Ollama and Gemini fail)"""
    if not GROQ_AVAILABLE:
        raise Exception("Groq API not available")
    
    if retries is None:
        retries = 2  # Groq is fast, fewer retries needed
    
    GROQ_REQUEST_TIMEOUT = 30  # 30 seconds timeout for Groq
    
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=GROQ_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    GROQ_API_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a Kubernetes SRE expert. Generate JSON remediation plans. Return ONLY valid JSON, no markdown."
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "temperature": 0.0,
                        "max_tokens": 500,
                        "top_p": 0.9,
                        "stream": False
                    },
                    timeout=GROQ_REQUEST_TIMEOUT,
                )
                
                if response.status_code != 200:
                    logger.error(f"Groq API error {response.status_code}: {response.text}")
                    if attempt < retries - 1:
                        backoff = 1.0 * (attempt + 1)
                        logger.info(f"Retrying Groq call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                        await asyncio.sleep(backoff)
                        continue
                    raise Exception(f"Groq API error: {response.status_code}")
                
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0].get("message", {}).get("content", "")
                    if content:
                        logger.debug(f"Groq response received (first 500 chars): {content[:500]}")
                        return content
                    else:
                        raise Exception("Groq returned empty content")
                else:
                    raise Exception(f"Groq response missing choices: {result}")
                    
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to Groq API (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                backoff = 1.0 * (attempt + 1)
                logger.info(f"Retrying Groq connection after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Cannot connect to Groq API") from e
        except httpx.TimeoutException as e:
            logger.error(f"Groq request timeout (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                backoff = 1.0 * (attempt + 1)
                logger.info(f"Retrying Groq call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Groq request timeout after {GROQ_REQUEST_TIMEOUT}s") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Groq HTTP error {e.response.status_code} (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1 and e.response.status_code >= 500:
                backoff = 1.0 * (attempt + 1)
                logger.info(f"Retrying Groq call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise Exception(f"Groq HTTP error {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Groq call error (attempt {attempt + 1}/{retries}): {type(e).__name__}: {e}")
            if attempt < retries - 1 and isinstance(e, (httpx.NetworkError, asyncio.TimeoutError)):
                backoff = 1.0 * (attempt + 1)
                logger.info(f"Retrying Groq call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(backoff)
                continue
            raise
    
    raise Exception("Groq API call failed after all retries")


def build_comprehensive_prompt(request, pod_info, events, logs, deployment, metrics, context, detected_errors):
    """Build optimized prompt - shorter for faster AI response while maintaining quality"""
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

    events_summary = " | ".join([f"{e['type']}:{e['reason']}" for e in events[:3]]) if events else "None"
    
    # Build early warnings section (OPTIMIZED - brief)
    early_warning_section = ""
    if context.get("early_warnings"):
        early_warns = context["early_warnings"]
        if isinstance(early_warns, list) and len(early_warns) > 0:
            early_warning_section = "\nEARLY WARNINGS: "
            for warn in early_warns[:2]:  # Show only 2 most recent
                early_warning_section += f"{warn.get('warning_type', 'N/A')} (risk:{warn.get('risk_score', 0):.0f}, tta:{warn.get('time_to_anomaly_seconds', 0)/60:.0f}min); "
    
    # Build ML predictions section (OPTIMIZED - brief, essential only)
    ml_predictions_section = ""
    if context.get("ml_predictions"):
        ml_pred = context["ml_predictions"]
        if ml_pred.get("latest"):
            latest = ml_pred["latest"]
            ml_predictions_section = f"\nML PRED: {latest.get('anomaly_type', 'N/A')} (conf:{latest.get('confidence', 0):.0%})"
            ml_predictions_section += f" | OOM:{latest.get('oom_score', 0):.2f} Crash:{latest.get('crash_loop_score', 0):.2f} CPU:{latest.get('high_cpu_score', 0):.2f}"
    
    # Build historical metrics section (OPTIMIZED - brief summary only)
    historical_metrics_section = ""
    if context.get("historical_metrics"):
        hist = context["historical_metrics"]
        if hist.get("current"):
            curr = hist["current"]
            historical_metrics_section = f"\nHIST: CPU={curr.get('cpu_utilization', 0):.0f}% Mem={curr.get('memory_utilization', 0):.0f}% Restarts={curr.get('restarts', 0)}"
            if curr.get('cpu_trend', 0) != 0 or curr.get('memory_trend', 0) != 0:
                historical_metrics_section += f" Trends:CPU={curr.get('cpu_trend', 0):.2f} Mem={curr.get('memory_trend', 0):.2f}"
    
    # ULTRA-OPTIMIZED MINIMAL PROMPT for fastest AI response (<2000 chars)
    # Only essential info, compact format
    target_name = deployment.get('name') if deployment else request.pod_name
    target_type = "deployment" if deployment else "pod"
    
    prompt = f"""K8s SRE: Generate JSON remediation for {request.issue_type} in {request.namespace}/{request.pod_name}.

ISSUE: {request.issue_type} | Severity: {request.severity}
POD: {request.pod_name} | Status: {pod_info.get('status', 'Unknown')} | Ready: {pod_info.get('ready', False)} | Restarts: {pod_info.get('restart_count', 0)}
DEPLOYMENT: {deployment.get('name') if deployment else 'None'} | Replicas: {deployment.get('replicas') if deployment else 'N/A'}
METRICS: CPU={metrics.get('cpu_millicores', 'N/A') if metrics else 'N/A'}m, Memory={metrics.get('memory_mib', 'N/A') if metrics else 'N/A'}MiB
{early_warning_section[:150] if early_warning_section else ''}
{ml_predictions_section[:150] if ml_predictions_section else ''}
EVENTS: {events_summary[:100] if events_summary else 'None'}
ERRORS: {', '.join([e['type'] for e in detected_errors[:2]]) if detected_errors else 'None'}

ACTIONS:

POD: restart, delete, evict, recreate
DEPLOYMENT: increase_memory(factor:1.5-2.0), increase_cpu(factor:1.5-2.0), restart_rollout, scale_up(replicas:+1-2), rollback_deployment
STATEFULSET: increase_memory, increase_cpu, rollback_statefulset
NODE: drain, uncordon

RULES:
- Target: Use "{target_name}" (actual name, not placeholder)
- Type: Use "{target_type}" (pod or deployment)
- Issue mapping: high_cpu→increase_cpu, high_memory/OOM→increase_memory, crash_loop→restart/restart_rollout
- If deployment: prefer restart_rollout over pod restart
- Confidence: 0.9 if clear, 0.8 if probable, 0.7 if uncertain
- Risk: low for restart, medium for resource changes, high for delete/rollback

JSON (return ONLY this, no markdown):
{{
  "actions": [{{"type": "{target_type}", "target": "{target_name}", "operation": "restart|increase_memory|increase_cpu|restart_rollout", "parameters": {{}}, "order": 0}}],
  "reasoning": "Brief 30-50 word explanation",
  "confidence": 0.85,
  "risk_level": "low|medium|high"
}}"""

    return prompt


async def call_ollama(prompt: str, retries: Optional[int] = None) -> str:
    """Call Ollama with configurable retries and exponential backoff - optimized for faster responses"""
    if retries is None:
        retries = OLLAMA_MAX_RETRIES
    
    # Optimize prompt length for faster processing (truncate if too long)
    max_prompt_length = 8000  # Limit prompt to ~8000 chars for faster processing
    if len(prompt) > max_prompt_length:
        logger.warning(f"Prompt too long ({len(prompt)} chars), truncating to {max_prompt_length} for faster processing")
        # Keep the beginning (issue info) and end (instructions)
        prompt_start = prompt[:3000]
        prompt_end = prompt[-2000:]
        prompt = prompt_start + "\n\n[... truncated for performance ...]\n\n" + prompt_end
    
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_REQUEST_TIMEOUT) as client:
                # Use correct Ollama API format with "options" object
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 300,  # Further reduced for faster response
                            "temperature": 0.0,
                            "top_p": 0.9,
                            "top_k": 10,  # Reduced for faster generation
                        }
                    },
                    timeout=OLLAMA_REQUEST_TIMEOUT,
                )

                if response.status_code != 200:
                    logger.error(f"Ollama error {response.status_code}: {response.text}")
                    if attempt < retries - 1:
                        backoff = OLLAMA_RETRY_BACKOFF_BASE ** attempt
                        logger.info(f"Retrying Ollama call after {backoff:.1f}s (attempt {attempt + 1}/{retries})")
                        await asyncio.sleep(backoff)
                        continue
                    raise Exception(f"Ollama API error: {response.status_code}")

                # Handle streaming response
                if response.headers.get("content-type") == "application/x-ndjson" or response.is_stream_consumed:
                    # Streaming response
                    raw_response = ""
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                if "response" in chunk:
                                    raw_response += chunk["response"]
                                if chunk.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                else:
                    # Non-streaming response
                    result = response.json()
                    raw_response = result.get("response", "")
                
                # Log the raw response for debugging (first 500 chars)
                if raw_response:
                    logger.debug(f"Ollama raw response (first 500 chars): {raw_response[:500]}")
                else:
                    logger.warning("Ollama returned empty response")
                return raw_response

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
    """Parse remediation plan from Ollama response with comprehensive JSON validation and error recovery"""
    try:
        # First, try to clean the response - remove markdown code blocks
        cleaned = response_text.strip()
        
        # Remove markdown code blocks if present
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()
        
        # Try direct JSON parse first
        try:
            plan = json.loads(cleaned)
            if validate_plan_structure(plan):
                return plan
        except json.JSONDecodeError:
            pass
        
        # Improved JSON extraction - handle nested JSON and code blocks
        # Find all JSON-like structures
        import re
        
        # Try to find JSON object boundaries more reliably
        # Look for balanced braces
        brace_count = 0
        start_idx = -1
        
        for i, char in enumerate(cleaned):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    # Found complete JSON object
                    json_str = cleaned[start_idx:i+1]
                    try:
                        plan = json.loads(json_str)
                        # Comprehensive validation before returning
                        if validate_plan_structure(plan):
                            return plan
                    except json.JSONDecodeError as e:
                        # Try to fix common JSON errors
                        # Fix trailing commas
                        json_str_fixed = re.sub(r',\s*}', '}', json_str)
                        json_str_fixed = re.sub(r',\s*]', ']', json_str_fixed)
                        try:
                            plan = json.loads(json_str_fixed)
                            if validate_plan_structure(plan):
                                logger.info("Fixed JSON by removing trailing commas")
                                return plan
                        except:
                            continue
        
        # Fallback: try original method
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}") + 1
        
        if start_idx == -1 or end_idx == 0:
            logger.warning(f"No JSON in response: {cleaned[:200]}")
            raise ValueError("No JSON found")
        
        json_str = cleaned[start_idx:end_idx]
        # Try to fix common JSON errors
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        try:
            plan = json.loads(json_str)
            
            # Validate structure comprehensively
            if not validate_plan_structure(plan):
                raise ValueError("Plan structure validation failed")
            
            return plan
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON even after fixing: {e}") from e

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
        
        # Ensure order field exists (default to index if missing)
        if "order" not in action:
            action["order"] = i
        elif not isinstance(action["order"], int):
            action["order"] = i
    
    return True


def validate_plan(plan: dict):
    if not plan.get("actions"):
        raise ValueError("No actions in plan")
    
    valid_types = {"pod", "deployment", "statefulset", "node"}
    valid_pod_ops = {"restart", "delete", "recreate"}  # recreate = delete + recreate
    valid_deploy_ops = {"increase_memory", "increase_cpu", "scale", "update_image", "restart_rollout"}
    valid_ss_ops = {"increase_memory", "increase_cpu"}
    valid_node_ops = {"drain", "uncordon"}
    
    # Normalize operations (handle hyphens vs underscores)
    operation_normalizations = {
        "increase-memory": "increase_memory",
        "increase-cpu": "increase_cpu",
        "decrease-memory": "decrease_memory",
        "decrease-cpu": "decrease_cpu",
        "restart-rollout": "restart_rollout",
        "update-image": "update_image",
        "rollback-deployment": "rollback_deployment",
    }
    
    for i, action in enumerate(plan["actions"]):
        # Normalize operation name
        op = action.get("operation", "")
        if op in operation_normalizations:
            action["operation"] = operation_normalizations[op]
            op = action["operation"]
        
        if action.get("type") not in valid_types:
            raise ValueError(f"Action {i}: Invalid type: {action.get('type')}")
        
        if action["type"] == "pod" and op not in valid_pod_ops:
            raise ValueError(f"Action {i}: Invalid pod operation: {op}")
        elif action["type"] == "deployment" and op not in valid_deploy_ops:
            raise ValueError(f"Action {i}: Invalid deployment operation: {op}")
        elif action["type"] == "statefulset" and op not in valid_ss_ops:
            raise ValueError(f"Action {i}: Invalid statefulset operation: {op}")
        elif action["type"] == "node" and op not in valid_node_ops:
            raise ValueError(f"Action {i}: Invalid node operation: {op}")
        
        # Ensure target is not empty
        target = action.get("target", "").strip()
        if not target:
            raise ValueError(f"Action {i}: target must be a non-empty string")
        
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
    """Generate intelligent fallback plan with actual resource names (never placeholders)"""
    issue_lower = issue_type.lower()
    
    # Always use actual pod name - never use placeholder
    pod_name = pod_info.get("name", "")
    if not pod_name:
        # If pod name not available, this is a critical error
        logger.error("Cannot generate fallback: pod name not available")
        raise ValueError("Pod name required for fallback plan generation")
    
    if "oom" in issue_lower or "memory" in issue_lower:
        # Use actual resource names instead of placeholders
        if deployment and deployment.get("name"):
            # Pod has deployment - use deployment operation
            return {
                "actions": [
                    {
                        "type": "deployment",
                        "target": deployment["name"],  # Use actual deployment name
                        "operation": "increase_memory",
                        "parameters": {"factor": 1.8},
                        "order": 0
                    }
                ],
                "reasoning": f"OOM detected in {issue_type}. Increasing memory by 80% to prevent recurrence.",
                "confidence": 0.85,
                "risk_level": "medium"
            }
        else:
            # Standalone pod - use pod restart (will be rescheduled with more resources if available)
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
                "reasoning": f"OOM detected in standalone pod {pod_name}. Restarting pod to clear memory and allow rescheduling.",
                "confidence": 0.80,
                "risk_level": "medium"
            }
    
    elif "crash" in issue_lower or "backoff" in issue_lower:
        # Use actual pod name
        if deployment and deployment.get("name"):
            return {
                "actions": [
                    {
                        "type": "deployment",
                        "target": deployment["name"],  # Use actual deployment name
                        "operation": "restart_rollout",
                        "parameters": {},
                        "order": 0
                    }
                ],
                "reasoning": f"Crash loop detected in deployment {deployment['name']}. Restarting rollout to recover.",
                "confidence": 0.85,
                "risk_level": "low"
            }
        else:
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
            return {"description": f"Kubernetes client not available. Pod: {namespace}/{pod_name}"}
        description = k8s_tools.describe_pod(namespace, pod_name)
        return {"description": description}
    except Exception as e:
        logger.error(f"Failed to get pod description: {e}")
        return {"description": f"Error: {str(e)}"}


@app.get("/namespace/{namespace}/overview")
async def get_namespace_overview(namespace: str):
    try:
        if not k8s_tools:
            return {"error": "Kubernetes client not available", "namespace": namespace}
        resources = k8s_tools.get_namespace_resources(namespace)
        return resources
    except Exception as e:
        logger.error(f"Failed to get namespace overview: {e}")
        return {"error": str(e), "namespace": namespace}


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
