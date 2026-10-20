import pytest

from company_mcp.errors import InvalidState, InventoryUnavailable
from conftest import place


def validated(service, order_id="DEVOPS-2026-001", **kwargs):
    order = place(service, order_id, **kwargs)
    return service.validate_order("commercial-manager", order.orderId)


def test_insufficient_inventory(service, publisher):
    order = validated(service, "ORDER-DOCKS", product_id="DOCK-USB-C", quantity=3, payment_amount="540.00")
    with pytest.raises(InventoryUnavailable) as err:
        service.reserve_inventory("fulfillment", order.orderId)
    assert err.value.details["shortages"] == [{"productId": "DOCK-USB-C", "requested": 3, "available": 2}]

    stock = service.get_inventory("fulfillment", "DOCK-USB-C")
    assert (stock.available, stock.reserved) == (2, 0)
    assert publisher.types()[-1] == "InventoryUnavailable"

    # Re-emitting the same shortage keeps the same eventId, so consumers can deduplicate.
    with pytest.raises(InventoryUnavailable):
        service.reserve_inventory("fulfillment", order.orderId)
    ids = {event["eventId"] for event in publisher.of_type("InventoryUnavailable")}
    assert len(ids) == 1


def test_cannot_reserve_unvalidated_order(service):
    order = place(service)
    with pytest.raises(InvalidState):
        service.reserve_inventory("fulfillment", order.orderId)


def test_release_returns_stock(service):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    released = service.release_inventory("fulfillment", order.orderId, "customer cancelled")
    assert released.status == "RELEASED"
    stock = service.get_inventory("fulfillment", "LAPTOP-PRO")
    assert (stock.available, stock.reserved) == (10, 0)

    service.release_inventory("fulfillment", order.orderId, "customer cancelled")
    stock = service.get_inventory("fulfillment", "LAPTOP-PRO")
    assert (stock.available, stock.reserved) == (10, 0)


def test_cannot_release_after_shipment(service):
    order = validated(service)
    service.reserve_inventory("fulfillment", order.orderId)
    service.register_payment("accountant", order.orderId)
    service.create_shipment("fulfillment", order.orderId)
    with pytest.raises(InvalidState):
        service.release_inventory("fulfillment", order.orderId, "too late")


def test_shipment_requires_reservation(service):
    order = validated(service)
    with pytest.raises(InvalidState):
        service.create_shipment("fulfillment", order.orderId)
