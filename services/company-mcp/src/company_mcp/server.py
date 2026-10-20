"""MCP surface. Callers are identified by their IAM role (see iam.py); each agent only
sees and can only call the tools its role grants; every call is logged as structured JSON."""

import json
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any, TypeVar

import anyio
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from pydantic import BeforeValidator, Field, StringConstraints
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import policy
from .errors import BusinessError, Forbidden
from .iam import Authenticator, Unauthenticated
from .models import (
    AgentId,
    BusinessId,
    Customer,
    Decision,
    DecisionType,
    Identity,
    InventoryItem,
    Invoice,
    Order,
    OrderLineRequest,
    OrderMargin,
    OrderProgress,
    OrderStatus,
    Payment,
    Percent,
    Reservation,
    Shipment,
    Task,
    TaskStatus,
    TaskType,
)
from .service import CompanyService

log = logging.getLogger(__name__)

_CORRELATION_ARGS = ("order_id", "correlation_id", "task_id", "decision_id", "invoice_id", "shipment_id")

T = TypeVar("T")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ErrorCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
Limit = Annotated[int, Field(ge=1, le=100)]
Amount = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"))]



def _status_list(value):
    """Accept a list, a JSON list sent as a string, or "A,B" — models phrase multi-value filters all three ways."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().upper() for v in value] if isinstance(value, list) else value


StatusFilter = Annotated[list[TaskStatus] | None, BeforeValidator(lambda v: None if v in (None, "", []) else _status_list(v))]

READ = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}

INSTRUCTIONS = """\
Company tools for the agentes-sas e-commerce company. These tools are the only way to read
or change business records. Every write is idempotent: repeating a call with the same
arguments returns the existing record instead of creating a duplicate, so it is always
safe to retry. Errors are JSON with errorCode, message and retryable. ESCALATION_REQUIRED
means the action exceeds your authority: follow the message to escalate instead of retrying.
"""


def _request_headers() -> dict[str, str]:
    return get_http_headers(include={"authorization"})


class CompanyPolicy(Middleware):
    def __init__(self, authenticator: Authenticator) -> None:
        self._authenticator = authenticator

    async def _agent(self) -> str | None:
        try:
            # May call STS on a cache miss, so keep it off the event loop.
            return await anyio.to_thread.run_sync(self._authenticator.identify, _request_headers())
        except Unauthenticated as exc:
            log.warning("unauthenticated request", extra={"fields": {"reason": str(exc)}})
            return None

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        agent = await self._agent()
        return [tool for tool in tools if policy.can_use(agent, tool.name)]

    async def on_call_tool(self, context, call_next):
        agent = await self._agent()
        tool = context.message.name
        arguments = context.message.arguments or {}
        fields: dict[str, Any] = {"agentId": agent, "tool": tool}
        fields.update({key: arguments[key] for key in _CORRELATION_ARGS if arguments.get(key)})

        if not policy.can_use(agent, tool):
            log.warning("tool call denied", extra={"fields": fields})
            raise ToolError(Forbidden(f"{agent or 'An unidentified caller'} may not use {tool}.").to_json())

        started = time.perf_counter()
        try:
            result = await call_next(context)
        except (ToolError, ValidationError) as exc:
            # Business rule or invalid arguments from the model: an expected outcome, not a server error.
            log.info("tool call rejected", extra={"fields": {**fields, "outcome": "rejected", "error": str(exc)}})
            raise
        except Exception:
            log.exception("tool call failed", extra={"fields": {**fields, "outcome": "error"}})
            raise
        duration_ms = round((time.perf_counter() - started) * 1000)
        log.info("tool call", extra={"fields": {**fields, "outcome": "ok", "durationMs": duration_ms}})
        return result


def create_server(service: CompanyService, authenticator: Authenticator) -> FastMCP:
    mcp = FastMCP(
        name="company-mcp",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        mask_error_details=True,
        middleware=[CompanyPolicy(authenticator)],
    )

    def call(method: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        try:
            agent = authenticator.identify(_request_headers())
        except Unauthenticated as exc:
            raise ToolError(Forbidden(str(exc), code="UNAUTHENTICATED").to_json()) from exc
        try:
            return method(agent, *args, **kwargs)
        except BusinessError as exc:
            raise ToolError(exc.to_json()) from exc

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Identity -----------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def whoami() -> Identity:
        """Your agent id, your manager, your direct reports and the tools you may use."""
        return call(service.whoami)

    # Orders --------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def place_order(
        order_id: BusinessId,
        customer_id: BusinessId,
        lines: Annotated[list[OrderLineRequest], Field(min_length=1, max_length=50)],
        payment_reference: Annotated[str, StringConstraints(min_length=1, max_length=64)],
        payment_amount: Amount,
        requested_discount_percent: Percent = Decimal(0),
    ) -> Order:
        """Record a new customer order and emit OrderPlaced. Prices come from the catalog."""
        return call(
            service.place_order,
            order_id=order_id,
            customer_id=customer_id,
            lines=lines,
            payment_reference=payment_reference,
            payment_amount=payment_amount,
            requested_discount_percent=requested_discount_percent,
        )

    @mcp.tool(annotations=READ)
    def get_order(order_id: BusinessId) -> Order:
        """Get an order with its lines, totals, requested discount and status."""
        return call(service.get_order, order_id)

    @mcp.tool(annotations=READ)
    def list_orders(status: OrderStatus | None = None, limit: Limit = 20) -> list[Order]:
        """List recent orders, newest first, optionally filtered by status."""
        return call(service.list_orders, status, limit)

    @mcp.tool(annotations=WRITE)
    def validate_order(order_id: BusinessId, discount_percent: Percent = Decimal(0)) -> Order:
        """Approve the commercial conditions of an order, granting a discount, and emit OrderValidated.
        Discounts above your authority fail with ESCALATION_REQUIRED until the CEO approves them."""
        return call(service.validate_order, order_id, discount_percent)

    @mcp.tool(annotations=READ)
    def get_order_status(order_id: BusinessId) -> OrderProgress:
        """Where an order stands across departments in one read: order status, stock reservation,
        payment, invoice and shipment, plus the order's tasks and CEO decisions."""
        return call(service.get_order_status, order_id)

    @mcp.tool(annotations={**WRITE, "destructiveHint": True})
    def cancel_order(order_id: BusinessId, reason: Text) -> Order:
        """Cancel an order that has not shipped and emit OrderCancelled. Releases any reserved stock and
        voids the invoice in the same step; a received payment is then refunded by Accounting.
        Fails with ALREADY_SHIPPED once a shipment exists."""
        return call(service.cancel_order, order_id, reason)

    @mcp.tool(annotations=READ)
    def get_order_margin(order_id: BusinessId) -> OrderMargin:
        """Cost and margin of an order at its current discount and at the customer's requested discount."""
        return call(service.get_order_margin, order_id)

    # Customers & inventory -----------------------------------------------------------

    @mcp.tool(annotations=READ)
    def get_customer(customer_id: BusinessId) -> Customer:
        """Get a customer's profile, status and credit limit."""
        return call(service.get_customer, customer_id)

    @mcp.tool(annotations=READ)
    def get_inventory(product_id: BusinessId) -> InventoryItem:
        """Get stock (available and reserved), price and cost for a product."""
        return call(service.get_inventory, product_id)

    @mcp.tool(annotations=WRITE)
    def reserve_inventory(order_id: BusinessId) -> Reservation:
        """Reserve stock for every line of a validated order, all or nothing, and emit InventoryReserved.
        Fails with INVENTORY_UNAVAILABLE (and emits InventoryUnavailable) when stock is short."""
        return call(service.reserve_inventory, order_id)

    @mcp.tool(annotations={**WRITE, "destructiveHint": True})
    def release_inventory(order_id: BusinessId, reason: Text) -> Reservation:
        """Return an order's reserved stock. Not possible once a shipment exists."""
        return call(service.release_inventory, order_id, reason)

    # Shipments -------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def create_shipment(order_id: BusinessId) -> Shipment:
        """Create the shipment for an order with reserved inventory and emit ShipmentCreated."""
        return call(service.create_shipment, order_id)

    @mcp.tool(annotations=READ)
    def track_shipment(shipment_id: Annotated[str, StringConstraints(pattern=r"^SHP-[A-Z0-9-]+$")]) -> Shipment:
        """Get a shipment's status, carrier and tracking number."""
        return call(service.track_shipment, shipment_id)

    # Accounting ------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def create_invoice(order_id: BusinessId) -> Invoice:
        """Issue the invoice for a validated order, store it as a document and emit InvoiceCreated."""
        return call(service.create_invoice, order_id)

    @mcp.tool(annotations=READ)
    def get_invoice(invoice_id: Annotated[str, StringConstraints(pattern=r"^INV-[A-Z0-9-]+$")]) -> Invoice:
        """Get an invoice by id (INV-<order id>)."""
        return call(service.get_invoice, invoice_id)

    @mcp.tool(annotations={**WRITE, "destructiveHint": True})
    def refund_payment(order_id: BusinessId, reason: Text) -> Payment:
        """Refund the received payment of a cancelled order and emit PaymentRefunded.
        Refunds above the accounting authority (5000) need an approved REFUND decision from the CEO,
        otherwise this fails with ESCALATION_REQUIRED."""
        return call(service.refund_payment, order_id, reason)

    @mcp.tool(annotations=WRITE)
    def register_payment(order_id: BusinessId) -> Payment:
        """Check the order's captured payment against its validated total.
        Emits PaymentReceived when they match, PaymentRejected otherwise."""
        return call(service.register_payment, order_id)

    # Tasks ------------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def assign_task(assigned_to: AgentId, task_type: TaskType, correlation_id: BusinessId, goal: Text) -> Task:
        """Delegate work to one of your direct reports and emit WorkAssigned.
        task_type is PascalCase, e.g. CreateShipment. The same type and correlation id is the same task;
        assigning a FAILED task again starts a new attempt."""
        return call(service.assign_task, assigned_to, task_type, correlation_id, goal)

    @mcp.tool(annotations=READ)
    def get_task(task_id: Annotated[str, StringConstraints(pattern=r"^TASK-[A-Za-z0-9-]+$")]) -> Task:
        """Get a task you assigned or that is assigned to you."""
        return call(service.get_task, task_id)

    @mcp.tool(annotations=READ)
    def list_my_tasks(status: StatusFilter = None, limit: Limit = 20) -> list[Task]:
        """Tasks assigned to you, newest first. Without `status` it returns your outstanding work
        (ASSIGNED and IN_PROGRESS) — call it after a restart to resume. `status` may list several states."""
        return call(service.list_my_tasks, status, limit)

    @mcp.tool(annotations=WRITE)
    def start_task(task_id: Annotated[str, StringConstraints(pattern=r"^TASK-[A-Za-z0-9-]+$")]) -> Task:
        """Mark one of your tasks as IN_PROGRESS."""
        return call(service.start_task, task_id)

    @mcp.tool(annotations=WRITE)
    def complete_task(
        task_id: Annotated[str, StringConstraints(pattern=r"^TASK-[A-Za-z0-9-]+$")], result: Text
    ) -> Task:
        """Complete one of your tasks with a short result summary and emit WorkCompleted to your manager."""
        return call(service.complete_task, task_id, result)

    @mcp.tool(annotations=WRITE)
    def fail_task(
        task_id: Annotated[str, StringConstraints(pattern=r"^TASK-[A-Za-z0-9-]+$")],
        error_code: ErrorCode,
        message: Text,
        retryable: bool,
    ) -> Task:
        """Report that one of your tasks failed and emit WorkFailed to your manager.
        retryable=true for temporary problems, false for business outcomes (e.g. INVENTORY_UNAVAILABLE)."""
        return call(service.fail_task, task_id, error_code, message, retryable)

    # Decisions ------------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def request_ceo_approval(
        decision_type: DecisionType, correlation_id: BusinessId, requested_value: Amount, reason: Text
    ) -> Decision:
        """Escalate a decision above your authority to the CEO and emit DecisionRequested.
        For SPECIAL_DISCOUNT, requested_value is the discount percent."""
        return call(service.request_ceo_approval, decision_type, correlation_id, requested_value, reason)

    @mcp.tool(annotations=READ)
    def get_decision(decision_id: Annotated[str, StringConstraints(pattern=r"^DECISION-[A-Z0-9-]+$")]) -> Decision:
        """Get an escalation and its outcome."""
        return call(service.get_decision, decision_id)

    @mcp.tool(annotations=READ)
    def list_pending_decisions(limit: Limit = 20) -> list[Decision]:
        """Escalations waiting for the CEO, newest first."""
        return call(service.list_pending_decisions, limit)

    @mcp.tool(annotations=WRITE)
    def approve_decision(
        decision_id: Annotated[str, StringConstraints(pattern=r"^DECISION-[A-Z0-9-]+$")], rationale: Text
    ) -> Decision:
        """Approve a pending escalation and emit DecisionApproved to the requesting manager."""
        return call(service.approve_decision, decision_id, rationale)

    @mcp.tool(annotations=WRITE)
    def reject_decision(
        decision_id: Annotated[str, StringConstraints(pattern=r"^DECISION-[A-Z0-9-]+$")], rationale: Text
    ) -> Decision:
        """Reject a pending escalation and emit DecisionRejected to the requesting manager."""
        return call(service.reject_decision, decision_id, rationale)

    return mcp
