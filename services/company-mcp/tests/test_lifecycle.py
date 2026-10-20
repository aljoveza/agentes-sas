"""Edge cases: nothing ships unpaid, cancellation undoes what it can, refunds respect authority."""

from decimal import Decimal

import pytest

from company_mcp.errors import EscalationRequired, InvalidState, InventoryUnavailable, OrderCancelled
from company_mcp.models import DecisionType
from conftest import place


def validated(service, order_id="O-1", **kwargs):
    order = place(service, order_id, **kwargs)
    return service.validate_order("commercial-manager", order.orderId)


def stock(service, product="LAPTOP-PRO"):
    item = service.get_inventory("fulfillment", product)
    return item.available, item.reserved


def test_nothing_ships_before_the_payment_is_received(service):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    with pytest.raises(InvalidState) as err:
        service.create_shipment("fulfillment", order.orderId)
    assert err.value.code == "PAYMENT_PENDING"
    service.register_payment("accountant", order.orderId)
    assert service.create_shipment("fulfillment", order.orderId).status == "CREATED"


def test_order_status_shows_every_department(service):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    service.assign_task("operations-manager", "fulfillment", "ReserveStock", order.orderId, "reserve")
    status = service.get_order_status("ceo", order.orderId)
    assert (status.status, status.reservation, status.payment, status.invoice, status.shipment) == \
           ("VALIDATED", "RESERVED", None, None, None)
    assert status.tasks == {"TASK-ReserveStock-O-1": "ASSIGNED"}


def test_cancel_releases_stock_voids_invoice_and_announces_refund(service, publisher):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    service.register_payment("accountant", order.orderId)
    service.create_invoice("accountant", order.orderId)
    assert stock(service) == (7, 3)

    cancelled = service.cancel_order("operations-manager", order.orderId, "supplier delay")
    assert (cancelled.status, cancelled.cancelReason) == ("CANCELLED", "supplier delay")
    assert stock(service) == (10, 0)
    assert service.get_invoice("accountant", "INV-O-1").status == "VOID"
    event = publisher.of_type("OrderCancelled")[0]["payload"]
    assert (event["inventoryReleased"], event["invoiceVoided"], event["refundDue"]) == (True, True, "9000.00")

    service.cancel_order("operations-manager", order.orderId, "supplier delay")
    assert stock(service) == (10, 0)
    assert publisher.types().count("OrderCancelled") == 1


def test_no_work_on_a_cancelled_order(service):
    order = validated(service)
    service.cancel_order("commercial-manager", order.orderId, "customer not eligible")
    for call in (lambda: service.reserve_inventory("fulfillment", order.orderId),
                 lambda: service.register_payment("accountant", order.orderId),
                 lambda: service.create_invoice("accountant", order.orderId),
                 lambda: service.validate_order("commercial-manager", order.orderId)):
        with pytest.raises(OrderCancelled):
            call()


def test_shipped_orders_cannot_be_cancelled(service):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    service.register_payment("accountant", order.orderId)
    service.create_shipment("fulfillment", order.orderId)
    with pytest.raises(InvalidState) as err:
        service.cancel_order("ceo", order.orderId, "too late")
    assert err.value.code == "ALREADY_SHIPPED"


def test_out_of_stock_then_cancel_then_small_refund(service, publisher):
    order = validated(service, "O-DOCKS", product_id="DOCK-USB-C", quantity=3, payment_amount="540.00")
    service.register_payment("accountant", order.orderId)
    with pytest.raises(InventoryUnavailable):
        service.reserve_inventory("fulfillment", order.orderId)
    service.cancel_order("operations-manager", order.orderId, "out of stock: 3 docks requested, 2 available")
    refunded = service.refund_payment("accountant", order.orderId, "order cancelled: out of stock")
    assert refunded.status == "REFUNDED"
    assert publisher.types()[-1] == "PaymentRefunded"
    assert service.refund_payment("accountant", order.orderId, "again").status == "REFUNDED"
    assert publisher.types().count("PaymentRefunded") == 1


def test_large_refunds_need_the_ceo(service):
    order = validated(service)
    service.register_payment("accountant", order.orderId)
    service.cancel_order("operations-manager", order.orderId, "out of stock")
    with pytest.raises(EscalationRequired) as err:
        service.refund_payment("accountant", order.orderId, "cancelled")
    assert "request_ceo_approval" in err.value.message

    decision = service.request_ceo_approval("accounting-manager", DecisionType.REFUND, order.orderId,
                                            Decimal("9000.00"), "Order cancelled, customer paid")
    service.approve_decision("ceo", decision.decisionId, "Customer is owed the money")
    assert service.refund_payment("accountant", order.orderId, "cancelled").status == "REFUNDED"


def test_refunds_only_for_cancelled_paid_orders(service):
    order = validated(service)
    with pytest.raises(Exception):
        service.refund_payment("accountant", order.orderId, "no payment yet")
    service.register_payment("accountant", order.orderId)
    with pytest.raises(InvalidState):
        service.refund_payment("accountant", order.orderId, "not cancelled")
