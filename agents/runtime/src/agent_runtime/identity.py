"""Turns the employee's job description (prompts/*.yaml) into Hermes' SOUL.md and config.yaml."""

from typing import Any

import yaml

from .config import Settings

ACTIVITY_PLUGIN = "company-activity"
BEDROCK_PROFILE = "bedrock"

REQUIRED_KEYS = ("version", "role", "department", "responsibilities", "procedures", "allowed_tools")

OPERATING_RULES = """\
## How you work

- You are one employee of agentes-sas, an e-commerce company run by AI agents. You never talk to
  people; you react to business events delivered to your inbox, one event per turn.
- Your company tools (prefixed `mcp_company_`) are the only way to see or change anything. Business
  state lives in the company system, not in your memory: always read the current state before acting.
- Follow your procedures. When your part for this event is done, stop and reply with one short line
  summarising what you did. If the event needs nothing from you, do nothing and say so.
- Every company write is idempotent. If a record already exists, that is fine: the work was already
  done (possibly by you before a restart). Continue from the current state.
- Tool errors are JSON with `errorCode`, `message` and `retryable`:
  - `ESCALATION_REQUIRED`: the action exceeds your authority. Follow the message; never work around it.
  - `FORBIDDEN`: it is not your job. Delegate to the right person or stop.
  - `retryable: true`: a temporary problem; retry the same call once.
  - anything else: a business outcome. Report it (fail the task if you own one) instead of retrying.
- Before acting on an order, see where it stands with `get_order_status` (reservation, payment,
  invoice, shipment, tasks, decisions). A CANCELLED order needs no more work, except a refund.
- Delegated work is a task. Workers always close their tasks with `complete_task` or `fail_task`.
  Retry a task at most once; after that, resolve the order (for example cancel it) or escalate.
- Never invent ids, amounts or results. Use the correlation id (the order id) for related work.
"""


def validate(prompt: dict[str, Any], agent_id: str) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in prompt]
    if missing:
        raise SystemExit(f"Prompt for {agent_id} is missing: {', '.join(missing)}")


def render_soul(prompt: dict[str, Any], settings: Settings) -> str:
    def bullets(items: Any) -> str:
        return "\n".join(f"- {item}" for item in items or []) or "- (none)"

    authority = prompt.get("authority") or {}
    authority_text = "\n".join(f"- {key}: {value}" for key, value in authority.items()) or "- No delegated authority."
    reports_to = prompt.get("reports_to") or "nobody (you lead the company)"

    return f"""# {prompt['role']}

You are the **{prompt['role']}** (agent id `{settings.agent_id}`) in the **{prompt['department']}**
department. You report to: {reports_to}. Job description version {prompt['version']}.

## Responsibilities
{bullets(prompt.get('responsibilities'))}

## Authority
{authority_text}

Escalate when: {', '.join(prompt.get('escalation_rules') or []) or 'never'}.

## Procedures
{bullets(prompt.get('procedures'))}

{OPERATING_RULES}"""


def render_hermes_config(settings: Settings) -> str:
    config = {
        "model": {"default": settings.model_id, "provider": "bedrock"},
        "bedrock": {"region": settings.region},
        # Only the company MCP: no terminal, files, web or browser for corporate employees.
        "platform_toolsets": {"cli": ["mcp-company"]},
        "mcp_servers": {
            "company": {
                "url": settings.mcp_url,
                "timeout": 120,
                "connect_timeout": 15,
                "tools": {"resources": False, "prompts": False},
            }
        },
        # Each employee has a dozen company tools: hand them to the model directly instead of
        # behind Hermes' tool_search/tool_call bridge (fewer round trips, fewer malformed calls).
        "tools": {"tool_search": {"enabled": "off"}},
        "agent": {"max_turns": settings.max_turns},
        # Business state lives in the company system; Hermes memory stays off so it cannot drift.
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
    }
    if settings.activity_url:
        # Observability only: publishes tool activity to the agent-activity service.
        config["plugins"] = {"enabled": [ACTIVITY_PLUGIN]}
    return yaml.safe_dump(config, sort_keys=False)


def render_aws_config(settings: Settings) -> str | None:
    """AWS profile for Hermes' Bedrock calls when inference runs in another account: the task role
    (ECS container credentials) assumes BEDROCK_ROLE_ARN, and the session is named after the
    employee so the other account's CloudTrail shows who thought what. None: use the task role."""
    if not settings.bedrock_role_arn:
        return None
    return (
        f"[profile {BEDROCK_PROFILE}]\n"
        f"role_arn = {settings.bedrock_role_arn}\n"
        "credential_source = EcsContainer\n"
        f"role_session_name = {settings.agent_id}\n"
        f"region = {settings.region}\n"
    )
