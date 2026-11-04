"""Google Cloud Run Function for Orchestrator Agent.

Orchestrates the multi-agent workflow:
1. Accepts a Load Balancer name / target URL.
2. Invokes the `run_traffic` Cloud Run Function (`run-traffic-fn`) to send 100 real curl requests.
3. Invokes the `verifier_agent` Cloud Run Function (`verifier-agent-fn`) with tool `verify_metric`.
4. If login is needed, returns `status="NEEDS_HUMAN_LOGIN"` and hands control to human.
"""

import base64
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Tuple

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agent import LBTrafficVerificationBundle, OrchestratorAgent
from src.config import settings
from src.verifier_agent import VOLTERRA_USAGE_DETAILS_URL

try:
    import functions_framework
except ImportError:
    class _FFShim:
        @staticmethod
        def http(fn: Any) -> Any:
            return fn

    functions_framework = _FFShim()  # type: ignore[assignment]


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Content-Type": "application/json",
}


def execute_orchestrator_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Execute Orchestrator Agent from a JSON request dictionary."""
    action = payload.get("action", "run")
    usage_url = str(payload.get("usage_url") or VOLTERRA_USAGE_DETAILS_URL).strip()
    if "run_traffic_function_url" in payload:
        run_traffic_fn = str(payload["run_traffic_function_url"]).strip()
    else:
        run_traffic_fn = os.getenv("RUN_TRAFFIC_FUNCTION_URL", settings.run_traffic_function_url)

    if "verifier_function_url" in payload:
        verifier_fn = str(payload["verifier_function_url"]).strip()
    else:
        verifier_fn = os.getenv("VERIFIER_FUNCTION_URL", settings.verifier_function_url)

    orchestrator = OrchestratorAgent(
        usage_url=usage_url,
        run_traffic_function_url=run_traffic_fn,
        verifier_function_url=verifier_fn,
    )

    if action == "resume":
        bundle_dict = payload.get("bundle")
        if not bundle_dict or not isinstance(bundle_dict, dict):
            return (
                {
                    "error": "Missing or invalid `bundle` parameter for resume action.",
                    "service": "orchestrator-agent-cloud-run-function",
                },
                400,
            )
        try:
            bundle = LBTrafficVerificationBundle(**bundle_dict)
            screenshot_bytes = None
            if payload.get("screenshot_base64"):
                try:
                    screenshot_bytes = base64.b64decode(payload["screenshot_base64"])
                except Exception:
                    screenshot_bytes = None

            resumed_bundle = orchestrator.resume_after_human_login(
                bundle=bundle,
                human_provided_input=payload.get("human_provided_input") or None,
                auth_token=payload.get("auth_token") or None,
                cookie=payload.get("cookie") or None,
                screenshot_path=payload.get("screenshot_path") or None,
                screenshot_bytes=screenshot_bytes,
                auto_capture_screenshot=bool(payload.get("auto_capture_screenshot", False)),
            )
            return (
                {
                    "service": "orchestrator-agent-cloud-run-function",
                    "action": "resume",
                    "status": resumed_bundle.status,
                    "verified_non_zero": resumed_bundle.verified_non_zero,
                    "needs_human_login": resumed_bundle.needs_human_login,
                    "bundle": resumed_bundle.model_dump(),
                },
                200,
            )
        except Exception as exc:
            return (
                {
                    "error": f"Failed to resume verification: {exc}",
                    "service": "orchestrator-agent-cloud-run-function",
                },
                500,
            )

    # Standard "run" action
    lb_name = str(payload.get("lb_name", "")).strip()
    if not lb_name:
        return (
            {
                "error": "Missing required parameter `lb_name`.",
                "service": "orchestrator-agent-cloud-run-function",
                "default_usage_url": VOLTERRA_USAGE_DETAILS_URL,
            },
            400,
        )

    target_url = payload.get("target_url") or None
    num_requests = int(payload.get("num_requests", 100))
    auth_token = payload.get("auth_token") or None
    cookie = payload.get("cookie") or None
    human_provided_input = payload.get("human_provided_input") or None

    bundle = orchestrator.run_workflow(
        lb_name=lb_name,
        target_url=target_url,
        num_requests=num_requests,
        auth_token=auth_token,
        cookie=cookie,
        human_provided_input=human_provided_input,
        open_browser_on_login=False,
        interactive_cli=False,
    )

    return (
        {
            "service": "orchestrator-agent-cloud-run-function",
            "action": "run",
            "status": bundle.status,
            "verified_non_zero": bundle.verified_non_zero,
            "needs_human_login": bundle.needs_human_login,
            "bundle": bundle.model_dump(),
        },
        200,
    )


@functions_framework.http
def orchestrator_agent_http(request: Any) -> Tuple[str, int, Dict[str, str]]:
    """HTTP Cloud Run Function entrypoint for Orchestrator Agent."""
    if getattr(request, "method", "GET") == "OPTIONS":
        return ("", 204, _CORS_HEADERS)

    if getattr(request, "method", "GET") == "GET" and not getattr(request, "args", {}).get("lb_name"):
        return (
            json.dumps(
                {
                    "status": "healthy",
                    "service": "orchestrator-agent-cloud-run-function",
                    "role": "Orchestrator Agent",
                    "run_traffic_function_url": os.getenv("RUN_TRAFFIC_FUNCTION_URL", settings.run_traffic_function_url),
                    "verifier_function_url": os.getenv("VERIFIER_FUNCTION_URL", settings.verifier_function_url),
                    "default_usage_url": VOLTERRA_USAGE_DETAILS_URL,
                }
            ),
            200,
            _CORS_HEADERS,
        )

    payload: Dict[str, Any] = {}
    if hasattr(request, "get_json"):
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload.update(body)
    if hasattr(request, "args") and request.args:
        for k, v in request.args.items():
            payload.setdefault(k, v)

    response_dict, status_code = execute_orchestrator_payload(payload)
    return (json.dumps(response_dict), status_code, _CORS_HEADERS)
