"""Deploy the Orchestrator Agent to Google Cloud Vertex AI Agent Garden / Agent Engine.

Usage:
    python3 deploy/deploy_agent_garden.py \
        --project "$GOOGLE_CLOUD_PROJECT" \
        --location "$GOOGLE_CLOUD_LOCATION" \
        --staging-bucket "$GCP_STAGING_BUCKET" \
        --run-traffic-fn-url "$RUN_TRAFFIC_FUNCTION_URL" \
        --verifier-fn-url "$VERIFIER_FUNCTION_URL"
"""

import argparse
import os
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent_garden.orchestrator_agent.agent import OrchestratorAgentGardenApp, root_agent


def deploy_to_agent_garden(
    project_id: str,
    location: str,
    staging_bucket: str,
    run_traffic_fn_url: str,
    verifier_fn_url: str,
    display_name: str = "volterra-lb-orchestrator-agent",
) -> str:
    """Deploy `OrchestratorAgent` to Vertex AI Agent Engine / Agent Garden and return its resource name."""
    try:
        import vertexai
        from vertexai import agent_engines
    except ImportError as exc:
        raise RuntimeError(
            "Missing `google-cloud-aiplatform[adk,agent_engines]`. Install with:\n"
            "  pip install 'google-cloud-aiplatform[adk,agent_engines]>=1.88.0' google-adk"
        ) from exc

    if not staging_bucket.startswith("gs://"):
        staging_bucket = f"gs://{staging_bucket}"

    os.environ["RUN_TRAFFIC_FUNCTION_URL"] = run_traffic_fn_url
    os.environ["VERIFIER_FUNCTION_URL"] = verifier_fn_url

    print(f"[Agent Garden] Initializing Vertex AI (project={project_id}, location={location}, bucket={staging_bucket})...")
    vertexai.init(project=project_id, location=location, staging_bucket=staging_bucket)

    requirements = [
        "google-cloud-aiplatform[adk,agent_engines]>=1.88.0",
        "google-adk>=0.1.0",
        "pydantic>=2.7.0",
        "python-dotenv>=1.0.1",
        "httpx>=0.27.0",
    ]

    app_instance = OrchestratorAgentGardenApp(
        run_traffic_function_url=run_traffic_fn_url,
        verifier_function_url=verifier_fn_url,
    )

    print(f"[Agent Garden] Deploying '{display_name}' to Vertex AI Agent Engine / Agent Garden...")
    remote_agent = agent_engines.create(
        agent_engine=app_instance,
        requirements=requirements,
        extra_packages=["src", "agent_garden", "cloud_run_functions"],
        display_name=display_name,
        description=(
            "Orchestrator Agent that runs 100 real curl requests via `run_traffic` Cloud Run Function "
            "and verifies non-zero Volterra load balancer usage via `verifier_agent` Cloud Run Function."
        ),
        env_vars={
            "RUN_TRAFFIC_FUNCTION_URL": run_traffic_fn_url,
            "VERIFIER_FUNCTION_URL": verifier_fn_url,
        },
    )

    resource_name = str(getattr(remote_agent, "resource_name", remote_agent))
    print(f"[Agent Garden] Deployed successfully! Resource Name: {resource_name}")
    print(
        f"[Agent Garden] View in Google Cloud Console: "
        f"https://console.cloud.google.com/vertex-ai/agents/agent-engines?project={project_id}"
    )
    return resource_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy Orchestrator Agent to Google Cloud Vertex AI Agent Garden / Agent Engine"
    )
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        required=not bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
        help="Google Cloud Project ID",
    )
    parser.add_argument(
        "--location",
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="Google Cloud region (default: us-central1)",
    )
    parser.add_argument(
        "--staging-bucket",
        default=os.getenv("GCP_STAGING_BUCKET", ""),
        required=not bool(os.getenv("GCP_STAGING_BUCKET")),
        help="GCS staging bucket (e.g. gs://my-project-agent-staging)",
    )
    parser.add_argument(
        "--run-traffic-fn-url",
        default=os.getenv("RUN_TRAFFIC_FUNCTION_URL", ""),
        help="Deployed Cloud Run Function URL for `run_traffic`",
    )
    parser.add_argument(
        "--verifier-fn-url",
        default=os.getenv("VERIFIER_FUNCTION_URL", ""),
        help="Deployed Cloud Run Function URL for `verifier_agent` (Sub-Agent 1)",
    )
    args = parser.parse_args()

    deploy_to_agent_garden(
        project_id=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
        run_traffic_fn_url=args.run_traffic_fn_url,
        verifier_fn_url=args.verifier_fn_url,
    )


if __name__ == "__main__":
    main()
