"""Business operations. Every method takes the calling agent as its first argument,
validates deterministically, and is idempotent: calling it again with the same
arguments returns the same record and never duplicates a business action.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel

from . import ids, policy
from .artifacts import ArtifactStore
from .errors import (
    BusinessError,
    DependencyFailure,
    EscalationRequired,
    Forbidden,
    IdempotencyConflict,
    InvalidInput,
    InvalidState,
    InventoryUnavailable,
    NotFound,
    OrderCancelled,
    WithinAuthority,
)
from .events import EventPublisher, deterministic_event_id, new_event
from .models import (
    Customer,
    CustomerStatus,
    Decision,
    DecisionStatus,
    DecisionType,
    Identity,
    InventoryItem,
    Invoice,
    InvoiceStatus,
    Order,
    OrderLine,
    OrderLineRequest,
    OrderMargin,
    OrderProgress,
    OrderStatus,
    Payment,
    PaymentStatus,
    Reservation,
    ReservationLine,
    ReservationStatus,
    Shipment,
    ShipmentStatus,
    Task,
    TaskStatus,
    money,
)
from .repository import (
    META,
    ConditionFailed,
    Repository,
    TransactionCancelled,
    customer_key,
    decision_key,
    index_sort_key,
    order_pk,
    product_key,
    task_key,
)

M = TypeVar("M", bound=BaseModel)

HUNDRED = Decimal(100)
# Task types used in the company's procedures (prompts/); get_order_status looks them up by id.
KNOWN_TASK_TYPES = ("ValidateCustomer", "ReserveStock", "ShipOrder", "FulfillOrder", "SettleOrder", "RefundOrder")
CARRIER = "DEMO-EXPRESS"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _percent(part: Decimal, whole: Decimal) -> Decimal:
    return money(part / whole * HUNDRED) if whole else Decimal("0.00")


def _discounted(subtotal: Decimal, discount_percent: Decimal) -> Decimal:
    return money(subtotal * (HUNDRED - discount_percent) / HUNDRED)


class CompanyService:
    def __init__(
        self,
        repo: Repository,
        publisher: EventPublisher,
        artifacts: ArtifactStore,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.repo = repo
        self.publisher = publisher
        self.artifacts = artifacts
        self.clock = clock

    # ------------------------------------------------------------------ identity

    def whoami(self, actor: str) -> Identity:
        return Identity(
            agentId=actor,
            reportsTo=policy.reports_to(actor),
            directReports=policy.direct_reports(actor),
            tools=sorted(policy.TOOL_GRANTS.get(actor, ())),
        )

    # ------------------------------------------------------------------ orders

    def place_order(
        self,
        actor: str,
        *,
        order_id: str,
        customer_id: str,
        lines: list[OrderLineRequest],
        payment_reference: str,
        payment_amount: Decimal,
        requested_discount_percent: Decimal = Decimal(0),
    ) -> Order:
        """Record a customer order. Prices come from the catalog, never from the caller."""
        if not lines:
            raise InvalidInput("An order needs at least one line.")
        product_ids = [line.productId for line in lines]
        if len(set(product_ids)) != len(product_ids):
            raise InvalidInput("Each product may appear only once per order.")

        existing = self.repo.get(order_pk(order_id))
        if existing:
            self._flush(existing)
            order = Order.model_validate(existing)
            requested = sorted((line.productId, line.quantity) for line in lines)
            stored = sorted((line.productId, line.quantity) for line in order.lines)
            if order.customerId != customer_id or requested != stored:
                raise IdempotencyConflict(f"Order {order_id} already exists with different content.")
            return order

        self._customer(customer_id)
        order_lines = []
        for line in lines:
            product = self._inventory(line.productId)
            order_lines.append(
                OrderLine(
                    productId=line.productId,
                    quantity=line.quantity,
                    unitPrice=product.unitPrice,
                    lineTotal=money(product.unitPrice * line.quantity),
                )
            )
        subtotal = money(sum((line.lineTotal for line in order_lines), Decimal(0)))
        now = self.clock()
        order = Order(
            orderId=order_id,
            customerId=customer_id,
            lines=order_lines,
            subtotal=subtotal,
            requestedDiscountPercent=requested_discount_percent,
            total=subtotal,
            paymentReference=payment_reference,
            paymentAmount=money(payment_amount),
            status=OrderStatus.PLACED,
            createdAt=now,
            updatedAt=now,
        )
        event = self._event(
            "OrderPlaced",
            order_id,
            actor,
            {
                "orderId": order_id,
                "customerId": customer_id,
                "lines": [{"productId": line.productId, "quantity": line.quantity} for line in order_lines],
                "subtotal": str(subtotal),
                "requestedDiscountPercent": str(requested_discount_percent),
                "currency": order.currency,
            },
        )
        item = self._item(
            order, order_pk(order_id), META, index=("ORDERS", index_sort_key(order.status, now, order_id))
        )
        stored, _ = self._create(item, event)
        return Order.model_validate(stored)

    def get_order(self, actor: str, order_id: str) -> Order:
        return self._order(order_id)

    def list_orders(self, actor: str, status: OrderStatus | None = None, limit: int = 20) -> list[Order]:
        items = self.repo.query_index("ORDERS", f"{status}#" if status else None, limit)
        return [Order.model_validate(item) for item in items]

    def validate_order(self, actor: str, order_id: str, discount_percent: Decimal = Decimal(0)) -> Order:
        """Approve commercial conditions. Discounts above the manager's authority need an approved CEO decision."""
        item = self._require(order_pk(order_id), META, f"Order {order_id} not found.")
        self._flush(item)
        order = Order.model_validate(item)
        self._not_cancelled(order)
        if order.status == OrderStatus.VALIDATED:
            if order.discountPercent == discount_percent:
                return order
            raise InvalidState(
                f"Order {order_id} was already validated with a {order.discountPercent}% discount."
            )

        customer = self._customer(order.customerId)
        if customer.status != CustomerStatus.ACTIVE:
            raise BusinessError(f"Customer {customer.customerId} is {customer.status}.", code="CUSTOMER_NOT_ACTIVE")

        self._check_discount_authority(actor, order_id, discount_percent)

        total = _discounted(order.subtotal, discount_percent)
        if total > customer.creditLimit:
            raise BusinessError(
                f"Order total {total} exceeds the customer's credit limit {customer.creditLimit}.",
                code="CREDIT_LIMIT_EXCEEDED",
            )

        now = self.clock()
        event = self._event(
            "OrderValidated",
            order_id,
            actor,
            {
                "orderId": order_id,
                "customerId": order.customerId,
                "lines": [{"productId": line.productId, "quantity": line.quantity} for line in order.lines],
                "discountPercent": str(discount_percent),
                "total": str(total),
                "currency": order.currency,
            },
        )
        updated = self._transition(
            item,
            allowed_from={OrderStatus.PLACED},
            to_status=OrderStatus.VALIDATED,
            updates={
                "discountPercent": discount_percent,
                "total": total,
                "validatedBy": actor,
                "updatedAt": now,
                "gsi1sk": index_sort_key(OrderStatus.VALIDATED, order.createdAt, order_id),
            },
            event=event,
        )
        return Order.model_validate(updated)

    def _check_discount_authority(self, actor: str, order_id: str, discount_percent: Decimal) -> None:
        limit = policy.authority_limit(actor, DecisionType.SPECIAL_DISCOUNT) or Decimal(0)
        if discount_percent <= limit:
            return
        decision_id = ids.decision_id(DecisionType.SPECIAL_DISCOUNT, order_id)
        stored = self.repo.get(*decision_key(decision_id))
        decision = Decision.model_validate(stored) if stored else None
        if decision and decision.status == DecisionStatus.APPROVED and decision.requestedValue >= discount_percent:
            return

        details = {"authorityLimit": str(limit), "requested": str(discount_percent), "decisionId": decision_id}
        if decision is None:
            raise EscalationRequired(
                f"A {discount_percent}% discount exceeds your authority of {limit}%. Call request_ceo_approval "
                f"with decision_type=SPECIAL_DISCOUNT, correlation_id={order_id}, requested_value={discount_percent} "
                "and wait for the CEO's decision.",
                details=details,
            )
        if decision.status == DecisionStatus.PENDING:
            raise EscalationRequired(
                f"Decision {decision_id} is still pending with the CEO. Wait for DecisionApproved or DecisionRejected.",
                details=details,
            )
        if decision.status == DecisionStatus.REJECTED:
            raise EscalationRequired(
                f"The CEO rejected decision {decision_id}: {decision.rationale}. "
                f"Validate with a discount of at most {limit}%.",
                details=details,
            )
        raise EscalationRequired(
            f"The CEO approved at most {decision.requestedValue}%; {discount_percent}% needs a new decision.",
            details=details,
        )

    def get_order_margin(self, actor: str, order_id: str) -> OrderMargin:
        order = self._order(order_id)
        cost = money(
            sum((self._inventory(line.productId).unitCost * line.quantity for line in order.lines), Decimal(0))
        )
        current_margin = order.total - cost
        requested_total = _discounted(order.subtotal, order.requestedDiscountPercent)
        requested_margin = requested_total - cost
        return OrderMargin(
            orderId=order_id,
            subtotal=order.subtotal,
            cost=cost,
            currentDiscountPercent=order.discountPercent,
            currentMargin=current_margin,
            currentMarginPercent=_percent(current_margin, order.total),
            requestedDiscountPercent=order.requestedDiscountPercent,
            marginAtRequestedDiscount=requested_margin,
            marginPercentAtRequestedDiscount=_percent(requested_margin, requested_total),
        )

    # ------------------------------------------------------------------ customers & inventory

    def get_customer(self, actor: str, customer_id: str) -> Customer:
        return self._customer(customer_id)

    def get_inventory(self, actor: str, product_id: str) -> InventoryItem:
        return self._inventory(product_id)

    def reserve_inventory(self, actor: str, order_id: str) -> Reservation:
        """Atomically reserve every line of a validated order, or nothing."""
        pk = order_pk(order_id)
        existing = self.repo.get(pk, "RESERVATION")
        if existing:
            return self._existing_reservation(existing, order_id)

        order = self._order(order_id)
        self._not_cancelled(order)
        if order.status != OrderStatus.VALIDATED:
            raise InvalidState(f"Order {order_id} must be validated before reserving inventory.")

        now = self.clock()
        reservation = Reservation(
            orderId=order_id,
            lines=[ReservationLine(productId=line.productId, quantity=line.quantity) for line in order.lines],
            status=ReservationStatus.RESERVED,
            reservedBy=actor,
            createdAt=now,
            updatedAt=now,
        )
        event = self._event(
            "InventoryReserved",
            order_id,
            actor,
            {"orderId": order_id, "lines": [line.model_dump() for line in reservation.lines]},
        )
        item = self._item(reservation, pk, "RESERVATION")
        item["pendingEvent"] = json.dumps(event)
        operations = [
            self.repo.tx_update(
                *product_key(line.productId),
                add={"available": -line.quantity, "reserved": line.quantity},
                condition="#available >= :quantity",
                values={":quantity": line.quantity},
            )
            for line in reservation.lines
        ]
        operations.append(self.repo.tx_put_new(item))
        try:
            self.repo.transact(operations)
        except TransactionCancelled:
            # Either a concurrent call already reserved (idempotent success) or stock is short.
            existing = self.repo.get(pk, "RESERVATION")
            if existing:
                return self._existing_reservation(existing, order_id)
            self._report_shortage(actor, order_id, reservation.lines)
            raise DependencyFailure(f"Reservation for {order_id} was interrupted; retry.") from None

        self._flush(item)
        return reservation

    def _existing_reservation(self, item: dict[str, Any], order_id: str) -> Reservation:
        self._flush(item)
        reservation = Reservation.model_validate(item)
        if reservation.status == ReservationStatus.RELEASED:
            raise InvalidState(f"Inventory for {order_id} was released; it cannot be reserved again.")
        return reservation

    def _report_shortage(self, actor: str, order_id: str, lines: list[ReservationLine]) -> None:
        shortages = []
        for line in lines:
            item = self._inventory(line.productId)
            if item.available < line.quantity:
                shortages.append(
                    {"productId": line.productId, "requested": line.quantity, "available": item.available}
                )
        if not shortages:
            return
        # No record to carry this event, so its id is derived from the order to stay deduplicable.
        self.publisher.publish(
            self._event(
                "InventoryUnavailable",
                order_id,
                actor,
                {"orderId": order_id, "shortages": shortages},
                event_id=deterministic_event_id("InventoryUnavailable", order_id),
            )
        )
        raise InventoryUnavailable(
            f"Not enough stock to reserve order {order_id}.", details={"shortages": shortages}
        )

    def release_inventory(self, actor: str, order_id: str, reason: str) -> Reservation:
        pk = order_pk(order_id)
        item = self._require(pk, "RESERVATION", f"No reservation exists for order {order_id}.")
        self._flush(item)
        reservation = Reservation.model_validate(item)
        if reservation.status == ReservationStatus.RELEASED:
            return reservation
        if self.repo.get(pk, "SHIPMENT"):
            raise InvalidState(f"Order {order_id} already has a shipment; its inventory cannot be released.")

        now = self.clock()
        operations = [
            self.repo.tx_update(
                pk,
                "RESERVATION",
                set_={"status": ReservationStatus.RELEASED.value, "reason": reason, "updatedAt": now},
                condition="#status = :reserved",
                values={":reserved": ReservationStatus.RESERVED.value},
            )
        ]
        operations += [
            self.repo.tx_update(
                *product_key(line.productId), add={"available": line.quantity, "reserved": -line.quantity}
            )
            for line in reservation.lines
        ]
        try:
            self.repo.transact(operations)
        except TransactionCancelled:
            current = Reservation.model_validate(self._require(pk, "RESERVATION", "Reservation disappeared."))
            if current.status == ReservationStatus.RELEASED:
                return current
            raise DependencyFailure(f"Release for {order_id} was interrupted; retry.") from None
        return reservation.model_copy(
            update={"status": ReservationStatus.RELEASED, "reason": reason, "updatedAt": now}
        )

    # ------------------------------------------------------------------ shipments

    def create_shipment(self, actor: str, order_id: str) -> Shipment:
        pk = order_pk(order_id)
        existing = self.repo.get(pk, "SHIPMENT")
        if existing:
            self._flush(existing)
            return Shipment.model_validate(existing)

        self._not_cancelled(self._order(order_id))
        reservation = self.repo.get(pk, "RESERVATION")
        if not reservation or reservation["status"] != ReservationStatus.RESERVED:
            raise InvalidState(f"Reserve inventory for order {order_id} before creating a shipment.")
        payment = self.repo.get(pk, "PAYMENT")
        if not payment or payment["status"] != PaymentStatus.RECEIVED:
            # Nothing leaves the warehouse unpaid: a platform rule, not a prompt instruction.
            raise InvalidState(
                f"Order {order_id} has no received payment yet; ship only after PaymentReceived.",
                code="PAYMENT_PENDING",
            )

        now = self.clock()
        shipment = Shipment(
            shipmentId=ids.shipment_id(order_id),
            orderId=order_id,
            status=ShipmentStatus.CREATED,
            carrier=CARRIER,
            trackingNumber=ids.tracking_number(order_id),
            createdBy=actor,
            createdAt=now,
            updatedAt=now,
        )
        event = self._event(
            "ShipmentCreated",
            order_id,
            actor,
            {
                "orderId": order_id,
                "shipmentId": shipment.shipmentId,
                "carrier": CARRIER,
                "trackingNumber": shipment.trackingNumber,
            },
        )
        stored, _ = self._create(self._item(shipment, pk, "SHIPMENT"), event)
        return Shipment.model_validate(stored)

    def track_shipment(self, actor: str, shipment_id: str) -> Shipment:
        order_id = ids.order_id_from(shipment_id, "SHP-")
        return Shipment.model_validate(
            self._require(order_pk(order_id), "SHIPMENT", f"Shipment {shipment_id} not found.")
        )

    # ------------------------------------------------------------------ accounting

    def create_invoice(self, actor: str, order_id: str) -> Invoice:
        pk = order_pk(order_id)
        existing = self.repo.get(pk, "INVOICE")
        if existing:
            self._flush(existing)
            return Invoice.model_validate(existing)

        order = self._order(order_id)
        self._not_cancelled(order)
        if order.status != OrderStatus.VALIDATED:
            raise InvalidState(f"Order {order_id} must be validated before it can be invoiced.")

        invoice_id = ids.invoice_id(order_id)
        invoice = Invoice(
            invoiceId=invoice_id,
            orderId=order_id,
            customerId=order.customerId,
            lines=order.lines,
            currency=order.currency,
            subtotal=order.subtotal,
            discountPercent=order.discountPercent,
            total=order.total,
            issuedBy=actor,
            createdAt=self.clock(),
        )
        invoice.artifactKey = self.artifacts.put_json(
            f"artifacts/invoices/{invoice_id}.json", invoice.model_dump(mode="json")
        )
        event = self._event(
            "InvoiceCreated",
            order_id,
            actor,
            {
                "invoiceId": invoice_id,
                "orderId": order_id,
                "total": str(invoice.total),
                "currency": invoice.currency,
                "artifactKey": invoice.artifactKey,
            },
        )
        stored, _ = self._create(self._item(invoice, pk, "INVOICE"), event)
        return Invoice.model_validate(stored)

    def get_invoice(self, actor: str, invoice_id: str) -> Invoice:
        order_id = ids.order_id_from(invoice_id, "INV-")
        return Invoice.model_validate(self._require(order_pk(order_id), "INVOICE", f"Invoice {invoice_id} not found."))

    def register_payment(self, actor: str, order_id: str) -> Payment:
        """Check the payment captured with the order against the validated total."""
        pk = order_pk(order_id)
        existing = self.repo.get(pk, "PAYMENT")
        if existing:
            self._flush(existing)
            return Payment.model_validate(existing)

        order = self._order(order_id)
        self._not_cancelled(order)
        if order.status != OrderStatus.VALIDATED:
            raise InvalidState(f"Order {order_id} must be validated before registering its payment.")

        matches = order.paymentAmount == order.total
        payment = Payment(
            orderId=order_id,
            paymentReference=order.paymentReference,
            amount=order.paymentAmount,
            expectedAmount=order.total,
            status=PaymentStatus.RECEIVED if matches else PaymentStatus.REJECTED,
            reason=None if matches else f"Paid {order.paymentAmount} but the validated total is {order.total}.",
            registeredBy=actor,
            createdAt=self.clock(),
        )
        event = self._event(
            "PaymentReceived" if matches else "PaymentRejected",
            order_id,
            actor,
            {
                "orderId": order_id,
                "paymentReference": payment.paymentReference,
                "amount": str(payment.amount),
                "expectedAmount": str(payment.expectedAmount),
                "reason": payment.reason,
            },
        )
        stored, _ = self._create(self._item(payment, pk, "PAYMENT"), event)
        return Payment.model_validate(stored)

    # ------------------------------------------------------------------ order lifecycle

    def get_order_status(self, actor: str, order_id: str) -> OrderProgress:
        """One read of where an order stands: reservation, payment, invoice, shipment, tasks, decisions."""
        order = self._order(order_id)
        children = {item["sk"]: item for item in self.repo.query_partition(order_pk(order_id))}
        # Tasks and decisions have deterministic ids per order, so they are read by key.
        related = self.repo.get_many(
            [task_key(ids.task_id(t, order_id)) for t in KNOWN_TASK_TYPES]
            + [decision_key(ids.decision_id(d, order_id)) for d in DecisionType]
        )
        payment = children.get("PAYMENT")
        return OrderProgress(
            orderId=order_id,
            status=order.status,
            customerId=order.customerId,
            total=order.total,
            discountPercent=order.discountPercent,
            requestedDiscountPercent=order.requestedDiscountPercent,
            cancelReason=order.cancelReason,
            reservation=children.get("RESERVATION", {}).get("status"),
            payment=payment.get("status") if payment else None,
            paymentAmount=payment.get("amount") if payment else None,
            invoice=children.get("INVOICE", {}).get("status", InvoiceStatus.ISSUED) if "INVOICE" in children else None,
            shipment=children.get("SHIPMENT", {}).get("status"),
            tasks={t["taskId"]: t["status"] for t in related if "taskId" in t},
            decisions={d["decisionId"]: d["status"] for d in related if "decisionId" in d},
        )

    def cancel_order(self, actor: str, order_id: str, reason: str) -> Order:
        """Cancel an order that has not shipped: releases reserved stock and voids the invoice, atomically.
        A received payment is left for Accounting to refund (OrderCancelled says so)."""
        pk = order_pk(order_id)
        item = self._require(pk, META, f"Order {order_id} not found.")
        self._flush(item)
        order = Order.model_validate(item)
        if order.status == OrderStatus.CANCELLED:
            return order
        if self.repo.get(pk, "SHIPMENT"):
            raise InvalidState(f"Order {order_id} has already shipped; it cannot be cancelled.", code="ALREADY_SHIPPED")

        now = self.clock()
        reservation = self.repo.get(pk, "RESERVATION")
        invoice = self.repo.get(pk, "INVOICE")
        payment = self.repo.get(pk, "PAYMENT")
        releases = reservation is not None and reservation["status"] == ReservationStatus.RESERVED
        voids = invoice is not None and invoice.get("status", InvoiceStatus.ISSUED) == InvoiceStatus.ISSUED
        event = self._event("OrderCancelled", order_id, actor, {
            "orderId": order_id,
            "reason": reason,
            "cancelledBy": actor,
            "inventoryReleased": releases,
            "invoiceVoided": voids,
            "paymentStatus": payment["status"] if payment else None,
            "refundDue": str(payment["amount"]) if payment and payment["status"] == PaymentStatus.RECEIVED else None,
        })
        operations = [self.repo.tx_update(
            pk, META,
            set_={"status": OrderStatus.CANCELLED.value, "cancelledBy": actor, "cancelReason": reason,
                  "updatedAt": now, "gsi1sk": index_sort_key(OrderStatus.CANCELLED, order.createdAt, order_id),
                  "pendingEvent": json.dumps(event)},
            condition="#status = :current", values={":current": order.status},
        )]
        if releases:
            operations.append(self.repo.tx_update(
                pk, "RESERVATION",
                set_={"status": ReservationStatus.RELEASED.value, "reason": f"order cancelled: {reason}", "updatedAt": now},
                condition="#status = :reserved", values={":reserved": ReservationStatus.RESERVED.value},
            ))
            operations += [
                self.repo.tx_update(*product_key(line["productId"]),
                                    add={"available": int(line["quantity"]), "reserved": -int(line["quantity"])})
                for line in reservation["lines"]
            ]
        if voids:
            operations.append(self.repo.tx_update(pk, "INVOICE", set_={"status": InvoiceStatus.VOID.value}))
        try:
            self.repo.transact(operations)
        except TransactionCancelled:
            latest = Order.model_validate(self._require(pk, META, "Order disappeared."))
            if latest.status == OrderStatus.CANCELLED:
                return latest
            raise DependencyFailure(f"Cancellation of {order_id} was interrupted; retry.") from None
        stored = self._require(pk, META, "Order disappeared.")
        self._flush(stored)
        return Order.model_validate(stored)

    def refund_payment(self, actor: str, order_id: str, reason: str) -> Payment:
        """Refund the received payment of a cancelled order. Above the accounting authority limit,
        an approved REFUND decision from the CEO is required."""
        pk = order_pk(order_id)
        item = self._require(pk, "PAYMENT", f"Order {order_id} has no registered payment; nothing to refund.")
        self._flush(item)
        payment = Payment.model_validate(item)
        if payment.status == PaymentStatus.REFUNDED:
            return payment
        if payment.status != PaymentStatus.RECEIVED:
            raise InvalidState(f"Payment for {order_id} is {payment.status}; only received payments are refunded.")
        if self._order(order_id).status != OrderStatus.CANCELLED:
            raise InvalidState(f"Order {order_id} is not cancelled; cancel it before refunding.")

        limit = policy.authority_limit("accounting-manager", DecisionType.REFUND) or Decimal(0)
        if payment.amount > limit:
            decision_id = ids.decision_id(DecisionType.REFUND, order_id)
            stored = self.repo.get(*decision_key(decision_id))
            decision = Decision.model_validate(stored) if stored else None
            if not (decision and decision.status == DecisionStatus.APPROVED and decision.requestedValue >= payment.amount):
                state = "pending with the CEO" if decision and decision.status == DecisionStatus.PENDING else \
                        "rejected by the CEO" if decision and decision.status == DecisionStatus.REJECTED else "not requested yet"
                raise EscalationRequired(
                    f"A refund of {payment.amount} exceeds the accounting authority of {limit}; decision {decision_id} is "
                    f"{state}. The Accounting Manager requests it with request_ceo_approval(decision_type=REFUND, "
                    f"correlation_id={order_id}, requested_value={payment.amount}).",
                    details={"authorityLimit": str(limit), "amount": str(payment.amount), "decisionId": decision_id},
                )

        event = self._event("PaymentRefunded", order_id, actor, {
            "orderId": order_id, "amount": str(payment.amount), "paymentReference": payment.paymentReference,
            "reason": reason, "refundedBy": actor,
        })
        updated = self._transition(
            item,
            allowed_from={PaymentStatus.RECEIVED},
            to_status=PaymentStatus.REFUNDED,
            updates={"refundedBy": actor, "refundReason": reason},
            event=event,
        )
        return Payment.model_validate(updated)

    def _not_cancelled(self, order: Order) -> None:
        if order.status == OrderStatus.CANCELLED:
            raise OrderCancelled(f"Order {order.orderId} was cancelled ({order.cancelReason}); no further work on it.")

    # ------------------------------------------------------------------ tasks

    def assign_task(self, actor: str, assigned_to: str, task_type: str, correlation_id: str, goal: str) -> Task:
        if assigned_to not in policy.direct_reports(actor):
            raise Forbidden(f"{assigned_to} does not report to {actor}.")

        task_id = ids.task_id(task_type, correlation_id)
        pk, sk = task_key(task_id)
        existing = self.repo.get(pk, sk)
        if existing:
            self._flush(existing)
            task = Task.model_validate(existing)
            if task.assignedTo != assigned_to or task.assignedBy != actor:
                raise IdempotencyConflict(f"Task {task_id} is already assigned to {task.assignedTo} by {task.assignedBy}.")
            if task.status != TaskStatus.FAILED:
                return task
            # Retrying failed work reuses the task with a new attempt number.
            attempt = task.attempt + 1
            now = self.clock()
            updated = self._transition(
                existing,
                allowed_from={TaskStatus.FAILED},
                to_status=TaskStatus.ASSIGNED,
                updates={
                    "attempt": attempt,
                    "goal": goal,
                    "updatedAt": now,
                    "gsi1sk": index_sort_key(TaskStatus.ASSIGNED, task.createdAt, task_id),
                },
                remove=["errorCode", "errorMessage", "retryable"],
                event=self._work_assigned(task.model_copy(update={"goal": goal, "attempt": attempt}), actor),
            )
            return Task.model_validate(updated)

        now = self.clock()
        task = Task(
            taskId=task_id,
            type=task_type,
            assignedBy=actor,
            assignedTo=assigned_to,
            status=TaskStatus.ASSIGNED,
            correlationId=correlation_id,
            goal=goal,
            createdAt=now,
            updatedAt=now,
        )
        item = self._item(
            task, pk, sk, index=(f"ASSIGNEE#{assigned_to}", index_sort_key(task.status, now, task_id))
        )
        stored, _ = self._create(item, self._work_assigned(task, actor))
        return Task.model_validate(stored)

    def _work_assigned(self, task: Task, actor: str) -> dict[str, Any]:
        return self._event(
            "WorkAssigned",
            task.correlationId,
            actor,
            {
                "taskId": task.taskId,
                "type": task.type,
                "assignedTo": task.assignedTo,
                "assignedBy": task.assignedBy,
                "correlationId": task.correlationId,
                "goal": task.goal,
                "attempt": task.attempt,
            },
        )

    def get_task(self, actor: str, task_id: str) -> Task:
        task = Task.model_validate(self._require(*task_key(task_id), f"Task {task_id} not found."))
        if actor not in (task.assignedTo, task.assignedBy, policy.CEO):
            raise Forbidden(f"Task {task_id} is not yours.")
        return task

    def list_my_tasks(self, actor: str, statuses: list[TaskStatus] | None = None, limit: int = 20) -> list[Task]:
        """Work assigned to the caller, newest first; outstanding work (ASSIGNED, IN_PROGRESS) by default.
        A restarted agent calls this to recover what it was doing."""
        wanted = statuses or [TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS]
        tasks = [
            Task.model_validate(item)
            for status in dict.fromkeys(wanted)
            for item in self.repo.query_index(f"ASSIGNEE#{actor}", f"{status}#", limit)
        ]
        return sorted(tasks, key=lambda t: t.createdAt, reverse=True)[:limit]

    def start_task(self, actor: str, task_id: str) -> Task:
        item, task = self._own_task(actor, task_id)
        if task.status == TaskStatus.IN_PROGRESS:
            return task
        updated = self._transition(
            item,
            allowed_from={TaskStatus.ASSIGNED},
            to_status=TaskStatus.IN_PROGRESS,
            updates=self._task_status_updates(task, TaskStatus.IN_PROGRESS),
        )
        return Task.model_validate(updated)

    def complete_task(self, actor: str, task_id: str, result: str) -> Task:
        item, task = self._own_task(actor, task_id)
        if task.status == TaskStatus.COMPLETED:
            return task
        event = self._event(
            "WorkCompleted",
            task.correlationId,
            actor,
            {
                "taskId": task_id,
                "type": task.type,
                "assignedTo": task.assignedTo,
                "assignedBy": task.assignedBy,
                "attempt": task.attempt,
                "result": result,
            },
        )
        updated = self._transition(
            item,
            allowed_from={TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS},
            to_status=TaskStatus.COMPLETED,
            updates={**self._task_status_updates(task, TaskStatus.COMPLETED), "result": result},
            event=event,
        )
        return Task.model_validate(updated)

    def fail_task(self, actor: str, task_id: str, error_code: str, message: str, retryable: bool) -> Task:
        item, task = self._own_task(actor, task_id)
        if task.status == TaskStatus.FAILED:
            return task
        event = self._event(
            "WorkFailed",
            task.correlationId,
            actor,
            {
                "taskId": task_id,
                "type": task.type,
                "agentId": actor,
                "assignedTo": task.assignedTo,
                "assignedBy": task.assignedBy,
                "attempt": task.attempt,
                "errorCode": error_code,
                "message": message,
                "retryable": retryable,
            },
        )
        updated = self._transition(
            item,
            allowed_from={TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS},
            to_status=TaskStatus.FAILED,
            updates={
                **self._task_status_updates(task, TaskStatus.FAILED),
                "errorCode": error_code,
                "errorMessage": message,
                "retryable": retryable,
            },
            event=event,
        )
        return Task.model_validate(updated)

    def _own_task(self, actor: str, task_id: str) -> tuple[dict[str, Any], Task]:
        item = self._require(*task_key(task_id), f"Task {task_id} not found.")
        self._flush(item)
        task = Task.model_validate(item)
        if task.assignedTo != actor:
            raise Forbidden(f"Task {task_id} is assigned to {task.assignedTo}, not {actor}.")
        return item, task

    def _task_status_updates(self, task: Task, status: TaskStatus) -> dict[str, Any]:
        return {"updatedAt": self.clock(), "gsi1sk": index_sort_key(status, task.createdAt, task.taskId)}

    # ------------------------------------------------------------------ decisions

    def request_ceo_approval(
        self,
        actor: str,
        decision_type: DecisionType,
        correlation_id: str,
        requested_value: Decimal,
        reason: str,
    ) -> Decision:
        if not policy.is_department_manager(actor):
            raise Forbidden("Only department managers escalate to the CEO; ask your manager.")
        limit = policy.authority_limit(actor, decision_type)
        if limit is not None and requested_value <= limit:
            raise WithinAuthority(
                f"{requested_value} is within your {decision_type} authority of {limit}; decide it yourself."
            )

        decision_id = ids.decision_id(decision_type, correlation_id)
        pk, sk = decision_key(decision_id)
        existing = self.repo.get(pk, sk)
        if existing:
            self._flush(existing)
            decision = Decision.model_validate(existing)
            if decision.requestedBy != actor:
                raise IdempotencyConflict(f"{decision_id} was requested by {decision.requestedBy}.")
            return decision

        now = self.clock()
        decision = Decision(
            decisionId=decision_id,
            decisionType=decision_type,
            requestedBy=actor,
            correlationId=correlation_id,
            requestedValue=requested_value,
            managerAuthorityLimit=limit,
            reason=reason,
            status=DecisionStatus.PENDING,
            createdAt=now,
            updatedAt=now,
        )
        event = self._event(
            "DecisionRequested",
            correlation_id,
            actor,
            {
                "decisionId": decision_id,
                "decisionType": decision_type,
                "requestedBy": actor,
                "correlationId": correlation_id,
                "requestedValue": str(requested_value),
                "managerAuthorityLimit": None if limit is None else str(limit),
                "reason": reason,
            },
        )
        item = self._item(decision, pk, sk, index=("DECISIONS", index_sort_key(decision.status, now, decision_id)))
        stored, _ = self._create(item, event)
        return Decision.model_validate(stored)

    def approve_decision(self, actor: str, decision_id: str, rationale: str) -> Decision:
        return self._decide(actor, decision_id, DecisionStatus.APPROVED, rationale)

    def reject_decision(self, actor: str, decision_id: str, rationale: str) -> Decision:
        return self._decide(actor, decision_id, DecisionStatus.REJECTED, rationale)

    def _decide(self, actor: str, decision_id: str, outcome: DecisionStatus, rationale: str) -> Decision:
        if actor != policy.CEO:
            raise Forbidden("Only the CEO decides escalations.")
        item = self._require(*decision_key(decision_id), f"Decision {decision_id} not found.")
        self._flush(item)
        decision = Decision.model_validate(item)
        if decision.status == outcome:
            return decision
        if decision.status != DecisionStatus.PENDING:
            raise InvalidState(f"Decision {decision_id} was already {decision.status}.")

        event = self._event(
            "DecisionApproved" if outcome == DecisionStatus.APPROVED else "DecisionRejected",
            decision.correlationId,
            actor,
            {
                "decisionId": decision_id,
                "decisionType": decision.decisionType,
                "requestedBy": decision.requestedBy,
                "correlationId": decision.correlationId,
                "requestedValue": str(decision.requestedValue),
                "decidedBy": actor,
                "rationale": rationale,
            },
        )
        updated = self._transition(
            item,
            allowed_from={DecisionStatus.PENDING},
            to_status=outcome,
            updates={
                "decidedBy": actor,
                "rationale": rationale,
                "updatedAt": self.clock(),
                "gsi1sk": index_sort_key(outcome, decision.createdAt, decision_id),
            },
            event=event,
        )
        return Decision.model_validate(updated)

    def get_decision(self, actor: str, decision_id: str) -> Decision:
        decision = Decision.model_validate(self._require(*decision_key(decision_id), f"Decision {decision_id} not found."))
        if actor not in (decision.requestedBy, policy.CEO):
            raise Forbidden(f"Decision {decision_id} was requested by {decision.requestedBy}.")
        return decision

    def list_pending_decisions(self, actor: str, limit: int = 20) -> list[Decision]:
        items = self.repo.query_index("DECISIONS", f"{DecisionStatus.PENDING}#", limit)
        return [Decision.model_validate(item) for item in items]

    # ------------------------------------------------------------------ plumbing

    def _event(
        self,
        event_type: str,
        correlation_id: str,
        actor: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return new_event(
            event_type,
            correlation_id=correlation_id,
            source=actor,
            payload=payload,
            timestamp=self.clock(),
            event_id=event_id,
        )

    @staticmethod
    def _item(
        record: BaseModel, pk: str, sk: str, index: tuple[str, str] | None = None
    ) -> dict[str, Any]:
        item = {"pk": pk, "sk": sk, **record.model_dump(exclude_none=True)}
        if index:
            item["gsi1pk"], item["gsi1sk"] = index
        return item

    def _create(self, item: dict[str, Any], event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create-once with the event stored on the record; returns (stored item, created)."""
        item = {**item, "pendingEvent": json.dumps(event)}
        created = self.repo.put_new(item)
        stored = item if created else self._require(item["pk"], item["sk"], "Record disappeared.")
        self._flush(stored)
        return stored, created

    def _transition(
        self,
        item: dict[str, Any],
        *,
        allowed_from: set[str],
        to_status: str,
        updates: dict[str, Any],
        remove: list[str] | None = None,
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a record between states with optimistic concurrency, storing its event in the same write."""
        current = item["status"]
        if current not in allowed_from:
            raise InvalidState(f"Cannot move from {current} to {to_status}.")
        set_ = {**updates, "status": str(to_status)}
        if event:
            set_["pendingEvent"] = json.dumps(event)
        try:
            updated = self.repo.update(
                item["pk"],
                item["sk"],
                set_=set_,
                remove=remove or [],
                condition="#status = :expected",
                values={":expected": current},
            )
        except ConditionFailed:
            latest = self._require(item["pk"], item["sk"], "Record disappeared.")
            if latest["status"] == to_status:
                self._flush(latest)
                return latest
            raise DependencyFailure("The record changed concurrently; retry.") from None
        self._flush(updated)
        return updated

    def _flush(self, item: dict[str, Any]) -> None:
        """Publish an event that was stored but not yet confirmed by EventBridge."""
        pending = item.get("pendingEvent")
        if not pending:
            return
        self.publisher.publish(json.loads(pending))
        self.repo.clear_pending_event(item["pk"], item["sk"], pending)
        item.pop("pendingEvent", None)

    def _require(self, pk: str, sk: str, message: str) -> dict[str, Any]:
        item = self.repo.get(pk, sk)
        if not item:
            raise NotFound(message)
        return item

    def _order(self, order_id: str) -> Order:
        return Order.model_validate(self._require(order_pk(order_id), META, f"Order {order_id} not found."))

    def _customer(self, customer_id: str) -> Customer:
        return Customer.model_validate(self._require(*customer_key(customer_id), f"Customer {customer_id} not found."))

    def _inventory(self, product_id: str) -> InventoryItem:
        return InventoryItem.model_validate(self._require(*product_key(product_id), f"Product {product_id} not found."))
