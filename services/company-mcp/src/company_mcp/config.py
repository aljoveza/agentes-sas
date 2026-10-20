"""Runtime settings, from the environment variables set by infrastructure/modules/company-mcp."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    table_name: str
    event_bus_name: str | None
    event_source: str
    bucket: str | None
    region: str
    host: str
    port: int
    log_level: str
    auth_mode: str
    audience: str
    agent_role_prefix: str
    account_id: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            table_name = os.environ["TABLE_NAME"]
        except KeyError:
            raise SystemExit("TABLE_NAME is required") from None
        return cls(
            table_name=table_name,
            event_bus_name=os.environ.get("EVENT_BUS_NAME") or None,
            event_source=os.environ.get("EVENT_SOURCE", "company.mcp"),
            bucket=os.environ.get("COMPANY_BUCKET") or None,
            region=os.environ.get("AWS_REGION", "us-east-1"),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            # "iam" verifies signed callers; "header" trusts X-Agent-Id and is for local development only.
            auth_mode=os.environ.get("AUTH_MODE", "iam"),
            audience=os.environ.get("MCP_AUDIENCE", "company-mcp"),
            agent_role_prefix=os.environ.get("AGENT_ROLE_PREFIX", ""),
            account_id=os.environ.get("ALLOWED_ACCOUNT_ID") or None,
        )
