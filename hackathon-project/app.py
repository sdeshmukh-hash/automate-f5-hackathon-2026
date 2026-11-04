"""Public Google Cloud Run Streamlit App for Orchestrator (Agent Garden) + Sub-Agent Cloud Run Functions."""

from pathlib import Path
from typing import Optional
import webbrowser
import streamlit as st

from src.agent import LBTrafficVerificationBundle, OrchestratorAgent
from src.config import settings
from src.verifier_agent import (
    VOLTERRA_USAGE_DETAILS_URL,
    clear_saved_session_auth,
    load_saved_session_auth,
)

st.set_page_config(
    page_title="LB Traffic & Usage Verifier (Google Cloud)",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .topology-wrap {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        border: 1px solid #334155;
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 18px;
        color: #f8fafc;
    }
    .topology-header {
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #94a3b8;
        margin-bottom: 12px;
        font-weight: 700;
    }
    .topology-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: nowrap;
    }
    .agent-node {
        flex: 1;
        border-radius: 10px;
        padding: 14px 12px;
        text-align: center;
        border: 2px solid #334155;
        background: #1e293b;
        min-width: 140px;
    }
    .agent-node.done {
        border-color: #10b981;
        background: rgba(16, 185, 129, 0.14);
    }
    .agent-node.active {
        border-color: #38bdf8;
        background: rgba(56, 189, 248, 0.2);
        box-shadow: 0 0 16px rgba(56, 189, 248, 0.45);
    }
    .agent-node.handoff {
        border-color: #f59e0b;
        background: rgba(245, 158, 11, 0.2);
        box-shadow: 0 0 16px rgba(245, 158, 11, 0.45);
    }
    .agent-node.failed {
        border-color: #ef4444;
        background: rgba(239, 68, 68, 0.18);
    }
    .node-icon { font-size: 1.45rem; margin-bottom: 4px; }
    .node-title { font-weight: 700; font-size: 0.9rem; color: #f8fafc; }
    .node-sub { font-size: 0.74rem; color: #cbd5e1; margin-top: 2px; }
    .node-badge {
        display: inline-block;
        margin-top: 6px;
        padding: 2px 9px;
        border-radius: 999px;
        font-size: 0.68rem;
        font-weight: 700;
    }
    .badge-idle { background: #334155; color: #94a3b8; }
    .badge-active { background: #0284c7; color: #f0f9ff; }
    .badge-done { background: #059669; color: #ecfdf5; }
    .badge-handoff { background: #d97706; color: #fffbeb; }
    .badge-failed { background: #dc2626; color: #fef2f2; }
    .edge-arrow { font-size: 1.25rem; color: #475569; font-weight: bold; }
    .edge-arrow.lit { color: #10b981; }
    .handoff-box {
        background: rgba(245, 158, 11, 0.15);
        border: 2px solid #f59e0b;
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 18px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_lb_topology(
    stage: int,
    status: str = "PENDING",
    verified: Optional[bool] = None,
) -> str:
    nodes = [
        ("🧭", "1. Orchestrator Agent", "Cloud Run Fn: orchestrator-agent-fn"),
        ("⚡", "2. Skill: run_traffic", "Cloud Run Fn: run-traffic-fn"),
        ("🔍", "3. Verifier Agent (Sub-Agent 1)", "Cloud Run Fn: verifier-agent-fn"),
        ("✅", "4. Non-Zero Verdict", "Asserts Usage Value > 0"),
    ]
    parts = [
        '<div class="topology-wrap">',
        '<div class="topology-header">☁️ Google Cloud Multi-Agent Architecture (Cloud Run Functions A2A System)</div>',
        '<div class="topology-row">',
    ]
    for idx, (icon, title, sub) in enumerate(nodes, start=1):
        cls = "idle"
        b_cls = "badge-idle"
        b_txt = "IDLE"
        if idx < stage:
            cls = "done"
            b_cls = "badge-done"
            b_txt = "COMPLETED"
        elif idx == stage:
            if idx == 3 and status == "NEEDS_HUMAN_LOGIN":
                cls = "handoff"
                b_cls = "badge-handoff"
                b_txt = "HUMAN LOGIN NEEDED"
            elif idx == 4 and verified is not None:
                if verified:
                    cls = "done"
                    b_cls = "badge-done"
                    b_txt = "NON-ZERO VERIFIED"
                else:
                    cls = "failed"
                    b_cls = "badge-failed"
                    b_txt = "ZERO / FAILED"
            else:
                cls = "active"
                b_cls = "badge-active"
                b_txt = "RUNNING..."

        parts.append(
            f'<div class="agent-node {cls}">'
            f'<div class="node-icon">{icon}</div>'
            f'<div class="node-title">{title}</div>'
            f'<div class="node-sub">{sub}</div>'
            f'<span class="node-badge {b_cls}">{b_txt}</span>'
            f"</div>"
        )
        if idx < len(nodes):
            edge_cls = "lit" if stage > idx else ""
            parts.append(f'<div class="edge-arrow {edge_cls}">━━▶</div>')
    parts.append("</div></div>")
    return "".join(parts)


if "lb_bundle" not in st.session_state:
    st.session_state.lb_bundle: Optional[LBTrafficVerificationBundle] = None
if "lb_stage" not in st.session_state:
    st.session_state.lb_stage = 0

with st.sidebar:
    st.header("☁️ Cloud Run Functions Endpoints")
    orchestrator_fn_in = st.text_input(
        "Orchestrator Agent Cloud Run Fn",
        value=settings.orchestrator_function_url,
        help="Target Cloud Run Function for orchestrator-agent-fn.",
    )
    run_traffic_fn_in = st.text_input(
        "Skill `run_traffic` Cloud Run Fn",
        value=settings.run_traffic_function_url,
        help="Target Cloud Run Function for generating 100 curl requests.",
    )
    st.subheader("Sub-Agent 1: `verifier_agent` Mode")
    verifier_mode = st.radio(
        "Execution Environment",
        [
            "🖥️ Local Browser / Playwright (Recommended for *.crt.volterra.us VPN)",
            "☁️ Cloud Run Function (GCP us-central1)",
        ],
        index=0,
        help="Local Browser uses your Mac's authenticated Chrome session and corporate VPN network routing.",
    )
    if "Local Browser" in verifier_mode:
        verifier_fn_in = ""
        st.caption("✨ Verifier Agent will run locally with Playwright and active Chrome window capture.")
    else:
        verifier_fn_in = st.text_input(
            "Sub-Agent 1 `verifier_agent` Cloud Run Fn",
            value=settings.verifier_function_url,
            help="Target Cloud Run Function for verifying Volterra metric > 0.",
        )
        st.caption("✨ Note: If GCP Cloud Run times out on private VPN hosts, it automatically falls back to local browser.")
    st.caption("✨ Multi-Agent A2A calls will be routed to these Cloud Run endpoints.")
    st.divider()
    st.header("🔐 Volterra / F5 XC Auth")
    usage_url_input = st.text_area(
        "Billing Usage Details URL (`verify_metric`)",
        value=VOLTERRA_USAGE_DETAILS_URL,
        height=115,
    )
    saved_auth = load_saved_session_auth()
    auth_token_input = st.text_input(
        "Volterra API Token (Optional)",
        value=saved_auth.get("auth_token", ""),
        type="password",
        help="Sent as 'Authorization: APIToken <token>'",
    )
    cookie_input = st.text_input(
        "Session Cookie (Optional)",
        value=saved_auth.get("cookie", ""),
        type="password",
        help="Sent as 'Cookie: <cookie>'",
    )
    if st.button("🗑️ Clear Saved Session Credentials", use_container_width=True):
        clear_saved_session_auth()
        st.rerun()

st.title("☁️ Multi-Agent System (Cloud Run Functions)")

if settings.running_in_cloud_run:
    st.warning(
        "⚠️ **Cloud Run Web Environment Detected:** This UI is currently running in a remote Google Cloud Run container (`us-central1`). "
        "Because GCP Cloud Run is isolated from your local Mac display and corporate VPN, browser window auto-capture cannot access your Mac screen here. "
        "👉 **For Zero Human Hand-off (Solution B):** Open **`http://localhost:8080`** on your Mac where the Verifier Agent automatically captures your authenticated Volterra Chrome tab!"
    )
else:
    st.success(
        "🖥️ **Solution B Active (Local Mac Host):** Orchestrator & Verifier Sub-Agent running locally with access to your authenticated "
        "Google Chrome tab and corporate VPN. Traffic skill invokes Cloud Run Function (`run-traffic-fn`). Zero human hand-off required!"
    )

st.caption(
    "**Orchestrator Agent** (Cloud Run Function `orchestrator-agent-fn`) accepts a Load Balancer name → invokes **`run_traffic`** "
    "(100 real `curl` requests via `run-traffic-fn`) → invokes **Verifier Agent (Sub-Agent 1 Cloud Run Function `verifier-agent-fn`)** "
    "with tool **`verify_metric`** on the live Volterra URL. If login access is needed, control is handed to you."
)

with st.form("lb_orchestrator_form"):
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
    lb_name_in = fcol1.text_input(
        "Load Balancer Name (required)",
        placeholder="e.g. blocking-waf-https or ves-io-http-loadbalancer-blocking-waf-https",
    )
    target_url_in = fcol2.text_input(
        "Load Balancer Curl Target URL (Optional override)",
        placeholder="e.g. https://ves-io-http-lb-prod-checkout.testing.crt.volterra.us",
    )
    num_req_in = fcol3.number_input(
        "Curl Requests (`run_traffic`)",
        min_value=1,
        max_value=1000,
        value=100,
        step=10,
    )
    submitted = st.form_submit_button(
        "🚀 Run Orchestrator: Execute `run_traffic` (100 real curl) + `VerifierAgent.verify_metric`",
        type="primary",
        use_container_width=True,
    )

topo_box = st.empty()
cur_bundle: Optional[LBTrafficVerificationBundle] = st.session_state.lb_bundle
topo_box.markdown(
    render_lb_topology(
        st.session_state.lb_stage,
        cur_bundle.status if cur_bundle else "PENDING",
        cur_bundle.verified_non_zero if cur_bundle else None,
    ),
    unsafe_allow_html=True,
)

if submitted:
    if not lb_name_in.strip():
        st.warning("Please enter a Load Balancer Name.")
    else:
        orchestrator = OrchestratorAgent(
            usage_url=usage_url_input.strip() or VOLTERRA_USAGE_DETAILS_URL,
            run_traffic_function_url=run_traffic_fn_in.strip(),
            verifier_function_url=verifier_fn_in.strip(),
        )
        bundle = orchestrator.create_bundle(
            lb_name=lb_name_in.strip(),
            target_url=target_url_in.strip() or None,
            num_requests=int(num_req_in),
        )

        with st.status("🤖 Running Orchestrator -> `run_traffic` -> `VerifierAgent.verify_metric`...", expanded=True) as sbox:
            st.session_state.lb_stage = 1
            topo_box.markdown(render_lb_topology(1), unsafe_allow_html=True)
            st.write(f"🧭 **Orchestrator Agent** accepted Load Balancer `{bundle.lb_name}`...")
            bundle = orchestrator.step_accept_and_plan(bundle)

            st.session_state.lb_stage = 2
            topo_box.markdown(render_lb_topology(2), unsafe_allow_html=True)
            st.write(f"⚡ **Skill `run_traffic`** sending **{bundle.num_requests} real curl requests** to `{bundle.target_url}`...")
            try:
                bundle = orchestrator.step_run_traffic_skill(bundle)
            except Exception as exc:
                st.error(f"❌ Error during `run_traffic`: {exc}")
                st.info(
                    "💡 If `run-traffic-fn` returned 403 Forbidden or failed, clear the "
                    "'Cloud Run Fn: run_traffic URL' in the left sidebar to execute `run_traffic` "
                    "directly from within the container."
                )
                st.stop()

            st.session_state.lb_stage = 3
            topo_box.markdown(render_lb_topology(3), unsafe_allow_html=True)
            st.write(f"🔍 **Verifier Agent (Sub-Agent 1)** invoking `verify_metric` on `{bundle.usage_url}`...")
            bundle = orchestrator.step_invoke_verifier_subagent(
                bundle,
                auth_token=auth_token_input or None,
                cookie=cookie_input or None,
                open_browser_on_login=not settings.running_in_cloud_run,
                interactive_cli=False,
                auto_capture_screenshot=not bool(verifier_fn_in.strip()),
            )

            if bundle.needs_human_login:
                st.session_state.lb_stage = 3
                st.session_state.lb_bundle = bundle
                topo_box.markdown(render_lb_topology(3, "NEEDS_HUMAN_LOGIN"), unsafe_allow_html=True)
                sbox.update(
                    label=f"🛑 Handed Control to Human: Login Needed for {bundle.lb_name}",
                    state="error",
                    expanded=True,
                )
            else:
                st.session_state.lb_stage = 4
                st.session_state.lb_bundle = bundle
                topo_box.markdown(render_lb_topology(4, bundle.status, bundle.verified_non_zero), unsafe_allow_html=True)
                sbox.update(
                    label=f"{'✅ VERIFIED NON-ZERO' if bundle.verified_non_zero else '❌ VERIFICATION FAILED'}: {bundle.lb_name}",
                    state="complete",
                    expanded=False,
                )

cur_bundle = st.session_state.lb_bundle
if cur_bundle is not None and cur_bundle.verification_result is not None:
    tr = cur_bundle.traffic_result
    vr = cur_bundle.verification_result

    # HUMAN LOGIN HAND-OFF SECTION
    if cur_bundle.needs_human_login:
        raw_lb = cur_bundle.lb_name.strip()
        search_term = f"ves-io-http-loadbalancer-{raw_lb}" if not raw_lb.startswith("ves-io-http-loadbalancer-") else raw_lb

        st.markdown(
            f'<div class="handoff-box">'
            f"<h4>🛑 Human Login Required — Control Handed to Human</h4>"
            f"<p><b>Reason:</b> {vr.human_handoff_reason}</p>"
            f"<p><b>Target Usage URL:</b> <a href='{vr.usage_url}' target='_blank' rel='noopener noreferrer' style='color:#38bdf8;'>{vr.usage_url}</a></p>"
            f"<p><b>Search Bar Query:</b> <code>{search_term}</code></p>"
            f"</div>",
            unsafe_allow_html=True,
        )

        st.markdown("### 📋 Steps to Complete Verification:")
        st.markdown(
            f"1. **Open & Log In:** Click the button below to open the Volterra Usage URL in your browser and log in.\n"
            f"2. **Search Load Balancer:** In the search bar on the usage page, search for: `{search_term}`.\n"
            f"3. **Verify via Screenshot:** Make sure the table row with Object Name `{search_term}` and column **Usage** is visible on screen, then click **'📸 Take Screenshot of Browser & Verify Metric'** below."
        )

        col_act1, col_act2 = st.columns([1, 1])
        with col_act1:
            if hasattr(st, "link_button"):
                st.link_button(
                    "🌐 Open Volterra Usage URL in Your Browser",
                    vr.usage_url,
                    use_container_width=True,
                )
            elif st.button("🌐 Open Volterra Usage URL in Browser Now"):
                webbrowser.open(vr.usage_url)

        st.markdown("---")
        st.markdown("#### 📸 Browser Screenshot Verification")
        st.caption(
            "The Verifier Agent will capture the browser screen, locate the table row "
            f"for `{search_term}`, search the column **Usage**, extract the value (e.g. `3 requests`), "
            "and pass the test if non-zero (> 0)."
        )

        btn_screen = st.button(
            "📸 Take Screenshot of Browser & Verify Metric",
            type="primary",
            use_container_width=True,
            help="Takes a screenshot of your active browser window, locates the LB row, and verifies the Usage column.",
        )

        st.markdown("**Or drop / upload a screenshot image directly** *(useful when running Streamlit remotely)*:")
        uploaded_screenshot = st.file_uploader(
            "Upload browser page screenshot (PNG/JPEG)",
            type=["png", "jpg", "jpeg"],
            label_visibility="collapsed",
        )
        btn_upload = st.button("🔍 Verify Uploaded Screenshot", use_container_width=True, disabled=uploaded_screenshot is None)

        if btn_screen or (btn_upload and uploaded_screenshot is not None):
            orchestrator = OrchestratorAgent(
                usage_url=cur_bundle.usage_url,
                run_traffic_function_url=run_traffic_fn_in.strip(),
                verifier_function_url=verifier_fn_in.strip(),
            )
            with st.spinner("Capturing screenshot and running Vision OCR to verify Usage column..."):
                if uploaded_screenshot is not None and (btn_upload or not btn_screen):
                    updated = orchestrator.resume_after_human_login(
                        cur_bundle,
                        screenshot_bytes=uploaded_screenshot.getvalue(),
                    )
                else:
                    updated = orchestrator.resume_after_human_login(
                        cur_bundle,
                        auto_capture_screenshot=True,
                    )
            st.session_state.lb_bundle = updated
            st.session_state.lb_stage = 3 if updated.needs_human_login else 4
            st.rerun()

    else:
        if vr.verified:
            st.success(f"✅ **{vr.summary}**")
        else:
            st.error(f"❌ **{vr.summary}**")

        if vr.screenshot_captured:
            with st.expander("📸 Captured Browser Screenshot & OCR Analysis", expanded=True):
                if vr.screenshot_path and Path(vr.screenshot_path).exists():
                    st.image(vr.screenshot_path, caption=f"Browser Screenshot — Table Row '{vr.lb_name}' with Usage Column", use_column_width=True)
                if vr.usage_column_text:
                    st.info(f"**Usage Column Detected:** `{vr.usage_column_text}` (Non-Zero: {vr.is_non_zero})")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Load Balancer Searched", vr.lb_name)
    m2.metric(
        "Skill `run_traffic` Sent",
        f"{tr.requests_sent if tr else 0} curl reqs",
        delta=f"{tr.successful_http_responses if tr else 0} HTTP responses",
    )
    m3.metric(
        "Usage Metric Value (86400s)",
        "LOGIN NEEDED" if vr.needs_human_login else f"{vr.metric_value:g}",
    )
    m4.metric("Sub-Agent 1 Status", vr.status)

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("⚡ Skill `run_traffic` Output (100 Real Curl Requests)")
        if tr:
            st.code(tr.curl_command_template, language="bash")
            st.write(
                f"**Target URL:** `{tr.target_url}` | **Duration:** `{tr.total_duration_sec}s` | "
                f"**Avg Latency:** `{tr.avg_latency_ms}ms` | **Status Codes:** `{tr.status_code_counts}`"
            )
            if tr.errors:
                st.warning(f"Curl stderr notes: `{'; '.join(tr.errors)}`")
            with st.expander("📜 Real Curl Execution Logs", expanded=True):
                for line in tr.sample_logs:
                    st.text(line)

    with col_right:
        st.subheader("🔍 Sub-Agent 1: `VerifierAgent.verify_metric` Result")
        st.markdown(f"**Live URL:** `{vr.usage_url}`")
        st.json(vr.model_dump())

    st.subheader("💬 Multi-Agent Execution Trace")
    for msg in cur_bundle.agent_messages:
        with st.chat_message("assistant"):
            st.markdown(f"**{msg.role_badge} — {msg.agent_name}** *({msg.timestamp})*")
            st.markdown(f"**{msg.headline}**")
            for b in msg.bullets:
                st.markdown(f"- {b}")
