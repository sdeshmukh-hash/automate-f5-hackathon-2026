"""Verifier Agent (Sub-Agent 1) with tool `verify_metric` (100% Real URL + Human Login Hand-Off).

Fetches live usage data from:
https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400

Once human login is complete, searches for an entry of the form `ves-io-http-loadbalancer-<LOADBALANCER_NAME>`
in the search bar / table, and fetches the non-zero value under the `Usage` column (e.g. "3 requests" -> 3.0).
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import webbrowser

logger = logging.getLogger(__name__)

from src.config import settings
from src.models import AgentMessage, BaseModel, Field

VOLTERRA_USAGE_DETAILS_URL = (
    "https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/"
    "RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400"
)

SESSION_CACHE_FILE = Path(__file__).resolve().parent.parent / ".session_auth.json"


class MetricVerificationResult(BaseModel):
    """Structured output from the real `verify_metric` tool."""

    tool_name: str = "verify_metric"
    agent_name: str = "Verifier Agent (Sub-Agent 1)"
    lb_name: str
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL
    metric_category: str = "RE – Requests – Essentials – Millions (dateTime=86400)"
    http_status: int = 0
    effective_url: str = ""
    needs_human_login: bool = False
    human_handoff_reason: str = ""
    human_action_instructions: List[str] = Field(default_factory=list)
    browser_opened: bool = False
    screenshot_captured: bool = False
    screenshot_path: Optional[str] = None
    usage_column_text: str = ""
    found: bool = False
    metric_value: float = 0.0
    is_non_zero: bool = False
    verified: bool = False
    status: str = "PENDING"
    matched_record: Dict[str, Any] = Field(default_factory=dict)
    raw_response_excerpt: str = ""
    timestamp: str = ""
    summary: str = ""


def get_lb_name_candidates(lb_name: str) -> List[str]:
    """Return prioritized candidate search strings for the Load Balancer in the Volterra billing table.

    The Volterra billing table displays object names of the form `ves-io-http-loadbalancer-<LOADBALANCER_NAME>`.
    If the caller provides either bare `<LOADBALANCER_NAME>`, `ves-io-http-loadbalancer-<LOADBALANCER_NAME>`,
    or `ves-io-http-lb-<LOADBALANCER_NAME>`, this function generates the candidate forms with the canonical
    `ves-io-http-loadbalancer-...` prioritized first.
    """
    clean = lb_name.strip()
    if not clean:
        return []

    candidates: List[str] = []

    if clean.startswith("ves-io-http-loadbalancer-"):
        bare = clean[len("ves-io-http-loadbalancer-") :]
        candidates.append(clean)
        if bare:
            candidates.append(bare)
            candidates.append(f"ves-io-http-lb-{bare}")
    elif clean.startswith("ves-io-http-lb-"):
        bare = clean[len("ves-io-http-lb-") :]
        candidates.append(f"ves-io-http-loadbalancer-{bare}")
        candidates.append(clean)
        if bare:
            candidates.append(bare)
    else:
        candidates.append(f"ves-io-http-loadbalancer-{clean}")
        candidates.append(clean)
        candidates.append(f"ves-io-http-lb-{clean}")

    # Deduplicate while preserving order
    seen = set()
    unique: List[str] = []
    for c in candidates:
        low = c.lower()
        if low not in seen:
            seen.add(low)
            unique.append(c)
    return unique


def _parse_numeric_usage(val: Any) -> Optional[float]:
    """Parse numeric usage from int, float, or string formats like '3 requests', '3.5 reqs', '3'."""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if isinstance(val, str):
        cleaned = val.strip().replace(",", "")
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:requests?|reqs?|million)?", cleaned, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def load_saved_session_auth() -> Dict[str, str]:
    """Load persisted human-provided authentication headers/cookies if available."""
    if SESSION_CACHE_FILE.exists():
        try:
            return json.loads(SESSION_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_session_auth(
    auth_token: Optional[str] = None,
    cookie: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    """Save human-provided authentication credentials for subsequent requests."""
    current = load_saved_session_auth()
    if auth_token is not None and auth_token.strip():
        current["auth_token"] = auth_token.strip()
    if cookie is not None and cookie.strip():
        current["cookie"] = cookie.strip()
    if extra_headers:
        headers = current.get("extra_headers", {})
        headers.update(extra_headers)
        current["extra_headers"] = headers
    SESSION_CACHE_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")


def clear_saved_session_auth() -> None:
    """Delete cached session credentials."""
    if SESSION_CACHE_FILE.exists():
        SESSION_CACHE_FILE.unlink()


def parse_human_auth_input(raw_input: str) -> Tuple[Optional[str], Optional[str], Dict[str, str], Optional[str]]:
    """Parse human-provided input: can be a 'Copy as cURL' command, Cookie string, APIToken, or raw page content.

    Returns (auth_token, cookie, extra_headers, direct_page_payload).
    """
    text = raw_input.strip()
    if not text:
        return None, None, {}, None

    auth_token: Optional[str] = None
    cookie: Optional[str] = None
    extra_headers: Dict[str, str] = {}

    # Case 1: Human pasted a "Copy as cURL" command from browser DevTools
    if text.startswith("curl "):
        for match in re.finditer(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", text):
            hdr = match.group(1)
            if ":" in hdr:
                k, v = hdr.split(":", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean.lower() == "cookie":
                    cookie = v_clean
                elif k_clean.lower() == "authorization":
                    auth_token = v_clean
                else:
                    extra_headers[k_clean] = v_clean
        b_match = re.search(r"(?:-b|--cookie)\s+['\"]([^'\"]+)['\"]", text)
        if b_match:
            cookie = b_match.group(1).strip()
        return auth_token, cookie, extra_headers, None

    # Case 2: Human pasted JSON or table text copied directly from the logged-in browser page
    if text.startswith("{") or text.startswith("[") or "\t" in text or "\n" in text:
        return None, None, {}, text

    # Case 3: Human pasted a Cookie header or API token directly
    if "=" in text and (";" in text or "session" in text.lower() or "token" in text.lower()):
        return None, text, {}, None

    return text, None, {}, None


def _extract_lb_value_from_json_recursive(
    data: Any,
    candidates_lower: List[str],
) -> Optional[Tuple[float, Dict[str, Any]]]:
    """Recursively search JSON dictionary or list for any candidate name and extract numeric usage value."""
    if isinstance(data, dict):
        dict_str_values = [str(v).lower() for v in data.values() if isinstance(v, (str, int, float))]
        dict_keys = [str(k).lower() for k in data.keys()]

        is_match = any(cand in s for cand in candidates_lower for s in dict_str_values) or any(
            cand in k for cand in candidates_lower for k in dict_keys
        )

        if is_match:
            # Check standard usage keys first (case-insensitive)
            usage_keys_priority = (
                "usage",
                "value",
                "count",
                "metric_value",
                "request_count",
                "requests",
                "total",
                "amount",
                "public_count",
            )
            for prio_key in usage_keys_priority:
                for k, v in data.items():
                    if k.lower() == prio_key:
                        parsed = _parse_numeric_usage(v)
                        if parsed is not None:
                            return parsed, data

            # Check if any key contains candidate and has numeric value
            for k, v in data.items():
                if any(cand in str(k).lower() for cand in candidates_lower):
                    parsed = _parse_numeric_usage(v)
                    if parsed is not None:
                        return parsed, {k: v}

            # Check any other non-metadata numeric field in the matching row/object
            for k, v in data.items():
                k_low = k.lower()
                if k_low not in (
                    "id",
                    "timestamp",
                    "datetime",
                    "date",
                    "namespace",
                    "tenant",
                    "version",
                    "name",
                    "object_name",
                    "object name",
                ):
                    parsed = _parse_numeric_usage(v)
                    if parsed is not None:
                        return parsed, data

        for v in data.values():
            res = _extract_lb_value_from_json_recursive(v, candidates_lower)
            if res is not None:
                return res

    elif isinstance(data, list):
        for item in data:
            res = _extract_lb_value_from_json_recursive(item, candidates_lower)
            if res is not None:
                return res

    return None


def extract_lb_value_from_json(data: Any, lb_name: str) -> Optional[Tuple[float, Dict[str, Any]]]:
    """Recursively search real JSON response data for `lb_name` (or `ves-io-http-loadbalancer-<lb_name>`)
    and extract its numeric metric value under the Usage / value fields.
    """
    candidates = [c.lower() for c in get_lb_name_candidates(lb_name)]
    if not candidates:
        candidates = [lb_name.strip().lower()]

    return _extract_lb_value_from_json_recursive(data, candidates)


def extract_lb_value_from_text(body: str, lb_name: str) -> Optional[Tuple[float, Dict[str, Any]]]:
    """Search HTML/table/text response for `lb_name` (or `ves-io-http-loadbalancer-<lb_name>`)
    and extract its numeric metric value under the Usage column.
    """
    candidates = get_lb_name_candidates(lb_name)
    if not candidates:
        candidates = [lb_name.strip()]

    for candidate in candidates:
        cand_esc = re.escape(candidate)

        # 1. Match "<candidate> ... <num> requests" (e.g. Volterra billing table row "ves-io-http-loadbalancer-... dg-telemetry 3 requests")
        pattern_req = re.compile(
            cand_esc + r"[\s\S]{1,300}?([0-9]+(?:\.[0-9]+)?)\s*requests?",
            re.IGNORECASE,
        )
        m = pattern_req.search(body)
        if m:
            val = float(m.group(1))
            return val, {
                "matched_candidate": candidate,
                "matched_snippet": m.group(0)[:200],
                "extracted_value": val,
                "field": "Usage",
            }

        # 2. Match table columns delimited by tabs, pipes, spaces or commas (e.g. copied TSV/CSV from table)
        pattern_table = re.compile(
            cand_esc + r"(?:[ \t]*[|\t,][ \t]*[^\n|\t,]+){1,3}[ \t]*[|\t,][ \t]*([0-9]+(?:\.[0-9]+)?)(?:\s*requests?)?",
            re.IGNORECASE,
        )
        m2 = pattern_table.search(body)
        if m2:
            val = float(m2.group(1))
            return val, {
                "matched_candidate": candidate,
                "matched_snippet": m2.group(0)[:200],
                "extracted_value": val,
                "field": "Usage",
            }

        # 3. Match multi-line copy (when copying columns as stacked lines from browser)
        pattern_multiline = re.compile(
            cand_esc + r"(?:\n[^\n]+){0,3}\n[^\n]*?([0-9]+(?:\.[0-9]+)?)\s*(?:requests?)?",
            re.IGNORECASE,
        )
        m3 = pattern_multiline.search(body)
        if m3:
            val = float(m3.group(1))
            return val, {
                "matched_candidate": candidate,
                "matched_snippet": m3.group(0)[:200],
                "extracted_value": val,
                "field": "Usage",
            }

        # 4. Proximity fallback: candidate followed by non-digits up to 160 chars and a number
        pattern_proximity = re.compile(
            cand_esc + r"[^0-9\n]{1,160}([0-9]+(?:\.[0-9]+)?)",
            re.IGNORECASE,
        )
        m4 = pattern_proximity.search(body)
        if m4:
            val = float(m4.group(1))
            return val, {
                "matched_candidate": candidate,
                "matched_snippet": m4.group(0)[:200],
                "extracted_value": val,
                "field": "Usage",
            }

    return None


def focus_active_volterra_browser_tab(usage_url: str = VOLTERRA_USAGE_DETAILS_URL) -> str:
    """On macOS, brings Google Chrome to the front and switches to or opens the Volterra billing tab.

    Returns 'activated' if an existing billing/usage tab was focused, 'opened' if a new tab was opened,
    or '' if tab management is unavailable.
    """
    if sys.platform != "darwin":
        return ""
    ascript = f"""
    tell application "Google Chrome"
        repeat with w in windows
            set tabIdx to 0
            repeat with t in tabs of w
                set tabIdx to tabIdx + 1
                if (URL of t) contains "billing/usage" or (URL of t) contains "usage_details" then
                    set active tab index of w to tabIdx
                    set index of w to 1
                    activate
                    return "activated"
                end if
            end repeat
        end repeat
        if (count of windows) > 0 then
            tell front window to make new tab with properties {{URL:"{usage_url}"}}
        else
            open location "{usage_url}"
        end if
        activate
        return "opened"
    end tell
    """
    try:
        proc = subprocess.run(["osascript", "-e", ascript], capture_output=True, text=True, timeout=6)
        if proc.returncode == 0:
            out = (proc.stdout or "").strip()
            return out or "activated"
    except Exception:
        pass

    # Fallback to macOS `open -a "Google Chrome" <url>` if AppleScript failed
    try:
        proc = subprocess.run(["/usr/bin/open", "-a", "Google Chrome", usage_url], capture_output=True, timeout=5)
        if proc.returncode == 0:
            return "opened"
    except Exception:
        pass
    return ""


def take_browser_screenshot(
    output_path: Optional[str] = None,
    focus_tab: bool = True,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
) -> Optional[str]:
    """Capture a screenshot of the user's browser/screen using OS-level facilities.

    On macOS, targets the Google Chrome / Chromium window directly via window ID and
    invokes `/usr/sbin/screencapture -l<wid> -o -x <path>`, falling back to full-screen capture.
    On Linux, attempts `scrot` or ImageMagick `import`.
    Returns the path to the saved PNG image or None if capture fails.
    """
    if not output_path:
        tmp_dir = Path("/tmp/lb_verifier_screenshots")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        output_path = str(tmp_dir / f"browser_screenshot_{ts}.png")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 1. macOS native window-targeted screencapture
    if sys.platform == "darwin":
        screencapture_bin = "/usr/sbin/screencapture"
        if Path(screencapture_bin).exists():
            if focus_tab:
                tab_action = focus_active_volterra_browser_tab(usage_url=usage_url)
                if tab_action == "opened":
                    time.sleep(5.0)
                else:
                    time.sleep(1.0)

            # Try to capture specifically the Google Chrome window by window ID
            find_win_bin = Path(__file__).resolve().parent / "bin" / "find_window"
            wid_str = ""
            if find_win_bin.exists():
                try:
                    p = subprocess.run([str(find_win_bin)], capture_output=True, text=True, timeout=3)
                    wid_str = p.stdout.strip()
                except Exception:
                    pass

            if wid_str and wid_str.isdigit():
                try:
                    res = subprocess.run(
                        [screencapture_bin, f"-l{wid_str}", "-o", "-x", str(target)],
                        capture_output=True,
                        timeout=10,
                    )
                    if res.returncode == 0 and target.exists() and target.stat().st_size > 0:
                        return str(target)
                except Exception:
                    pass

            # Full-screen capture fallback
            try:
                res = subprocess.run([screencapture_bin, "-x", str(target)], capture_output=True, timeout=10)
                if res.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    return str(target)
            except Exception:
                pass

    # 2. Linux scrot / import
    for tool in ("scrot", "import"):
        try:
            if tool == "scrot":
                cmd = [tool, str(target)]
            else:
                cmd = [tool, "-window", "root", str(target)]
            res = subprocess.run(cmd, capture_output=True, timeout=10)
            if res.returncode == 0 and target.exists() and target.stat().st_size > 0:
                return str(target)
        except Exception:
            continue

    return None


def ocr_screenshot_image(image_path: str) -> str:
    """Extract text from a screenshot image using native macOS Vision OCR or local tesseract."""
    # 1. macOS native compiled Vision OCR binary
    mac_ocr_bin = Path(__file__).resolve().parent / "bin" / "mac_ocr"
    if mac_ocr_bin.exists():
        try:
            proc = subprocess.run([str(mac_ocr_bin), image_path], capture_output=True, text=True, timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass

    # 2. Tesseract CLI fallback
    try:
        proc = subprocess.run(["tesseract", image_path, "stdout"], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass

    return ""


def verify_screenshot(
    screenshot: Any,
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
) -> MetricVerificationResult:
    """Analyze a browser screenshot of the Volterra Billing Usage details page.

    Locates the table row for `ves-io-http-loadbalancer-<LOADBALANCER_NAME>` (or bare `<LOADBALANCER_NAME>`),
    searches the column 'Usage', and fetches the non-zero value (e.g. '3 requests' -> 3.0).
    If it is non-zero, passes verification.
    """
    clean_lb = lb_name.strip()
    candidates = get_lb_name_candidates(clean_lb)
    primary_search_key = candidates[0] if candidates else clean_lb
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    temp_path: Optional[str] = None
    image_bytes: bytes = b""

    if isinstance(screenshot, (str, Path)):
        p = Path(screenshot)
        if p.exists():
            temp_path = str(p)
            try:
                image_bytes = p.read_bytes()
            except Exception:
                pass
    elif isinstance(screenshot, bytes):
        image_bytes = screenshot
        tmp_dir = Path("/tmp/lb_verifier_screenshots")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        temp_file = tmp_dir / f"screenshot_{ts}.png"
        temp_file.write_bytes(screenshot)
        temp_path = str(temp_file)

    found = False
    metric_value = 0.0
    usage_text = ""
    matched_record: Dict[str, Any] = {}
    ocr_raw_text = ""

    # 1. Local Vision OCR on the screenshot
    if temp_path:
        ocr_raw_text = ocr_screenshot_image(temp_path)
        if ocr_raw_text:
            text_res = extract_lb_value_from_text(ocr_raw_text, clean_lb)
            if text_res is not None:
                metric_value, matched_record = text_res
                found = True
                usage_text = f"{metric_value:g} requests"

    # 2. If OCR not available or failed, check if image_bytes has text fallback or sample table
    if not found and image_bytes:
        try:
            body_str = image_bytes.decode("utf-8", errors="ignore")
            text_res = extract_lb_value_from_text(body_str, clean_lb)
            if text_res is not None:
                metric_value, matched_record = text_res
                found = True
                usage_text = f"{metric_value:g} requests"
        except Exception:
            pass

    is_non_zero = found and (metric_value > 0)
    verified = is_non_zero

    if verified:
        status = "VERIFIED_NON_ZERO"
        summary = (
            f"VERIFIED: Screenshot captured of browser page for Load Balancer '{primary_search_key}'. "
            f"Found in table under column 'Usage' with non-zero value = {metric_value:g} (> 0)."
        )
    elif found:
        status = "FAILED_ZERO_VALUE"
        summary = (
            f"FAILED: Screenshot captured of browser page for Load Balancer '{primary_search_key}'. "
            f"Found in table, but 'Usage' column value is {metric_value:g} (expected non-zero > 0)."
        )
    else:
        status = "FAILED_NOT_FOUND"
        summary = (
            f"FAILED: Load Balancer '{primary_search_key}' was not found in the browser screenshot."
        )

    return MetricVerificationResult(
        lb_name=clean_lb,
        usage_url=usage_url,
        http_status=200,
        needs_human_login=False,
        screenshot_captured=bool(temp_path or image_bytes),
        screenshot_path=temp_path,
        usage_column_text=usage_text,
        found=found,
        metric_value=metric_value,
        is_non_zero=is_non_zero,
        verified=verified,
        status=status,
        matched_record=matched_record,
        raw_response_excerpt=ocr_raw_text[:500] if ocr_raw_text else "",
        timestamp=now_iso,
        summary=summary,
    )


def find_browser_executable() -> Optional[str]:
    """Find installed Chromium or Google Chrome executable on the host system."""
    env_browser = os.getenv("CHROME_BIN") or os.getenv("CHROMIUM_PATH")
    if env_browser and Path(env_browser).exists():
        return env_browser

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/local/bin/chromium",
        "/usr/local/bin/google-chrome",
    ]
    for cand in candidates:
        if Path(cand).exists():
            return cand

    for bin_name in ["google-chrome", "chromium", "chromium-browser"]:
        found = shutil.which(bin_name)
        if found:
            return found

    return None


def verify_with_playwright(
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    headless: bool = True,
    timeout_sec: int = 25,
) -> Optional[MetricVerificationResult]:
    """Automate navigation to Volterra usage page via Playwright with persistent context.

    Only prompts human if actual sign-in/SSO redirect or form is detected.
    Otherwise automatically types in the search bar, locates the row, and verifies the Usage column!
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            exec_path = find_browser_executable()
            user_data_dir = Path("/tmp/lb_verifier_playwright_profile")
            user_data_dir.mkdir(parents=True, exist_ok=True)

            context = p.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=headless,
                executable_path=exec_path if (exec_path and Path(exec_path).exists()) else None,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--window-size=1600,1000",
                ],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(usage_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass

                clean_lb = lb_name.strip()
                candidates = get_lb_name_candidates(clean_lb)
                primary_search_key = candidates[0] if candidates else clean_lb
                now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                # Detect if login/SSO is required
                curr_url = page.url.lower()
                is_login = any(k in curr_url for k in ["/login", "/signin", "okta", "oauth", "saml", "idp", "auth0"])
                if not is_login:
                    try:
                        if page.locator("input[type='password']").count() > 0:
                            is_login = True
                    except Exception:
                        pass

                tmp_dir = Path("/tmp/lb_verifier_screenshots")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                shot_path = str(tmp_dir / f"playwright_{int(datetime.now().timestamp())}.png")
                page.screenshot(path=shot_path)

                if is_login:
                    # ONLY hand off to human if sign-in is actually needed!
                    return MetricVerificationResult(
                        lb_name=clean_lb,
                        usage_url=usage_url,
                        effective_url=page.url,
                        http_status=200,
                        needs_human_login=True,
                        screenshot_captured=True,
                        screenshot_path=shot_path,
                        human_handoff_reason=f"Authentication required: Page redirected to login ({page.url}).",
                        human_action_instructions=[
                            f"1. Log in to Volterra in the opened browser tab: {page.url}",
                            f"2. Once logged in, in the search bar, search for: '{primary_search_key}'",
                            f"3. Click '📸 Take Screenshot of Browser & Verify' to complete verification.",
                        ],
                        status="NEEDS_HUMAN_LOGIN",
                        timestamp=now_iso,
                        summary=f"SIGN-IN REQUIRED: Browser redirected to login ({page.url}).",
                    )

                # Not a login page -> Attempt automated search and table scrape
                for sel in [
                    "input[placeholder*='Search' i]",
                    "input[placeholder*='search' i]",
                    "input[type='search']",
                    "input[aria-label*='Search' i]",
                    ".search-input input",
                ]:
                    try:
                        box = page.locator(sel).first
                        if box.is_visible():
                            box.fill(primary_search_key)
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(2000)
                            page.screenshot(path=shot_path)
                            break
                    except Exception:
                        continue

                # Run verify_screenshot on the filtered view
                res = verify_screenshot(shot_path, clean_lb, usage_url)
                if res.found:
                    return res

                # Check page inner text as well
                body_text = page.locator("body").inner_text()
                text_res = extract_lb_value_from_text(body_text, clean_lb)
                if text_res is not None:
                    val, matched = text_res
                    is_non_zero = val > 0
                    return MetricVerificationResult(
                        lb_name=clean_lb,
                        usage_url=usage_url,
                        effective_url=page.url,
                        http_status=200,
                        needs_human_login=False,
                        screenshot_captured=True,
                        screenshot_path=shot_path,
                        usage_column_text=f"{val:g} requests",
                        found=True,
                        metric_value=val,
                        is_non_zero=is_non_zero,
                        verified=is_non_zero,
                        status="VERIFIED_NON_ZERO" if is_non_zero else "FAILED_ZERO_VALUE",
                        matched_record=matched,
                        timestamp=now_iso,
                        summary=f"VERIFIED: Playwright automated browser navigated to usage page without login. Found '{primary_search_key}' with Usage = {val:g} (> 0).",
                    )

                return res
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Playwright verification error: %s", exc)

    return None


def _verify_with_headless_chromium_cli(
    exec_path: str,
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    timeout_sec: int = 20,
) -> Optional[MetricVerificationResult]:
    """Execute direct headless Chromium CLI to dump DOM and capture screenshot without Python Playwright."""
    clean_lb = lb_name.strip()
    candidates = get_lb_name_candidates(clean_lb)
    primary_search_key = candidates[0] if candidates else clean_lb
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tmp_dir = Path("/tmp/lb_verifier_chromium")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    shot_path = tmp_dir / f"screen_{int(datetime.now().timestamp())}.png"
    user_data_dir = tmp_dir / "user_data"

    cmd = [
        exec_path,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--user-data-dir={user_data_dir}",
        "--window-size=1600,1000",
        f"--screenshot={shot_path}",
        "--dump-dom",
        "--virtual-time-budget=4000",
        usage_url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        rendered_dom = proc.stdout or ""

        # Check if login is needed
        lower_dom = rendered_dom.lower()
        is_login = any(
            k in lower_dom
            for k in [
                'type="password"',
                'name="password"',
                "okta-sign-in",
                "sign in to your account",
                "enter your credentials",
                'id="okta-signin-submit"',
            ]
        )

        if is_login:
            return MetricVerificationResult(
                lb_name=clean_lb,
                usage_url=usage_url,
                http_status=200,
                needs_human_login=True,
                screenshot_captured=shot_path.exists() and shot_path.stat().st_size > 0,
                screenshot_path=str(shot_path) if shot_path.exists() else None,
                human_handoff_reason="Login/SSO form detected in rendered browser DOM.",
                human_action_instructions=[
                    f"1. Log in to Volterra in the opened browser tab: {usage_url}",
                    f"2. Once logged in, in the search bar, search for: '{primary_search_key}'",
                    f"3. Click '📸 Take Screenshot of Browser & Verify' to complete verification.",
                ],
                status="NEEDS_HUMAN_LOGIN",
                timestamp=now_iso,
                summary=f"SIGN-IN REQUIRED: Login form detected on {usage_url}.",
            )

        # Login is NOT needed! Check if screenshot or rendered DOM contains the table and usage data
        if shot_path.exists() and shot_path.stat().st_size > 0:
            res = verify_screenshot(str(shot_path), clean_lb, usage_url)
            if res.found:
                return res

        # Also parse rendered DOM directly
        text_res = extract_lb_value_from_text(rendered_dom, clean_lb)
        if text_res is not None:
            val, matched = text_res
            is_non_zero = val > 0
            return MetricVerificationResult(
                lb_name=clean_lb,
                usage_url=usage_url,
                http_status=200,
                needs_human_login=False,
                screenshot_captured=shot_path.exists(),
                screenshot_path=str(shot_path) if shot_path.exists() else None,
                usage_column_text=f"{val:g} requests",
                found=True,
                metric_value=val,
                is_non_zero=is_non_zero,
                verified=is_non_zero,
                status="VERIFIED_NON_ZERO" if is_non_zero else "FAILED_ZERO_VALUE",
                matched_record=matched,
                timestamp=now_iso,
                summary=(
                    f"VERIFIED: Chromium rendered page without sign-in required. "
                    f"Found '{primary_search_key}' with Usage = {val:g} (> 0)."
                ),
            )
    except Exception as exc:
        logger.warning("Headless Chromium CLI execution error: %s", exc)

    return None


def _capture_and_verify_active_browser(
    clean_lb: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    max_attempts: int = 4,
) -> Optional[MetricVerificationResult]:
    """Focus or open the Volterra usage tab in Chrome, capture window screenshot(s), and verify via OCR.

    If a newly opened tab is still loading the table on the first screenshot, retries up to `max_attempts`.
    """
    last_res: Optional[MetricVerificationResult] = None
    for attempt in range(max_attempts):
        shot_path = take_browser_screenshot(
            focus_tab=(attempt == 0),
            usage_url=usage_url,
        )
        if not (shot_path and Path(shot_path).exists() and Path(shot_path).stat().st_size > 0):
            break
        res = verify_screenshot(shot_path, clean_lb, usage_url)
        last_res = res
        if res.found:
            logger.info(
                "Successfully verified metric from active browser window (attempt %d): %s requests",
                attempt + 1,
                res.metric_value,
            )
            return res
        # If OCR text indicates a login/sign-in screen, stop retrying
        lower_ocr = (res.raw_response_excerpt or "").lower()
        if any(k in lower_ocr for k in ("sign in", "password", "okta", "enter your credentials")):
            break
        if attempt + 1 < max_attempts:
            time.sleep(2.5)
    return last_res


def verify_with_browser(
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    headless: bool = True,
    timeout_sec: int = 25,
) -> Optional[MetricVerificationResult]:
    """Automate navigation and verification using active Chrome session, Playwright, or Chromium.

    Only involves the human if an actual login/sign-in page or password field is detected.
    """
    clean_lb = lb_name.strip()

    # 1. First, check if the user has Google Chrome on macOS (opens new tab if usage tab is not open)
    try:
        res = _capture_and_verify_active_browser(clean_lb, usage_url=usage_url)
        if res is not None and res.found:
            return res
    except Exception as exc:
        logger.debug("Active window screenshot check failed or skipped: %s", exc)

    # 2. Try Playwright Python API
    res = verify_with_playwright(
        lb_name=lb_name,
        usage_url=usage_url,
        headless=headless,
        timeout_sec=timeout_sec,
    )
    if res is not None:
        return res

    # 3. Try native headless Chromium CLI fallback
    exec_path = find_browser_executable()
    if exec_path:
        return _verify_with_headless_chromium_cli(
            exec_path=exec_path,
            lb_name=lb_name,
            usage_url=usage_url,
            timeout_sec=timeout_sec,
        )

    return None


def _fetch_volterra_usage_url(
    usage_url: str,
    auth_token: Optional[str] = None,
    cookie: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout_sec: int = 10,
) -> Tuple[int, str, str, str]:
    """Perform a real HTTP GET via curl against `usage_url`.

    Returns (http_status, effective_url, body, stderr).
    """
    saved = load_saved_session_auth()
    token = auth_token or saved.get("auth_token") or getattr(settings, "volterra_api_token", "")
    session_cookie = cookie or saved.get("cookie") or getattr(settings, "volterra_cookie", "")
    merged_headers: Dict[str, str] = {}
    if saved.get("extra_headers"):
        merged_headers.update(saved["extra_headers"])
    if extra_headers:
        merged_headers.update(extra_headers)

    cmd = [
        "curl",
        "-k",
        "-s",
        "-S",
        "-L",
        "--connect-timeout",
        str(timeout_sec),
        "--max-time",
        str(timeout_sec + 5),
        "-H",
        "Accept: application/json, text/html, */*",
    ]

    if token:
        if token.lower().startswith("apitoken ") or token.lower().startswith("bearer "):
            cmd.extend(["-H", f"Authorization: {token}"])
        else:
            cmd.extend(["-H", f"Authorization: APIToken {token}"])

    if session_cookie:
        cmd.extend(["-H", f"Cookie: {session_cookie}"])

    for hk, hv in merged_headers.items():
        cmd.extend(["-H", f"{hk}: {hv}"])

    cmd.extend(
        [
            "-w",
            "\n__CURL_META__:%{http_code}|%{url_effective}",
            usage_url,
        ]
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 7,
            check=False,
        )
        output = proc.stdout or ""
        err = (proc.stderr or "").strip()
        if "__CURL_META__:" in output:
            body, meta_part = output.rsplit("__CURL_META__:", 1)
            parts = meta_part.strip().split("|", 1)
            status_code = int(parts[0].strip() or "0")
            eff_url = parts[1].strip() if len(parts) > 1 else usage_url
            return status_code, eff_url, body.strip(), err
        return 0, usage_url, output.strip(), err
    except Exception as exc:
        return 0, usage_url, "", str(exc)


def _detect_login_required(
    http_status: int,
    effective_url: str,
    body: str,
    lb_name: str,
) -> Tuple[bool, str]:
    """Determine if the response requires human login/authentication."""
    if http_status in (401, 403):
        return True, f"HTTP {http_status} Unauthorized/Forbidden returned by {effective_url}."

    lower_url = effective_url.lower()
    for login_marker in ("/login", "/signin", "okta", "sso", "oauth", "saml", "auth0", "/idp/"):
        if login_marker in lower_url:
            return True, f"Redirected to authentication/SSO login page ({effective_url})."

    lower_body = body.lower()
    if any(
        phrase in lower_body
        for phrase in (
            "sign in to your account",
            "enter your credentials",
            "okta-sign-in",
            "authentication required",
            "session expired",
            "please log in",
            'name="password"',
            'type="password"',
        )
    ):
        return True, "Response contains an HTML login form requiring interactive human authentication."

    # Volterra / F5 XC web URLs serve an Angular/React SPA shell (`<app-root>`) when fetched
    # without an authenticated API/session context or browser execution.
    if "<html" in lower_body or "<!doctype html" in lower_body:
        candidates = get_lb_name_candidates(lb_name)
        if not any(cand.lower() in lower_body for cand in candidates):
            return (
                True,
                "Target URL returned an unauthenticated Web Console HTML/SPA shell without billing usage data.",
            )

    return False, ""


def verify_metric(
    lb_name: str,
    usage_url: str = VOLTERRA_USAGE_DETAILS_URL,
    auth_token: Optional[str] = None,
    cookie: Optional[str] = None,
    human_provided_input: Optional[str] = None,
    open_browser_on_login: bool = True,
    interactive_cli: bool = False,
    screenshot_path: Optional[str] = None,
    screenshot_bytes: Optional[bytes] = None,
    auto_capture_screenshot: bool = False,
) -> MetricVerificationResult:
    """Tool `verify_metric`: Fetch usage data from the actual Volterra URL or browser screenshot,
    search for `ves-io-http-loadbalancer-<LOADBALANCER_NAME>`, and verify that its value under
    the `Usage` column is non-zero (> 0).

    If login access is needed, hands control to the human (`needs_human_login=True`).
    When resumed with a browser screenshot, extracts text via Vision OCR and verifies the non-zero value.
    """
    clean_lb = lb_name.strip()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = get_lb_name_candidates(clean_lb)
    # Primary search key for the browser search bar in the billing table
    primary_search_key = candidates[0] if candidates else clean_lb

    # 1. Direct Screenshot Verification Mode
    if screenshot_path or screenshot_bytes:
        return verify_screenshot(
            screenshot=screenshot_path or screenshot_bytes,
            lb_name=clean_lb,
            usage_url=usage_url,
        )

    if auto_capture_screenshot:
        active_res = _capture_and_verify_active_browser(clean_lb, usage_url=usage_url)
        if active_res is not None and active_res.screenshot_captured:
            return active_res

    extra_headers: Dict[str, str] = {}
    direct_payload: Optional[str] = None

    if human_provided_input and human_provided_input.strip():
        parsed_token, parsed_cookie, parsed_headers, direct_payload = parse_human_auth_input(
            human_provided_input
        )
        if parsed_token:
            auth_token = parsed_token
        if parsed_cookie:
            cookie = parsed_cookie
        if parsed_headers:
            extra_headers.update(parsed_headers)
        if auth_token or cookie or extra_headers:
            save_session_auth(auth_token=auth_token, cookie=cookie, extra_headers=extra_headers)

    if auth_token or cookie:
        save_session_auth(auth_token=auth_token, cookie=cookie)

    # 2. Automated Browser (Playwright / Chromium) Verification
    # Opens the URL in a headless browser with JavaScript execution and session persistence.
    # ONLY hands off to human if an actual login/SSO redirect or form is detected.
    # If session is active or page loads, it types into the search bar, locates the row,
    # reads the Usage column, and completes verification with ZERO human hand-off!
    if not direct_payload and not auth_token and not cookie:
        browser_res = verify_with_browser(
            lb_name=clean_lb,
            usage_url=usage_url,
            headless=True,
            timeout_sec=20,
        )
        if browser_res is not None:
            return browser_res

    # If the human directly pasted the table/JSON data from their logged-in browser window:
    if direct_payload:
        http_status = 200
        effective_url = usage_url
        raw_body = direct_payload
        stderr = ""
    else:
        http_status, effective_url, raw_body, stderr = _fetch_volterra_usage_url(
            usage_url=usage_url,
            auth_token=auth_token,
            cookie=cookie,
            extra_headers=extra_headers,
        )

    # Check if network connection failed completely
    if http_status == 0 and not raw_body:
        return MetricVerificationResult(
            lb_name=clean_lb,
            usage_url=usage_url,
            http_status=0,
            effective_url=effective_url,
            needs_human_login=True,
            human_handoff_reason=(
                f"Could not reach {usage_url} directly via curl ({stderr or 'connection/TLS/VPN required'}). "
                "Handing control to human to open the URL in browser and verify."
            ),
            human_action_instructions=[
                f"1. Ensure you are connected to the network/VPN and log in at: {usage_url}",
                f"2. In the search bar on the usage page, search for: '{primary_search_key}'",
                f"3. Locate the row with Object Name '{primary_search_key}' so its 'Usage' column is visible.",
                "4. Click '📸 Take Screenshot of Browser & Verify' to let Verifier Agent read the Usage column automatically.",
            ],
            status="NEEDS_HUMAN_LOGIN",
            raw_response_excerpt=stderr[:400],
            timestamp=now_iso,
            summary=f"HUMAN ACTION REQUIRED: Unable to fetch {usage_url} without human network/login session ({stderr}).",
        )

    # Check if login / authentication is required
    if not direct_payload:
        login_needed, login_reason = _detect_login_required(
            http_status=http_status,
            effective_url=effective_url,
            body=raw_body,
            lb_name=clean_lb,
        )
        if login_needed:
            browser_opened = False
            if open_browser_on_login:
                try:
                    browser_opened = webbrowser.open(usage_url)
                except Exception:
                    browser_opened = False

            # If running in interactive CLI mode, pause and let the human log in right in the terminal!
            if interactive_cli and sys.stdin.isatty():
                print("\n" + "=" * 78)
                print("🛑 [HUMAN LOGIN HAND-OFF] Authentication required for Volterra Usage URL:")
                print(f"   URL: {usage_url}")
                print(f"   Reason: {login_reason}")
                print(f"   Search Bar Query: {primary_search_key}")
                print(f"   Target Column: Usage (under Object Name: {primary_search_key})")
                print("   The URL has been opened in your browser. Please log in.")
                print("   Press ENTER to take a browser screenshot and verify, or paste screenshot file path:")
                print("=" * 78)
                user_auth = input("Press ENTER to screenshot browser (or enter screenshot image path) > ").strip()
                if user_auth and Path(user_auth).exists():
                    return verify_metric(
                        lb_name=clean_lb,
                        usage_url=usage_url,
                        screenshot_path=user_auth,
                        open_browser_on_login=False,
                        interactive_cli=False,
                    )
                else:
                    return verify_metric(
                        lb_name=clean_lb,
                        usage_url=usage_url,
                        auto_capture_screenshot=True,
                        open_browser_on_login=False,
                        interactive_cli=False,
                    )

            return MetricVerificationResult(
                lb_name=clean_lb,
                usage_url=usage_url,
                http_status=http_status,
                effective_url=effective_url,
                needs_human_login=True,
                human_handoff_reason=login_reason,
                human_action_instructions=[
                    f"1. Log in to Volterra in the opened browser tab: {usage_url}",
                    f"2. Once logged in, in the search bar in the browser, search for: '{primary_search_key}'",
                    f"3. Locate the entry with Object Name '{primary_search_key}' so its 'Usage' column is visible (e.g. '3 requests').",
                    "4. Click '📸 Take Screenshot of Browser & Verify' to let Verifier Agent inspect the screen, extract the Usage value, and verify non-zero.",
                ],
                browser_opened=browser_opened,
                status="NEEDS_HUMAN_LOGIN",
                raw_response_excerpt=raw_body[:500],
                timestamp=now_iso,
                summary=(
                    f"HANDED OFF TO HUMAN FOR LOGIN: {login_reason} "
                    f"Opened {usage_url} for human authentication."
                ),
            )

    # Parse authenticated response (JSON or HTML/text) for `lb_name` / `primary_search_key`
    found = False
    metric_value = 0.0
    matched_record: Dict[str, Any] = {}

    try:
        parsed_json = json.loads(raw_body)
        res = extract_lb_value_from_json(parsed_json, clean_lb)
        if res is not None:
            metric_value, matched_record = res
            found = True
    except Exception:
        pass

    if not found:
        res = extract_lb_value_from_text(raw_body, clean_lb)
        if res is not None:
            metric_value, matched_record = res
            found = True

    is_non_zero = found and (metric_value > 0)
    verified = is_non_zero

    if verified:
        status = "VERIFIED_NON_ZERO"
        summary = (
            f"VERIFIED: Load Balancer '{primary_search_key}' found at {usage_url} "
            f"with non-zero Usage value = {metric_value:g} (> 0)."
        )
    elif found:
        status = "FAILED_ZERO_VALUE"
        summary = (
            f"FAILED: Load Balancer '{primary_search_key}' found at {usage_url}, "
            f"but Usage value is {metric_value:g} (expected non-zero > 0)."
        )
    else:
        status = "FAILED_NOT_FOUND"
        summary = (
            f"FAILED: Load Balancer '{primary_search_key}' was not found in the response from {usage_url} "
            f"(HTTP {http_status})."
        )

    return MetricVerificationResult(
        lb_name=clean_lb,
        usage_url=usage_url,
        http_status=http_status,
        effective_url=effective_url,
        needs_human_login=False,
        found=found,
        metric_value=metric_value,
        is_non_zero=is_non_zero,
        verified=verified,
        status=status,
        matched_record=matched_record,
        raw_response_excerpt=raw_body[:500],
        timestamp=now_iso,
        summary=summary,
    )


class VerifierAgent:
    """Verifier Agent (Sub-Agent 1) equipped with the real `verify_metric` tool."""

    agent_id = "verifier_agent_1"
    agent_name = "Verifier Agent (Sub-Agent 1)"
    role_badge = "🔍 SUB-AGENT 1: VERIFIER"

    def __init__(self, usage_url: str = VOLTERRA_USAGE_DETAILS_URL) -> None:
        self.usage_url = usage_url

    def verify_metric(
        self,
        lb_name: str,
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        human_provided_input: Optional[str] = None,
        open_browser_on_login: bool = True,
        interactive_cli: bool = False,
        screenshot_path: Optional[str] = None,
        screenshot_bytes: Optional[bytes] = None,
        auto_capture_screenshot: bool = False,
    ) -> Tuple[MetricVerificationResult, AgentMessage]:
        """Run the `verify_metric` tool for `lb_name` and return result + agent message."""
        result = verify_metric(
            lb_name=lb_name,
            usage_url=self.usage_url,
            auth_token=auth_token,
            cookie=cookie,
            human_provided_input=human_provided_input,
            open_browser_on_login=open_browser_on_login,
            interactive_cli=interactive_cli,
            screenshot_path=screenshot_path,
            screenshot_bytes=screenshot_bytes,
            auto_capture_screenshot=auto_capture_screenshot,
        )
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        if result.needs_human_login:
            msg = AgentMessage(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                role_badge="🛑 HUMAN LOGIN HAND-OFF",
                timestamp=now_str,
                headline=f"Login access required for `{result.usage_url}` — Handing control to human",
                bullets=[
                    f"Reason: {result.human_handoff_reason}",
                    *result.human_action_instructions,
                ],
                artifact_summary="PAUSED: Waiting for Human Login / Authenticated Session",
            )
            return result, msg

        status_icon = "✅" if result.verified else "❌"
        bullets = [
            f"Target URL: {result.usage_url} (HTTP {result.http_status})",
            f"Searched Load Balancer Name: `{result.lb_name}`",
            f"Observed Metric Value: `{result.metric_value:g}` | Non-Zero (`> 0`): `{result.is_non_zero}`",
        ]
        if result.screenshot_captured:
            bullets.insert(2, f"Source: Browser Page Screenshot ({result.screenshot_path or 'captured buffer'}) via Vision OCR")
            if result.usage_column_text:
                bullets.append(f"Usage Column Text: `{result.usage_column_text}`")

        msg = AgentMessage(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            role_badge=self.role_badge,
            timestamp=now_str,
            headline=f"{status_icon} Tool `verify_metric`: {result.summary}",
            bullets=bullets,
            artifact_summary=(
                f"LB '{result.lb_name}' Value={result.metric_value:g} ({result.status})"
            ),
        )
        return result, msg
