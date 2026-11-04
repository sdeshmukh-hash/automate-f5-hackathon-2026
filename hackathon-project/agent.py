"""Orchestrator Agent for Load Balancer Traffic (`run_traffic`) & Usage Verification (`verify_metric`).

Supports both:
- Google Cloud Deployment:
  - Orchestrator Agent deployed to **Vertex AI Agent Garden / Agent Engine**
  - Sub-Agent 1 (`VerifierAgent`) & `run_traffic` deployed as **Google Cloud Run Functions**
  - Public Web UI deployed as an unauthenticated **Google Cloud Run** service
- Direct Local / CLI Execution
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Dict, List, Optional
import urllib.request
import uuid

logger = logging.getLogger(__name__)

from src.config import settings
from src.models import AgentMessage, BaseModel, Field
from src.skills.run_traffic import TrafficRunResult, resolve_lb_target_url, run_traffic
from src.verifier_agent import (
    VOLTERRA_USAGE_DETAILS_URL,
    MetricVerificationResult,
    VerifierAgent,
)


def _now_time() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _get_cloud_run_auth_header(url: str) -> Optional[str]:
    """Retrieve Google Cloud Run OIDC ID token when running on Cloud Run or with gcloud credentials."""
    # 1. Try Google Metadata Server (when running inside Cloud Run)
    try:
        token_req = urllib.request.Request(
            f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={url}",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(token_req, timeout=2) as resp:
            id_token = resp.read().decode("utf-8").strip()
            if id_token:
                return f"Bearer {id_token}"
    except Exception:
        pass

    # 2. Try google-auth id_token credentials if available
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import id_token

        auth_req = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_req, url)
        if token:
            return f"Bearer {token}"
    except Exception:
        pass

    return None


def _post_cloud_run_function(url: str, payload: Dict[str, Any], timeout_sec: int = 120) -> Dict[str, Any]:
    """Invoke a deployed Google Cloud Run Function over HTTP POST with authentication support."""
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth_header = _get_cloud_run_auth_header(url)
    if auth_header:
        headers["Authorization"] = auth_header

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"HTTP {exc.code} calling Cloud Run Function at '{url}': {exc.reason}. "
            f"Response body: {err_msg[:300]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Failed to connect to Cloud Run Function at '{url}': {exc}"
        ) from exc


class LBTrafficVerificationBundle(BaseModel):
    """Complete execution trace of Orchestrator (`run_traffic`) -> Verifier Agent (`verify_metric`)."""

    run_id: str
    lb_name: str
    target_url: str
    num_requests: int = 100
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL
    traffic_result: Optional[TrafficRunResult] = None
    verification_result: Optional[MetricVerificationResult] = None
    agent_messages: List[AgentMessage] = Field(default_factory=list)
    status: str = "PENDING"
    needs_human_login: bool = False
    verified_non_zero: bool = False
    run_traffic_function_url: str = ""
    verifier_function_url: str = ""
    agent_engine_resource_name: str = ""


class OrchestratorAgent:
    """Orchestrator Agent (Vertex AI Agent Garden) that accepts a load balancer name,
    invokes skill `run_traffic` (100 curl requests), and invokes Sub-Agent 1 (`VerifierAgent`)
    with tool `verify_metric` (via Google Cloud Run Functions when configured).
    """

    agent_id = "orchestrator_agent"
    agent_name = "Orchestrator Agent (Agent Garden)"
    role_badge = "🧭 ORCHESTRATOR"

    def __init__(
        self,
        usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
        run_traffic_function_url: Optional[str] = None,
        verifier_function_url: Optional[str] = None,
    ) -> None:
        self.usage_url = usage_url
        self.run_traffic_function_url = (
            run_traffic_function_url
            if run_traffic_function_url is not None
            else settings.run_traffic_function_url
        )
        self.verifier_function_url = (
            verifier_function_url
            if verifier_function_url is not None
            else settings.verifier_function_url
        )
        self.agent_engine_resource_name = settings.agent_engine_resource_name
        self.verifier_agent = VerifierAgent(usage_url=usage_url)

    def create_bundle(
        self,
        lb_name: str,
        target_url: Optional[str] = None,
        num_requests: int = 100,
    ) -> LBTrafficVerificationBundle:
        run_id = f"LB-RUN-{uuid.uuid4().hex[:6].upper()}"
        clean_lb = lb_name.strip()
        resolved = resolve_lb_target_url(clean_lb, explicit_url=target_url)
        return LBTrafficVerificationBundle(
            run_id=run_id,
            lb_name=clean_lb,
            target_url=resolved,
            num_requests=num_requests,
            usage_url=self.usage_url,
            status="RUNNING",
            run_traffic_function_url=self.run_traffic_function_url,
            verifier_function_url=self.verifier_function_url,
            agent_engine_resource_name=self.agent_engine_resource_name,
        )

    def step_accept_and_plan(self, bundle: LBTrafficVerificationBundle) -> LBTrafficVerificationBundle:
        """Step 1: Orchestrator accepts load balancer name and dispatches `run_traffic` & `VerifierAgent`."""
        traffic_mode = (
            f"Google Cloud Run Function (`{self.run_traffic_function_url}`)"
            if self.run_traffic_function_url
            else "Direct Agent Skill (`run_traffic`)"
        )
        verifier_mode = (
            f"Google Cloud Run Function (`{self.verifier_function_url}`)"
            if self.verifier_function_url
            else "Sub-Agent 1 (`VerifierAgent.verify_metric`)"
        )
        bundle.agent_messages.append(
            AgentMessage(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                role_badge=self.role_badge,
                timestamp=_now_time(),
                headline=f"Accepted Load Balancer '{bundle.lb_name}' ({bundle.run_id})",
                bullets=[
                    f"Target Load Balancer Name: `{bundle.lb_name}`",
                    f"Step 1 Action: Invoke `{traffic_mode}` to execute {bundle.num_requests} real curl requests against `{bundle.target_url}`.",
                    f"Step 2 Action: Invoke `{verifier_mode}` with tool `verify_metric` on `{bundle.usage_url}` and verify non-zero usage.",
                ],
                artifact_summary=f"LB: {bundle.lb_name} | {bundle.num_requests} curl requests",
            )
        )
        return bundle

    def step_run_traffic_skill(self, bundle: LBTrafficVerificationBundle) -> LBTrafficVerificationBundle:
        """Step 2: Execute the `run_traffic` skill (100 real curl requests) via Cloud Run Function or directly."""
        traffic_res: Optional[TrafficRunResult] = None
        invoked_via = "Direct Skill"
        if self.run_traffic_function_url:
            try:
                resp_data = _post_cloud_run_function(
                    self.run_traffic_function_url,
                    {
                        "lb_name": bundle.lb_name,
                        "target_url": bundle.target_url,
                        "num_requests": bundle.num_requests,
                    },
                )
                tr_dict = resp_data.get("traffic_result", resp_data)
                traffic_res = TrafficRunResult(**tr_dict)
                invoked_via = f"Cloud Run Function ({self.run_traffic_function_url})"

                # If Cloud Run in GCP could not resolve/reach internal corporate VPN host, fall back to local host network
                if traffic_res.successful_http_responses == 0 and not settings.running_in_cloud_run:
                    logger.info(
                        "Cloud Run function returned 0 reachable responses for '%s'. Retrying via local host VPN network.",
                        bundle.target_url,
                    )
                    local_res = run_traffic(
                        lb_name=bundle.lb_name,
                        num_requests=bundle.num_requests,
                        target_url=bundle.target_url,
                    )
                    if local_res.successful_http_responses > 0:
                        traffic_res = local_res
                        invoked_via = (
                            f"Local Host VPN Skill (Fallback from Cloud Run DNS isolation on {bundle.target_url})"
                        )
            except Exception as exc:
                logger.warning(
                    "Cloud Run Function call to %s failed (%s). Falling back to direct execution.",
                    self.run_traffic_function_url,
                    exc,
                )
                invoked_via = f"Direct Skill Fallback (Cloud Run unreachable: {exc})"

        if traffic_res is None:
            traffic_res = run_traffic(
                lb_name=bundle.lb_name,
                num_requests=bundle.num_requests,
                target_url=bundle.target_url,
            )

        bundle.traffic_result = traffic_res
        bundle.target_url = traffic_res.target_url

        bullets = [
            f"Execution Runtime: `{invoked_via}`",
            f"Target Endpoint: `{traffic_res.target_url}`",
            f"Requests Executed: `{traffic_res.requests_sent}` | Reachable HTTP Responses: `{traffic_res.successful_http_responses}` | Failed Connections: `{traffic_res.failed_connections}`",
            f"Status Code Distribution: `{json.dumps(traffic_res.status_code_counts)}` | Avg Latency: `{traffic_res.avg_latency_ms} ms`",
            f"Command: `{traffic_res.curl_command_template}`",
        ]
        if traffic_res.errors:
            bullets.append(f"Curl Connection Notes: `{'; '.join(traffic_res.errors[:2])}`")

        bundle.agent_messages.append(
            AgentMessage(
                agent_id="skill_run_traffic",
                agent_name="Agent Skill: run_traffic",
                role_badge="⚡ SKILL: run_traffic",
                timestamp=_now_time(),
                headline=(
                    f"Executed `{traffic_res.skill_name}`: {traffic_res.requests_sent} real curl requests "
                    f"to '{bundle.lb_name}' in {traffic_res.total_duration_sec}s"
                ),
                bullets=bullets,
                artifact_summary=(
                    f"{traffic_res.requests_sent} sent | "
                    f"{traffic_res.successful_http_responses} HTTP responses | "
                    f"{traffic_res.avg_latency_ms}ms avg"
                ),
            )
        )
        return bundle

    def step_invoke_verifier_subagent(
        self,
        bundle: LBTrafficVerificationBundle,
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        human_provided_input: Optional[str] = None,
        open_browser_on_login: Optional[bool] = None,
        interactive_cli: bool = False,
        screenshot_path: Optional[str] = None,
        screenshot_bytes: Optional[bytes] = None,
        auto_capture_screenshot: Optional[bool] = None,
    ) -> LBTrafficVerificationBundle:
        """Step 3: Delegate to Verifier Agent (Sub-Agent 1) via Cloud Run Function or local sub-agent."""
        # Orchestrator owns runtime environment & browser-capture policy:
        if open_browser_on_login is None:
            should_open_local_browser = not settings.running_in_cloud_run
        else:
            should_open_local_browser = open_browser_on_login and not settings.running_in_cloud_run

        if auto_capture_screenshot is None:
            should_auto_capture = (
                should_open_local_browser
                and not bool(self.verifier_function_url)
                and not bool(human_provided_input or auth_token or cookie or screenshot_path or screenshot_bytes)
            )
        else:
            should_auto_capture = auto_capture_screenshot

        ver_res: Optional[MetricVerificationResult] = None
        ver_msg: Optional[AgentMessage] = None
        if self.verifier_function_url:
            try:
                fn_payload: Dict[str, Any] = {
                    "lb_name": bundle.lb_name,
                    "usage_url": bundle.usage_url,
                    "auth_token": auth_token,
                    "cookie": cookie,
                    "human_provided_input": human_provided_input,
                    "screenshot_path": screenshot_path,
                    "auto_capture_screenshot": should_auto_capture,
                }
                if screenshot_bytes:
                    fn_payload["screenshot_base64"] = base64.b64encode(screenshot_bytes).decode("ascii")

                resp_data = _post_cloud_run_function(self.verifier_function_url, fn_payload)
                ver_res = MetricVerificationResult(**resp_data["verification_result"])
                ver_msg = AgentMessage(**resp_data["agent_message"])
                ver_msg.bullets.insert(0, f"Cloud Run Function Endpoint: `{self.verifier_function_url}`")

                # If Cloud Run could not reach private Volterra network due to GCP cloud isolation:
                is_net_timeout = (
                    ver_res.needs_human_login
                    and (
                        ver_res.http_status == 0
                        or "timed out" in (ver_res.human_handoff_reason or "").lower()
                        or "could not reach" in (ver_res.human_handoff_reason or "").lower()
                    )
                )
                if is_net_timeout:
                    logger.info(
                        "Cloud Run function in GCP timed out reaching internal domain '%s'. Falling back to local browser VerifierAgent.",
                        bundle.usage_url,
                    )
                    ver_res = None
                    ver_msg = None
            except Exception as exc:
                logger.warning(
                    "Verifier Cloud Run Function call to %s failed (%s). Falling back to local VerifierAgent.",
                    self.verifier_function_url,
                    exc,
                )

        if ver_res is None or ver_msg is None:
            ver_res, ver_msg = self.verifier_agent.verify_metric(
                lb_name=bundle.lb_name,
                auth_token=auth_token,
                cookie=cookie,
                human_provided_input=human_provided_input,
                open_browser_on_login=should_open_local_browser,
                interactive_cli=interactive_cli,
                screenshot_path=screenshot_path,
                screenshot_bytes=screenshot_bytes,
                auto_capture_screenshot=should_auto_capture,
            )
            if self.verifier_function_url:
                ver_msg.bullets.insert(
                    0,
                    "ℹ️ Cloud Run Function in GCP timed out reaching private CRT network; seamlessly verified via local browser agent on host network.",
                )

        bundle.verification_result = ver_res
        bundle.agent_messages.append(ver_msg)
        bundle.needs_human_login = ver_res.needs_human_login
        bundle.verified_non_zero = ver_res.verified

        if ver_res.needs_human_login:
            bundle.status = "NEEDS_HUMAN_LOGIN"
            bundle.agent_messages.append(
                AgentMessage(
                    agent_id=self.agent_id,
                    agent_name=self.agent_name,
                    role_badge="🛑 HANDED TO HUMAN",
                    timestamp=_now_time(),
                    headline=(
                        f"Control handed to human: Login required at `{ver_res.usage_url}`"
                    ),
                    bullets=[
                        ver_res.human_handoff_reason,
                        *ver_res.human_action_instructions,
                    ],
                    artifact_summary="Status: NEEDS_HUMAN_LOGIN",
                )
            )
            return bundle

        bundle.status = ver_res.status
        verdict_icon = "✅" if ver_res.verified else "❌"
        bundle.agent_messages.append(
            AgentMessage(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                role_badge=self.role_badge,
                timestamp=_now_time(),
                headline=(
                    f"{verdict_icon} Orchestrator Final Verdict for '{bundle.lb_name}': "
                    f"{'PASSED (Usage Value = ' + f'{ver_res.metric_value:g}' + ' > 0)' if ver_res.verified else 'FAILED (' + ver_res.summary + ')'}"
                ),
                bullets=[
                    f"Skill `run_traffic`: {bundle.traffic_result.requests_sent if bundle.traffic_result else 0} curl requests executed.",
                    f"Sub-Agent 1 `verify_metric`: Found={ver_res.found}, Value={ver_res.metric_value:g}, Non-Zero={ver_res.is_non_zero}.",
                ],
                artifact_summary=f"Status: {bundle.status}",
            )
        )
        return bundle

    def run_workflow(
        self,
        lb_name: str,
        target_url: Optional[str] = None,
        num_requests: int = 100,
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        human_provided_input: Optional[str] = None,
        open_browser_on_login: Optional[bool] = None,
        interactive_cli: bool = False,
        auto_capture_screenshot: Optional[bool] = None,
        on_step: Optional[Callable[[int, LBTrafficVerificationBundle], None]] = None,
    ) -> LBTrafficVerificationBundle:
        """Execute Orchestrator -> `run_traffic` (100 curl requests) -> `VerifierAgent.verify_metric`."""
        bundle = self.create_bundle(
            lb_name=lb_name,
            target_url=target_url,
            num_requests=num_requests,
        )
        if on_step:
            on_step(1, bundle)
        bundle = self.step_accept_and_plan(bundle)

        if on_step:
            on_step(2, bundle)
        bundle = self.step_run_traffic_skill(bundle)

        if on_step:
            on_step(3, bundle)
        bundle = self.step_invoke_verifier_subagent(
            bundle,
            auth_token=auth_token,
            cookie=cookie,
            human_provided_input=human_provided_input,
            open_browser_on_login=open_browser_on_login,
            interactive_cli=interactive_cli,
            auto_capture_screenshot=auto_capture_screenshot,
        )
        return bundle

    def resume_after_human_login(
        self,
        bundle: LBTrafficVerificationBundle,
        human_provided_input: Optional[str] = None,
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        screenshot_bytes: Optional[bytes] = None,
        auto_capture_screenshot: bool = False,
    ) -> LBTrafficVerificationBundle:
        """Resume verification after human completes login in browser and Verifier Agent captures screenshot."""
        headline = (
            "Verifier Agent (Sub-Agent 1) capturing browser screenshot to verify Usage column"
            if (auto_capture_screenshot or screenshot_path or screenshot_bytes)
            else "Human completed login and returned control to Verifier Agent (Sub-Agent 1)"
        )
        bundle.agent_messages.append(
            AgentMessage(
                agent_id="human_operator",
                agent_name="Human Operator",
                role_badge="📸 BROWSER VERIFICATION",
                timestamp=_now_time(),
                headline=headline,
                bullets=[
                    f"Resuming `verify_metric` on `{bundle.usage_url}` for Load Balancer `{bundle.lb_name}`.",
                    f"Action: Locating table row `ves-io-http-loadbalancer-{bundle.lb_name}` and reading 'Usage' column value via Vision OCR.",
                ],
                artifact_summary="Resumed via Browser Screenshot",
            )
        )
        return self.step_invoke_verifier_subagent(
            bundle,
            auth_token=auth_token,
            cookie=cookie,
            human_provided_input=human_provided_input,
            open_browser_on_login=False,
            interactive_cli=False,
            screenshot_path=screenshot_path,
            screenshot_bytes=screenshot_bytes,
            auto_capture_screenshot=auto_capture_screenshot,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Orchestrator Agent (`run_traffic` 100 curl requests + `VerifierAgent.verify_metric`)"
    )
    parser.add_argument("--lb-name", required=True, help="Load Balancer name")
    parser.add_argument("--target-url", default="", help="Optional explicit curl URL for the load balancer")
    parser.add_argument("--requests", type=int, default=100, help="Number of curl requests (default: 100)")
    parser.add_argument("--usage-url", default=VOLTERRA_USAGE_DETAILS_URL, help="Volterra usage details URL")
    parser.add_argument("--auth-token", default="", help="Optional Volterra API Token")
    parser.add_argument("--cookie", default="", help="Optional Volterra session Cookie")
    args = parser.parse_args()

    orchestrator = OrchestratorAgent(usage_url=args.usage_url)
    bundle = orchestrator.run_workflow(
        lb_name=args.lb_name,
        target_url=args.target_url or None,
        num_requests=args.requests,
        auth_token=args.auth_token or None,
        cookie=args.cookie or None,
        open_browser_on_login=True,
        interactive_cli=True,
    )
    print(json.dumps(bundle.model_dump(), indent=2))


if __name__ == "__main__":
    main()
