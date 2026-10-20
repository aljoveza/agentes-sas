# One employee = one Hermes configuration = one ECS Express Mode service with a stable
# identity. The task role is the employee's identity: it can think (Bedrock), read its
# own inbox, read its own job description and keep its own files. Everything else —
# business records and events — goes through the Company MCP, which recognises the
# employee by this role. The agent image runs a local signing proxy (company-mcp-proxy)
# next to Hermes; Hermes only ever sees http://127.0.0.1.
#
# Express Mode is built for web services, and an employee is a queue worker: the runtime
# answers the load balancer's health check on /health (proxy alive) and nothing else.

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  family = "${var.name_prefix}-${var.agent_id}"

  environment = merge({
    AGENT_ID              = var.agent_id
    AGENT_ROLE            = var.agent_role
    DEPARTMENT            = var.department
    REPORTS_TO            = var.reports_to
    AWS_REGION            = data.aws_region.current.region
    BEDROCK_MODEL_ID      = var.bedrock_model_id
    BEDROCK_ROLE_ARN      = var.bedrock_role_arn
    AGENT_QUEUE_URL       = var.queue_url
    COMPANY_MCP_URL       = "http://127.0.0.1:${var.mcp_proxy_port}/mcp"
    MCP_PROXY_UPSTREAM    = var.company_mcp_url
    MCP_PROXY_PORT        = tostring(var.mcp_proxy_port)
    MCP_AUDIENCE          = var.mcp_audience
    HEALTH_PORT           = tostring(var.health_port)
    PROMPT_APPLICATION_ID = var.appconfig.application_id
    PROMPT_ENVIRONMENT_ID = var.appconfig.environment_id
    PROMPT_PROFILE_ID     = var.appconfig.profile_id
    COMPANY_BUCKET        = var.bucket_name
    # Each turn's transcript goes to sessions/archives/<agent>/<order>/ (own prefix only).
    SESSION_ARCHIVE_BUCKET = var.bucket_name
    AGENT_ACTIVITY_URL     = var.agent_activity_url
    LOG_LEVEL              = var.log_level
  }, var.extra_environment)

  tags = merge(var.tags, {
    AgentId    = var.agent_id
    Department = var.department
  })
}

# Task role ----------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = local.family
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = local.tags
}

data "aws_iam_policy_document" "task" {
  # Thinking happens either in this account or, when its Bedrock quota is too low,
  # through a Bedrock-only role in another account (see bedrock_role_arn).
  dynamic "statement" {
    for_each = var.bedrock_role_arn == "" ? [1] : []

    content {
      sid       = "Reason"
      actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = var.bedrock_resource_arns
    }
  }

  dynamic "statement" {
    for_each = var.bedrock_role_arn == "" ? [] : [1]

    content {
      sid       = "ReasonInBedrockAccount"
      actions   = ["sts:AssumeRole"]
      resources = [var.bedrock_role_arn]
    }
  }

  statement {
    sid       = "ReadOwnInbox"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "ReadOwnJobDescription"
    actions   = ["appconfig:StartConfigurationSession", "appconfig:GetLatestConfiguration"]
    resources = [var.appconfig.arn]
  }

  statement {
    sid     = "OwnFiles"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${var.bucket_arn}/agents/${var.agent_id}/*",
      "${var.bucket_arn}/memory/${var.agent_id}/*",
      "${var.bucket_arn}/sessions/archives/${var.agent_id}/*",
    ]
  }

  statement {
    sid       = "ListOwnFiles"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "agents/${var.agent_id}/*",
        "memory/${var.agent_id}/*",
        "sessions/archives/${var.agent_id}/*",
      ]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "authority"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# Service ------------------------------------------------------------------

module "service" {
  source = "../express-service"

  name_prefix = var.name_prefix
  name        = var.agent_id
  enabled     = var.running
  image       = var.image
  port        = var.health_port
  cpu         = var.cpu
  memory      = var.memory
  environment = local.environment

  # Stable identity: one employee, never autoscaled. Express Mode deploys as a canary,
  # so during a rollout the old and new task briefly overlap; the inbox hands each event
  # to one of them and every business action is idempotent.
  min_tasks = 1
  max_tasks = 1

  cluster_name            = var.cluster_name
  execution_role_arn      = var.execution_role_arn
  infrastructure_role_arn = var.infrastructure_role_arn
  task_role_arn           = aws_iam_role.task.arn
  subnet_ids              = var.subnet_ids
  security_group_ids      = var.security_group_ids
  log_retention_days      = var.log_retention_days
  tags                    = local.tags
}
