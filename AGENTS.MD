# [AGENTS.md](http://AGENTS.md)

## Project Overview

This repository implements a distributed multi-agent AI company running on AWS.

The system simulates an e-commerce company operated by autonomous AI agents organized into corporate roles and departments.

The initial organization contains three departments:

- Accounting
- Operations
- Commercial / Sales

Each department contains:

- One manager/orchestrator
- One worker/subagent

All department managers report to a CEO agent.

The agents are implemented using **Hermes Agent** and run as independent Docker containers on **Amazon ECS Express Mode** (Fargate): one Express service per employee, plus Express services for the Company MCP, the agent-activity service and Nexo. Express Mode targets web services, so each employee (a queue worker) answers a `/health` check and runs its IAM signing proxy inside its own container (see `infrastructure/README.md`).

The project is intended primarily as a technical showcase for a DevOpsDays Bogotá 2026 talk demonstrating how traditional DevOps and distributed-systems concepts can be applied to autonomous AI agents.

The architecture should emphasize:

- Infrastructure as Code
- Event-driven architecture
- Containerized agents
- Least privilege
- Stateless compute
- Durable external state
- Prompt/configuration management
- Agent observability
- Agent orchestration
- Failure recovery
- Idempotency
- Human-readable architecture

## Implementation Status

| Area | Status |
|---|---|
| Company MCP (29 tools, business rules, idempotency, IAM caller identity) | Done (`services/company-mcp`) |
| Seven Hermes employees, agent runtime, job descriptions | Done (`agents/`, `prompts/`), running locally |
| Event-driven workflow (EventBridge, per-employee SQS inboxes, DLQs) | Done locally on LocalStack; AWS Terraform written |
| Decision escalation (discounts, refunds) | Done |
| Business edge cases (out of stock, cancellations, refunds, ship-after-payment) | Done |
| Live company view (Nexo + agent-activity service) | Done locally |
| Failure handling (retries, DLQs + redrive, restart recovery) | Done |
| AWS deployment | Terraform validated, all ten services on ECS Express Mode (agents, MCP, agent-activity, Nexo); not yet applied |

## Local Development

The whole company runs locally (see `README.md` and `local/README.md`):

- LocalStack (started with `lstk`) emulates EventBridge, SQS, DynamoDB, S3 and CloudWatch Logs. The same
  Terraform modules and org chart create them (`infrastructure/local`).
- The Company MCP, the agent-activity service and the seven employees (each with its `mcp-proxy`) run with
  docker compose (`local/compose.yaml`). Agents call real Amazon Bedrock with credentials exported by
  `local/aws-credentials.sh` into a mounted file (refreshable without restarts).
- Locally the MCP trusts an `X-Agent-Id` header added by each employee's proxy (`AUTH_MODE=header`);
  in AWS the proxy signs calls with the task role and the MCP verifies them through STS.
- Prompts are mounted from `prompts/` locally and served by AppConfig in AWS.
- Nexo (`services/nexo`) runs on the host: `local/nexo`.

---

# Core Concept

The system maps traditional corporate concepts into software primitives.


