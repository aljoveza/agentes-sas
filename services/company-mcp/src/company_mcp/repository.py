"""Single-table access. Key layout (mirrors infrastructure/modules/dynamodb):

  ORDER#<id>        META | RESERVATION | INVOICE | PAYMENT | SHIPMENT
  CUSTOMER#<id>     PROFILE
  PRODUCT#<id>      INVENTORY
  TASK#<id>         META      gsi1: ASSIGNEE#<agent> / <status>#<createdAt>#<id>
  DECISION#<id>     META      gsi1: DECISIONS        / <status>#<createdAt>#<id>
  (orders)                    gsi1: ORDERS           / <status>#<createdAt>#<id>

Every mutation that announces something stores the event on the record as
`pendingEvent` in the same write. The service publishes it and then clears it,
so a crash between the write and the publish is repaired by the next retry.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from .errors import DependencyFailure

META = "META"


def order_pk(order_id: str) -> str:
    return f"ORDER#{order_id}"


def customer_key(customer_id: str) -> tuple[str, str]:
    return f"CUSTOMER#{customer_id}", "PROFILE"


def product_key(product_id: str) -> tuple[str, str]:
    return f"PRODUCT#{product_id}", "INVENTORY"


def task_key(task_id: str) -> tuple[str, str]:
    return f"TASK#{task_id}", META


def decision_key(decision_id: str) -> tuple[str, str]:
    return f"DECISION#{decision_id}", META


def index_sort_key(status: str, created_at: str, record_id: str) -> str:
    return f"{status}#{created_at}#{record_id}"


class ConditionFailed(Exception):
    """A conditional write lost: the record exists or is not in the expected state."""


class TransactionCancelled(Exception):
    """A transaction was rejected; callers re-read state to find out why."""


def _dependency_failure(exc: Exception) -> DependencyFailure:
    return DependencyFailure(f"DynamoDB unavailable: {exc}")


class Repository:
    def __init__(self, table: Any) -> None:
        self._table = table
        # The resource's client serializes plain Python values itself, transactions included.
        self._client = table.meta.client

    @property
    def table_name(self) -> str:
        return self._table.name

    # Reads -----------------------------------------------------------------

    def get(self, pk: str, sk: str = META) -> dict[str, Any] | None:
        try:
            return self._table.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")
        except (BotoCoreError, ClientError) as exc:
            raise _dependency_failure(exc) from exc

    def query_partition(self, pk: str) -> list[dict[str, Any]]:
        """Every item under one partition, e.g. an order and its child records."""
        try:
            return self._table.query(KeyConditionExpression=Key("pk").eq(pk), ConsistentRead=True).get("Items", [])
        except (BotoCoreError, ClientError) as exc:
            raise _dependency_failure(exc) from exc

    def get_many(self, keys: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Batch read of known keys (no Scan: the MCP role is not allowed to scan the table)."""
        items: list[dict[str, Any]] = []
        pending = [{"pk": pk, "sk": sk} for pk, sk in dict.fromkeys(keys)]
        try:
            while pending:
                batch, pending = pending[:100], pending[100:]
                request = {self.table_name: {"Keys": batch, "ConsistentRead": True}}
                while request:
                    response = self._client.batch_get_item(RequestItems=request)
                    items.extend(response.get("Responses", {}).get(self.table_name, []))
                    request = response.get("UnprocessedKeys") or None
        except (BotoCoreError, ClientError) as exc:
            raise _dependency_failure(exc) from exc
        return items

    def query_index(self, partition: str, prefix: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        condition = Key("gsi1pk").eq(partition)
        if prefix:
            condition = condition & Key("gsi1sk").begins_with(prefix)
        try:
            response = self._table.query(
                IndexName="gsi1", KeyConditionExpression=condition, Limit=limit, ScanIndexForward=False
            )
        except (BotoCoreError, ClientError) as exc:
            raise _dependency_failure(exc) from exc
        return response.get("Items", [])

    # Writes ----------------------------------------------------------------

    def put(self, item: Mapping[str, Any]) -> None:
        """Unconditional write; used only for seeding reference data."""
        try:
            self._table.put_item(Item=dict(item))
        except (BotoCoreError, ClientError) as exc:
            raise _dependency_failure(exc) from exc

    def put_new(self, item: Mapping[str, Any]) -> bool:
        """Create the item if its key is free. Returns False if it already exists."""
        try:
            self._table.put_item(Item=dict(item), ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise _dependency_failure(exc) from exc
        except BotoCoreError as exc:
            raise _dependency_failure(exc) from exc

    def update(
        self,
        pk: str,
        sk: str,
        *,
        set_: Mapping[str, Any],
        remove: Iterable[str] = (),
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Conditional update returning the new item. Raises ConditionFailed when the condition does not hold."""
        expression, expr_names, expr_values = self._update_expression(set_, remove)
        expr_names.update(names or {})
        expr_values.update(values or {})
        kwargs: dict[str, Any] = {
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": expression,
            "ExpressionAttributeNames": expr_names,
            "ReturnValues": "ALL_NEW",
        }
        if expr_values:
            kwargs["ExpressionAttributeValues"] = expr_values
        if condition:
            kwargs["ConditionExpression"] = condition
        try:
            return self._table.update_item(**kwargs)["Attributes"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConditionFailed(f"{pk}/{sk}") from exc
            raise _dependency_failure(exc) from exc
        except BotoCoreError as exc:
            raise _dependency_failure(exc) from exc

    def clear_pending_event(self, pk: str, sk: str, pending: str) -> None:
        """Mark the record's event as published, unless it was replaced meanwhile."""
        try:
            self.update(
                pk,
                sk,
                set_={},
                remove=["pendingEvent"],
                condition="#pendingEvent = :pending",
                values={":pending": pending},
            )
        except ConditionFailed:
            pass

    # Transactions ------------------------------------------------------------

    def tx_put_new(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": dict(item),
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }

    def tx_update(
        self,
        pk: str,
        sk: str,
        *,
        set_: Mapping[str, Any] | None = None,
        add: Mapping[str, int] | None = None,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update inside a transaction. `add` applies numeric deltas (SET #a = #a + :d)."""
        expression, expr_names, expr_values = self._update_expression(set_ or {}, (), add or {})
        expr_names.update(names or {})
        expr_values.update(values or {})
        update: dict[str, Any] = {
            "TableName": self.table_name,
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": expression,
            "ExpressionAttributeNames": expr_names,
        }
        if expr_values:
            update["ExpressionAttributeValues"] = expr_values
        if condition:
            update["ConditionExpression"] = condition
        return {"Update": update}

    def transact(self, operations: list[dict[str, Any]]) -> None:
        try:
            self._client.transact_write_items(TransactItems=operations)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "TransactionCanceledException":
                raise TransactionCancelled(str(exc)) from exc
            raise _dependency_failure(exc) from exc
        except BotoCoreError as exc:
            raise _dependency_failure(exc) from exc

    # Helpers -----------------------------------------------------------------

    @staticmethod
    def _update_expression(
        set_: Mapping[str, Any], remove: Iterable[str], add: Mapping[str, int] | None = None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        # Every attribute is aliased: names like "status" are DynamoDB reserved words.
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        assignments: list[str] = []
        for attr, value in set_.items():
            names[f"#{attr}"] = attr
            values[f":set_{attr}"] = value
            assignments.append(f"#{attr} = :set_{attr}")
        for attr, delta in (add or {}).items():
            names[f"#{attr}"] = attr
            values[f":add_{attr}"] = delta
            assignments.append(f"#{attr} = #{attr} + :add_{attr}")
        removals = list(remove)
        for attr in removals:
            names[f"#{attr}"] = attr
        parts = []
        if assignments:
            parts.append("SET " + ", ".join(assignments))
        if removals:
            parts.append("REMOVE " + ", ".join(f"#{attr}" for attr in removals))
        return " ".join(parts), names, values
