data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  prefix     = "${var.name}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # A cross-region inference profile (us./eu./apac./global. prefix) needs access to the
  # profile and to the underlying foundation model in every routed region; a plain
  # model id (e.g. deepseek.v3.2) is invoked on demand in this region only.
  bedrock_uses_profile     = can(regex("^(us|eu|apac|global)\\.", var.bedrock_model_id))
  bedrock_foundation_model = replace(var.bedrock_model_id, "/^(us|eu|apac|global)\\./", "")
  bedrock_resource_arns = local.bedrock_uses_profile ? [
    "arn:aws:bedrock:${local.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
    "arn:aws:bedrock:*::foundation-model/${local.bedrock_foundation_model}",
    ] : [
    "arn:aws:bedrock:${local.region}::foundation-model/${var.bedrock_model_id}",
  ]

  mcp_audience = "${local.prefix}-company-mcp"
  images = {
    agent          = "${aws_ecr_repository.this["hermes-agent"].repository_url}:${var.agent_image_tag}"
    company_mcp    = "${aws_ecr_repository.this["company-mcp"].repository_url}:${var.company_mcp_image_tag}"
    agent_activity = "${aws_ecr_repository.this["agent-activity"].repository_url}:${var.agent_activity_image_tag}"
    nexo           = "${aws_ecr_repository.this["nexo"].repository_url}:${var.nexo_image_tag}"
  }
}

module "organization" {
  source = "../organization"
}

locals {
  organization = module.organization.agents
  event_routes = module.organization.event_routes
}

# Network --------------------------------------------------------------------

module "network" {
  source = "../network"

  name       = local.prefix
  cidr_block = var.vpc_cidr
  # Only the running services need outbound access; parked, the NAT gateway is the
  # biggest idle cost (~$37/month with its public IP).
  nat_gateway = var.running
  tags        = var.tags
}

resource "aws_security_group" "agents" {
  name        = "${local.prefix}-agents"
  description = "Hermes agents: outbound only."
  vpc_id      = module.network.vpc_id

  tags = var.tags
}