| Corporate concept     | Technical implementation                 |
| --------------------- | ---------------------------------------- |
| Employee              | Hermes agent                             |
| Job description       | Agent prompt / [SOUL.md](http://SOUL.md) |
| Department            | Bounded context                          |
| Manager               | Orchestrator agent                       |
| CEO                   | Top-level decision agent                 |
| Company procedures    | MCP tools                                |
| Communication         | Events                                   |
| Employee permissions  | IAM                                      |
| Business records      | DynamoDB                                 |
| Documents / artifacts | S3                                       |
| Office                | Nexo (demo store + live company view)    |
| Employee activity     | Observability events                     |
| Company policy        | Prompt + deterministic guardrails        |
| Escalation            | DecisionRequested event                  |
| Deployment            | ECS task/service                         |
| Employee replacement  | Stateless container restart              |


A central idea of the project is:

> Prompts describe what an agent SHOULD do. IAM and deterministic APIs define what an agent CAN do.

---

# High-Level Architecture

The architecture is divided into four primary planes.

## 1. AI Plane

Responsible for reasoning.

Components:

- Hermes Agent
- Amazon Bedrock

Hermes agents should use Amazon Bedrock using AWS IAM credentials supplied through ECS Task Roles.

Avoid storing static Bedrock API credentials.

Conceptual flow:

```
Hermes
   |
   v
ECS Task Role
   |
   v
Amazon Bedrock
```

---

# 2. Business Plane

Responsible for communication between agents and business workflow.

Components:

- Amazon EventBridge
- Amazon SQS

Agents SHOULD NOT communicate primarily through direct synchronous HTTP calls.

The preferred communication model is asynchronous and event-driven.

Example:

```
OrderValidated (published by the Company MCP)
    |
    v
EventBridge
    |
    +---------------------------+
    |                           |
    v                           v
operations-manager inbox    accounting-manager inbox
    |                           |
    v                           v
Operations Manager          Accounting Manager      (in parallel)
```

Each **employee** has its own SQS inbox (`<prefix>-<agent-id>`) with a dead-letter queue. Department
queues were rejected: a manager and its worker sharing one queue would consume each other's messages.

EventBridge rules route events to inboxes. They are derived from the org chart
(`infrastructure/modules/organization`), shared by the AWS and LocalStack stacks:

- department subscriptions (for example `OrderValidated` → operations-manager and accounting-manager);
- directed events by payload field: `WorkAssigned` → `payload.assignedTo`, `WorkCompleted`/`WorkFailed` →
  `payload.assignedBy`, `DecisionApproved`/`DecisionRejected` → `payload.requestedBy`.

Only the Company MCP publishes business events (`Source = company.mcp`), right after the state change they
describe. Agents have no EventBridge permissions: they read their inbox and act through MCP tools.

The system must tolerate duplicate delivery.

All business mutations must therefore be idempotent.

---

# 3. State Plane

Responsible for durable company state.

Components:

- Amazon DynamoDB
- Amazon S3

IMPORTANT:

Do NOT use Hermes internal memory as the authoritative source of business state.

Business state includes:

- Orders
- Customers
- Inventory
- Payments
- Invoices
- Shipments
- Decisions

These records belong in DynamoDB.

Example tables may include:

```
Orders
Inventory
Payments
Shipments
AgentTasks
Decisions
```

A single-table DynamoDB design is acceptable if it simplifies the implementation.

## S3

S3 is used for durable object storage.

Suggested structure:

```
s3://<company-bucket>/
    agents/
        ceo/
        accounting-manager/
        accountant/
        operations-manager/
        fulfillment/
        commercial-manager/
        sales-rep/

    artifacts/
        invoices/
        reports/
        exports/

    memory/

    sessions/
        archives/

    audit/
```

S3 must NOT be treated as a POSIX replacement for Hermes SQLite state.

Do NOT mount a standard S3 bucket as `/opt/data` and expect SQLite to operate correctly.

Hermes runtime state may remain ephemeral.

Durable information that matters to the system must be explicitly externalized.

---

# 4. Observability Plane

Responsible for showing what agents are doing.

The demo surface is **Nexo** (`services/nexo`), the project's own demo store:

- **La tienda**: a customer browses a catalog read live from the Company MCP and places an order.
- **Detrás del pedido**: for one order, its milestones, the seven employees' live state on that
  order, the business-event story, and a live activity timeline (tool calls, delegation,
  escalation, decisions).
- **Dentro de cada agente**: each agent's Hermes transcript for that order.

Live agent activity comes from the **agent-activity service** (`services/agent-activity`):

```
CEO Hermes --------------------\
                                \
Accounting Manager -------------\
                                  \
Accountant -----------------------> agent-activity service ----> Nexo
                                  /     (per employee, per order)
Operations Manager -------------/
                                /
Sales Manager -----------------/
```

Each agent publishes over HTTP from two places: the agent runtime (turns, heartbeats) and the
`company-activity` Hermes plugin (tool calls, and inter-agent messages derived from Company MCP
calls). The plugin is based on the hooks of
[hermes-pixel-office](https://github.com/teknium1/hermes-pixel-office) (MIT); the Pixel Office UI
itself was dropped in favour of Nexo.

An employee handles one business event at a time, so every tool call and message between its
`turn_started` and `turn_finished` belongs to that turn's order.

Business events and observability events MUST remain conceptually separate.

Business events:

```
EventBridge + SQS
```

Observability events:

```
agent-activity service (in memory; rebuilt by heartbeats after a restart)
```

---

# Agent Activity Events

Published by every agent (runtime + Hermes plugin):

- employee_ready, heartbeat
- turn_started, turn_finished, turn_failed (one turn = one business event)
- tool_started, tool_finished
- agent_message (delegate, report, escalate, decide)

Every event carries corporate metadata.

Recommended payload:

```
{
  "eventType": "tool_started",
  "timestamp": "ISO-8601",
  "agentId": "accountant",
  "agentName": "Accountant",
  "department": "accounting",
  "reportsTo": "accounting-manager",
  "sessionId": "string",
  "taskId": "string",
  "metadata": {"tool": "mcp__company__create_invoice"}
}
```

Inter-agent message events:

```
{
  "eventType": "agent_message",
  "from": "operations-manager",
  "to": "fulfillment",
  "kind": "delegate",
  "correlationId": "ORDER-123",
  "taskId": "TASK-123",
  "message": "FulfillOrder ORDER-123"
}
```

These events exist only for visualization and observability.

They must never be required for business correctness.

---

# Initial Organization

The MVP organization contains seven persistent agents.

```
CEO
 |
 +------------------+------------------+
 |                  |                  |
 v                  v                  v
```

Accounting Operations Commercial  
Manager Manager Manager  
| | |  
v v v  
Accountant Fulfillment Sales Rep

Agent identifiers:

```
ceo
accounting-manager
accountant
operations-manager
fulfillment
commercial-manager
sales-rep
```

---

# Agent Deployment Model

Each persistent employee should be treated as an independent logical agent.

Preferred model:

```
one logical employee
    =
one Hermes agent configuration
    =
one ECS service/task
```

For the initial demo:

```
desiredCount = 1
```

Do NOT automatically scale managers horizontally.

For example, this should not occur:

```
accounting-manager
    |
    +-- replica 1
    +-- replica 2
    +-- replica 3
```

Corporate actors require stable identities.

Horizontal worker pools may be explored later, but they are outside the MVP.

---

# Stateless Agent Principle

ECS containers should be considered disposable.

The preferred principle is:

> Agents are cattle, not pets.

If an ECS task is destroyed, the system should be able to start another instance and reconstruct the agent from external configuration and durable state.

A restarted agent should recover from:

- prompt configuration
- durable business state
- persistent agent memory if implemented
- outstanding AgentTasks
- business events

Do not make application correctness depend on the local container filesystem.

---

# Hermes Runtime State

Hermes may use local ephemeral storage for its native runtime files.

This may include:

- sessions
- local SQLite state
- temporary files
- caches

Do not directly synchronize a live SQLite database to S3.

If session archival is needed, use a safe export or snapshot mechanism.

For the MVP, session history does not need to survive an agent replacement if all important business state is externalized.

---

# Agent Memory

Agent memory and business state are different concepts.

Agent memory answers questions such as:

- What has this employee learned?
- What preferences or operational knowledge should persist?
- What long-term context should influence future reasoning?

Business state answers:

- What orders exist?
- Has an invoice been created?
- Has payment been received?
- Was inventory reserved?
- Has the shipment been dispatched?

Business state MUST live in DynamoDB or another deterministic external datastore.

For the first implementation, agent long-term memory may be simple.

Possible implementations:

1. S3-backed memory snapshots
2. Custom Hermes memory provider
3. Future integration with Amazon Bedrock AgentCore Memory

AgentCore Memory is NOT required for the MVP.

---

# Prompt Management

Prompts represent corporate job descriptions.

Prompts should be centrally managed.

Preferred service:

Amazon AppConfig

Each persistent employee should have a role definition.

Suggested structure:

```
prompts/
    ceo.yaml

    accounting/
        manager.yaml
        accountant.yaml

    operations/
        manager.yaml
        fulfillment.yaml

    commercial/
        manager.yaml
        sales-rep.yaml
```

Prompt configuration should define at minimum:

```
role: Accounting Manager

department: accounting

reports_to: ceo

responsibilities:
  - Review accounting events
  - Coordinate accounting tasks
  - Validate invoices
  - Reconcile payments

authority:
  approval_limit: 5000

escalation_rules:
  - transaction_above_5000
  - suspicious_transaction

allowed_tools:
  - get_order
  - get_invoice
  - create_invoice
  - register_payment
  - assign_task
  - request_ceo_approval
```

The prompt may be converted into or used to generate the Hermes [`SOUL.md`](http://SOUL.md).

Prompts should include a version.

Every task or major decision should record the prompt version used by the agent when practical.

---

# MCP / Company API

Agents must not receive unrestricted AWS or database access.

Prefer a deterministic internal tool layer exposed through MCP.

Conceptually:

```
Hermes
   |
   v
Company MCP Server
   |
   +--> DynamoDB
   +--> EventBridge
   +--> S3
   +--> other APIs
```

MCP tools (`services/company-mcp`, streamable HTTP):

```
whoami
get_order, list_orders, get_order_status, get_order_margin, place_order (order simulator / store)
validate_order, cancel_order
get_customer, get_inventory, reserve_inventory, release_inventory
create_shipment, track_shipment
register_payment, create_invoice, get_invoice, refund_payment
assign_task, get_task, list_my_tasks, start_task, complete_task, fail_task
request_ceo_approval, get_decision, list_pending_decisions, approve_decision, reject_decision
```

Each employee only sees and can only call the tools its role grants (`policy.py`; mirrored in each
prompt's `allowed_tools` and checked by tests). Deterministic rules enforced by the MCP include: discounts
above 10% need an approved CEO decision; refunds above 5000 need an approved CEO decision; nothing ships
before the payment is RECEIVED; cancelled orders accept no further work (ORDER_CANCELLED); shipped orders
cannot be cancelled. Errors are JSON `{errorCode, message, retryable}`.

MCP tools should enforce deterministic validation.

The LLM should never be trusted as the only layer enforcing business rules.

---

# IAM Strategy

Every agent role must follow least privilege.

Example:

Accounting agent:

```
ALLOW:
  read orders
  read payments
  create invoices
  register reconciliations

DENY / NO PERMISSION:
  reserve inventory
  create shipments
```

Operations agent:

```
ALLOW:
  read orders
  read inventory
  reserve inventory
  create shipment

NO PERMISSION:
  create invoice
  modify payment records
```

CEO:

```
Has broader decision capabilities but should still operate through controlled APIs.
```

IAM permissions should reflect corporate authority whenever technically practical.

As implemented:

- **Agent task role**: invoke the configured Bedrock model (or, with `bedrock_role_arn`, only assume a
  Bedrock-only role in another account: Hermes alone uses it, with the employee id as session name), read/delete messages on its own inbox, read its
  own AppConfig profile, read/write its own S3 prefixes (`agents/<id>/`, `memory/<id>/`,
  `sessions/archives/<id>/`). No DynamoDB, no EventBridge.
- **Company MCP task role**: DynamoDB Get/BatchGet/Query/Put/Update/ConditionCheck (no Delete, no Scan),
  `events:PutEvents` only as `company.mcp`, S3 `artifacts/` and `audit/`.
- **Caller identity**: each employee's signing proxy (`company-mcp-proxy`, inside the agent container in AWS) presigns an STS `GetCallerIdentity` request with
  the task role; the MCP verifies it with STS and maps role `<prefix>-<agent-id>` to the employee. Tool-level
  authority (which employee may call what) is enforced by the MCP's policy.

---

# Business Event Model

All important workflow transitions should generate explicit events.

Initial event catalog:

```
OrderPlaced
OrderValidated

PaymentReceived
PaymentRejected

InventoryReserved
InventoryUnavailable

InvoiceCreated

ShipmentRequested
ShipmentCreated
ShipmentDispatched
ShipmentDelivered

RefundRequested
OrderCancelled
PaymentRefunded

WorkAssigned
WorkCompleted
WorkFailed

DecisionRequested
DecisionApproved
DecisionRejected
```

Recommended envelope:

```
{
  "eventId": "uuid",
  "eventType": "OrderPlaced",
  "eventVersion": "1",
  "timestamp": "ISO-8601",
  "correlationId": "ORDER-123",
  "causationId": null,
  "source": "order-simulator",
  "payload": {}
}
```

Every event should contain:

- globally unique eventId
- event type
- schema version
- timestamp
- correlationId
- source
- payload

Prefer explicit schemas.

Version events from the beginning.

---

# Idempotency

SQS and EventBridge workflows may deliver messages more than once.

All business actions must be idempotent.

Example:

Calling:

```
create_invoice(order_id="ORDER-123")
```

twice must not create two invoices.

Preferred strategies:

- deterministic resource IDs
- DynamoDB conditional writes
- idempotency records
- event IDs
- task status transitions

---

# Agent Task Model

Agent-to-agent delegation should be represented explicitly.

Suggested object:

```
{
  "taskId": "TASK-123",
  "type": "CreateShipment",
  "assignedBy": "operations-manager",
  "assignedTo": "fulfillment",
  "status": "PENDING",
  "correlationId": "ORDER-123",
  "goal": "Create shipment for order ORDER-123",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601"
}
```

Suggested states:

```
PENDING
ASSIGNED
IN_PROGRESS
COMPLETED
FAILED
ESCALATED
```

Task types used by the procedures: `ValidateCustomer` (sales-rep), `ReserveStock` and `ShipOrder`
(fulfillment), `SettleOrder` and `RefundOrder` (accountant). Task ids are deterministic
(`TASK-<type>-<orderId>`): assigning the same task twice is a no-op; assigning a FAILED task starts a new
attempt.

Store tasks durably.

Do not rely exclusively on conversational context to remember delegated work.

---

# Decision Escalation

Managers have limited authority.

Important decisions must be escalated to the CEO.

Example:

```
Sales Rep
    |
    v
Commercial Manager
    |
    | discount exceeds authorization
    v
DecisionRequested
    |
    v
CEO
```

The CEO may respond with:

```
DecisionApproved
```

or:

```
DecisionRejected
```

Example decision:

```
{
  "decisionId": "DECISION-123",
  "requestedBy": "commercial-manager",
  "decisionType": "SPECIAL_DISCOUNT",
  "correlationId": "ORDER-123",
  "requestedValue": 25,
  "managerAuthorityLimit": 10,
  "status": "PENDING"
}
```

Decision records should be stored durably.

---

# Demo Scenario

The primary demo should simulate an e-commerce sale.

Example:

```
Order #DEVOPS-2026-001
Product: Laptop
Quantity: 3
Total: 9000
```

Flow:

1. Simulator emits `OrderPlaced`.
2. Commercial Manager receives the event.
3. Sales Rep validates customer and commercial conditions.
4. Commercial emits `OrderValidated`.
5. Operations Manager and Accounting Manager receive `OrderValidated` in parallel.
6. Fulfillment reserves the inventory (`ReserveStock`).
7. Accountant registers the payment and issues the invoice (`SettleOrder`).
8. Once the stock is reserved and the payment received, Fulfillment creates the shipment (`ShipOrder`).
9. Nexo shows the order's story and every agent's activity live.

The scenario should demonstrate parallel activity where possible.

---

# Escalation Demo Scenario

At least one scenario should intentionally exceed a manager's authority.

Example:

```
Customer requests 25% discount.
```

Commercial Manager policy:

```
maximum discount = 10%
```

Expected flow:

```
Sales Rep
    |
    v
Commercial Manager
    |
    v
DecisionRequested
    |
    v
CEO
    |
    +--> inspect order
    +--> inspect margin
    +--> inspect customer
    +--> inspect inventory
    |
    v
DecisionApproved / DecisionRejected
```

Nexo should make this escalation visible (the manager waiting for the CEO, the decision).

---

# Business Edge Cases

Handled by procedures (prompts) on top of deterministic Company MCP rules:

- **Out of stock**: Fulfillment fails `ReserveStock` with INVENTORY_UNAVAILABLE; Operations cancels the
  order (`cancel_order` releases stock and voids the invoice atomically); Accounting refunds a received
  payment. Refunds above 5000 need a CEO `REFUND` decision (enforced by `refund_payment`).
- **Nothing ships unpaid**: `create_shipment` requires a RECEIVED payment; Operations ships after both
  `InventoryReserved` and `PaymentReceived`.
- **Customer not eligible / over credit, discount rejected by the CEO, payment mismatch**: the owning
  manager cancels the order with the reason.
- **Cancelled orders**: every further operation answers ORDER_CANCELLED; shipped orders cannot be cancelled.
- **Technical failures**: a task is retried once; after a second failure the manager resolves the order.

---

# Failure Scenarios

The architecture should eventually demonstrate at least one failure case.

Examples:

## Agent restart

Kill the Fulfillment ECS task during an operation.

Expected behavior:

- ECS recreates agent.
- outstanding task remains durable.
- operation resumes or is retried safely.
- no duplicate shipment is created.

## Duplicate event

Publish `OrderPlaced` twice.

Expected behavior:

- only one logical order exists.
- duplicate processing does not create duplicate invoices or shipments.

## Agent unavailable

Operations agent is temporarily unavailable.

Expected behavior:

- SQS buffers messages.
- processing resumes when the consumer returns.

These are valuable DevOps demonstrations and should influence architecture decisions.

---

# Infrastructure as Code

All AWS infrastructure should be created using Infrastructure as Code.

Preferred:

Terraform.

Avoid manual console-only infrastructure.

Modules (one AWS environment; the root configuration is `infrastructure/`, state in an existing S3
bucket via `backend.conf`):

```
infrastructure/
    main.tf, backend.conf      AWS root
    local/                     LocalStack root (business and state planes only)
    modules/
        company/               composition: the whole company
        organization/          org chart and event routing (shared with local/)
        express-service/       one ECS Express Mode service (used by every service below)
        ecs-agent/             one employee: Express service (1 task), task role
        company-mcp/           the MCP service (internal HTTPS URL), its role
        nexo/                  the demo store (public URL, basic auth), read-only trace role
        event-bus/             bus, routing rules, archive, audit log
        queue/                 inbox + DLQ
        dynamodb/              single table
        s3/                    company bucket
        appconfig/             job descriptions (one AppConfig environment per employee)
        network/               VPC, public + private subnets, NAT, gateway endpoints
```

The agent ECS module should support deploying the same image with different:

- agent IDs
- prompt configuration
- IAM roles
- queue configuration
- environment variables

---

# Repository Layout

```
.
├── AGENTS.md, README.md
├── agents/
│   ├── Dockerfile               Hermes image + agent runtime
│   ├── runtime/                 event adapter: inbox → one Hermes turn per event
│   └── plugins/company-activity Hermes plugin publishing activity events
├── prompts/                     job descriptions (ceo.yaml, <department>/*.yaml)
├── services/
│   ├── company-mcp/             Company MCP (+ mcp-proxy, seed data)
│   ├── agent-activity/          live activity per employee and per order
│   ├── order-simulator/         scenarios, random market, traces
│   └── nexo/                    demo store and live company view
├── demo/scenarios/              reproducible scenarios (JSON)
├── infrastructure/              Terraform (AWS root, local/, modules/)
└── local/                       docker compose and helper scripts for running locally
```

Tests live next to each component (`<component>/tests`).

---

# Service Boundaries

Keep responsibilities separated.

## Agent Runtime

Responsibilities:

- run Hermes
- load identity/prompt
- consume assigned work
- call MCP tools (business events are emitted by the MCP, never by agents)
- emit observability events (turns, heartbeats; tool activity via the Hermes plugin)
- recover unfinished tasks at startup (a recovery turn)
- in AWS: run the IAM signing proxy for its own MCP calls and answer the load balancer's `/health`

Should NOT:

- publish business events directly
- implement raw business database access
- contain infrastructure logic
- own the activity view's global state

## Company MCP

Responsibilities:

- expose deterministic tools
- validate arguments
- enforce business invariants
- write business state
- generate business events where appropriate

## Event Adapter

Part of the agent runtime (`agents/runtime`), not a separate service.

Responsibilities:

- consume SQS
- translate business event into agent work
- invoke Hermes (`hermes -z`, one turn per event)
- track execution status (delete on success, retry later on failure; DLQ after 3 attempts)

Do not embed business logic unnecessarily.

## Agent Activity Service

Responsibilities:

- receive Hermes activity events from every agent
- maintain temporary visualization state, per employee and per order
- expose it to Nexo

It must not be part of business-critical workflow.

## Order Simulator

Responsibilities:

- produce deterministic demo scenarios
- generate reproducible orders (through the MCP's `place_order`, as the `order-simulator` identity)
- trigger happy-path and failure scenarios
- generate a random but replayable market (`--seed`)

## Nexo

The demo store and live company view (`services/nexo`, Spanish UI). It places orders through the Company
MCP and shows, per order, the business events, durable records, live agent activity and transcripts. It
must not be part of business-critical workflow beyond placing orders like any customer. In AWS it is the
only public service (ECS Express Mode, HTTP basic auth with a password from SSM); it calls the MCP as the
`order-simulator` role, reads the trace with read-only DynamoDB/Logs permissions, and reads transcripts
from the session archive in S3: after every turn the agent runtime exports its Hermes session (Markdown,
redacted) to `sessions/archives/<agent>/<order>/<session>.md`. Locally they come from `docker compose exec`.

---

# Configuration

Avoid hardcoded service identifiers.

Use environment variables for deployment-specific configuration.

Agent runtime variables:

```
AGENT_ID, AGENT_ROLE, DEPARTMENT, REPORTS_TO
AWS_REGION
BEDROCK_MODEL_ID                 Bedrock model id or inference profile
BEDROCK_ROLE_ARN                 optional: role in another account Hermes assumes for Bedrock
AGENT_QUEUE_URL                  the employee's inbox
QUEUE_ENDPOINT_URL               local only (LocalStack)
COMPANY_MCP_URL                  http://127.0.0.1:9000/mcp (the signing proxy)
MCP_PROXY_UPSTREAM               AWS only: start the signing proxy in this container, forwarding here
MCP_PROXY_PORT, MCP_AUDIENCE     the in-container proxy's port (9000) and token audience
HEALTH_PORT                      AWS only: serve /health for the Express Mode load balancer
PROMPT_SOURCE                    appconfig | file
PROMPT_APPLICATION_ID, PROMPT_ENVIRONMENT_ID, PROMPT_PROFILE_ID   (appconfig)
PROMPT_FILE                      (file, local)
COMPANY_BUCKET
AGENT_ACTIVITY_URL               observability; empty disables publishing
HERMES_MAX_TURNS, HERMES_TURN_TIMEOUT, RESUME_ON_START
LOG_LEVEL
```

Company MCP: `TABLE_NAME`, `EVENT_BUS_NAME`, `EVENT_SOURCE`, `COMPANY_BUCKET`, `AUTH_MODE` (iam | header),
`MCP_AUDIENCE`, `AGENT_ROLE_PREFIX`, `ALLOWED_ACCOUNT_ID`. mcp-proxy: `UPSTREAM_URL`, `MCP_AUDIENCE`,
`SIGNING_MODE` (iam | header), `AGENT_ID`, `LISTEN_PORT`. Locally, `AWS_ENDPOINT_URL` points boto3 at
LocalStack. Nexo: `NEXO_HOST`, `NEXO_PORT`, `NEXO_PASSWORD` (AWS: basic auth, from SSM), `COMPANY_MCP_URL`,
`SIMULATOR_AUTH` (iam in AWS), `MCP_AUDIENCE`, `AGENT_ACTIVITY_URL`, `TABLE_NAME`,
`BUSINESS_EVENTS_LOG_GROUP`, `TRANSCRIPTS` (docker | s3 | off), `SESSION_ARCHIVE_BUCKET`. Agent runtime (AWS):
`SESSION_ARCHIVE_BUCKET` enables the per-turn transcript archive.

Secrets must come from AWS Secrets Manager or SSM Parameter Store where appropriate.

Do not commit secrets.

---

# Logging

Logs should be structured JSON whenever possible.

Every business log should include:

```
agentId
eventId
correlationId
taskId
orderId
department
```

when available.

Example:

```
{
  "level": "INFO",
  "message": "Invoice created",
  "agentId": "accountant",
  "correlationId": "ORDER-123",
  "taskId": "TASK-91",
  "invoiceId": "INV-123"
}
```

This should make tracing a business transaction across agents possible.

---

# Correlation

Every business workflow should maintain a shared `correlationId`.

For e-commerce scenarios, the order ID may serve as the correlation ID.

Example:

```
OrderPlaced
    correlationId = ORDER-123

InventoryReserved
    correlationId = ORDER-123

InvoiceCreated
    correlationId = ORDER-123

ShipmentCreated
    correlationId = ORDER-123

DecisionRequested
    correlationId = ORDER-123
```

This is essential for debugging and for explaining distributed tracing during the presentation.

---

# Error Handling

Do not silently discard failed agent work.

Use:

- retries
- Dead Letter Queues
- explicit failed task state
- structured error events

Suggested event:

```
WorkFailed
```

Payload:

```
{
  "taskId": "TASK-123",
  "agentId": "fulfillment",
  "errorCode": "INVENTORY_API_FAILURE",
  "retryable": true
}
```

The system should distinguish retryable infrastructure errors from deterministic business failures.

---

# Security Principles

1. Use ECS Task Roles.
2. Avoid long-lived AWS credentials.
3. Apply least privilege.
4. Restrict agents to MCP tools whenever possible.
5. Keep agents in private networking where practical.
6. Store secrets in managed AWS secret stores.
7. Treat prompt content as configuration, not trusted authorization.
8. Validate all agent-generated tool inputs.
9. Audit significant actions and decisions.
10. Never give an LLM unrestricted administrative AWS access.

---

# Important Architectural Decisions

These decisions are currently considered intentional.

## Use

- Hermes Agent
- Amazon Bedrock
- Amazon ECS Express Mode (Fargate) for every service: one task per employee, internal HTTPS URLs for
  the MCP and agent-activity, a public one for Nexo
- EventBridge
- SQS (one inbox + DLQ per employee)
- DynamoDB
- S3
- AWS AppConfig
- IAM
- MCP
- Nexo (demo store) + agent-activity service, fed by a Hermes plugin based on hermes-pixel-office hooks
- IAM-signed calls to the MCP (STS GetCallerIdentity tokens via a local signing proxy)
- Terraform
- LocalStack (via `lstk`) for local development

## Do not use by default

- EFS for Hermes persistence
- Pixel Agents
- shared Hermes home directories
- direct S3 filesystem mounting for SQLite
- unrestricted AWS CLI access from agents
- agent conversation history as business state
- synchronous agent-to-agent coupling as the primary architecture
- agents publishing business events or reading the database directly
- EC2 instances, Cloud Map or hand-built load balancers for the services (Express Mode provides them)
- Hermes built-in tools for employees (terminal, files, web, memory): employees only get company tools

---

# MVP Implementation Order

Codex should prefer incremental implementation.

## Phase 1 — Local business skeleton

**Status:** Done: Company MCP, schemas as Pydantic models, task and decision models, order simulator.

Implement:

- event schemas
- DynamoDB-compatible repository interfaces
- task model
- decision model
- mock MCP tools
- deterministic order simulator

No AWS deployment required initially.

Acceptance:

One simulated order can flow through logical departments.

## Phase 2 — Hermes agents

**Status:** Done: agent image, runtime, job descriptions, identities, MCP integration.

Implement:

- Hermes container
- role configuration
- prompt loading
- individual agent identities
- Company MCP integration

Acceptance:

At least one Hermes agent can consume a task and execute Company MCP tools.

## Phase 3 — Event-driven AWS

**Status:** Done on LocalStack; AWS Terraform validated and planned, not yet applied. Queues are per employee, not per department.

Implement:

- EventBridge
- per-employee SQS inboxes (with DLQs)
- DynamoDB
- S3
- agent event adapters

Acceptance:

`OrderPlaced` flows asynchronously through at least two departments.

## Phase 4 — Full organization

**Status:** Done locally (seven containers); defined in Terraform for AWS (seven ECS Express Mode services).

Deploy:

- CEO
- Accounting Manager
- Accountant
- Operations Manager
- Fulfillment
- Commercial Manager
- Sales Rep

Acceptance:

All seven persistent roles are independently deployed.

## Phase 5 — Decision escalation

**Status:** Done: discount and refund escalations, deterministic limits.

Implement:

- DecisionRequested
- CEO queue
- approval workflow
- deterministic authorization limits

Acceptance:

A manager automatically escalates a decision outside its authority.

## Phase 6 — Live company view

**Status:** Done locally: Nexo + agent-activity service; both defined in Terraform as ECS Express Mode services.

Implement:

- remote activity publisher (Hermes plugin + runtime)
- agent-activity service (per employee, per order)
- corporate metadata
- Nexo: the customer's order and the seven distributed agents working on it, live

Acceptance:

One browser shows, for an order placed in Nexo, real-time activity for all seven distributed agents.

## Phase 7 — Failure demo

**Status:** Done: idempotency, DLQs + redrive, task recovery on restart, edge-case cancellations and refunds.

Implement:

- idempotency
- DLQs
- task recovery
- agent restart scenario

Acceptance:

Restarting a worker during the demo does not corrupt business state or create duplicate business actions.

---

# Development Rules for Codex

When modifying this repository:

1. Preserve the four-plane architecture:
   - AI
   - Business
   - State
   - Observability
2. Do not introduce direct coupling between agents unless there is a strong technical reason.
3. Prefer explicit events over hidden side effects.
4. Prefer deterministic APIs over free-form LLM actions.
5. Keep business state outside Hermes.
6. Treat agent containers as disposable.
7. Use stable schemas.
8. Make all business mutations idempotent.
9. Add tests for business invariants.
10. Avoid infrastructure choices that make the live demo fragile.
11. Optimize first for architectural clarity and demo reliability, not maximum feature count.
12. Keep components small enough that their role can be explained during a 30-minute technical presentation.
13. Before adding a new AWS service, explain what architectural problem it solves.
14. Avoid duplicating capabilities already supplied by EventBridge, SQS, DynamoDB, S3, AppConfig, IAM, or Hermes.
15. Prefer simple, inspectable implementations over complex framework abstractions.

---

# Testing Strategy

Tests should exist at three levels.

## Unit

Test:

- event validation
- authorization rules
- task transitions
- idempotency
- MCP input validation

## Integration

Test:

- queue → agent → MCP
- MCP → DynamoDB
- business mutation → event
- decision escalation

## Scenario

Scenario tests should exercise complete workflows.

Required scenarios:

```
happy_path_order

insufficient_inventory

special_discount_requires_ceo

duplicate_order_event

agent_restart_during_processing
```

---

# Demo Reliability

The live demo must be reproducible.

Provide deterministic scenario fixtures.

Example:

```
demo/scenarios/
    01-happy-path.json
    02-ceo-approval.json
    03-inventory-failure.json
    04-agent-restart.json
```

Avoid requiring unpredictable external APIs during the main presentation.

Where third-party integrations would normally exist, use deterministic mock services for the conference demo.

---

# Primary Engineering Question

When making architectural decisions, optimize for demonstrating this idea:

> What happens when we operate autonomous AI agents using the same principles we use to operate reliable distributed systems?

The project should demonstrate that AI agents still need:

- configuration management
- authorization
- state management
- asynchronous communication
- observability
- failure recovery
- idempotency
- deployment automation
- clear service boundaries

The agents provide reasoning.

The platform provides reliability.

---

# MVP Definition of Done

The initial showcase is complete when:

1. Seven corporate Hermes agents run independently.
2. Agents use Amazon Bedrock for inference.
3. Agents have centrally managed role prompts.
4. A simulated e-commerce order triggers an EventBridge workflow.
5. Commercial, Operations, and Accounting perform different responsibilities.
6. Managers can delegate work to their departmental worker.
7. A decision outside manager authority is escalated to the CEO.
8. Business state persists independently from agent containers.
9. Restarting an agent does not destroy the company state.
10. Business actions are idempotent.
11. IAM or deterministic tool permissions restrict agent capabilities.
12. Nexo shows all distributed agents working on an order, live.
13. The UI visually shows agents working, delegating, waiting, and escalating.
14. The entire AWS environment can be provisioned through Terraform.
15. The complete demo can be reproduced from documented commands.

---

# Non-Goals for the First Version

Do not prioritize:

- dozens of agents
- autonomous hiring/firing
- dynamic department creation
- multi-region deployment
- production-grade ERP functionality
- complex vector databases
- advanced RAG
- arbitrary internet access
- fully autonomous financial transactions
- horizontal manager scaling
- perfect persistence of Hermes conversation history

These may be explored later.

The first objective is to create a clear, reliable, technically defensible distributed-agent showcase.