"""FastAPI & Vertex AI Agent Garden / A2A server for the Orchestrator Agent (`run_traffic` & `verify_metric`)."""

from typing import Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent_garden.orchestrator_agent.agent import OrchestratorAgentGardenApp
from src.agent import LBTrafficVerificationBundle, OrchestratorAgent
from src.config import settings
from src.models import BaseModel
from src.verifier_agent import (
    VOLTERRA_USAGE_DETAILS_URL,
    MetricVerificationResult,
    VerifierAgent,
    clear_saved_session_auth,
)

app = FastAPI(
    title="Orchestrator Agent (Agent Garden) & Verifier API",
    description=(
        "Real curl traffic generation (`run_traffic` - 100 requests) and live Volterra metric "
        "verification (`verify_metric`) with Human Login Hand-Off. Exposes A2A / Agent Garden endpoints."
    ),
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_RUN_BUNDLES: Dict[str, LBTrafficVerificationBundle] = {}


class LBVerifyRequest(BaseModel):
    lb_name: str
    target_url: Optional[str] = None
    num_requests: int = 100
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL
    auth_token: Optional[str] = None
    cookie: Optional[str] = None
    human_provided_input: Optional[str] = None
    open_browser_on_login: bool = False


class HumanLoginResumeRequest(BaseModel):
    run_id: Optional[str] = None
    lb_name: str
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL
    human_provided_input: str = ""
    auth_token: Optional[str] = None
    cookie: Optional[str] = None


@app.get("/health")
def health_check() -> dict:
    return {
        "status": "healthy",
        "service": "orchestrator-agent-garden",
        "default_usage_url": VOLTERRA_USAGE_DETAILS_URL,
        "run_traffic_function_url": settings.run_traffic_function_url,
        "verifier_function_url": settings.verifier_function_url,
        "simulation": False,
    }


@app.get("/.well-known/agent.json")
def agent_garden_card() -> dict:
    """Vertex AI Agent Garden / A2A Agent Card metadata endpoint."""
    return {
        "name": "volterra-lb-orchestrator-agent",
        "description": (
            "Orchestrator Agent that accepts a load balancer name, invokes the `run_traffic` "
            "Cloud Run Function (100 real curl requests), and delegates to the `verifier_agent` "
            "Cloud Run Function (`verify_metric`) on the live Volterra usage URL."
        ),
        "version": "4.0.0",
        "capabilities": {
            "streaming": False,
            "human_in_the_loop_login_handoff": True,
        },
        "skills": [
            {
                "id": "run_traffic",
                "name": "run_traffic",
                "description": "Run 100 real curl requests against a target load balancer.",
                "endpoint": settings.run_traffic_function_url or "/api/lb/run-and-verify",
            },
            {
                "id": "verify_metric",
                "name": "verify_metric (Verifier Agent Sub-Agent 1)",
                "description": f"Fetch {VOLTERRA_USAGE_DETAILS_URL}, search for lb_name, and verify usage > 0.",
                "endpoint": settings.verifier_function_url or "/api/lb/verify-only",
            },
        ],
    }


@app.post("/query")
def query_agent_garden(req: LBVerifyRequest) -> dict:
    """Vertex AI Agent Engine / Agent Garden standard query endpoint."""
    if not req.lb_name.strip():
        raise HTTPException(status_code=400, detail="Load balancer name (`lb_name`) is required.")
    garden_app = OrchestratorAgentGardenApp(
        run_traffic_function_url=settings.run_traffic_function_url,
        verifier_function_url=settings.verifier_function_url,
    )
    garden_app.set_up()
    return garden_app.query(
        lb_name=req.lb_name.strip(),
        target_url=req.target_url or "",
        num_requests=req.num_requests,
        human_provided_input=req.human_provided_input or "",
    )


@app.post("/api/lb/run-and-verify", response_model=LBTrafficVerificationBundle)
def run_traffic_and_verify(req: LBVerifyRequest) -> LBTrafficVerificationBundle:
    """Orchestrator endpoint: runs `run_traffic` (100 real curl requests) then `VerifierAgent.verify_metric`."""
    if not req.lb_name.strip():
        raise HTTPException(status_code=400, detail="Load balancer name (`lb_name`) is required.")
    orchestrator = OrchestratorAgent(usage_url=req.usage_url)
    bundle = orchestrator.run_workflow(
        lb_name=req.lb_name,
        target_url=req.target_url,
        num_requests=req.num_requests,
        auth_token=req.auth_token,
        cookie=req.cookie,
        human_provided_input=req.human_provided_input,
        open_browser_on_login=req.open_browser_on_login,
        interactive_cli=False,
    )
    _RUN_BUNDLES[bundle.run_id] = bundle
    return bundle


@app.post("/api/lb/resume-after-login", response_model=LBTrafficVerificationBundle)
def resume_after_human_login(req: HumanLoginResumeRequest) -> LBTrafficVerificationBundle:
    """Resume verification after the human logs in and provides Cookie / APIToken / cURL / page content."""
    orchestrator = OrchestratorAgent(usage_url=req.usage_url)
    bundle = _RUN_BUNDLES.get(req.run_id or "")
    if bundle is None:
        bundle = orchestrator.create_bundle(lb_name=req.lb_name, num_requests=100)
    updated = orchestrator.resume_after_human_login(
        bundle=bundle,
        human_provided_input=req.human_provided_input,
        auth_token=req.auth_token,
        cookie=req.cookie,
    )
    _RUN_BUNDLES[updated.run_id] = updated
    return updated


@app.post("/api/lb/verify-only", response_model=MetricVerificationResult)
def verify_metric_only(req: LBVerifyRequest) -> MetricVerificationResult:
    """Sub-Agent 1 (`VerifierAgent`) endpoint: invokes `verify_metric` directly."""
    if not req.lb_name.strip():
        raise HTTPException(status_code=400, detail="Load balancer name (`lb_name`) is required.")
    verifier = VerifierAgent(usage_url=req.usage_url)
    result, _ = verifier.verify_metric(
        lb_name=req.lb_name,
        auth_token=req.auth_token,
        cookie=req.cookie,
        human_provided_input=req.human_provided_input,
        open_browser_on_login=req.open_browser_on_login,
        interactive_cli=False,
    )
    return result


@app.post("/api/auth/clear")
def clear_auth() -> dict:
    clear_saved_session_auth()
    return {"status": "cleared"}
