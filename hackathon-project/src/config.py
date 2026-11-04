"""Configuration loader for the Hackathon Project (Local & Google Cloud Run / Agent Garden)."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini").lower()
    model_name: str = os.getenv("MODEL_NAME", "gemini-2.5-flash")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("PORT", os.getenv("API_PORT", "8080")))
    volterra_lb_domain: str = os.getenv("VOLTERRA_LB_DOMAIN", "testing.crt.volterra.us")
    volterra_api_token: str = os.getenv("VOLTERRA_API_TOKEN", "")
    volterra_cookie: str = os.getenv("VOLTERRA_COOKIE", "")

    # Google Cloud Run Functions & Vertex AI Agent Garden / Agent Engine
    gcp_project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("GCP_PROJECT_ID", "vesio-dev"))
    gcp_region: str = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("GCP_REGION", "us-central1"))
    gcp_staging_bucket: str = os.getenv("GCP_STAGING_BUCKET", "")
    orchestrator_function_url: str = os.getenv(
        "ORCHESTRATOR_FUNCTION_URL",
        "https://orchestrator-agent-fn-802969428036.us-central1.run.app",
    )
    verifier_function_url: str = os.getenv(
        "VERIFIER_FUNCTION_URL",
        "https://verifier-agent-fn-802969428036.us-central1.run.app",
    )
    run_traffic_function_url: str = os.getenv(
        "RUN_TRAFFIC_FUNCTION_URL",
        "https://run-traffic-fn-802969428036.us-central1.run.app",
    )
    agent_engine_resource_name: str = os.getenv("AGENT_ENGINE_RESOURCE_NAME", "")
    running_in_cloud_run: bool = bool(os.getenv("K_SERVICE"))


settings = Settings()
