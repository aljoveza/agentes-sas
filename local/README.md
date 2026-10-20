# Local company

The whole company on your machine. LocalStack (run by `lstk`, Hobby plan) emulates the AWS
business and state planes (EventBridge, SQS, DynamoDB, S3, CloudWatch Logs), created by the same
Terraform modules and org chart as AWS (`infrastructure/local`). The Company MCP and the seven Hermes employees
run as containers. Only the model is real: agents call Amazon Bedrock with your AWS session.

```
 ./sim run 01 ──► company-mcp ──► DynamoDB, S3 ─┐        (LocalStack :4566)
                      │ publishes               │
                      ▼                         │
                 EventBridge ──rules──► SQS inbox per agent
                                               │
          agent-<id> (Hermes + runtime) ◄──────┘  one turn per event
               │ http://127.0.0.1:9000/mcp
          mcp-proxy-<id>  ── X-Agent-Id: <id> ──► company-mcp
               │
               └── Bedrock (real AWS, your credentials)
```

| | Local | AWS |
|---|---|---|
| Business/state planes | LocalStack via `lstk` (Hobby plan) | EventBridge, SQS, DynamoDB, S3 |
| Agents, MCP | containers (`agent-<id>`, `mcp-proxy-<id>` share a network namespace) | ECS Express Mode services (the proxy runs inside the agent container) |
| Nexo, agent-activity | host process, container | ECS Express Mode services (Nexo public, with basic auth) |
| Caller identity | proxy sends `X-Agent-Id`; MCP runs `AUTH_MODE=header` | proxy signs with the task role; MCP verifies via STS |
| Prompts | `prompts/*.yaml` mounted read-only | AppConfig |
| Model | Bedrock with your session | Bedrock with the task role |

## Setup (once)

Docker, `uv`, `lstk` logged in (`lstk login`), and the AWS CLI with access to a Bedrock model
(see the prerequisites in the root `README.md`).

```sh
cd local
cp .env.example .env          # set BEDROCK_MODEL_ID
aws login                     # or SSO / a profile with Bedrock access
AWS_PROFILE=<profile> ./aws-credentials.sh --watch   # exports it for the agents, re-exported every 30 min
```

## Run

```sh
lstk start                                    # LocalStack on :4566 (reachable from containers)
docker compose up -d --build                  # Terraform, seed data, MCP (no Bedrock needed)
docker compose --profile agents up -d --build # + the 7 employees (they call Bedrock)
docker compose logs -f agent-commercial-manager agent-sales-rep   # watch employees work (JSON logs)

./sim run 01                                  # happy path
./feed                                        # live: every tool call + each employee's turns
./sim trace DEVOPS-2026-001                   # event timeline + records for that order
```

**Nexo** (the demo store and live company view): `./nexo`, then <http://127.0.0.1:8090>
(see `services/nexo/README.md`).
Buy in *La tienda* (the scenario cards preset the demo stories), then follow the order in
*Detrás del pedido*: milestones, the seven employees' live state on that order ("Ahora: reserva
inventario", "Esperando al CEO", "Ocupado con otro pedido"), the business-event story, the live
activity timeline (*En vivo*), and each agent's transcript. Live activity comes from the
`agent-activity` service on :8113 (part of the stack; see `services/agent-activity/README.md`).

`./feed` merges the Company MCP log (✓/✗ tool calls by agent) with the agents' turn logs (▶ event
picked up, ■ the agent's own summary). `./feed --all` replays from the start; `--since 10m` too.

