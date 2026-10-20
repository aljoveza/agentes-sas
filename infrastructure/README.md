# Infrastructure

Terraform for the AWS side of the agent company (see `../AGENTS.md`). One environment, one root
configuration in this directory.

```
main.tf, variables.tf ...  root: provider, backend, the company module
backend.conf               remote state settings (the bucket is created outside Terraform)
modules/company            composition: the whole company
modules/express-service    one ECS Express Mode service (every service below uses it)
modules/ecs-agent          one employee = one Express service (1 task) + task role; signing proxy in the image
modules/company-mcp        deterministic tool layer, the only writer of records and publisher of events
modules/nexo               the demo store: public Express service, basic auth, read-only order trace
modules/event-bus          EventBridge bus, routing rules, archive, audit log
modules/queue              SQS inbox + DLQ
modules/dynamodb           single-table ledger
modules/s3                 company document bucket
modules/appconfig          role prompts, one AppConfig environment per employee
modules/network            VPC, public + private subnets, NAT (only while running), S3/DynamoDB gateway endpoints
modules/organization       the org chart and the event routing derived from it
local/                     LocalStack root (bus, queues, table, bucket) used by ../local/compose.yaml
```

## Compute: everything is ECS Express Mode

Ten services, all `aws_ecs_express_gateway_service` in one cluster. For each one ECS creates the
Fargate service and task definition, an HTTPS load balancer with a certificate and a generated
URL, the target group, security groups, auto scaling and a deployment alarm, and rolls out every
change as a canary.

| Service | Subnets → load balancer | Tasks | Health check | Why it is shaped like this |
|---|---|---|---|---|
| 7 employees (`ceo`, `sales-rep`, …) | private → internal | 1–1 | `/health` (runtime, port 8080) | One stable identity per employee. They are queue workers: the URL only answers health checks |
| `company-mcp` | private → internal | 1–3 (CPU) | `/health` | Stateless API; IAM identity, not network position, decides who may call it |
| `agent-activity` | private → internal | 1–1 | `/health` | In-memory live activity: a second copy would split it |
| `nexo` | **public → internet-facing** | 1–1 | `/health` | The store the audience opens; HTTP basic auth (password in SSM) |

Express Mode shares one load balancer among up to 25 services with the same network shape, so this
is two load balancers: one internal, one public.

**What Express Mode decides for us** (and what we adapted to):

- **One container per task.** The Terraform resource has no task-definition or sidecar option, so
  the IAM signing proxy runs *inside* the agent image: the agent runtime starts
  `company-mcp-proxy` on `127.0.0.1:9000` and stops the employee if it dies. Locally the proxy is
  still a separate container.
- **x86_64 tasks.** The resource exposes no CPU architecture: build and push `linux/amd64` images.
- **Every service is a web service.** Workers need a `/health` endpoint; the agents' runtime serves
  one on port 8080 (proxy alive → 200).
- **Canary deployments.** During a rollout the old and new task of an employee briefly overlap.
  The inbox gives each event to one of them and every business action is idempotent, so this is
  safe — and a good live demonstration of why idempotency matters.
- **30 s stop timeout.** A turn in progress when a task stops is retried from the inbox.
- **The managed infrastructure policy is not enough (v6, October 2026).** The first deployment
  rolled back with `PROVISIONING lifecycle hook(s) failed. AccessDenied`. CloudTrail showed the
  role also needs `ecs:DescribeServices`/`ecs:UpdateService` (on our services),
  `cloudwatch:DescribeAlarms` and `ec2:DescribeAccountAttributes`; they are in the inline policy
  `express-mode-gaps` (`modules/company`).
- **Declare the scaling metric.** Omitting `auto_scaling_metric`/`auto_scaling_target_value` makes
  the AWS provider (6.68) fail with "inconsistent result after apply"; `express-service` sets them.
- **No ECS Exec, no Cloud Map.** Services find each other by their generated HTTPS URLs, which
  Terraform passes along as environment variables (`COMPANY_MCP_URL`, `AGENT_ACTIVITY_URL`).

## Deploy

```sh
# 1. Point backend.conf at your existing state bucket.
cp terraform.tfvars.example terraform.tfvars   # bedrock_model_id and the four image tags
terraform init -backend-config=backend.conf
terraform apply                                # running = false: everything except the services

# 2. Set Nexo's password (never in code or state).
aws ssm put-parameter --name "$(terraform output -raw nexo_password_parameter)" \
  --type SecureString --overwrite --value '<password>'

# 3. Build and push linux/amd64 images to the ecr_repositories output (run from the repo root):
docker buildx build --platform linux/amd64 --build-context company-mcp=services/company-mcp \
  -t <hermes-agent repo>:<sha> --push agents
docker buildx build --platform linux/amd64 -t <company-mcp repo>:<sha> --push services/company-mcp
docker buildx build --platform linux/amd64 -t <agent-activity repo>:<sha> --push services/agent-activity
docker buildx build --platform linux/amd64 --build-context order-simulator=services/order-simulator \
  --build-context company-mcp=services/company-mcp --build-context scenarios=demo/scenarios \
  -t <nexo repo>:<sha> --push services/nexo

# 4. Create the Express services.
terraform apply -var running=true          # with the image tags in terraform.tfvars
terraform output company                   # urls.nexo is the store (user name: anything)
```

Seed the demo data once with `company-mcp-seed` against the table (as in `local/compose.yaml`).

`running = false` removes the ten Express services (and with them the load balancers) and the NAT
gateway with its public IP; queues, the table, the bucket, roles and prompts keep the company. That is
the cheap state between rehearsals: about $2/month instead of about $190/month running 24/7
(≈ $0.26/hour; Bedrock tokens are billed separately).

Other cost choices: agents run with 0.5 vCPU / 1 GB (at 0.25 vCPU starting each Hermes turn took ~60 s; `agent_cpu`
sets it), and Container Insights is off (`container_insights = true` to enable).

To run the order simulator from your machine, assume the `order_simulator_role_arn` output role
(`SIMULATOR_AUTH=iam`, `COMPANY_MCP_URL=<urls.company_mcp>/mcp`). The internal load balancer is only
reachable from inside the VPC, so in practice Nexo places the demo orders. Nexo runs as the same role,
so the MCP sees it as `order-simulator`.

## Bedrock in another account

The development account has no Bedrock quota, so the agents can think in another account:
set `bedrock_role_arn` to a Bedrock-only role there (the `bedrock_invoker_role_arn` output of the
aws-management repository, which trusts exactly the seven `agentes-sas-dev-<agent-id>` task roles).

- Each agent's task role then gets `sts:AssumeRole` on that role instead of `bedrock:InvokeModel`.
- The runtime writes an AWS profile (`role_arn`, `credential_source = EcsContainer`,
  `role_session_name = <agent-id>`) and gives it **only to the Hermes process**. The inbox client and
  the signing proxy keep the task role, so the Company MCP still recognises the employee.
- In the Bedrock account, CloudTrail shows each invocation as `<role>/<agent-id>`.

No API key or other secret is stored anywhere. Leave `bedrock_role_arn` empty to invoke Bedrock with
the task role in this account.

## Event contract

Only the Company MCP publishes (`Source = company.mcp`, `DetailType = <eventType>`), after the state change
the event describes; `Detail` is the envelope from `AGENTS.md`, whose `source` is the acting agent. Agents have
no EventBridge permissions: they read their SQS inbox and act through MCP tools, signed with their task role
by the signing proxy. Directed events carry the addressee in `payload.assignedTo`, `payload.assignedBy`
or `payload.requestedBy`; routing is derived from the org chart (`terraform output` → `company.event_routes`).
