"""Duplicate delivery and retries must never duplicate business actions."""

import pytest

from company_mcp.errors import DependencyFailure, IdempotencyConflict
from conftest import place


def test_duplicate_order_event(service, publisher):
    first = place(service)
    second = place(service)
    assert first == second
    assert publisher.types() == ["OrderPlaced"]


def test_same_order_id_with_different_content_is_a_conflict(service):
    place(service)
    with pytest.raises(IdempotencyConflict):
        place(service, quantity=4)


def test_every_mutation_is_idempotent(service, publisher):
    order = place(service)
    for _ in range(2):
        service.validate_order("commercial-manager", order.orderId)
        service.reserve_inventory("fulfillment", order.orderId)
        service.register_payment("accountant", order.orderId)
        service.create_shipment("fulfillment", order.orderId)
        service.create_invoice("accountant", order.orderId)

    stock = service.get_inventory("fulfillment", "LAPTOP-PRO")
    assert (stock.available, stock.reserved) == (7, 3)
    assert publisher.types() == [
        "OrderPlaced",
        "OrderValidated",
        "InventoryReserved",
        "PaymentReceived",
        "ShipmentCreated",
        "InvoiceCreated",
    ]


def test_event_survives_an_eventbridge_outage(service, publisher, repo):
    """The invoice is written with its event; if publishing fails, the retry publishes it exactly once."""
    order = place(service)
    service.validate_order("commercial-manager", order.orderId)

    publisher.failures_left = 1
    with pytest.raises(DependencyFailure):
        service.create_invoice("accountant", order.orderId)
    stored = repo.get(f"ORDER#{order.orderId}", "INVOICE")
    assert stored is not None and "pendingEvent" in stored

    invoice = service.create_invoice("accountant", order.orderId)
    assert invoice.invoiceId == "INV-DEVOPS-2026-001"
    assert publisher.types().count("InvoiceCreated") == 1
    assert "pendingEvent" not in repo.get(f"ORDER#{order.orderId}", "INVOICE")

    service.create_invoice("accountant", order.orderId)
    assert publisher.types().count("InvoiceCreated") == 1


def test_pending_event_is_flushed_by_any_later_call(service, publisher):
    """A stuck event is published by the next mutation that touches the record."""
    order = place(service)
    publisher.failures_left = 1
    with pytest.raises(DependencyFailure):
        service.validate_order("commercial-manager", order.orderId)
    service.validate_order("commercial-manager", order.orderId)
    assert publisher.types() == ["OrderPlaced", "OrderValidated"]
