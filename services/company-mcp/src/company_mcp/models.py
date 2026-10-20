"""Business records. These are what tools return and what is stored in DynamoDB."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .ids import BUSINESS_ID_PATTERN, TASK_TYPE_PATTERN

BusinessId = Annotated[str, StringConstraints(pattern=BUSINESS_ID_PATTERN)]
AgentId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,63}$")]
TaskType = Annotated[str, StringConstraints(pattern=TASK_TYPE_PATTERN)]
Percent = Annotated[Decimal, Field(ge=0, le=100)]

CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENTS)


class Record(BaseModel):
    # Storage attributes (pk, sk, gsi1pk, pendingEvent, ...) are ignored on read.
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    @field_validator("*")
    @classmethod
    def _two_decimals(cls, value):
        # DynamoDB normalises 9000.00 to 9000; amounts and percents always read back as x.xx.
        return money(value) if isinstance(value, Decimal) else value


class OrderStatus(StrEnum):
    PLACED = "PLACED"
    VALIDATED = "VALIDATED"
    CANCELLED = "CANCELLED"


class CustomerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    RELEASED = "RELEASED"


class PaymentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"
    REFUNDED = "REFUNDED"


class ShipmentStatus(StrEnum):
    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class DecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionType(StrEnum):
    SPECIAL_DISCOUNT = "SPECIAL_DISCOUNT"
    REFUND = "REFUND"
    EXCEPTION = "EXCEPTION"


class OrderLineRequest(BaseModel):
    productId: BusinessId
    quantity: int = Field(gt=0, le=1000)


class OrderLine(Record):
    productId: str
    quantity: int
    unitPrice: Decimal
    lineTotal: Decimal


class Order(Record):
    orderId: str
    customerId: str
    lines: list[OrderLine]
    currency: str = "USD"
    subtotal: Decimal
    requestedDiscountPercent: Decimal = Decimal(0)
    discountPercent: Decimal = Decimal(0)
    total: Decimal
    paymentReference: str
    paymentAmount: Decimal
    status: OrderStatus
    validatedBy: str | None = None
    cancelledBy: str | None = None
    cancelReason: str | None = None
    createdAt: str
    updatedAt: str


class Customer(Record):
    customerId: str
    name: str
    email: str
    segment: str
    status: CustomerStatus
    creditLimit: Decimal


class InventoryItem(Record):
    productId: str
    name: str
    unitPrice: Decimal
    unitCost: Decimal
    available: int
    reserved: int


class ReservationLine(Record):
    productId: str
    quantity: int


class Reservation(Record):
    orderId: str
    lines: list[ReservationLine]
    status: ReservationStatus
    reservedBy: str
    reason: str | None = None
    createdAt: str
    updatedAt: str


class InvoiceStatus(StrEnum):
    ISSUED = "ISSUED"
    VOID = "VOID"


class Invoice(Record):
    invoiceId: str
    orderId: str
    customerId: str
    lines: list[OrderLine]
    currency: str
    subtotal: Decimal
    discountPercent: Decimal
    total: Decimal
    issuedBy: str
    status: InvoiceStatus = InvoiceStatus.ISSUED
    artifactKey: str | None = None
    createdAt: str


class Payment(Record):
    orderId: str
    paymentReference: str
    amount: Decimal
    expectedAmount: Decimal
    status: PaymentStatus
    reason: str | None = None
    registeredBy: str
    refundedBy: str | None = None
    refundReason: str | None = None
    createdAt: str


class Shipment(Record):
    shipmentId: str
    orderId: str
    status: ShipmentStatus
    carrier: str
    trackingNumber: str
    createdBy: str
    createdAt: str
    updatedAt: str


class Task(Record):
    taskId: str
    type: str
    assignedBy: str
    assignedTo: str
    status: TaskStatus
    correlationId: str
    goal: str
    attempt: int = 1
    result: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    retryable: bool | None = None
    createdAt: str
    updatedAt: str


class Decision(Record):
    decisionId: str
    decisionType: DecisionType
    requestedBy: str
    correlationId: str
    requestedValue: Decimal
    managerAuthorityLimit: Decimal | None = None
    reason: str
    status: DecisionStatus
    decidedBy: str | None = None
    rationale: str | None = None
    createdAt: str
    updatedAt: str


class OrderProgress(BaseModel):
    """Where an order stands across departments, in one read."""

    orderId: str
    status: OrderStatus
    customerId: str
    total: Decimal
    discountPercent: Decimal
    requestedDiscountPercent: Decimal
    cancelReason: str | None = None
    reservation: str | None = None
    payment: str | None = None
    paymentAmount: Decimal | None = None
    invoice: str | None = None
    shipment: str | None = None
    tasks: dict[str, str] = {}
    decisions: dict[str, str] = {}


class OrderMargin(BaseModel):
    orderId: str
    subtotal: Decimal
    cost: Decimal
    currentDiscountPercent: Decimal
    currentMargin: Decimal
    currentMarginPercent: Decimal
    requestedDiscountPercent: Decimal
    marginAtRequestedDiscount: Decimal
    marginPercentAtRequestedDiscount: Decimal


class Identity(BaseModel):
    agentId: str
    reportsTo: str | None
    directReports: list[str]
    tools: list[str]
