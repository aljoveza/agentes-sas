# The org chart, shared by the AWS stack (modules/company) and the LocalStack stack
# (infrastructure/local). Adding an employee is adding an entry here, a prompt file,
# and its tool grants in services/company-mcp/src/company_mcp/policy.py.
#
#   subscribes_to  department-level events delivered to this agent's inbox.
# Agents never publish events themselves: every event comes from a Company MCP
# operation, after the state change it describes.

locals {
  organization = {
    ceo = {
      role          = "CEO"
      department    = "executive"
      reports_to    = ""
      prompt        = "ceo.yaml"
      subscribes_to = ["DecisionRequested"]
    }

    commercial-manager = {
      role          = "Commercial Manager"
      department    = "commercial"
      reports_to    = "ceo"
      prompt        = "commercial/manager.yaml"
      subscribes_to = ["OrderPlaced"]
    }
    sales-rep = {
      role          = "Sales Rep"
      department    = "commercial"
      reports_to    = "commercial-manager"
      prompt        = "commercial/sales-rep.yaml"
      subscribes_to = []
    }

    operations-manager = {
      role       = "Operations Manager"
      department = "operations"
      reports_to = "ceo"
      prompt     = "operations/manager.yaml"
      # Reserve on validation; ship once the payment is received too.
      subscribes_to = ["OrderValidated", "PaymentReceived"]
    }
    fulfillment = {
      role          = "Fulfillment"
      department    = "operations"
      reports_to    = "operations-manager"
      prompt        = "operations/fulfillment.yaml"
      subscribes_to = []
    }

    accounting-manager = {
      role       = "Accounting Manager"
      department = "accounting"
      reports_to = "ceo"
      prompt     = "accounting/manager.yaml"
      # Settle on validation; refund (or escalate the refund) when an order is cancelled.
      subscribes_to = ["OrderValidated", "OrderCancelled"]
    }
    accountant = {
      role          = "Accountant"
      department    = "accounting"
      reports_to    = "accounting-manager"
      prompt        = "accounting/accountant.yaml"
      subscribes_to = []
    }
  }

  direct_reports = {
    for id, _ in local.organization : id => [
      for other_id, other in local.organization : other_id if other.reports_to == id
    ]
  }

  # Routing derived from the org chart. Directed events carry the addressee in
  # the payload (assignedTo / assignedBy / requestedBy).
  event_routes = {
    for id, agent in local.organization : id => {
      for route, r in {
        assigned-work = {
          enabled = true
          pattern = { "detail-type" = ["WorkAssigned"], detail = { payload = { assignedTo = [id] } } }
        }
        work-results = {
          enabled = length(local.direct_reports[id]) > 0
          pattern = { "detail-type" = ["WorkCompleted", "WorkFailed"], detail = { payload = { assignedBy = [id] } } }
        }
        decision-results = {
          enabled = agent.reports_to == "ceo"
          pattern = { "detail-type" = ["DecisionApproved", "DecisionRejected"], detail = { payload = { requestedBy = [id] } } }
        }
        subscriptions = {
          enabled = length(agent.subscribes_to) > 0
          pattern = { "detail-type" = agent.subscribes_to }
        }
      } : route => jsonencode(r.pattern) if r.enabled
    }
  }
}

output "agents" {
  description = "Agents keyed by id: role, department, reports_to, prompt (path under prompts/), subscribes_to."
  value       = local.organization
}

output "event_routes" {
  description = "EventBridge patterns (JSON) per agent and route name."
  value       = local.event_routes
}
