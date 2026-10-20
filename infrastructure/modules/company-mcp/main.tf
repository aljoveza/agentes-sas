# The deterministic tool layer. It is the only compute with write access to
# business records, and it publishes domain facts (InvoiceCreated,
# ShipmentCreated, ...) after a successful, idempotent write. Stateless, so it
# scales out like any web API: an ECS Express Mode service behind the internal HTTPS
# load balancer. Callers are authenticated by IAM, not by network position: their
# signed STS token must resolve to a role named <name_prefix>-<agent-id> in this account.

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  name         = "${var.name_prefix}-company-mcp"
  event_source = "company.mcp"
}

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
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "task" {
  # No DeleteItem or Scan: business records are appended or transitioned, never removed.
  statement {
    sid = "BusinessRecords"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:ConditionCheckItem",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }

  statement {
    sid       = "PublishDomainEvents"
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]

    condition {
      test     = "StringEquals"
      variable = "events:source"
      values   = [local.event_source]
    }
  }

  statement {
    sid       = "Artifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/artifacts/*", "${var.bucket_arn}/audit/*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "company-api"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

module "service" {
  source = "../express-service"

  name_prefix = var.name_prefix
  name        = "company-mcp"
  enabled     = var.running
  image       = var.image
  port        = var.port
  cpu         = var.cpu
  memory      = var.memory
  min_tasks   = var.min_tasks
  max_tasks   = var.max_tasks

  environment = {
    AWS_REGION         = data.aws_region.current.region
    PORT               = tostring(var.port)
    TABLE_NAME         = var.table_name
    EVENT_BUS_NAME     = var.event_bus_name
    EVENT_SOURCE       = local.event_source
    COMPANY_BUCKET     = var.bucket_name
    LOG_LEVEL          = var.log_level
    AUTH_MODE          = "iam"
    MCP_AUDIENCE       = var.audience
    AGENT_ROLE_PREFIX  = "${var.name_prefix}-"
    ALLOWED_ACCOUNT_ID = data.aws_caller_identity.current.account_id
  }

  cluster_name            = var.cluster_name
  execution_role_arn      = var.execution_role_arn
  infrastructure_role_arn = var.infrastructure_role_arn
  task_role_arn           = aws_iam_role.task.arn
  subnet_ids              = var.subnet_ids
  security_group_ids      = var.security_group_ids
  log_retention_days      = var.log_retention_days
  tags                    = var.tags
}
