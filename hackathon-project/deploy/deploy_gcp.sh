#!/usr/bin/env bash
# ==============================================================================
# Google Cloud Architecture (Cloud Run Functions):
# 1. Skill (`run-traffic-fn`) -> Cloud Run Function (100 curl requests)
# 2. Sub-Agent 1 (`verifier-agent-fn`) -> Cloud Run Function (verify_metric)
# 3. Orchestrator Agent (`orchestrator-agent-fn`) -> Cloud Run Function
# 4. Streamlit Mission Control UI (`lb-traffic-verifier-ui`) -> Cloud Run
# ==============================================================================

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$HOME/google-cloud-sdk/bin:/opt/homebrew/share/google-cloud-sdk/bin:$PATH"

RAW_PROJECT="${1:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
REGION="${2:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
AR_REPO="${3:-cloud-run-agents}"

if [ -z "$RAW_PROJECT" ] || [ "$RAW_PROJECT" = "(unset)" ]; then
  echo "Usage: bash deploy/deploy_gcp.sh <GCP_PROJECT_ID_OR_NUMBER> [REGION=us-central1]" >&2
  exit 1
fi

if [[ "$RAW_PROJECT" =~ ^[0-9]+$ ]]; then
  echo "ℹ️  Resolving Project Number (${RAW_PROJECT}) to Project ID..."
  RESOLVED_PROJECT_ID=$(gcloud projects describe "$RAW_PROJECT" --format="value(projectId)" 2>/dev/null || true)
  PROJECT_ID="${RESOLVED_PROJECT_ID:-$RAW_PROJECT}"
else
  PROJECT_ID="$RAW_PROJECT"
fi

gcloud config set project "$PROJECT_ID"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/lb-agent-suite:latest"

echo "=============================================================================="
echo "☁️  Target Architecture (Cloud Run Functions A2A System):"
echo "    • Orchestrator Agent  : Cloud Run Function (orchestrator-agent-fn)"
echo "    • Sub-Agent 1         : Cloud Run Function (verifier-agent-fn)"
echo "    • Skill run_traffic   : Cloud Run Function (run-traffic-fn)"
echo "    • Web Mission Control : Cloud Run (lb-traffic-verifier-ui)"
echo "=============================================================================="

echo "[1/6] Enabling Cloud Run, Artifact Registry, and Vertex AI APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  --project="$PROJECT_ID" || true

echo "[2/6] Building & Pushing Multi-Agent Container to Artifact Registry..."
if ! gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Container repository for Cloud Run Functions" \
    --project="$PROJECT_ID"
fi
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build --platform linux/amd64 -t "$IMAGE_URI" .
docker push "$IMAGE_URI"

echo "[3/6] Deploying Cloud Run Function 1: Skill 'run_traffic' (100 curl requests)..."
gcloud run deploy run-traffic-fn \
  --image="$IMAGE_URI" \
  --region="$REGION" \
  --platform=managed \
  --port=8080 \
  --memory=512Mi \
  --timeout=300 \
  --set-env-vars="FUNCTION_TARGET=run_traffic_http" \
  --project="$PROJECT_ID"

RUN_TRAFFIC_FN_URL=$(gcloud run services describe run-traffic-fn --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
echo "  ✅ run-traffic-fn: ${RUN_TRAFFIC_FN_URL}"

echo "[4/6] Deploying Cloud Run Function 2: Sub-Agent 1 ('verifier_agent' / 'verify_metric')..."
gcloud run deploy verifier-agent-fn \
  --image="$IMAGE_URI" \
  --region="$REGION" \
  --platform=managed \
  --port=8080 \
  --memory=512Mi \
  --timeout=120 \
  --set-env-vars="FUNCTION_TARGET=verifier_agent_http" \
  --project="$PROJECT_ID"

VERIFIER_FN_URL=$(gcloud run services describe verifier-agent-fn --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
echo "  ✅ verifier-agent-fn: ${VERIFIER_FN_URL}"

echo "[5/6] Deploying Cloud Run Function 3: Orchestrator Agent ('orchestrator-agent-fn')..."
gcloud run deploy orchestrator-agent-fn \
  --image="$IMAGE_URI" \
  --region="$REGION" \
  --platform=managed \
  --port=8080 \
  --memory=512Mi \
  --timeout=300 \
  --set-env-vars="FUNCTION_TARGET=orchestrator_agent_http,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},RUN_TRAFFIC_FUNCTION_URL=${RUN_TRAFFIC_FN_URL},VERIFIER_FUNCTION_URL=${VERIFIER_FN_URL}" \
  --project="$PROJECT_ID"

ORCHESTRATOR_FN_URL=$(gcloud run services describe orchestrator-agent-fn --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
echo "  ✅ orchestrator-agent-fn: ${ORCHESTRATOR_FN_URL}"

echo "[6/6] Deploying Public Streamlit Mission Control to Cloud Run..."
gcloud run deploy lb-traffic-verifier-ui \
  --image="$IMAGE_URI" \
  --region="$REGION" \
  --platform=managed \
  --port=8080 \
  --memory=1Gi \
  --timeout=300 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},ORCHESTRATOR_FUNCTION_URL=${ORCHESTRATOR_FN_URL},RUN_TRAFFIC_FUNCTION_URL=${RUN_TRAFFIC_FN_URL},VERIFIER_FUNCTION_URL=${VERIFIER_FN_URL}" \
  --project="$PROJECT_ID"

PUBLIC_APP_URL=$(gcloud run services describe lb-traffic-verifier-ui --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")

echo "=============================================================================="
echo "🎉 DEPLOYMENT SUMMARY:"
echo "  • 🧭 Orchestrator Agent (Cloud Run Function) : ${ORCHESTRATOR_FN_URL}"
echo "  • ⚡ Skill run_traffic (Cloud Run Function)  : ${RUN_TRAFFIC_FN_URL}"
echo "  • 🔍 Sub-Agent 1 Verifier (Cloud Run Function): ${VERIFIER_FN_URL}"
echo "  • 🌐 Streamlit Mission Control UI             : ${PUBLIC_APP_URL}"
echo "=============================================================================="
