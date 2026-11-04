"""Real Web UI & API Server for the Orchestrator (`run_traffic`) + Verifier Agent (`verify_metric`).

Uses only real URLs (no simulation). If login access is required for
https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400,
control is handed to the human in the browser and UI.

Run with:
    python3 server.py
Then open http://127.0.0.1:8080 in your browser.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from typing import Any, Dict, Optional

from src.agent import LBTrafficVerificationBundle, OrchestratorAgent
from src.verifier_agent import VOLTERRA_USAGE_DETAILS_URL, clear_saved_session_auth

HOST = "127.0.0.1"
PORT = 8080

LAST_BUNDLE: Optional[LBTrafficVerificationBundle] = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Orchestrator & Verifier Agent — Real LB Traffic & Usage Verification</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --green: #10b981;
      --amber: #f59e0b;
      --red: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 24px 32px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    h1 { margin: 0 0 6px 0; font-size: 1.65rem; }
    .sub { color: var(--muted); font-size: 0.92rem; margin-bottom: 20px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 18px;
    }
    .form-grid {
      display: grid;
      grid-template-columns: 2fr 2fr 1fr;
      gap: 12px;
      margin-bottom: 12px;
    }
    label { display: block; font-size: 0.78rem; color: var(--muted); margin-bottom: 4px; font-weight: 600; }
    input, textarea {
      width: 100%;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid #475569;
      background: #0f172a;
      color: #f8fafc;
      font-size: 0.9rem;
      font-family: inherit;
    }
    .run-btn {
      width: 100%;
      padding: 12px;
      background: linear-gradient(90deg, #0284c7, #2563eb);
      color: #fff;
      font-weight: 700;
      font-size: 0.95rem;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      margin-top: 10px;
    }
    .resume-btn {
      width: 100%;
      padding: 12px;
      background: linear-gradient(90deg, #d97706, #059669);
      color: #fff;
      font-weight: 700;
      font-size: 0.95rem;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      margin-top: 10px;
    }
    .topology {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .node {
      flex: 1;
      text-align: center;
      padding: 14px 10px;
      border-radius: 10px;
      border: 2px solid var(--border);
      background: #0f172a;
      transition: all 0.25s ease;
    }
    .node.active { border-color: var(--accent); box-shadow: 0 0 16px rgba(56,189,248,0.4); }
    .node.done { border-color: var(--green); background: rgba(16,185,129,0.12); }
    .node.handoff { border-color: var(--amber); background: rgba(245,158,11,0.18); box-shadow: 0 0 16px rgba(245,158,11,0.4); }
    .node.failed { border-color: var(--red); background: rgba(239,68,68,0.15); }
    .arrow { color: #475569; font-weight: bold; font-size: 1.2rem; }
    .arrow.lit { color: var(--green); }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric-box {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
    }
    .metric-val { font-size: 1.25rem; font-weight: 700; margin-top: 4px; word-break: break-all; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    pre {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
      font-size: 0.8rem;
      color: #e2e8f0;
    }
    .banner-ok {
      background: rgba(16,185,129,0.15);
      border: 1px solid var(--green);
      color: #6ee7b7;
      padding: 12px 16px;
      border-radius: 8px;
      font-weight: 600;
      margin-bottom: 16px;
    }
    .banner-handoff {
      background: rgba(245,158,11,0.15);
      border: 2px solid var(--amber);
      color: #fde68a;
      padding: 16px 20px;
      border-radius: 12px;
      margin-bottom: 16px;
    }
    .banner-fail {
      background: rgba(239,68,68,0.15);
      border: 1px solid var(--red);
      color: #fca5a5;
      padding: 12px 16px;
      border-radius: 8px;
      font-weight: 600;
      margin-bottom: 16px;
    }
    a { color: #38bdf8; }
  </style>
</head>
<body>
  <h1>🌐 Orchestrator & Verifier Agent — Real LB Traffic & Usage Verification</h1>
  <div class="sub">
    <b>Orchestrator Agent</b> accepts Load Balancer name → runs skill <code>run_traffic</code> (100 real curl requests) →
    delegates to <b>Verifier Agent (Sub-Agent 1)</b> with tool <code>verify_metric</code> on the live Volterra URL.
    If login access is needed, control is handed to you.
  </div>

  <div class="card">
    <div class="form-grid">
      <div>
        <label>Load Balancer Name (required)</label>
        <input id="lb_name" placeholder="Enter your Load Balancer name" value="" />
      </div>
      <div>
        <label>Load Balancer Curl Target URL (Optional override)</label>
        <input id="target_url" placeholder="https://<lb_name>.testing.crt.volterra.us" value="" />
      </div>
      <div>
        <label>Skill <code>run_traffic</code> Requests</label>
        <input id="num_requests" type="number" value="100" />
      </div>
    </div>
    <div style="margin-bottom:10px;">
      <label>Verifier Agent (Sub-Agent 1) <code>verify_metric</code> Actual Usage Details URL</label>
      <input id="usage_url" value="https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400" />
    </div>
    <div class="two-col">
      <div>
        <label>Optional Volterra API Token (<code>Authorization: APIToken ...</code>)</label>
        <input id="auth_token" type="password" placeholder="Optional API Token" />
      </div>
      <div>
        <label>Optional Logged-In Session Cookie (<code>Cookie: ...</code>)</label>
        <input id="cookie" type="password" placeholder="Optional Session Cookie" />
      </div>
    </div>
    <button class="run-btn" onclick="runWorkflow()">
      🚀 Run Orchestrator: Execute Skill `run_traffic` (100 Real curl) + Sub-Agent 1 `verify_metric`
    </button>
  </div>

  <div class="card">
    <div style="font-size:0.78rem;color:#94a3b8;text-transform:uppercase;font-weight:700;margin-bottom:10px;">
      ⚡ Live Multi-Agent Topology
    </div>
    <div class="topology">
      <div id="node1" class="node">🧭<br><b>1. Orchestrator Agent</b><br><small>Accept LB Name</small></div>
      <div id="arr1" class="arrow">━━▶</div>
      <div id="node2" class="node">⚡<br><b>2. Skill: run_traffic</b><br><small>100 Real curl Requests</small></div>
      <div id="arr2" class="arrow">━━▶</div>
      <div id="node3" class="node">🔍<br><b>3. Verifier Agent (Sub-Agent 1)</b><br><small>Tool: verify_metric</small></div>
      <div id="arr3" class="arrow">━━▶</div>
      <div id="node4" class="node">✅<br><b>4. Non-Zero Verdict</b><br><small>Value &gt; 0 Check</small></div>
    </div>
  </div>

  <div id="handoff_panel" class="banner-handoff" style="display:none;">
    <h3 style="margin-top:0;">🛑 Human Login Access Required — Control Handed to Human</h3>
    <div id="handoff_reason" style="margin-bottom:8px;"></div>
    <div style="margin-bottom:10px;">
      <b>Live Volterra Usage URL:</b>
      <a id="handoff_link" href="#" target="_blank">Open Volterra Billing Usage Details in Browser</a>
    </div>
    <ul id="handoff_steps" style="margin-top:4px;margin-bottom:12px;"></ul>
    <label style="color:#fde68a;">Paste Logged-In Session Cookie / APIToken / "Copy as cURL" / Usage Table Content:</label>
    <textarea id="human_input" rows="4" placeholder="After logging in to testing.crt.volterra.us in your browser, paste your Cookie, APIToken, 'Copy as cURL' from DevTools Network tab, or copied usage table text/JSON here..."></textarea>
    <button class="resume-btn" onclick="resumeAfterHumanLogin()">
      👤 Resume `verify_metric` With Logged-In Human Session
    </button>
  </div>

  <div id="results_area" style="display:none;">
    <div id="verdict_banner"></div>
    <div class="metrics-grid">
      <div class="metric-box"><label>Load Balancer Searched</label><div id="m_lb" class="metric-val">-</div></div>
      <div class="metric-box"><label>Skill `run_traffic` Sent</label><div id="m_reqs" class="metric-val">-</div></div>
      <div class="metric-box"><label>Usage Value (86400s)</label><div id="m_val" class="metric-val">-</div></div>
      <div class="metric-box"><label>Sub-Agent 1 Status</label><div id="m_status" class="metric-val">-</div></div>
    </div>
    <div class="two-col">
      <div class="card">
        <h3 style="margin-top:0;">⚡ Skill <code>run_traffic</code> Output (100 Real Curl Requests)</h3>
        <pre id="traffic_out"></pre>
      </div>
      <div class="card">
        <h3 style="margin-top:0;">🔍 Sub-Agent 1: <code>verify_metric</code> Result</h3>
        <pre id="verifier_out"></pre>
      </div>
    </div>
  </div>

  <script>
    function setStage(stage, state) {
      for (let i = 1; i <= 4; i++) {
        const n = document.getElementById('node' + i);
        n.className = 'node';
        if (i < stage) n.classList.add('done');
        else if (i === stage) {
          if (state === 'handoff') n.classList.add('handoff');
          else if (state === 'ok') n.classList.add('done');
          else if (state === 'fail') n.classList.add('failed');
          else n.classList.add('active');
        }
        if (i < 4) {
          document.getElementById('arr' + i).className = (stage > i) ? 'arrow lit' : 'arrow';
        }
      }
    }

    function renderResponse(data) {
      const vr = data.verification_result || {};
      const tr = data.traffic_result || {};

      document.getElementById('results_area').style.display = 'block';

      if (data.needs_human_login || vr.needs_human_login) {
        setStage(3, 'handoff');
        const hp = document.getElementById('handoff_panel');
        hp.style.display = 'block';
        document.getElementById('handoff_reason').textContent = 'Reason: ' + (vr.human_handoff_reason || '');
        const link = document.getElementById('handoff_link');
        link.href = vr.usage_url || '#';
        link.textContent = vr.usage_url || '';

        const ul = document.getElementById('handoff_steps');
        ul.innerHTML = '';
        (vr.human_action_instructions || []).forEach(step => {
          const li = document.createElement('li');
          li.textContent = step;
          ul.appendChild(li);
        });
        document.getElementById('verdict_banner').style.display = 'none';
      } else {
        document.getElementById('handoff_panel').style.display = 'none';
        setStage(4, vr.verified ? 'ok' : 'fail');
        const banner = document.getElementById('verdict_banner');
        banner.style.display = 'block';
        banner.className = vr.verified ? 'banner-ok' : 'banner-fail';
        banner.textContent = vr.summary || '';
      }

      document.getElementById('m_lb').textContent = vr.lb_name || data.lb_name || '-';
      document.getElementById('m_reqs').textContent = (tr.requests_sent ?? 0) + ' (' + (tr.successful_http_responses ?? 0) + ' HTTP)';
      document.getElementById('m_val').textContent = vr.needs_human_login ? 'LOGIN NEEDED' : (vr.metric_value ?? 0);
      document.getElementById('m_status').textContent = vr.status || data.status || '-';

      document.getElementById('traffic_out').textContent = JSON.stringify(tr, null, 2);
      document.getElementById('verifier_out').textContent = JSON.stringify(vr, null, 2);
    }

    async function runWorkflow() {
      const lb = document.getElementById('lb_name').value.trim();
      if (!lb) {
        alert('Please enter a Load Balancer Name.');
        return;
      }
      setStage(1);
      setTimeout(() => setStage(2), 200);

      const payload = {
        lb_name: lb,
        target_url: document.getElementById('target_url').value.trim(),
        num_requests: parseInt(document.getElementById('num_requests').value || '100', 10),
        usage_url: document.getElementById('usage_url').value.trim(),
        auth_token: document.getElementById('auth_token').value.trim(),
        cookie: document.getElementById('cookie').value.trim()
      };
      const res = await fetch('/api/lb/run-and-verify', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      renderResponse(data);
    }

    async function resumeAfterHumanLogin() {
      const humanInput = document.getElementById('human_input').value.trim();
      const payload = {
        lb_name: document.getElementById('lb_name').value.trim(),
        usage_url: document.getElementById('usage_url').value.trim(),
        auth_token: document.getElementById('auth_token').value.trim(),
        cookie: document.getElementById('cookie').value.trim(),
        human_provided_input: humanInput
      };
      const res = await fetch('/api/lb/resume-after-login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      renderResponse(data);
    }
  </script>
</body>
</html>
"""


class RealWorkflowHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the real web UI and API endpoints."""

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        raw = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "usage_url": VOLTERRA_USAGE_DETAILS_URL})
            return

        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        global LAST_BUNDLE
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        payload = json.loads(raw_body or "{}")

        usage_url = payload.get("usage_url") or VOLTERRA_USAGE_DETAILS_URL
        orchestrator = OrchestratorAgent(usage_url=usage_url)

        if self.path == "/api/lb/run-and-verify":
            bundle = orchestrator.run_workflow(
                lb_name=payload.get("lb_name", ""),
                target_url=payload.get("target_url") or None,
                num_requests=int(payload.get("num_requests", 100)),
                auth_token=payload.get("auth_token") or None,
                cookie=payload.get("cookie") or None,
                open_browser_on_login=True,
                interactive_cli=False,
            )
            LAST_BUNDLE = bundle
            self._send_json(200, bundle.model_dump())
            return

        if self.path == "/api/lb/resume-after-login":
            if LAST_BUNDLE is not None and LAST_BUNDLE.lb_name == payload.get("lb_name"):
                bundle = orchestrator.resume_after_human_login(
                    LAST_BUNDLE,
                    human_provided_input=payload.get("human_provided_input"),
                    auth_token=payload.get("auth_token") or None,
                    cookie=payload.get("cookie") or None,
                )
            else:
                bundle = orchestrator.create_bundle(lb_name=payload.get("lb_name", ""))
                bundle = orchestrator.resume_after_human_login(
                    bundle,
                    human_provided_input=payload.get("human_provided_input"),
                    auth_token=payload.get("auth_token") or None,
                    cookie=payload.get("cookie") or None,
                )
            LAST_BUNDLE = bundle
            self._send_json(200, bundle.model_dump())
            return

        if self.path == "/api/session/clear":
            clear_saved_session_auth()
            self._send_json(200, {"status": "cleared"})
            return

        self._send_json(404, {"error": "Not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    server = HTTPServer((HOST, PORT), RealWorkflowHandler)
    print(f"Real LB Traffic & Usage Verification UI running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
