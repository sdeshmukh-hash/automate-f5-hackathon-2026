FROM python:3.11-slim

# Install curl, CA certificates, tesseract-ocr, and Chromium for automated Playwright verification
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tesseract-ocr \
    chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

# Multi-mode entrypoint supporting:
# 1. Cloud Run Functions (`FUNCTION_TARGET=run_traffic_http` or `FUNCTION_TARGET=verifier_agent_http`)
# 2. Orchestrator Agent Garden Service (`SERVICE_MODE=orchestrator_api`)
# 3. Public Streamlit Web App (default)
CMD ["sh", "-c", "if [ -n \"${FUNCTION_TARGET:-}\" ]; then exec functions-framework --source=main.py --target=\"${FUNCTION_TARGET}\" --port=\"${PORT:-8080}\"; elif [ \"${SERVICE_MODE:-}\" = \"orchestrator_api\" ]; then exec uvicorn api:app --host 0.0.0.0 --port \"${PORT:-8080}\"; else exec streamlit run app.py --server.port=\"${PORT:-8080}\" --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false; fi"]
