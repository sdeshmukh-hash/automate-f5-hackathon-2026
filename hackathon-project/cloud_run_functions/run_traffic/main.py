"""Google Cloud Run Function for Agent Skill `run_traffic`.

Executes 100 real `curl` requests against the specified load balancer (`lb_name` or `target_url`)
and returns `TrafficRunResult` telemetry.
"""

import json
from pathlib import Path
import sys
from typing import Any, Dict, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.skills.run_traffic import run_traffic

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


def execute_run_traffic_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Execute `run_traffic` skill from a JSON request dictionary."""
    lb_name = str(payload.get("lb_name", "")).strip()
    if not lb_name:
        return (
            {
                "error": "Missing required parameter `lb_name`.",
                "service": "run-traffic-cloud-run-function",
                "skill": "run_traffic",
            },
            400,
        )

    target_url = payload.get("target_url") or None
    num_requests = int(payload.get("num_requests", 100))

    traffic_res = run_traffic(
        lb_name=lb_name,
        num_requests=num_requests,
        target_url=target_url,
    )
    return (
        {
            "service": "run-traffic-cloud-run-function",
            "skill": "run_traffic",
            "traffic_result": traffic_res.model_dump(),
        },
        200,
    )


@functions_framework.http
def run_traffic_http(request: Any) -> Tuple[str, int, Dict[str, str]]:
    """HTTP Cloud Run Function entrypoint for skill `run_traffic` (100 curl requests)."""
    if getattr(request, "method", "GET") == "OPTIONS":
        return ("", 204, _CORS_HEADERS)

    if getattr(request, "method", "GET") == "GET" and not getattr(request, "args", {}).get("lb_name"):
        return (
            json.dumps(
                {
                    "status": "healthy",
                    "service": "run-traffic-cloud-run-function",
                    "skill": "run_traffic",
                    "default_requests": 100,
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

    response_dict, status_code = execute_run_traffic_payload(payload)
    return (json.dumps(response_dict), status_code, _CORS_HEADERS)
