"""Deterministic identifiers. The same business request always maps to the same record,
which is what makes retries and duplicate events harmless."""

import hashlib
import re

from .errors import InvalidInput

BUSINESS_ID_PATTERN = r"^[A-Z0-9][A-Z0-9-]{1,63}$"
TASK_TYPE_PATTERN = r"^[A-Z][A-Za-z0-9]{2,40}$"

_BUSINESS_ID = re.compile(BUSINESS_ID_PATTERN)


def invoice_id(order_id: str) -> str:
    return f"INV-{order_id}"


def shipment_id(order_id: str) -> str:
    return f"SHP-{order_id}"


def tracking_number(order_id: str) -> str:
    return "TRK-" + hashlib.sha256(order_id.encode()).hexdigest()[:10].upper()


def task_id(task_type: str, correlation_id: str) -> str:
    return f"TASK-{task_type}-{correlation_id}"


def decision_id(decision_type: str, correlation_id: str) -> str:
    return f"DECISION-{decision_type.replace('_', '-')}-{correlation_id}"


def order_id_from(prefixed_id: str, prefix: str) -> str:
    """INV-ORDER-1 -> ORDER-1 for prefix 'INV-'."""
    if not prefixed_id.startswith(prefix) or not _BUSINESS_ID.match(prefixed_id[len(prefix) :]):
        raise InvalidInput(f"'{prefixed_id}' is not a valid id; expected {prefix}<order id>.")
    return prefixed_id[len(prefix) :]
