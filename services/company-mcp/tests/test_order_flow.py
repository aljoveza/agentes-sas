"""Happy path from AGENTS.MD: Order #DEVOPS-2026-001, 3 laptops, 9000."""

import json
from decimal import Decimal

from company_mcp.models import OrderStatus
from conftest import BUCKET, place


def test_happy_path_order(service, publisher, s3):
    order = place(service)
    assert order.total == Decimal("9000.00")
    assert order.status == OrderStatus.PLACED

    validated = service.validate_order("commercial-manager", order.orderId)
    assert validated.status == OrderStatus.VALIDATED

    reservation = service.reserve_inventory("fulfillment", order.orderId)
    assert reservation.status == "RESERVED"
    stock = service.get_inventory("fulfillment", "LAPTOP-PRO")
    assert (stock.available, stock.reserved) == (7, 3)

    payment = service.register_payment("accountant", order.orderId)
    assert payment.status == "RECEIVED"

    shipment = service.create_shipment("fulfillment", order.orderId)
    assert shipment.shipmentId == "SHP-DEVOPS-2026-001"

    invoice = service.create_invoice("accountant", order.orderId)
    assert invoice.invoiceId == "INV-DEVOPS-2026-001"
    document = json.loads(s3.get_object(Bucket=BUCKET, Key=invoice.artifactKey)["Body"].read())
    assert document["total"] == "9000.00"

    assert publisher.types() == [
        "OrderPlaced",
        "OrderValidated",
        "InventoryReserved",
        "PaymentReceived",
        "ShipmentCreated",
        "InvoiceCreated",
    ]
    assert {event["correlationId"] for event in publisher.events} == {"DEVOPS-2026-001"}
    assert len({event["eventId"] for event in publisher.events}) == 6


def test_events_record_the_acting_agent(service, publisher):
    order = place(service)
    service.validate_order("commercial-manager", order.orderId)
    assert [event["source"] for event in publisher.events] == ["order-simulator", "commercial-manager"]


def test_blocked_customer_cannot_be_validated(service):
    order = place(service, "ORDER-BLOCKED", customer_id="CUST-BLOCKED")
    try:
        service.validate_order("commercial-manager", order.orderId)
    except Exception as exc:
        assert exc.code == "CUSTOMER_NOT_ACTIVE"
    else:
        raise AssertionError("expected CUSTOMER_NOT_ACTIVE")


def test_credit_limit_is_enforced(service):
    order = place(service, "ORDER-GLOBEX", customer_id="CUST-GLOBEX", quantity=2, payment_amount="6000.00")
    try:
        service.validate_order("commercial-manager", order.orderId)
    except Exception as exc:
        assert exc.code == "CREDIT_LIMIT_EXCEEDED"
    else:
        raise AssertionError("expected CREDIT_LIMIT_EXCEEDED")


def test_payment_mismatch_is_rejected(service, publisher):
    order = place(service, payment_amount="8000.00")
    service.validate_order("commercial-manager", order.orderId)
    payment = service.register_payment("accountant", order.orderId)
    assert payment.status == "REJECTED"
    assert publisher.types()[-1] == "PaymentRejected"


def test_list_orders_by_status(service):
    place(service, "ORDER-A")
    place(service, "ORDER-B")
    service.validate_order("commercial-manager", "ORDER-B")
    assert [o.orderId for o in service.list_orders("ceo", OrderStatus.VALIDATED)] == ["ORDER-B"]
    assert {o.orderId for o in service.list_orders("ceo")} == {"ORDER-A", "ORDER-B"}