**Random market** (so the demo doesn't look scripted): `./sim market` places a stream of varied
orders at random intervals: normal orders, discounts the commercial manager can grant, discounts
that need the CEO (approved or rejected depending on the product's margin), stock shortages,
underpayments, a small customer over its credit limit, and duplicate submissions.

```sh
./sim market --dry-run                        # preview: random seed, nothing placed
./sim market --orders 5                       # place them, 30–90 s apart (prints the seed)
./sim market --orders 5 --seed 640840         # replay exactly the same customers and orders
./sim market --orders 3 --min-wait 10 --max-wait 20   # faster pace
```

Scenarios (`demo/scenarios/`): `01` happy path, `02` CEO approval, `03` inventory failure,
`04` duplicate order, `05` agent restart, `06` out of stock with a refund that needs the CEO. Order ids are fixed so runs are reproducible; replay a
scenario with fresh orders with `--run-id N`.

Each agent runs a recovery turn when it starts (resuming its unfinished tasks), then handles one
event per turn. A turn that fails is retried after 30 s; after 3 attempts the event moves to the
agent's dead-letter queue (`agentes-sas-local-<id>-dlq`).

### When an agent keeps failing

Each failed turn is retried; after 3 attempts the event moves to that agent's dead-letter queue, so
nothing is lost. The most common cause locally is expired AWS credentials (Bedrock answers
*"…credentials are still expired"* in `./feed`):

```sh
AWS_PROFILE=<profile> ./aws-credentials.sh   # agents pick it up on their next turn, no restart needed
./redrive commercial-manager                 # move its dead-lettered events back to its inbox (or --all)
```

If the profile's own session expired too, run `aws sso login --profile <profile>` first.

### Failure drills

```sh
# Agent restart: kill Fulfillment while it works on an order
./sim run 05
docker compose logs -f agent-fulfillment      # wait for "turn started" ... WorkAssigned (ReserveStock)
docker compose kill agent-fulfillment && docker compose up -d agent-fulfillment
./sim trace DEVOPS-2026-005                   # one reservation, one shipment

# Agent unavailable: events wait in the inbox
docker compose stop agent-operations-manager
./sim run 01 --run-id 2
docker compose start agent-operations-manager # it picks up OrderValidated from SQS
```

### Look inside

```sh
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1 AWS_ENDPOINT_URL=http://localhost:4566
aws sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/agentes-sas-local-fulfillment-dlq --attribute-names ApproximateNumberOfMessages
aws dynamodb scan --table-name agentes-sas-local-company --max-items 20
aws s3 ls s3://agentes-sas-local-company/artifacts/invoices/
```

### Question an employee

Every turn an employee takes is stored as a Hermes session in its container, so you can check what it
saw, what it did, and why.

```sh
./sessions ceo                                   # its turns, newest first (title + id)
./transcript ceo 20261013_221504_7a6bc3          # full Markdown transcript: event, tool calls, results, answer
./ask ceo "What margin did you see?" --resume 20261013_221504_7a6bc3   # one question, in that turn's context
./talk ceo                                       # interactive chat (add --resume <id> to continue a turn)
```

The employee keeps its real identity and tools in these chats: questions are safe, but anything you
ask it to *do* (approve, assign, invoice) really happens and shows up in `./feed` and Nexo.
Sessions live in the container and are lost when it is recreated; `./transcript` saves them to
`local/transcripts/`.

### Be an employee in the browser

```sh
docker compose --profile inspector up -d      # official MCP Inspector on http://localhost:6274
docker compose logs mcp-inspector | grep -m1 'localhost:6274?'   # the link to open (includes its access token)
```

It lists every employee (and the order simulator) as a server; each connection sends that
employee's `X-Agent-Id` (`inspector/mcp.json`). Connect one, open **Tools**, and you only see that
role's tools; calls go through the same rules, and their events show up in `./sim trace`. Set
`MCP_INSPECTOR_API_TOKEN` in `.env` to keep the same link across restarts.

The MCP is also reachable directly at `http://localhost:8080/mcp` with an `X-Agent-Id` header.

Without the `agents` profile nothing calls Bedrock: you can drive the MCP yourself (the simulator,
the MCP Inspector with `X-Agent-Id`, or `aws --endpoint-url` against LocalStack). Events still
accumulate in the agents' inboxes and are picked up once the employees start.

## Reset

```sh
./reset                       # containers + Terraform state + LocalStack data, together
```

Always reset both sides: LocalStack outlives the compose stack, and `docker compose down -v` alone
would leave LocalStack resources that the next Terraform run no longer knows about. `lstk stop`
also discards LocalStack data (no persistence by default), so after it run `./reset` too.
