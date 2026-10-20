# Business plane: one custom bus, routing rules into agent inboxes, an archive
# for replaying incidents, and an audit log of every event (query by
# correlationId in CloudWatch Logs Insights).
#
# Event contract (PutEvents):
#   Source     = company.mcp (the only publisher; agents act through MCP operations)
#   DetailType = eventType, e.g. "OrderPlaced"
#   Detail     = the full envelope { eventId, eventType, eventVersion, correlationId, source, payload }
#                where envelope.source is the agent whose operation caused the event.

resource "aws_cloudwatch_event_bus" "this" {
  name = var.name

  tags = var.tags
}

resource "aws_cloudwatch_event_archive" "all" {
  name             = "${var.name}-all"
  description      = "Every business event, for replay during failure drills."
  event_source_arn = aws_cloudwatch_event_bus.this.arn
  retention_days   = var.archive_retention_days
}

locals {
  routes = merge([
    for subscriber, sub in var.subscriptions : {
      for route, pattern in sub.patterns : "${subscriber}-${route}" => {
        subscriber = subscriber
        pattern    = pattern
        queue_arn  = sub.queue_arn
      }
    }
  ]...)
}

resource "aws_cloudwatch_event_rule" "route" {
  for_each = local.routes

  name           = each.key
  event_bus_name = aws_cloudwatch_event_bus.this.name
  event_pattern  = each.value.pattern

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "route" {
  for_each = local.routes

  rule           = aws_cloudwatch_event_rule.route[each.key].name
  event_bus_name = aws_cloudwatch_event_bus.this.name
  target_id      = "inbox"
  arn            = each.value.queue_arn
}

# Each inbox only accepts messages from the rules that route to it.
data "aws_iam_policy_document" "inbox" {
  for_each = var.subscriptions

  statement {
    sid       = "AllowRoutingRules"
    actions   = ["sqs:SendMessage"]
    resources = [each.value.queue_arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [for key, route in local.routes : aws_cloudwatch_event_rule.route[key].arn if route.subscriber == each.key]
    }
  }
}

resource "aws_sqs_queue_policy" "inbox" {
  for_each = var.subscriptions

  queue_url = each.value.queue_url
  policy    = data.aws_iam_policy_document.inbox[each.key].json
}

# Audit trail --------------------------------------------------------------

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/aws/events/${var.name}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

data "aws_iam_policy_document" "audit" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.audit.arn}:*"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "audit" {
  policy_name     = "${var.name}-audit"
  policy_document = data.aws_iam_policy_document.audit.json
}

resource "aws_cloudwatch_event_rule" "audit" {
  name           = "audit-log"
  event_bus_name = aws_cloudwatch_event_bus.this.name
  event_pattern  = jsonencode({ source = [{ prefix = "" }] })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "audit" {
  rule           = aws_cloudwatch_event_rule.audit.name
  event_bus_name = aws_cloudwatch_event_bus.this.name
  target_id      = "audit-log"
  arn            = aws_cloudwatch_log_group.audit.arn
}
