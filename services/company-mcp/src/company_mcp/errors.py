"""Business errors. Every error an agent sees has a stable code and says whether retrying can help."""

import json
from typing import Any


class BusinessError(Exception):
    code = "BUSINESS_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"errorCode": self.code, "message": self.message, "retryable": self.retryable}
        if self.details:
            body["details"] = self.details
        return body

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class InvalidInput(BusinessError):
    code = "INVALID_INPUT"


class NotFound(BusinessError):
    code = "NOT_FOUND"


class Forbidden(BusinessError):
    code = "FORBIDDEN"


class InvalidState(BusinessError):
    code = "INVALID_STATE"


class EscalationRequired(BusinessError):
    code = "ESCALATION_REQUIRED"


class WithinAuthority(BusinessError):
    code = "WITHIN_AUTHORITY"


class InventoryUnavailable(BusinessError):
    code = "INVENTORY_UNAVAILABLE"


class OrderCancelled(BusinessError):
    code = "ORDER_CANCELLED"


class IdempotencyConflict(BusinessError):
    code = "IDEMPOTENCY_CONFLICT"


class DependencyFailure(BusinessError):
    """Infrastructure hiccup (DynamoDB, EventBridge, S3). Safe to retry: every mutation is idempotent."""

    code = "DEPENDENCY_FAILURE"
    retryable = True
