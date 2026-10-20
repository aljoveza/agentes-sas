"""Escalation demo: the customer asks for 25%, the Commercial Manager may grant 10%."""

from decimal import Decimal

import pytest

from company_mcp.errors import EscalationRequired, Forbidden, InvalidState, WithinAuthority
from company_mcp.models import DecisionType
from conftest import place

ORDER = "DEVOPS-2026-002"
DECISION = "DECISION-SPECIAL-DISCOUNT-DEVOPS-2026-002"


@pytest.fixture
def order(service):
    return place(service, ORDER, payment_amount="6750.00", requested_discount="25")


def escalate(service, value="25"):
    return service.request_ceo_approval(
        "commercial-manager", DecisionType.SPECIAL_DISCOUNT, ORDER, Decimal(value), "Strategic customer"
    )


def test_discount_above_authority_requires_escalation(service, order):
    with pytest.raises(EscalationRequired) as err:
        service.validate_order("commercial-manager", ORDER, Decimal(25))
    assert "request_ceo_approval" in err.value.message


def test_discount_within_authority_needs_no_ceo(service, order):
    with pytest.raises(WithinAuthority):
        escalate(service, "10")
    assert service.validate_order("commercial-manager", ORDER, Decimal(10)).total == Decimal("8100.00")


def test_approved_escalation(service, publisher, order):
    decision = escalate(service)
    assert decision.decisionId == DECISION
    assert decision.managerAuthorityLimit == Decimal(10)
    requested = publisher.of_type("DecisionRequested")[0]
    assert requested["payload"]["requestedBy"] == "commercial-manager"

    with pytest.raises(EscalationRequired, match="pending"):
        service.validate_order("commercial-manager", ORDER, Decimal(25))

    assert [d.decisionId for d in service.list_pending_decisions("ceo")] == [DECISION]
    margin = service.get_order_margin("ceo", ORDER)
    assert margin.marginAtRequestedDiscount == Decimal("150.00")

    service.approve_decision("ceo", DECISION, "Margin stays positive")
    approved = publisher.of_type("DecisionApproved")[0]
    assert approved["payload"]["requestedBy"] == "commercial-manager"  # routes back to the requester
    assert service.list_pending_decisions("ceo") == []

    validated = service.validate_order("commercial-manager", ORDER, Decimal(25))
    assert validated.total == Decimal("6750.00")
    assert service.register_payment("accountant", ORDER).status == "RECEIVED"


def test_rejected_escalation(service, order):
    escalate(service)
    service.reject_decision("ceo", DECISION, "Margin too thin")
    with pytest.raises(EscalationRequired, match="rejected"):
        service.validate_order("commercial-manager", ORDER, Decimal(25))
    assert service.validate_order("commercial-manager", ORDER, Decimal(10)).status == "VALIDATED"
    # The customer paid for 25% off, so accounting sees the mismatch.
    assert service.register_payment("accountant", ORDER).status == "REJECTED"


def test_decisions_are_final_and_idempotent(service, publisher, order):
    escalate(service)
    escalate(service)
    service.approve_decision("ceo", DECISION, "ok")
    service.approve_decision("ceo", DECISION, "ok")
    assert publisher.types().count("DecisionRequested") == 1
    assert publisher.types().count("DecisionApproved") == 1
    with pytest.raises(InvalidState):
        service.reject_decision("ceo", DECISION, "changed my mind")


def test_only_the_ceo_decides(service, order):
    escalate(service)
    with pytest.raises(Forbidden):
        service.approve_decision("commercial-manager", DECISION, "self-approval")


def test_only_department_managers_escalate(service, order):
    with pytest.raises(Forbidden):
        service.request_ceo_approval("sales-rep", DecisionType.SPECIAL_DISCOUNT, ORDER, Decimal(25), "please")
