from pathlib import Path

import pytest

from agent_runtime.config import Settings

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        agent_id="fulfillment",
        agent_role="Fulfillment",
        department="operations",
        reports_to="operations-manager",
        region="us-east-1",
        model_id="us.anthropic.example-model",
        bedrock_role_arn=None,
        queue_url="",
        queue_endpoint_url=None,
        mcp_url="http://127.0.0.1:9000/mcp",
        prompt_source="file",
        prompt_file=PROMPTS / "operations" / "fulfillment.yaml",
        prompt_application_id=None,
        prompt_environment_id=None,
        prompt_profile_id=None,
        hermes_home=tmp_path / "home",
        workdir=tmp_path / "work",
        max_turns=30,
        turn_timeout=5,
        resume_on_start=False,
        log_level="INFO",
        activity_url=None,
        plugins_dir=Path(__file__).resolve().parents[2] / "plugins",
        mcp_proxy_upstream=None,
        mcp_proxy_port=9000,
        mcp_proxy_command="company-mcp-proxy",
        health_port=None,
        session_archive_bucket=None,
    )
