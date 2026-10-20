# Nexo, the demo store and live company view, as a public ECS Express Mode service
# (public subnets -> internet-facing HTTPS load balancer). It is a customer of the
# company, not part of it: it places orders through the Company MCP as the order
# simulator (signed with the role below), reads an order's records and business
# events for the "Detrás del pedido" trace, and shows live activity from the
# agent-activity service. Anyone with the URL could spend Bedrock tokens by buying,
# so the whole site sits behind HTTP basic auth with a password kept in SSM.

data "aws_region" "current" {}

# Created with a placeholder; set the real password outside Terraform so it never
# lands in code or state:
#   aws ssm put-parameter --name <name> --type SecureString --overwrite --value '<password>'
resource "aws_ssm_parameter" "password" {
  name        = "/${var.name_prefix}/nexo/password"
  description = "Nexo basic-auth password (set outside Terraform)."
  type        = "SecureString"
  value       = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }

  tags = var.tags
}

data "aws_iam_policy_document" "read_password" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [aws_ssm_parameter.password.arn]
  }
}

resource "aws_iam_role_policy" "read_password" {
  name   = "nexo-password"
  role   = var.execution_role_name
  policy = data.aws_iam_policy_document.read_password.json
}

# Read-only view of one order: its records, its business events and the agents' transcripts.
data "aws_iam_policy_document" "trace" {
  statement {
    sid       = "ReadBusinessRecords"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }

  statement {
    sid       = "ReadTranscripts"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/sessions/archives/*"]
  }

  statement {
    sid       = "ListTranscripts"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["sessions/archives/*"]
    }
  }

  statement {
    sid       = "ReadBusinessEvents"
    actions   = ["logs:FilterLogEvents"]
    resources = ["${var.business_events_log_group_arn}:*"]
  }
}

resource "aws_iam_role_policy" "trace" {
  name   = "nexo-trace"
  role   = var.task_role_name
  policy = data.aws_iam_policy_document.trace.json
}

module "service" {
  source = "../express-service"

  name_prefix = var.name_prefix
  name        = "nexo"
  enabled     = var.running
  image       = var.image
  port        = var.port
  cpu         = 256
  memory      = 512

  environment = {
    AWS_REGION                = data.aws_region.current.region
    NEXO_HOST                 = "0.0.0.0"
    NEXO_PORT                 = tostring(var.port)
    COMPANY_MCP_URL           = "${var.company_mcp_url}/mcp"
    SIMULATOR_AUTH            = "iam"
    MCP_AUDIENCE              = var.mcp_audience
    AGENT_ACTIVITY_URL        = var.agent_activity_url
    TABLE_NAME                = var.table_name
    BUSINESS_EVENTS_LOG_GROUP = var.business_events_log_group
    # Agents archive each turn's transcript to S3; locally Nexo uses `docker compose exec`.
    TRANSCRIPTS            = "s3"
    SESSION_ARCHIVE_BUCKET = var.bucket_name
  }
  secrets = {
    NEXO_PASSWORD = aws_ssm_parameter.password.arn
  }

  cluster_name            = var.cluster_name
  execution_role_arn      = var.execution_role_arn
  infrastructure_role_arn = var.infrastructure_role_arn
  task_role_arn           = var.task_role_arn
  subnet_ids              = var.public_subnet_ids
  security_group_ids      = var.security_group_ids
  log_retention_days      = var.log_retention_days
  tags                    = var.tags

  depends_on = [aws_iam_role_policy.read_password]
}
