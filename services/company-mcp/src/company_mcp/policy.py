"""Corporate authority, enforced in code. Prompts describe what an agent SHOULD do;
this module (plus IAM) defines what it CAN do.

The org chart mirrors infrastructure/modules/company/organization.tf.
"""

from decimal import Decimal

from .models import DecisionType

CEO = "ceo"
ORDER_SIMULATOR = "order-simulator"

REPORTS_TO: dict[str, str | None] = {
    "ceo": None,
    "commercial-manager": "ceo",
    "sales-rep": "commercial-manager",
    "operations-manager": "ceo",
    "fulfillment": "operations-manager",
    "accounting-manager": "ceo",
    "accountant": "accounting-manager",
}

# Decisions a manager may take alone, by decision type. Missing type = no delegated authority.
AUTHORITY: dict[str, dict[DecisionType, Decimal]] = {
    "commercial-manager": {DecisionType.SPECIAL_DISCOUNT: Decimal(10)},
    "operations-manager": {},
    "accounting-manager": {DecisionType.REFUND: Decimal(5000)},
}

_SELF = {"whoami", "get_order", "get_order_status", "get_task", "list_my_tasks", "start_task", "complete_task", "fail_task"}
# Managers own their department's orders end to end, including cancelling them.
_MANAGER = _SELF | {"list_orders", "assign_task", "request_ceo_approval", "get_decision", "cancel_order"}

TOOL_GRANTS: dict[str, frozenset[str]] = {
    "ceo": frozenset(
        {
            "whoami",
            "get_order",
            "get_order_status",
            "list_orders",
            "get_order_margin",
            "cancel_order",
            "get_customer",
            "get_inventory",
            "get_invoice",
            "track_shipment",
            "get_task",
            "assign_task",
            "get_decision",
            "list_pending_decisions",
            "approve_decision",
            "reject_decision",
        }
    ),
    "commercial-manager": frozenset(_MANAGER | {"get_customer", "validate_order"}),
    "sales-rep": frozenset(_SELF | {"get_customer"}),
    "operations-manager": frozenset(_MANAGER | {"get_inventory", "track_shipment"}),
    "fulfillment": frozenset(
        _SELF | {"get_inventory", "reserve_inventory", "release_inventory", "create_shipment", "track_shipment"}
    ),
    "accounting-manager": frozenset(_MANAGER | {"get_invoice", "create_invoice", "register_payment", "refund_payment"}),
    "accountant": frozenset(_SELF | {"get_invoice", "create_invoice", "register_payment", "refund_payment"}),
    # The storefront: places orders and reads the catalog (prices, stock) like a real shop page.
    ORDER_SIMULATOR: frozenset({"whoami", "place_order", "get_order", "get_inventory"}),
}


def is_known(agent_id: str | None) -> bool:
    return agent_id in TOOL_GRANTS


def can_use(agent_id: str | None, tool: str) -> bool:
    return tool in TOOL_GRANTS.get(agent_id or "", frozenset())


def reports_to(agent_id: str) -> str | None:
    return REPORTS_TO.get(agent_id)


def direct_reports(agent_id: str) -> list[str]:
    return sorted(a for a, manager in REPORTS_TO.items() if manager == agent_id)


def is_department_manager(agent_id: str) -> bool:
    return REPORTS_TO.get(agent_id) == CEO


def authority_limit(agent_id: str, decision_type: DecisionType) -> Decimal | None:
    return AUTHORITY.get(agent_id, {}).get(decision_type)
