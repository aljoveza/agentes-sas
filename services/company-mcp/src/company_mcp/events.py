"""Business event envelope and publishers."""

import json
import logging
import uuid
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from .errors import DependencyFailure

log = logging.getLogger(__name__)


class Event(BaseModel):
    eventId: str
    eventType: str
    eventVersion: str = "1"
    timestamp: str
    correlationId: str
    causationId: str | None = None
    source: str
    payload: dict[str, Any]


def new_event(
    event_type: str,
    *,
    correlation_id: str,
    source: str,
    payload: dict[str, Any],
    timestamp: str,
    event_id: str | None = None,
    causation_id: str | None = None,
) -> dict[str, Any]:
    """Build an envelope as JSON-safe data. `source` is the agent that caused the event."""
    event = Event(
        eventId=event_id or str(uuid.uuid4()),
        eventType=event_type,
        timestamp=timestamp,
        correlationId=correlation_id,
        causationId=causation_id,
        source=source,
        payload=payload,
    )
    return json.loads(event.model_dump_json())


def deterministic_event_id(*parts: str) -> str:
    """For events that have no record to live on, so re-emitting yields the same eventId."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "company-mcp:" + ":".join(parts)))


class EventPublisher(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


class EventBridgePublisher:
    """Publishes to the business bus as Source=<source>, DetailType=eventType, Detail=envelope."""

    def __init__(self, client: Any, bus_name: str, source: str) -> None:
        self._client = client
        self._bus_name = bus_name
        self._source = source

    def publish(self, event: dict[str, Any]) -> None:
        try:
            response = self._client.put_events(
                Entries=[
                    {
                        "EventBusName": self._bus_name,
                        "Source": self._source,
                        "DetailType": event["eventType"],
                        "Detail": json.dumps(event),
                    }
                ]
            )
        except (BotoCoreError, ClientError) as exc:
            raise DependencyFailure(f"EventBridge unavailable: {exc}") from exc
        if response.get("FailedEntryCount"):
            entry = response["Entries"][0]
            raise DependencyFailure(f"EventBridge rejected {event['eventType']}: {entry.get('ErrorMessage')}")
        log.info(
            "event published",
            extra={"fields": {"eventId": event["eventId"], "eventType": event["eventType"], "correlationId": event["correlationId"]}},
        )


class LogPublisher:
    """Local development without EventBridge: events are written to the log only."""

    def publish(self, event: dict[str, Any]) -> None:
        log.info("event (local, not published)", extra={"fields": {"event": event}})
