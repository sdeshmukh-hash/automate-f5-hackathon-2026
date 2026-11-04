# ☁️ Multi-Agent Load Balancer Verification System (Google Cloud Run Functions)

A zero-simulation AI multi-agent system built for **Volterra / F5 Distributed Cloud Load Balancer Traffic Generation (`run_traffic`) & Billing Metric Verification (`verify_metric`)** with **Human Login Hand-Off**, deployed on **Google Cloud**.

## 🏗️ Google Cloud Architecture

1. **🧭 Orchestrator Agent — Google Cloud Run Function ([`cloud_run_functions/orchestrator_agent/main.py`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/cloud_run_functions/orchestrator_agent/main.py) & [`src/agent.py`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/src/agent.py))**
   - HTTP entrypoint `orchestrator_agent_http` deployed as Cloud Run Function `orchestrator-agent-fn`.
   - Accepts a **Load Balancer name** (`lb_name`), invokes the **`run-traffic-fn` Cloud Run Function** (100 real `curl` requests), and delegates verification to **Sub-Agent 1 (`verifier-agent-fn` Cloud Run Function)**.
2. **⚡ Skill `run_traffic` — Google Cloud Run Function ([`cloud_run_functions/run_traffic/main.py`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/cloud_run_functions/run_traffic/main.py))**
   - HTTP entrypoint `run_traffic_http` deployed as Cloud Run Function `run-traffic-fn`.
   - Executes **100 real `curl` requests** against the target load balancer and returns real HTTP status codes and latency telemetry.
3. **🔍 Sub-Agent 1 (`VerifierAgent`) — Google Cloud Run Function ([`cloud_run_functions/verifier_agent/main.py`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/cloud_run_functions/verifier_agent/main.py))**
   - HTTP entrypoint `verifier_agent_http` deployed as Cloud Run Function `verifier-agent-fn`.
   - Invokes `verify_metric` against:
     `https://testing.crt.volterra.us/web/workspaces/billing/usage/usage_details/RE%20%E2%80%93%20Requests%20%E2%80%93%20Essentials%20%E2%80%93%20Millions/details;dateTime=86400`
   - If login access is required, returns `status="NEEDS_HUMAN_LOGIN"` and hands control to the human in the public Web UI.
4. **🌐 Public Streamlit Web UI — Google Cloud Run ([`app.py`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/app.py) & [`Dockerfile`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/Dockerfile))**
   - Deployed to **Google Cloud Run** (`lb-traffic-verifier-ui`) with `--allow-unauthenticated` so it is accessible as a public `https://....run.app` URL to everyone.

---

## 🚀 Deploy to Google Cloud (One Command)

```bash
cd /Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project
bash deploy/deploy_gcp.sh vesio-dev us-central1
```

Or deploy via **Google Cloud Build** ([`cloudbuild.yaml`](file:///Users/s.deshmukh/.gemini/antigravity/scratch/hackathon-project/cloudbuild.yaml)):

```bash
gcloud builds submit --config=cloudbuild.yaml --project=vesio-dev
```
