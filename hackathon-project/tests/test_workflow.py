"""Integration tests for Orchestrator (Agent Garden), Sub-Agent Cloud Run Functions (`run_traffic` & `verify_metric`), and Human Login Hand-Off."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_garden.orchestrator_agent.agent import OrchestratorAgentGardenApp
from cloud_run_functions.orchestrator_agent.main import execute_orchestrator_payload
from cloud_run_functions.run_traffic.main import execute_run_traffic_payload
from cloud_run_functions.verifier_agent.main import execute_verifier_payload
from src.agent import OrchestratorAgent
from src.skills.run_traffic import resolve_lb_target_url, run_traffic
from src.verifier_agent import (
    VOLTERRA_USAGE_DETAILS_URL,
    VerifierAgent,
    _detect_login_required,
    clear_saved_session_auth,
    extract_lb_value_from_json,
    extract_lb_value_from_text,
    parse_human_auth_input,
    verify_screenshot,
    find_browser_executable,
    verify_with_browser,
)


class TestLBTrafficAndVerifierWorkflow(unittest.TestCase):
    """Test real `run_traffic` (100 curl requests), `verify_metric`, Cloud Run Functions, Agent Garden, and human login hand-off."""

    def setUp(self) -> None:
        clear_saved_session_auth()

    def tearDown(self) -> None:
        clear_saved_session_auth()

    def test_default_volterra_url_constant(self) -> None:
        self.assertEqual(
            VOLTERRA_USAGE_DETAILS_URL,
            "https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/"
            "RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400",
        )

    def test_resolve_lb_target_url_uses_volterra_domain_when_bare_name(self) -> None:
        url = resolve_lb_target_url("ves-io-http-lb-prod-checkout")
        self.assertEqual(url, "https://ves-io-http-lb-prod-checkout.testing.crt.volterra.us")

    def test_run_traffic_executes_100_real_curl_requests(self) -> None:
        result = run_traffic(
            lb_name="ves-io-http-lb-custom-test",
            num_requests=100,
        )
        self.assertEqual(result.skill_name, "run_traffic")
        self.assertEqual(result.requested_count, 100)
        self.assertEqual(result.requests_sent, 100)
        self.assertEqual(
            result.target_url,
            "https://ves-io-http-lb-custom-test.testing.crt.volterra.us",
        )
        self.assertEqual(
            result.successful_http_responses + result.failed_connections,
            100,
        )

    def test_cloud_run_function_handlers_for_subagents(self) -> None:
        # 1. Test `run_traffic` Cloud Run Function payload handler
        tr_body, tr_code = execute_run_traffic_payload(
            {"lb_name": "ves-io-http-lb-custom-test", "num_requests": 5}
        )
        self.assertEqual(tr_code, 200)
        self.assertEqual(tr_body["service"], "run-traffic-cloud-run-function")
        self.assertEqual(tr_body["traffic_result"]["requests_sent"], 5)

        # 2. Test `verifier_agent` (Sub-Agent 1) Cloud Run Function payload handler
        ver_body, ver_code = execute_verifier_payload(
            {
                "lb_name": "ves-io-http-lb-custom-test",
                "human_provided_input": json.dumps(
                    {
                        "items": [
                            {
                                "name": "ves-io-http-lb-custom-test",
                                "metric": "RE – Load Balancer – Public – Count",
                                "value": 42,
                            }
                        ]
                    }
                ),
            }
        )
        self.assertEqual(ver_code, 200)
        self.assertEqual(ver_body["service"], "verifier-agent-cloud-run-function")
        self.assertTrue(ver_body["verification_result"]["verified"])
        self.assertEqual(ver_body["verification_result"]["metric_value"], 42.0)

    def test_orchestrator_cloud_run_function_handler(self) -> None:
        body, code = execute_orchestrator_payload(
            {
                "lb_name": "ves-io-http-lb-fn-test",
                "num_requests": 5,
                "run_traffic_function_url": "",
                "verifier_function_url": "",
                "human_provided_input": json.dumps(
                    {
                        "items": [
                            {
                                "name": "ves-io-http-lb-fn-test",
                                "value": 99.5,
                            }
                        ]
                    }
                ),
            }
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["service"], "orchestrator-agent-cloud-run-function")
        self.assertEqual(body["status"], "VERIFIED_NON_ZERO")
        self.assertTrue(body["verified_non_zero"])
        self.assertEqual(body["bundle"]["traffic_result"]["requests_sent"], 5)
        self.assertEqual(body["bundle"]["verification_result"]["metric_value"], 99.5)

    def test_agent_garden_orchestrator_app(self) -> None:
        app = OrchestratorAgentGardenApp()
        app.set_up()
        res = app.query(
            lb_name="ves-io-http-lb-custom-test",
            num_requests=5,
            human_provided_input=json.dumps(
                {
                    "items": [
                        {
                            "name": "ves-io-http-lb-custom-test",
                            "value": 100,
                        }
                    ]
                }
            ),
        )
        self.assertEqual(res["status"], "VERIFIED_NON_ZERO")
        self.assertTrue(res["verified_non_zero"])
        self.assertEqual(res["traffic_result"]["requests_sent"], 5)

    def test_verify_metric_hands_control_to_human_when_login_needed_and_resumes(self) -> None:
        orchestrator = OrchestratorAgent(run_traffic_function_url="", verifier_function_url="")

        # 1. Run workflow: executes 100 real curl requests via `run_traffic`, then queries actual
        #    Volterra URL and hands control to human when authentication/network login is required.
        bundle = orchestrator.run_workflow(
            lb_name="ves-io-http-lb-custom-test",
            num_requests=100,
            open_browser_on_login=False,
            interactive_cli=False,
        )
        self.assertIsNotNone(bundle.traffic_result)
        assert bundle.traffic_result is not None
        self.assertEqual(bundle.traffic_result.skill_name, "run_traffic")
        self.assertEqual(bundle.traffic_result.requests_sent, 100)

        self.assertEqual(bundle.status, "NEEDS_HUMAN_LOGIN")
        self.assertTrue(bundle.needs_human_login)
        self.assertIsNotNone(bundle.verification_result)
        assert bundle.verification_result is not None
        self.assertEqual(bundle.verification_result.usage_url, VOLTERRA_USAGE_DETAILS_URL)
        self.assertTrue(bundle.verification_result.needs_human_login)
        self.assertFalse(bundle.verification_result.verified)

        # 2. Human logs in at VOLTERRA_USAGE_DETAILS_URL and provides the usage details response
        authenticated_volterra_response = json.dumps(
            {
                "items": [
                    {
                        "name": "ves-io-http-lb-custom-test",
                        "metric": "RE – Load Balancer – Public – Count",
                        "value": 100,
                    }
                ]
            }
        )
        resumed = orchestrator.resume_after_human_login(
            bundle=bundle,
            human_provided_input=authenticated_volterra_response,
        )
        self.assertEqual(resumed.status, "VERIFIED_NON_ZERO")
        self.assertFalse(resumed.needs_human_login)
        self.assertTrue(resumed.verified_non_zero)
        assert resumed.verification_result is not None
        self.assertFalse(resumed.verification_result.needs_human_login)
        self.assertTrue(resumed.verification_result.found)
        self.assertEqual(resumed.verification_result.metric_value, 100.0)
        self.assertTrue(resumed.verification_result.is_non_zero)
        self.assertTrue(resumed.verification_result.verified)

    def test_orchestrator_routes_to_remote_cloud_run_functions(self) -> None:
        def mock_post(url, payload, timeout_sec=120):
            if "run-traffic" in url:
                return {
                    "service": "run-traffic-cloud-run-function",
                    "traffic_result": {
                        "skill_name": "run_traffic",
                        "lb_name": payload["lb_name"],
                        "target_url": payload["target_url"],
                        "requested_count": payload["num_requests"],
                        "requests_sent": payload["num_requests"],
                        "successful_http_responses": payload["num_requests"],
                        "failed_connections": 0,
                        "status_code_counts": {"200": payload["num_requests"]},
                        "avg_latency_ms": 15.2,
                        "total_duration_sec": 1.5,
                        "curl_command_template": "curl -s -o /dev/null -w '%{http_code}' ...",
                    },
                }
            elif "verifier-agent" in url:
                return {
                    "service": "verifier-agent-cloud-run-function",
                    "verification_result": {
                        "lb_name": payload["lb_name"],
                        "usage_url": payload["usage_url"],
                        "found": True,
                        "metric_name": "RE – Load Balancer – Public – Count",
                        "metric_value": 75.0,
                        "is_non_zero": True,
                        "verified": True,
                        "status": "VERIFIED_NON_ZERO",
                        "summary": "Non-zero metric verified: 75",
                        "needs_human_login": False,
                    },
                    "agent_message": {
                        "agent_id": "subagent_1_verifier",
                        "agent_name": "Verifier Agent (Sub-Agent 1)",
                        "role_badge": "🔍 SUBAGENT 1: VERIFIER",
                        "timestamp": "12:00:00 UTC",
                        "headline": "Usage metric verified: 75 > 0",
                        "bullets": ["Mock remote response"],
                        "artifact_summary": "Passed",
                    },
                }
            raise ValueError(f"Unexpected url {url}")

        with patch("src.agent._post_cloud_run_function", side_effect=mock_post):
            orchestrator = OrchestratorAgent(
                run_traffic_function_url="https://run-traffic-fn-802969428036.us-central1.run.app",
                verifier_function_url="https://verifier-agent-fn-802969428036.us-central1.run.app",
            )
            bundle = orchestrator.run_workflow(
                lb_name="ves-io-http-lb-mock",
                num_requests=25,
            )
            self.assertEqual(bundle.status, "VERIFIED_NON_ZERO")
            self.assertTrue(bundle.verified_non_zero)
            self.assertEqual(bundle.traffic_result.requests_sent, 25)
            self.assertEqual(bundle.verification_result.metric_value, 75.0)

    def test_zero_value_load_balancer_fails_verification(self) -> None:
        verifier = VerifierAgent()
        zero_payload = json.dumps(
            {
                "items": [
                    {
                        "name": "zero-traffic-lb",
                        "metric": "RE – Load Balancer – Public – Count",
                        "value": 0,
                    }
                ]
            }
        )
        result, _ = verifier.verify_metric(
            lb_name="zero-traffic-lb",
            human_provided_input=zero_payload,
            open_browser_on_login=False,
            interactive_cli=False,
        )
        self.assertFalse(result.needs_human_login)
        self.assertTrue(result.found)
        self.assertEqual(result.metric_value, 0.0)
        self.assertFalse(result.is_non_zero)
        self.assertFalse(result.verified)
        self.assertEqual(result.status, "FAILED_ZERO_VALUE")

    def test_detect_login_required_on_spa_shell_or_401(self) -> None:
        is_login, reason = _detect_login_required(
            http_status=401,
            effective_url=VOLTERRA_USAGE_DETAILS_URL,
            body="",
            lb_name="re-public-lb-prod",
        )
        self.assertTrue(is_login)
        self.assertIn("401", reason)

        is_spa_login, _ = _detect_login_required(
            http_status=200,
            effective_url=VOLTERRA_USAGE_DETAILS_URL,
            body="<!doctype html><html><head><title>F5 Distributed Cloud Console</title></head><body><app-root></app-root></body></html>",
            lb_name="re-public-lb-prod",
        )
        self.assertTrue(is_spa_login)

    def test_parse_human_auth_input_supports_curl_and_tokens(self) -> None:
        curl_cmd = (
            "curl 'https://testing.crt.volterra.us/api/web/namespaces/system/billing/usage' "
            "-H 'Cookie: sid=abc123xyz' -H 'Authorization: APIToken my-secret-token'"
        )
        token, cookie, headers, direct = parse_human_auth_input(curl_cmd)
        self.assertEqual(token, "APIToken my-secret-token")
        self.assertEqual(cookie, "sid=abc123xyz")
        self.assertIsNone(direct)

    def test_volterra_json_and_text_extraction(self) -> None:
        sample_payload = {
            "items": [
                {"name": "other-lb-01", "value": 0},
                {
                    "name": "re-public-lb-prod",
                    "metric": "RE – Load Balancer – Public – Count",
                    "value": 100,
                },
            ]
        }
        extracted = extract_lb_value_from_json(sample_payload, "re-public-lb-prod")
        self.assertIsNotNone(extracted)
        assert extracted is not None
        self.assertEqual(extracted[0], 100.0)

        text_extracted = extract_lb_value_from_text(
            "Row 1: re-public-lb-prod | RE – Load Balancer – Public – Count | 100",
            "re-public-lb-prod",
        )
        self.assertIsNotNone(text_extracted)
        assert text_extracted is not None
        self.assertEqual(text_extracted[0], 100.0)


    def test_ves_io_http_loadbalancer_matching_and_requests_usage(self) -> None:
        # 1. Test searching by bare name "blocking-waf-https" against table text with "ves-io-http-loadbalancer-blocking-waf-https"
        table_snippet = "ves-io-http-loadbalancer-blocking-waf-https\tdg-telemetry\t3 requests"
        text_res = extract_lb_value_from_text(table_snippet, "blocking-waf-https")
        self.assertIsNotNone(text_res)
        assert text_res is not None
        self.assertEqual(text_res[0], 3.0)

        # 2. Test space-delimited browser copy snippet
        browser_copy = """
        1 of 5 items
        Object Name Namespace Usage
        ves-io-http-loadbalancer-blocking-waf-https   dg-telemetry   3 requests
        """
        browser_res = extract_lb_value_from_text(browser_copy, "blocking-waf-https")
        self.assertIsNotNone(browser_res)
        assert browser_res is not None
        self.assertEqual(browser_res[0], 3.0)

        # 3. Test searching against JSON containing "ves-io-http-loadbalancer-blocking-waf-https" and "Usage: 3 requests"
        json_payload = [
            {
                "Object Name": "ves-io-http-loadbalancer-blocking-waf-https",
                "Namespace": "dg-telemetry",
                "Usage": "3 requests",
            }
        ]
        json_res = extract_lb_value_from_json(json_payload, "blocking-waf-https")
        self.assertIsNotNone(json_res)
        assert json_res is not None
        self.assertEqual(json_res[0], 3.0)

        # 4. End-to-end verification via VerifierAgent with bare name "blocking-waf-https"
        verifier = VerifierAgent()
        result, msg = verifier.verify_metric(
            lb_name="blocking-waf-https",
            human_provided_input=table_snippet,
            open_browser_on_login=False,
            interactive_cli=False,
        )
        self.assertFalse(result.needs_human_login)
        self.assertTrue(result.found)
        self.assertEqual(result.metric_value, 3.0)
        self.assertTrue(result.is_non_zero)
        self.assertTrue(result.verified)
        self.assertEqual(result.status, "VERIFIED_NON_ZERO")

    def test_verify_screenshot_on_browser_image(self) -> None:
        sample_screenshot = Path(
            "/Users/s.deshmukh/.gemini/antigravity/brain/a5c168b4-f474-4510-b81b-930d26ef7b0e/.user_uploaded/media_1793776982021_f36e833e.png"
        )
        if not sample_screenshot.exists():
            self.skipTest("Sample screenshot not present on disk.")

        # 1. Test verify_screenshot with full loadbalancer name
        res_full = verify_screenshot(
            screenshot=str(sample_screenshot),
            lb_name="ves-io-http-loadbalancer-blocking-waf-https",
        )
        self.assertTrue(res_full.found)
        self.assertEqual(res_full.metric_value, 3.0)
        self.assertTrue(res_full.is_non_zero)
        self.assertTrue(res_full.verified)
        self.assertEqual(res_full.status, "VERIFIED_NON_ZERO")
        self.assertTrue(res_full.screenshot_captured)
        self.assertIn("3 requests", res_full.usage_column_text)

        # 2. Test verify_screenshot with bare name provided by streamlit app ("blocking-waf-https")
        res_bare = verify_screenshot(
            screenshot=str(sample_screenshot),
            lb_name="blocking-waf-https",
        )
        self.assertTrue(res_bare.found)
        self.assertEqual(res_bare.metric_value, 3.0)
        self.assertTrue(res_bare.is_non_zero)
        self.assertTrue(res_bare.verified)
        self.assertEqual(res_bare.status, "VERIFIED_NON_ZERO")

    def test_verifier_agent_and_orchestrator_resume_with_screenshot(self) -> None:
        sample_screenshot = Path(
            "/Users/s.deshmukh/.gemini/antigravity/brain/a5c168b4-f474-4510-b81b-930d26ef7b0e/.user_uploaded/media_1793776982021_f36e833e.png"
        )
        if not sample_screenshot.exists():
            self.skipTest("Sample screenshot not present on disk.")

        # 1. VerifierAgent with screenshot_path
        verifier = VerifierAgent()
        ver_res, ver_msg = verifier.verify_metric(
            lb_name="blocking-waf-https",
            screenshot_path=str(sample_screenshot),
            open_browser_on_login=False,
            interactive_cli=False,
        )
        self.assertFalse(ver_res.needs_human_login)
        self.assertTrue(ver_res.verified)
        self.assertEqual(ver_res.metric_value, 3.0)
        self.assertIn("3 requests", ver_res.usage_column_text)
        self.assertIn("Browser Page Screenshot", ver_msg.bullets[2])

        # 2. OrchestratorAgent resume_after_human_login with screenshot
        orchestrator = OrchestratorAgent(run_traffic_function_url="", verifier_function_url="")
        bundle = orchestrator.create_bundle(lb_name="blocking-waf-https", num_requests=5)
        bundle.needs_human_login = True
        bundle.status = "NEEDS_HUMAN_LOGIN"

        resumed_bundle = orchestrator.resume_after_human_login(
            bundle=bundle,
            screenshot_path=str(sample_screenshot),
        )
        self.assertFalse(resumed_bundle.needs_human_login)
        self.assertTrue(resumed_bundle.verified_non_zero)
        self.assertEqual(resumed_bundle.status, "VERIFIED_NON_ZERO")
        self.assertIsNotNone(resumed_bundle.verification_result)
        self.assertEqual(resumed_bundle.verification_result.metric_value, 3.0)

    def test_browser_executable_and_automated_verification(self) -> None:
        exec_path = find_browser_executable()
        # On macOS or Linux with Chrome installed, verify detection succeeds
        if exec_path:
            self.assertTrue(Path(exec_path).exists())

        # Test verify_with_browser on mock HTML page without requiring login
        mock_html = """
        <html><body>
        <div class="table">
            <div class="row">
                <span>ves-io-http-loadbalancer-blocking-waf-https</span>
                <span>dg-telemetry</span>
                <span>3 requests</span>
            </div>
        </div>
        </body></html>
        """
        with patch("src.verifier_agent.take_browser_screenshot", return_value=None), \
             patch("src.verifier_agent.verify_with_playwright", return_value=None), \
             patch("src.verifier_agent._verify_with_headless_chromium_cli") as mock_cli:
            from src.verifier_agent import MetricVerificationResult
            mock_cli.return_value = MetricVerificationResult(
                lb_name="blocking-waf-https",
                usage_url=VOLTERRA_USAGE_DETAILS_URL,
                http_status=200,
                needs_human_login=False,
                found=True,
                metric_value=3.0,
                is_non_zero=True,
                verified=True,
                status="VERIFIED_NON_ZERO",
                usage_column_text="3 requests",
                summary="VERIFIED: Browser automated verification succeeded without human login.",
            )
            res = verify_with_browser("blocking-waf-https")
            self.assertIsNotNone(res)
            assert res is not None
            self.assertFalse(res.needs_human_login)
            self.assertTrue(res.verified)
            self.assertEqual(res.metric_value, 3.0)


if __name__ == "__main__":
    unittest.main()
