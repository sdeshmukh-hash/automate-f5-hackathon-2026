"""Orchestrator Agent definition for Google Cloud Vertex AI Agent Garden / Agent Engine (ADK).

- Orchestrator Agent (`root_agent`): Accepts a Load Balancer name (`lb_name`), runs `run_traffic_tool`
  (100 real `curl` requests via the `run-traffic-fn` Cloud Run Function), and delegates to
  Sub-Agent 1 (`verifier_agent` / `verify_metric_tool` deployed as the `verifier-agent-fn` Cloud Run Function).
- If login access is required on Volterra (`https://testing.crt.volterra.us/...`), returns
  `status="NEEDS_HUMAN_LOGIN"` and hands control to the human operator.
"""

import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agent import OrchestratorAgent, _post_cloud_run_function
from src.config import settings
from src.skills.run_traffic import run_traffic
from src.verifier_agent import VOLTERRA_USAGE_DETAILS_URL, verify_metric


def run_traffic_tool(
    lb_name: str,
    target_url: str = "",
    num_requests: int = 100,
) -> Dict[str, Any]:
    """Agent Skill `run_traffic`: Execute 100 real curl requests against the target load balancer.

    Args:
        lb_name: The load balancer name or hostname.
        target_url: Optional explicit HTTP/HTTPS endpoint URL to curl.
        num_requests: Number of curl requests to execute (default 100).

    Returns:
        Dictionary containing `requests_sent`, `successful_http_responses`, `status_code_counts`, and `avg_latency_ms`.
    """
    fn_url = os.getenv("RUN_TRAFFIC_FUNCTION_URL", settings.run_traffic_function_url)
    if fn_url:
        try:
            resp = _post_cloud_run_function(
                fn_url,
                {
                    "lb_name": lb_name,
                    "target_url": target_url or None,
                    "num_requests": num_requests,
                },
            )
            return resp.get("traffic_result", resp)
        except Exception:
            pass

    result = run_traffic(
        lb_name=lb_name,
        num_requests=num_requests,
        target_url=target_url or None,
    )
    return result.model_dump()


def verify_metric_tool(
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    human_provided_input: str = "",
) -> Dict[str, Any]:
    """Sub-Agent 1 Tool `verify_metric`: Fetch usage data from the Volterra usage details URL,
    search for `lb_name`, and verify the value is non-zero (> 0).

    If authentication/login is required, sets `needs_human_login=True` (`status="NEEDS_HUMAN_LOGIN"`)
    and hands control to the human operator.

    Args:
        lb_name: The load balancer name to search for in the Volterra usage details table.
        usage_url: The Volterra usage details URL.
        human_provided_input: Optional session Cookie, APIToken, 'Copy as cURL', or page content supplied by the human after logging in.

    Returns:
        Dictionary containing `verified`, `is_non_zero`, `metric_value`, `needs_human_login`, and `status`.
    """
    fn_url = os.getenv("VERIFIER_FUNCTION_URL", settings.verifier_function_url)
    if fn_url:
        try:
            resp = _post_cloud_run_function(
                fn_url,
                {
                    "lb_name": lb_name,
                    "usage_url": usage_url,
                    "human_provided_input": human_provided_input or None,
                },
            )
            return resp.get("verification_result", resp)
        except Exception:
            pass

    res = verify_metric(
        lb_name=lb_name,
        usage_url=usage_url,
        human_provided_input=human_provided_input or None,
        open_browser_on_login=False,
        interactive_cli=False,
    )
    return res.model_dump()


class OrchestratorAgentGardenApp:
    """Vertex AI Agent Engine / Reasoning Engine application class for the Orchestrator Agent."""

    def __init__(
        self,
        run_traffic_function_url: Optional[str] = None,
        verifier_function_url: Optional[str] = None,
    ) -> None:
        self.run_traffic_function_url = run_traffic_function_url or os.getenv(
            "RUN_TRAFFIC_FUNCTION_URL", ""
        )
        self.verifier_function_url = verifier_function_url or os.getenv(
            "VERIFIER_FUNCTION_URL", ""
        )

    def set_up(self) -> None:
        """Initialize the Orchestrator Agent inside Vertex AI Agent Engine."""
        self._orchestrator = OrchestratorAgent(
            run_traffic_function_url=self.run_traffic_function_url,
            verifier_function_url=self.verifier_function_url,
        )

    def query(
        self,
        lb_name: str,
        target_url: str = "",
        num_requests: int = 100,
        human_provided_input: str = "",
    ) -> Dict[str, Any]:
        """Execute the full Orchestrator workflow (`run_traffic` -> `verify_metric`) on Vertex AI."""
        if not hasattr(self, "_orchestrator"):
            self.set_up()
        bundle = self._orchestrator.run_workflow(
            lb_name=lb_name,
            target_url=target_url or None,
            num_requests=num_requests,
            human_provided_input=human_provided_input or None,
            open_browser_on_login=False,
            interactive_cli=False,
        )
        return bundle.model_dump()


# Construct Google ADK `root_agent` and `verifier_subagent` when `google-adk` is installed
try:
    from google.adk.agents import Agent as AdkAgent

    verifier_subagent = AdkAgent(
        name="verifier_agent",
        model=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
        description=(
            "Sub-Agent 1 (Verifier Agent): Invokes the `verify_metric_tool` Cloud Run Function to fetch "
            "usage data from Volterra (`RE – Load Balancer – Public – Count`), search for the load balancer "
            "name, verify that its value is non-zero (> 0), or hand control to the human if login is required."
        ),
        instruction=(
            "You are Verifier Agent (Sub-Agent 1). Always call `verify_metric_tool` with the given `lb_name`. "
            "If `needs_human_login` is True (`status == 'NEEDS_HUMAN_LOGIN'`), instruct the human to log in at "
            "the Volterra URL and provide their session Cookie, APIToken, or page data. Otherwise report whether "
            "`metric_value > 0`."
        ),
        tools=[verify_metric_tool],
    )

    root_agent = AdkAgent(
        name="orchestrator_agent",
        model=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
        description=(
            "Orchestrator Agent deployed to Google Cloud Vertex AI Agent Garden. Accepts a load balancer name, "
            "runs 100 curl requests via `run_traffic_tool`, and delegates verification to `verifier_agent`."
        ),
        instruction=(
            "You are the Orchestrator Agent. When given a load balancer name (`lb_name`):\n"
            "1. Invoke `run_traffic_tool(lb_name=lb_name, num_requests=100)` to run 100 real curl requests.\n"
            "2. Delegate to `verifier_agent` (or call `verify_metric_tool`) to fetch usage data from "
            f"`{VOLTERRA_USAGE_DETAILS_URL}` and verify that the metric value for `lb_name` is non-zero (> 0).\n"
            "3. Never simulate data. If login is required (`NEEDS_HUMAN_LOGIN`), hand control to the human."
        ),
        tools=[run_traffic_tool, verify_metric_tool],
        sub_agents=[verifier_subagent],
    )
except ImportError:
    root_agent = OrchestratorAgentGardenApp()  # type: ignore[assignment]
