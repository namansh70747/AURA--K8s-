from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from anthropic import Anthropic
from tools import KubernetesTools
from prompts import KUBERNETES_EXPERT

app = FastAPI(title="AURA MCP Server")

# Initialize Anthropic client
anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
k8s_tools = KubernetesTools()

class IssueRequest(BaseModel):
    issue_id: str
    pod_name: str
    namespace: str
    issue_type: str
    severity: str
    description: str

class RecommendationResponse(BaseModel):
    action: str
    action_details: str
    reasoning: str
    confidence: float

@app.post("/analyze", response_model=RecommendationResponse)
async def analyze_issue(request: IssueRequest):
    """Analyze issue and get AI recommendation"""
    try:
        # Gather context
        context = await _gather_context(request.pod_name, request.namespace)
        
        # Build prompt
        prompt = _build_analysis_prompt(request, context)
        
        # Call Claude
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=KUBERNETES_EXPERT,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Parse response
        recommendation = _parse_recommendation(message.content[0].text)
        
        return recommendation
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def _gather_context(pod_name: str, namespace: str):
    """Gather comprehensive context about the issue"""
    context = {}
    
    # Get pod details
    context["pod"] = k8s_tools.get_pod(namespace, pod_name)
    
    # Get pod logs
    context["logs"] = k8s_tools.get_pod_logs(namespace, pod_name, lines=100)
    
    # Get events
    context["events"] = k8s_tools.get_events(namespace, pod_name)
    
    # Get deployment
    context["deployment"] = k8s_tools.get_deployment_for_pod(namespace, pod_name)
    
    return context

def _build_analysis_prompt(request: IssueRequest, context: dict) -> str:
    """Build comprehensive analysis prompt"""
    prompt = f"""
Analyze this Kubernetes issue and provide a remediation recommendation:

**Issue Details:**
- Type: {request.issue_type}
- Severity: {request.severity}
- Pod: {request.pod_name}
- Namespace: {request.namespace}
- Description: {request.description}

**Pod Status:**
{context.get('pod', 'Not available')}

**Recent Logs:**
{context.get('logs', 'Not available')}

**Recent Events:**
{context.get('events', 'Not available')}

**Deployment Info:**
{context.get('deployment', 'Not available')}

Please provide:
1. Root cause analysis
2. Recommended action (choose from: restart_pod, increase_memory, increase_cpu, scale_deployment, clean_logs)
3. Detailed action steps
4. Confidence level (0-1)
5. Reasoning for your recommendation

Respond in JSON format:
{{
  "action": "<action_name>",
  "action_details": "<detailed steps>",
  "reasoning": "<your analysis>",
  "confidence": <0-1>
}}
"""
    return prompt

def _parse_recommendation(response: str) -> RecommendationResponse:
    """Parse Claude's response"""
    import json
    
    # Extract JSON from response
    try:
        # Try to find JSON in response
        start = response.find('{')
        end = response.rfind('}') + 1
        json_str = response[start:end]
        data = json.loads(json_str)
        
        return RecommendationResponse(**data)
    except:
        # Fallback
        return RecommendationResponse(
            action="restart_pod",
            action_details="Restart pod to recover",
            reasoning="Failed to parse AI response, using fallback",
            confidence=0.5
        )

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
