"""Agent Skill: `run_traffic`

Executes 100 real `curl` requests against a target Load Balancer name or URL and
returns actual HTTP status codes, latencies, and curl execution logs.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from src.config import settings
from src.models import BaseModel, Field


class TrafficRunResult(BaseModel):
    """Real execution result emitted by the `run_traffic` skill."""

    skill_name: str = "run_traffic"
    lb_name: str
    target_url: str
    requested_count: int = 100
    requests_sent: int = 100
    successful_http_responses: int = 0
    failed_connections: int = 0
    status_code_counts: Dict[str, int] = Field(default_factory=dict)
    avg_latency_ms: float = 0.0
    total_duration_sec: float = 0.0
    curl_command_template: str = ""
    timestamp: str = ""
    sample_logs: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


def resolve_lb_target_url(lb_name: str, explicit_url: Optional[str] = None) -> str:
    """Resolve a load balancer name into an actual curlable HTTPS/HTTP URL."""
    if explicit_url and explicit_url.strip():
        url = explicit_url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            url = f"https://{url}"
        return url

    cleaned = lb_name.strip()
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned

    default_domain = getattr(settings, "volterra_lb_domain", "testing.crt.volterra.us")
    if "." in cleaned:
        return f"https://{cleaned}"
    return f"https://{cleaned}.{default_domain}"


def _single_curl_request(idx: int, target_url: str, timeout_sec: int = 5) -> Tuple[int, str, float, str]:
    """Execute a single real curl command and return (index, http_code, latency_ms, stderr)."""
    start = time.perf_counter()
    cmd = [
        "curl",
        "-k",
        "-s",
        "-S",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--connect-timeout",
        str(timeout_sec),
        "--max-time",
        str(timeout_sec + 2),
        target_url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 3,
            check=False,
        )
        code = (proc.stdout or "").strip() or "000"
        err = (proc.stderr or "").strip()
    except Exception as exc:
        code = "000"
        err = str(exc)
    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
    return idx, code, elapsed_ms, err


def run_traffic(
    lb_name: str,
    num_requests: int = 100,
    target_url: Optional[str] = None,
    concurrency: int = 20,
    timeout_sec: int = 5,
) -> TrafficRunResult:
    """Skill `run_traffic`: Execute `num_requests` (default 100) real curl requests on `lb_name`."""
    clean_lb_name = lb_name.strip()
    resolved_url = resolve_lb_target_url(clean_lb_name, explicit_url=target_url)
    curl_template = (
        f'for i in $(seq 1 {num_requests}); do '
        f'curl -k -s -S -o /dev/null -w "%{{http_code}}\\n" "{resolved_url}"; done'
    )

    start_all = time.perf_counter()
    status_counts: Dict[str, int] = {}
    latencies: List[float] = []
    sample_logs: List[str] = []
    errors: List[str] = []
    live_success = 0
    failed = 0

    if num_requests > 0:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, num_requests))) as pool:
            futures = [
                pool.submit(_single_curl_request, i, resolved_url, timeout_sec)
                for i in range(1, num_requests + 1)
            ]
            for fut in as_completed(futures):
                idx, code, latency_ms, err = fut.result()
                latencies.append(latency_ms)
                status_counts[code] = status_counts.get(code, 0) + 1
                if code != "000":
                    live_success += 1
                else:
                    failed += 1
                    if err and len(errors) < 5 and err not in errors:
                        errors.append(err)
                if len(sample_logs) < 10:
                    err_suffix = f" [error: {err}]" if err and code == "000" else ""
                    sample_logs.append(
                        f"[Req #{idx:03d}/{num_requests}] curl -ksS {resolved_url} -> HTTP {code} ({latency_ms:.1f}ms){err_suffix}"
                    )

    total_duration = round(time.perf_counter() - start_all, 2)
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return TrafficRunResult(
        skill_name="run_traffic",
        lb_name=clean_lb_name,
        target_url=resolved_url,
        requested_count=num_requests,
        requests_sent=num_requests,
        successful_http_responses=live_success,
        failed_connections=failed,
        status_code_counts=status_counts,
        avg_latency_ms=avg_latency,
        total_duration_sec=total_duration,
        curl_command_template=curl_template,
        timestamp=now_iso,
        sample_logs=sorted(sample_logs),
        errors=errors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real curl traffic skill (100 requests)")
    parser.add_argument("--lb-name", required=True, help="Load Balancer name")
    parser.add_argument("--requests", type=int, default=100, help="Number of curl requests (default: 100)")
    parser.add_argument("--target-url", default="", help="Optional explicit target URL")
    args = parser.parse_args()

    result = run_traffic(
        lb_name=args.lb_name,
        num_requests=args.requests,
        target_url=args.target_url or None,
    )
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
