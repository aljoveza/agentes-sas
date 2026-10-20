"""Settings from the environment (set by infrastructure/modules/ecs-agent, or local/compose.yaml)."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    agent_id: str
    agent_role: str
    department: str
    reports_to: str
    region: str
    model_id: str
    bedrock_role_arn: str | None
    queue_url: str
    queue_endpoint_url: str | None
    mcp_url: str
    prompt_source: str
    prompt_file: Path | None
    prompt_application_id: str | None
    prompt_environment_id: str | None
    prompt_profile_id: str | None
    hermes_home: Path
    workdir: Path
    max_turns: int
    turn_timeout: int
    resume_on_start: bool
    log_level: str
    activity_url: str | None
    plugins_dir: Path
    mcp_proxy_upstream: str | None
    mcp_proxy_port: int
    mcp_proxy_command: str
    health_port: int | None
    session_archive_bucket: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ

        def required(name: str) -> str:
            value = env.get(name)
            if not value:
                raise SystemExit(f"{name} is required")
            return value

        source = env.get("PROMPT_SOURCE", "appconfig")
        return cls(
            agent_id=required("AGENT_ID"),
            agent_role=required("AGENT_ROLE"),
            department=required("DEPARTMENT"),
            reports_to=env.get("REPORTS_TO", ""),
            region=env.get("AWS_REGION", "us-east-1"),
            model_id=required("BEDROCK_MODEL_ID"),
            # Invoke Bedrock through a role in another account (one with Bedrock quota).
            bedrock_role_arn=env.get("BEDROCK_ROLE_ARN") or None,
            queue_url=required("AGENT_QUEUE_URL"),
            # Local only: the inbox lives in LocalStack while Bedrock is real AWS.
            queue_endpoint_url=env.get("QUEUE_ENDPOINT_URL") or None,
            mcp_url=required("COMPANY_MCP_URL"),
            prompt_source=source,
            prompt_file=Path(required("PROMPT_FILE")) if source == "file" else None,
            prompt_application_id=required("PROMPT_APPLICATION_ID") if source == "appconfig" else None,
            prompt_environment_id=required("PROMPT_ENVIRONMENT_ID") if source == "appconfig" else None,
            prompt_profile_id=required("PROMPT_PROFILE_ID") if source == "appconfig" else None,
            hermes_home=Path(env.get("HERMES_HOME", "/opt/data")),
            workdir=Path(env.get("AGENT_WORKDIR", "/tmp/agent-work")),
            max_turns=int(env.get("HERMES_MAX_TURNS", "30")),
            turn_timeout=int(env.get("HERMES_TURN_TIMEOUT", "600")),
            resume_on_start=env.get("RESUME_ON_START", "true").lower() == "true",
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            activity_url=env.get("AGENT_ACTIVITY_URL") or None,
            plugins_dir=Path(env.get("AGENT_PLUGINS_DIR", "/opt/agent-runtime/plugins")),
            # ECS Express Mode only: run the signing proxy in this container, answer health checks.
            mcp_proxy_upstream=env.get("MCP_PROXY_UPSTREAM") or None,
            mcp_proxy_port=int(env.get("MCP_PROXY_PORT", "9000")),
            mcp_proxy_command=env.get("MCP_PROXY_COMMAND", "/opt/mcp-proxy/.venv/bin/company-mcp-proxy"),
            health_port=int(env["HEALTH_PORT"]) if env.get("HEALTH_PORT") else None,
            # Archive each turn's transcript to S3 (sessions/archives/<agent>/<order>/), for Nexo.
            session_archive_bucket=env.get("SESSION_ARCHIVE_BUCKET") or None,
        )
