# What the audience sees of the company from CloudWatch: saved Logs Insights queries
# (pick them from the list instead of typing on stage) and one dashboard. Every log line
# and business event carries the order id as correlationId, so plain structured logs
# give a distributed trace without a tracing system.

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region

  agent_log_groups = [for id in var.agent_ids : var.log_groups[id]]
  # The agents and the Company MCP: everything that acts on an order.
  company_log_groups = concat(local.agent_log_groups, [var.log_groups["company-mcp"]])

  # Logs Insights widgets name their log groups inline.
  sources = {
    company = join(" | ", [for g in local.company_log_groups : "SOURCE '${g}'"])
    agents  = join(" | ", [for g in local.agent_log_groups : "SOURCE '${g}'"])
    mcp     = "SOURCE '${var.log_groups["company-mcp"]}'"
    events  = "SOURCE '${var.business_events_log_group}'"
  }

  queries = {
    order_trace = <<-EOT
      # Replace ORDER-ID with an order id (e.g. from Nexo): its whole story, across services.
      fields @timestamp, agentId, message, tool, eventType, outcome, seconds
      | filter @message like "ORDER-ID"
      | sort @timestamp asc
    EOT

    rejected_calls = <<-EOT
      # Tool calls the Company MCP refused (business rules) or failed: prompts say what an
      # agent should do, the MCP decides what it can do.
      fields @timestamp, agentId, tool, outcome, error
      | filter message like /^tool call/ and outcome != "ok"
      | sort @timestamp desc
    EOT

    agent_turns = <<-EOT
      # One turn = one business event. How busy each employee is, and how long it thinks.
      filter message = "turn completed" or message = "turn failed; will retry"
      | stats count(*) as turns, sum(strcontains(message, "failed")) as failed,
              avg(seconds) as avgSeconds, max(seconds) as maxSeconds by agentId
      | sort turns desc
    EOT

    escalations = <<-EOT
      # Decisions above a manager's authority: requested, approved or rejected by the CEO.
      fields @timestamp, agentId, tool, eventType, correlationId, outcome
      | filter tool in ["request_ceo_approval", "approve_decision", "reject_decision"]
          or eventType in ["DecisionRequested", "DecisionApproved", "DecisionRejected"]
      | sort @timestamp desc
    EOT
  }
}

resource "aws_cloudwatch_query_definition" "order_trace" {
  name            = "${var.name_prefix}/Order trace"
  log_group_names = local.company_log_groups
  query_string    = local.queries.order_trace
}

resource "aws_cloudwatch_query_definition" "rejected_calls" {
  name            = "${var.name_prefix}/Rejected tool calls"
  log_group_names = [var.log_groups["company-mcp"]]
  query_string    = local.queries.rejected_calls
}

resource "aws_cloudwatch_query_definition" "agent_turns" {
  name            = "${var.name_prefix}/Turns per agent"
  log_group_names = local.agent_log_groups
  query_string    = local.queries.agent_turns
}

resource "aws_cloudwatch_query_definition" "escalations" {
  name            = "${var.name_prefix}/Escalations to the CEO"
  log_group_names = local.company_log_groups
  query_string    = local.queries.escalations
}

locals {
  queue_metric = { for kind, attribute in { inbox = "name", dlq = "dlq_name" } : kind => [
    for id in var.agent_ids : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queues[id][attribute], { label = id }]
  ] }

  widgets = [
    {
      type = "text", x = 0, y = 0, width = 24, height = 2
      properties = {
        markdown = "## ${var.name_prefix}: an e-commerce company run by seven AI agents\nInboxes and dead-letter queues per employee, their turns, what the Company MCP allowed or refused, and the business events. Saved queries: Logs Insights → Queries → `${var.name_prefix}/…`"
      }
    },
    {
      type = "metric", x = 0, y = 2, width = 12, height = 6
      properties = {
        title = "Work waiting in each inbox", region = local.region, view = "timeSeries", stacked = true
        stat  = "Maximum", period = 60, metrics = local.queue_metric.inbox
      }
    },
    {
      type = "metric", x = 12, y = 2, width = 12, height = 6
      properties = {
        title = "Dead-letter queues (should stay at 0)", region = local.region, view = "timeSeries"
        stat  = "Maximum", period = 60, metrics = local.queue_metric.dlq
      }
    },
    {
      type = "log", x = 0, y = 8, width = 12, height = 6
      properties = {
        title = "Turns per agent", region = local.region, view = "table"
        query = "${local.sources.agents} | ${replace(local.queries.agent_turns, "/(?m)^#.*\\n/", "")}"
      }
    },
    {
      type = "log", x = 12, y = 8, width = 12, height = 6
      properties = {
        title = "Company MCP tool calls by outcome", region = local.region, view = "timeSeries", stacked = true
        query = "${local.sources.mcp} | filter message like /^tool call/ | stats count(*) by outcome, bin(1m)"
      }
    },
    {
      type = "log", x = 0, y = 14, width = 12, height = 7
      properties = {
        title = "Rejected tool calls", region = local.region, view = "table"
        query = "${local.sources.mcp} | ${replace(local.queries.rejected_calls, "/(?m)^#.*\\n/", "")} | limit 20"
      }
    },
    {
      type = "log", x = 12, y = 14, width = 12, height = 7
      properties = {
        title = "Business events", region = local.region, view = "table"
        query = "${local.sources.events} | fields @timestamp, detail.eventType, detail.correlationId, detail.source | sort @timestamp desc | limit 20"
      }
    },
    {
      type = "metric", x = 0, y = 21, width = 24, height = 6
      properties = {
        title   = "CPU per service (%)", region = local.region, view = "timeSeries", stat = "Average", period = 60
        metrics = [for name in var.service_names : ["AWS/ECS", "CPUUtilization", "ClusterName", var.cluster_name, "ServiceName", name, { label = name }]]
      }
    },
  ]
}

resource "aws_cloudwatch_dashboard" "company" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({ widgets = local.widgets })
}