resource "aws_vpc_security_group_egress_rule" "agents_all" {
  security_group_id = aws_security_group.agents.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# The Company MCP, agent-activity and Nexo: Express Mode adds their load balancer
# ingress; these rules only cover what the tasks call out to.
resource "aws_security_group" "services" {
  name        = "${local.prefix}-services"
  description = "Company services: outbound only (Express Mode manages load balancer ingress)."
  vpc_id      = module.network.vpc_id

  tags = var.tags
}

resource "aws_vpc_security_group_egress_rule" "services_all" {
  security_group_id = aws_security_group.services.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# State plane ------------------------------------------------------------------

module "ledger" {
  source = "../dynamodb"

  name                = "${local.prefix}-company"
  deletion_protection = var.deletion_protection
  tags                = var.tags
}

module "documents" {
  source = "../s3"

  name          = "${local.prefix}-company-${local.account_id}"
  force_destroy = var.force_destroy
  tags          = var.tags
}

# Business plane ---------------------------------------------------------------

module "inbox" {
  source   = "../queue"
  for_each = local.organization

  name = "${local.prefix}-${each.key}"
  tags = merge(var.tags, { AgentId = each.key, Department = each.value.department })
}

module "event_bus" {
  source = "../event-bus"

  name                   = "${local.prefix}-business"
  archive_retention_days = var.archive_retention_days
  log_retention_days     = var.log_retention_days
  tags                   = var.tags

  subscriptions = {
    for id, _ in local.organization : id => {
      queue_arn = module.inbox[id].arn
      queue_url = module.inbox[id].url
      patterns  = local.event_routes[id]
    }
  }
}

# Configuration ----------------------------------------------------------------

module "prompts" {
  source = "../appconfig"

  name    = "${local.prefix}-prompts"
  prompts = { for id, agent in local.organization : id => "${var.prompts_dir}/${agent.prompt}" }
  tags    = var.tags
}

# Compute ----------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  for_each = toset(["hermes-agent", "company-mcp", "agent-activity", "nexo"])

  name                 = "${local.prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_destroy

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "this" {
  name = local.prefix

  # Container Insights adds ~200 custom metrics (~$15/month). Express Mode already
  # publishes service metrics and a deployment alarm; enable it for deeper debugging.
  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }

  tags = var.tags
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# Shared by all tasks: pull images and write logs. Business permissions live
# on each task role.
resource "aws_iam_role" "task_execution" {
  name               = "${local.prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ECS Express Mode acts with this role to create and manage each service's load
# balancer, listener rules, target group, certificate, security groups, scaling and alarm.
data "aws_iam_policy_document" "ecs_infrastructure_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_infrastructure" {
  name               = "${local.prefix}-ecs-express-infrastructure"
  assume_role_policy = data.aws_iam_policy_document.ecs_infrastructure_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_infrastructure" {
  role       = aws_iam_role.ecs_infrastructure.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
}

# Calls Express Mode makes with this role that the managed policy (v6) does not grant,
# found in CloudTrail on the first deployment (AccessDenied, then rollback): the
# deployment lifecycle hook reads and updates the service and reads its rollback alarm,
# and Elastic Load Balancing checks ec2:DescribeAccountAttributes with the caller's
# credentials when creating the load balancer.
data "aws_iam_policy_document" "ecs_infrastructure_extra" {
  statement {
    sid       = "ManageOwnServices"
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = ["arn:aws:ecs:${local.region}:${local.account_id}:service/${aws_ecs_cluster.this.name}/*"]
  }

  statement {
    sid       = "ReadAlarmsAndAccount"
    actions   = ["cloudwatch:DescribeAlarms", "ec2:DescribeAccountAttributes"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_infrastructure_extra" {
  name   = "express-mode-gaps"
  role   = aws_iam_role.ecs_infrastructure.id
  policy = data.aws_iam_policy_document.ecs_infrastructure_extra.json

  # Services reference the role through this policy (see local.express), so they are
  # created after, and destroyed before, both policies.
  depends_on = [aws_iam_role_policy_attachment.ecs_infrastructure]
}

locals {
  express = {
    cluster_name       = aws_ecs_cluster.this.name
    execution_role_arn = aws_iam_role.task_execution.arn
    # Built from the inline policy so every service is created after the role has all
    # its permissions, and destroyed before they are removed.
    infrastructure_role_arn = "arn:aws:iam::${local.account_id}:role/${aws_iam_role_policy.ecs_infrastructure_extra.role}"
  }
}

module "company_mcp" {
  source = "../company-mcp"

  name_prefix             = local.prefix
  cluster_name            = local.express.cluster_name
  image                   = local.images.company_mcp
  port                    = var.company_mcp_port
  audience                = local.mcp_audience
  running                 = var.running
  subnet_ids              = module.network.private_subnet_ids
  security_group_ids      = [aws_security_group.services.id]
  execution_role_arn      = local.express.execution_role_arn
  infrastructure_role_arn = local.express.infrastructure_role_arn
  table_name              = module.ledger.name
  table_arn               = module.ledger.arn
  event_bus_name          = module.event_bus.name
  event_bus_arn           = module.event_bus.arn
  bucket_name             = module.documents.name
  bucket_arn              = module.documents.arn
  log_level               = var.log_level
  log_retention_days      = var.log_retention_days
  tags                    = var.tags
}

# Observability only: in-memory live activity of every employee, rebuilt from their
# heartbeats after a restart. One task, internal load balancer, no AWS permissions.
module "agent_activity" {
  source = "../express-service"

  name_prefix = local.prefix
  name        = "agent-activity"
  enabled     = var.running
  image       = local.images.agent_activity
  port        = 8113
  environment = { PORT = "8113", LOG_LEVEL = var.log_level }

  cluster_name            = local.express.cluster_name
  execution_role_arn      = local.express.execution_role_arn
  infrastructure_role_arn = local.express.infrastructure_role_arn
  subnet_ids              = module.network.private_subnet_ids
  security_group_ids      = [aws_security_group.services.id]
  log_retention_days      = var.log_retention_days
  tags                    = var.tags
}

module "agent" {
  source   = "../ecs-agent"
  for_each = local.organization

  name_prefix = local.prefix
  agent_id    = each.key
  agent_role  = each.value.role
  department  = each.value.department
  reports_to  = each.value.reports_to

  cluster_name            = local.express.cluster_name
  image                   = local.images.agent
  cpu                     = var.agent_cpu
  memory                  = var.agent_memory
  running                 = var.running
  subnet_ids              = module.network.private_subnet_ids
  security_group_ids      = [aws_security_group.agents.id]
  execution_role_arn      = local.express.execution_role_arn
  infrastructure_role_arn = local.express.infrastructure_role_arn

  queue_arn   = module.inbox[each.key].arn
  queue_url   = module.inbox[each.key].url
  bucket_name = module.documents.name
  bucket_arn  = module.documents.arn

  appconfig = {
    application_id = module.prompts.application_id
    environment_id = module.prompts.agents[each.key].environment_id
    profile_id     = module.prompts.agents[each.key].profile_id
    arn            = module.prompts.agents[each.key].arn
  }

  bedrock_model_id      = var.bedrock_model_id
  bedrock_resource_arns = local.bedrock_resource_arns
  bedrock_role_arn      = var.bedrock_role_arn

  company_mcp_url    = module.company_mcp.url
  mcp_audience       = local.mcp_audience
  agent_activity_url = module.agent_activity.url
  log_level          = var.log_level
  log_retention_days = var.log_retention_days
  tags               = var.tags
}

# The order simulator is a caller like any agent: the Company MCP only lets it place
# and read orders. Nexo runs as this role, and so does whoever runs ./sim against AWS.
data "aws_iam_policy_document" "order_simulator_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "order_simulator" {
  name                 = "${local.prefix}-order-simulator"
  assume_role_policy   = data.aws_iam_policy_document.order_simulator_assume.json
  max_session_duration = 3600

  tags = var.tags
}

# Front end ----------------------------------------------------------------------

module "nexo" {
  source = "../nexo"

  name_prefix             = local.prefix
  running                 = var.running
  cluster_name            = local.express.cluster_name
  image                   = local.images.nexo
  execution_role_arn      = local.express.execution_role_arn
  execution_role_name     = aws_iam_role.task_execution.name
  infrastructure_role_arn = local.express.infrastructure_role_arn
  task_role_arn           = aws_iam_role.order_simulator.arn
  task_role_name          = aws_iam_role.order_simulator.name
  public_subnet_ids       = module.network.public_subnet_ids
  security_group_ids      = [aws_security_group.services.id]

  company_mcp_url               = module.company_mcp.url
  mcp_audience                  = local.mcp_audience
  agent_activity_url            = module.agent_activity.url
  table_name                    = module.ledger.name
  table_arn                     = module.ledger.arn
  business_events_log_group     = module.event_bus.audit_log_group
  business_events_log_group_arn = module.event_bus.audit_log_group_arn
  bucket_name                   = module.documents.name
  bucket_arn                    = module.documents.arn
  log_retention_days            = var.log_retention_days
  tags                          = var.tags
}

# Observability ------------------------------------------------------------------

module "observability" {
  source = "../observability"

  name_prefix = local.prefix
  agent_ids   = keys(local.organization)
  log_groups = merge(
    { for id, agent in module.agent : id => agent.log_group },
    { "company-mcp" = module.company_mcp.log_group },
  )
  business_events_log_group = module.event_bus.audit_log_group
  queues                    = { for id, q in module.inbox : id => { name = q.name, dlq_name = q.dlq_name } }
  cluster_name              = aws_ecs_cluster.this.name
  service_names             = concat(keys(local.organization), ["company-mcp", "agent-activity", "nexo"])
}
