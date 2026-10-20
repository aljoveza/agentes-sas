# agentes-sas

**An e-commerce company run by seven AI agents, operated like a distributed system.**

A customer buys laptops in a demo store. Behind the checkout, seven AI employees (a CEO, three
department managers and three specialists) validate the customer, reserve stock, take the payment,
issue the invoice, ship, and escalate decisions above their authority to the CEO. They are
[Hermes Agent](https://github.com/NousResearch/hermes-agent) instances on Amazon Bedrock, each in its
own container, coordinating only through events and a deterministic company API.

Built for a DevOpsDays Bogotá 2026 talk around one question: *what happens when we operate
autonomous AI agents with the same principles we use for reliable distributed systems?*

> Prompts describe what an agent **should** do. IAM and deterministic APIs define what it **can** do.
> The agents provide reasoning; the platform provides reliability.

---

## How it works

```
 Nexo (demo store) ──place_order──► Company MCP ──writes──► DynamoDB, S3
                                       │ publishes business events
                                       ▼
                                  EventBridge ──rules (from the org chart)──► SQS inbox per employee
                                                                                   │ one event = one turn
              ┌────────────────────────────────────────────────────────────────────┘
              ▼
     employee container: agent runtime ─► Hermes ─► Bedrock (the model)
                                            │ company tools only
                                            ▼
                                       signing proxy (adds the employee's identity) ─► Company MCP
              │
              └─ activity events ─► agent-activity service ─► Nexo "Detrás del pedido" (live)
```

| Plane | What | Built with |
|---|---|---|
| AI | Seven Hermes employees with job descriptions (`prompts/`) | Hermes Agent, Amazon Bedrock (any tool-calling model, e.g. DeepSeek V3.2 or Claude) |
| Business | Events between departments; a queue per employee | EventBridge, SQS (+ dead-letter queues) |
| State | Orders, stock, payments, invoices, tasks, decisions | DynamoDB (single table), S3 |
| Observability | The customer's order and every agent working on it, live | Nexo, agent-activity service, JSON logs, event archive |

**Principles you can see in the code**

- **Agents can only act through the Company MCP** (`services/company-mcp`): 29 deterministic tools. Each
  employee only sees its role's tools, and the MCP knows *who* is calling from the employee's IAM role
  (locally, from a header added by its proxy), never from what the model claims.
- **Business rules live in code, not prompts.** Discounts above 10% need an approved CEO decision,
  refunds above 5,000 too, nothing ships before the payment is received, cancelled orders take no more
  work, and shipped orders can't be cancelled.
- **Every write is idempotent.** Deterministic ids and conditional writes make retries and duplicate
  events harmless; each change stores its event on the record so a crash can't lose it.
- **Agents are cattle.** Containers are disposable; work lives in queues and durable tasks. A restarted
  agent recovers its unfinished tasks; failed turns retry, then land in a dead-letter queue.
- **Edge cases are procedures on top of those rules:** out of stock → the order is cancelled, stock
  released, invoice voided, payment refunded (with CEO approval above 5,000); ineligible customers,
  rejected discounts and underpayments are cancelled with the reason.

The full design, decisions and conventions are in [`AGENTS.md`](AGENTS.md).

## Repository layout

```
agents/                    employee image: Hermes + runtime (inbox → one Hermes turn per event)
  plugins/company-activity Hermes plugin publishing each employee's activity
prompts/                   job descriptions: role, authority, procedures, allowed tools
services/
  company-mcp/             Company MCP: the only way to read or change business state
  agent-activity/          live activity of every agent, per order (observability only)
  order-simulator/         scripted scenarios, a random "market", order traces
  nexo/                    Nexo: the demo store and live company view (Spanish UI)
demo/scenarios/            reproducible demo scenarios (JSON)
infrastructure/            Terraform: AWS (root) and LocalStack (local/), shared modules
local/                     run everything on your machine: docker compose + helper scripts
```

---

## Run it locally

Everything runs on your machine except the model: LocalStack emulates the AWS services, the agents
and services run in Docker, and the agents call Amazon Bedrock with your AWS credentials.

### Prerequisites

- **Docker** with Compose v2.24 or newer (Docker Desktop on macOS works; images are built for your CPU).
- **[uv](https://docs.astral.sh/uv/)** (Python tooling for the helper scripts and Nexo).
- **LocalStack CLI `lstk`** with a LocalStack account; the free *Hobby* plan is enough
  (`lstk login`).
- **AWS CLI v2** and an AWS account with **Amazon Bedrock access to a model that supports tool use**,
  with a non-zero on-demand quota in your region. Check both before you start:
  ```sh
  aws bedrock list-foundation-models --region us-east-1 --query 'modelSummaries[?contains(modelId,`deepseek`)].modelId'
  aws service-quotas list-service-quotas --service-code bedrock --region us-east-1 \
    --query "Quotas[?contains(QuotaName,'DeepSeek V3.2')].[QuotaName,Value]" --output table
  ```
  Each order costs roughly 10–20 short agent turns. A quota of 0 shows up as
  `ThrottlingException: Too many tokens per day`.
- Optional: **Node.js 22+** (Nexo's front-end tests), **Terraform 1.10+** (only for AWS; locally it
  runs in a container).

### 1. Configure

```sh
git clone <this repository> agentes-sas && cd agentes-sas/local
cp .env.example .env
# edit .env: BEDROCK_MODEL_ID=deepseek.v3.2 (or e.g. global.anthropic.claude-haiku-4-5-20251001-v1:0)
```

### 2. Start LocalStack

```sh
lstk login        # once
lstk start        # LocalStack on localhost:4566
```

### 3. Give the agents AWS credentials for Bedrock

In a **separate terminal**, keep your session exported for the agents (it is re-exported every
30 minutes; only Bedrock uses it):

```sh
aws sso login --profile <your-profile>     # or `aws login`, or any profile with Bedrock access
cd local && AWS_PROFILE=<your-profile> ./aws-credentials.sh --watch
```

### 4. Start the company

```sh
docker compose up -d --build                    # Terraform → LocalStack, demo data, Company MCP, agent-activity
docker compose --profile agents up -d --build   # the seven employees (+ their MCP proxies)
```

The first build downloads the Hermes and Terraform images and can take a few minutes. Each employee
starts with a short recovery turn (it checks for unfinished work).

### 5. Open the store and buy something

```sh
./nexo            # then open http://127.0.0.1:8090
```

In **La tienda**, pick a scenario card (for example *¿Un 25% de descuento?*) or fill the cart, then
**Confirmar compra**. **Detrás del pedido** follows the order: milestones, each employee's live state,
the business-event story, a live activity timeline and the agents' transcripts.

### 6. Watch it from the terminal

```sh
./feed                          # every tool call and every agent turn, live
./sim trace <order-id>          # an order's event timeline and records
```

### Stop, reset

```sh
docker compose --profile agents stop     # pause the employees (no Bedrock calls while stopped)
./reset                                  # wipe everything: containers, Terraform state, LocalStack data
```

`./reset` keeps LocalStack and the Terraform state in sync; resetting only one of them breaks the next
start. Every `docker compose up` re-seeds the demo stock (10 laptops, 2 USB-C docks, …).

**No Bedrock, no cost:** without `--profile agents` nothing calls the model. You can still drive the
company yourself through the MCP Inspector (below) or the simulator; events wait in the inboxes
until the employees start.

---

## Demo playbook

All commands run from `local/`.

| Scenario | Command | What to watch |
|---|---|---|
| Happy path | `./sim run 01` | Commercial validates; Operations and Accounting work in parallel; shipped after payment |
| CEO approval | `./sim run 02` | 25% discount → *Esperando al CEO* → decision on the margin |
| Out of stock | `./sim run 03` | `INVENTORY_UNAVAILABLE` → order cancelled → stock released, invoice voided, refund |
| Duplicate order | `./sim run 04` | One order, one event: idempotency |
| Agent restart | `./sim run 05` + kill `agent-fulfillment` | The restarted agent recovers its task; nothing duplicated |
| Large refund | `./sim run 06` | 12 laptops, 10 in stock → cancelled → a 36,000 refund needs the CEO |
| Random customers | `./sim market --orders 5` | Varied, replayable orders (`--seed N`; `--dry-run` to preview) |

Add `--run-id N` to replay a scenario with fresh order ids. Nexo's scenario cards place the same
stories from the browser.

**Question the employees.** Every turn is a stored Hermes session:

```sh
./sessions ceo                                  # the CEO's turns
./transcript ceo <session-id>                   # full transcript: event, tool calls, results, answer
./ask ceo "Why did you approve it?" --resume <session-id>
./talk ceo                                      # interactive (the employee keeps its real tools)
```

**Be an employee.** `docker compose --profile inspector up -d` starts the official MCP Inspector on
<http://localhost:6274> (open the link printed by `docker compose logs mcp-inspector`); each saved
connection acts as one employee and only sees that role's tools.

**Failure drills** (agent restart, agent unavailable) and the rest of the local tooling are in
[`local/README.md`](local/README.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| An agent's turns fail with *"credentials are still expired"* | The exported session expired. `AWS_PROFILE=<p> ./aws-credentials.sh` (agents pick it up on the next turn), then `./redrive <agent>` (or `--all`) to retry events that reached the dead-letter queue. |
| *ThrottlingException: Too many tokens per day* | Your Bedrock quota for that model is 0 or used up. Check Service Quotas, request an increase, or set another model in `.env`. |
| `infra` fails: *LocalStack is not reachable* | Run `lstk start` first. |
| `infra` fails on existing resources after a restart | LocalStack and the Terraform state are out of sync: `./reset`, then start again. |
| Nexo says *Actividad en vivo no disponible* | The `agent-activity` service isn't running: `docker compose up -d agent-activity`. |
| An order sits in `PLACED` | Are the agents running (`docker compose --profile agents ps`) and do they have credentials? Check `./feed`. |

## Tests

```sh
(cd services/company-mcp && uv run pytest)        # business rules, idempotency, IAM caller identity
(cd services/agent-activity && uv run pytest)
(cd services/order-simulator && uv run pytest)
(cd services/nexo && uv run pytest && node --test tests/model.test.mjs)
(cd agents/runtime && uv run pytest)              # inbox handling, identity files, activity plugin
(cd infrastructure && terraform init -backend=false && terraform validate)
```

## Deploy to AWS

The same design runs on AWS with Terraform (`infrastructure/`), and every service is an **Amazon ECS
Express Mode** service: one per employee (a single task each), the Company MCP and the agent-activity
service behind an internal HTTPS load balancer, and Nexo on a public HTTPS URL with basic auth.
Around them: EventBridge, SQS, DynamoDB, S3, AppConfig for the job descriptions, and IAM roles per
employee. In AWS the MCP authenticates each employee by its task role (a presigned STS
`GetCallerIdentity` token minted by a signing proxy inside the employee's container).

See [`infrastructure/README.md`](infrastructure/README.md) for the steps and for what Express Mode
changes (one container per task, x86_64 images, health checks, canary deployments). You provide
an existing S3 bucket for the Terraform state (`backend.conf`), a Bedrock model with quota in the
target account, and four `linux/amd64` images pushed to the ECR repositories Terraform creates.

## Safety notes

- Agents get **only** the company tools: no shell, files, web or memory. Everything they change goes
  through the MCP's permissions and rules.
- Locally, `local/.aws-agent/credentials` holds your exported AWS session (git-ignored). Prefer a
  profile that can only invoke Bedrock.
- No real payments: amounts, payments and refunds are simulated.

## License

[MIT](LICENSE).

## Credits

The `company-activity` Hermes plugin is based on the hooks of
[hermes-pixel-office](https://github.com/teknium1/hermes-pixel-office) (MIT, see
`agents/plugins/company-activity/LICENSE-hermes-pixel-office`).
