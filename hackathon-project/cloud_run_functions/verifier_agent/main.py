"""Google Cloud Run Function for Verifier Agent (Sub-Agent 1) with tool `verify_metric`.

Fetches usage data from the actual Volterra URL:
https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400

Searches for `ves-io-http-loadbalancer-<LOADBALANCER_NAME>` and verifies that its Usage value is non-zero (> 0).
If login access is required, returns `status="NEEDS_HUMAN_LOGIN"` and hands control to the human.
"""

import base64
import json
from pathlib import Path
import sys
from typing import Any, Dict, Tuple

# Ensure project root is on sys.path when invoked from subdirectory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.verifier_agent import VOLTERRA_USAGE_DETAILS_URL, VerifierAgent

try:
    import functions_framework
except ImportError:
    # Lightweight decorator shim when functions-framework is not installed locally
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


def execute_verifier_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Execute Sub-Agent 1 (`VerifierAgent.verify_metric`) from a JSON request dictionary."""
    lb_name = str(payload.get("lb_name", "")).strip()
    if not lb_name:
        return (
            {
                "error": "Missing required parameter `lb_name`.",
                "service": "verifier-agent-cloud-run-function",
                "default_usage_url": VOLTERRA_USAGE_DETAILS_URL,
            },
            400,
        )

    usage_url = str(payload.get("usage_url") or VOLTERRA_USAGE_DETAILS_URL).strip()
    auth_token = payload.get("auth_token") or None
    cookie = payload.get("cookie") or None
    human_provided_input = payload.get("human_provided_input") or None
    screenshot_path = payload.get("screenshot_path") or None
    auto_capture_screenshot = bool(payload.get("auto_capture_screenshot", False))
    screenshot_bytes = None
    if payload.get("screenshot_base64"):
        try:
            screenshot_bytes = base64.b64decode(payload["screenshot_base64"])
        except Exception:
            screenshot_bytes = None

    verifier = VerifierAgent(usage_url=usage_url)
    # On Cloud Run Functions, never call server-side webbrowser.open; the public UI opens the tab in the client's browser
    ver_result, agent_msg = verifier.verify_metric(
        lb_name=lb_name,
        auth_token=auth_token,
        cookie=cookie,
        human_provided_input=human_provided_input,
        open_browser_on_login=False,
        interactive_cli=False,
        screenshot_path=screenshot_path,
        screenshot_bytes=screenshot_bytes,
        auto_capture_screenshot=auto_capture_screenshot,
    )

    return (
        {
            "service": "verifier-agent-cloud-run-function",
            "sub_agent": "VerifierAgent (Sub-Agent 1)",
            "tool": "verify_metric",
            "verification_result": ver_result.model_dump(),
            "agent_message": agent_msg.model_dump(),
        },
        200,
    )


@functions_framework.http
def verifier_agent_http(request: Any) -> Tuple[str, int, Dict[str, str]]:
    """HTTP Cloud Run Function entrypoint for Sub-Agent 1 (`VerifierAgent`)."""
    if getattr(request, "method", "GET") == "OPTIONS":
        return ("", 204, _CORS_HEADERS)

    if getattr(request, "method", "GET") == "GET" and not getattr(request, "args", {}).get("lb_name"):
        return (
            json.dumps(
                {
                    "status": "healthy",
                    "service": "verifier-agent-cloud-run-function",
                    "sub_agent": "VerifierAgent (Sub-Agent 1)",
                    "tool": "verify_metric",
                    "usage_url": VOLTERRA_USAGE_DETAILS_URL,
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

    response_dict, status_code = execute_verifier_payload(payload)
    return (json.dumps(response_dict), status_code, _CORS_HEADERS)
