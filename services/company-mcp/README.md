# Company MCP

The deterministic tool layer between Hermes agents and company state. It is the only
component with write access to business records (DynamoDB), and it publishes the business
events that describe each change (EventBridge).

- **Identity**: callers are recognised by their IAM role, not by anything they claim. The caller presigns an
  STS `GetCallerIdentity` request (bound to this MCP's audience) and sends it as
  `Authorization: Company-STS <token>`; the MCP asks STS who signed it and maps role
  `<AGENT_ROLE_PREFIX><agent-id>` to the agent. In ECS the `company-mcp-proxy` (started inside the agent container by the runtime) does the signing, so Hermes
  just calls `http://127.0.0.1:9000/mcp`. Each agent only sees and can only call the tools granted in
  `src/company_mcp/policy.py`.
- **Only writer, only publisher**: agents have no DynamoDB or EventBridge access; every change and every business
  event comes from an MCP operation.
- **Authority**: limits such as the Commercial Manager's 10% discount are enforced here, not in prompts.
  Exceeding them fails with `ESCALATION_REQUIRED` until the CEO approves a decision.
- **Idempotency**: record ids are derived from the request (`INV-<order>`, `TASK-<type>-<correlation>`, ...)
  and written with conditional writes, so retries and duplicate events never duplicate business actions.
- **Events**: each change stores its event on the record in the same write, then publishes and clears it.
  If publishing fails, the next call touching that record publishes it (same `eventId`).
- **Errors**: JSON `{errorCode, message, retryable}`; unexpected errors are masked.

## Develop

```sh
uv sync
uv run pytest
```

## Run locally

The whole company (LocalStack, this MCP, the agents) runs from the repository root;
see `../../local/README.md`.

## Environment

| Variable | Purpose |
|---|---|
| `TABLE_NAME` | DynamoDB table (required) |
| `EVENT_BUS_NAME` | EventBridge bus; empty logs events instead of publishing |
| `EVENT_SOURCE` | EventBridge `Source` (default `company.mcp`) |
| `COMPANY_BUCKET` | Bucket for invoice documents; empty skips them |
| `PORT`, `LOG_LEVEL`, `AWS_REGION` | Server settings |
| `AUTH_MODE` | `iam` (default) or `header` (trusts `X-Agent-Id`; local only) |
| `MCP_AUDIENCE` | Audience callers must sign for, e.g. `agentes-sas-dev-company-mcp` |
| `AGENT_ROLE_PREFIX` | Role name prefix, e.g. `agentes-sas-dev-` (role `agentes-sas-dev-accountant` → `accountant`) |
| `ALLOWED_ACCOUNT_ID` | Account callers must belong to; defaults to the MCP's own account |
| `AWS_ENDPOINT_URL` | Standard boto3 override; points every client at LocalStack locally |

The proxy (`company-mcp-proxy`) reads `UPSTREAM_URL`, `MCP_AUDIENCE`, `AWS_REGION` and `LISTEN_PORT` (9000);
with `SIGNING_MODE=header` (local only) it sends `X-Agent-Id: $AGENT_ID` instead of a signed token.

## Seed demo data

`company-mcp-seed` resets products (`LAPTOP-PRO` x10, `MONITOR-27` x25, `DOCK-USB-C` x2) and
customers (`CUST-ACME`, `CUST-GLOBEX` with a 5000 credit limit, `CUST-BLOCKED`). Orders are untouched.
