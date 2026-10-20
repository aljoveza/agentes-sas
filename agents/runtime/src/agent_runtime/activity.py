"""Turn-level events for the agent-activity service (shown live in Nexo). Fail-open, never required."""

import json
import logging
import urllib.request
from datetime import UTC, datetime
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


class ActivityPublisher:
    def __init__(self, settings: Settings) -> None:
        self._url = (settings.activity_url or "").rstrip("/")
        self._identity = {
            "agentId": settings.agent_id,
            "agentName": settings.agent_role,
            "department": settings.department,
            "reportsTo": settings.reports_to,
        }
        self._warned = False

    def publish(self, event_type: str, **metadata: Any) -> None:
        if not self._url:
            return
        event = {
            **self._identity,
            "eventType": event_type,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "metadata": metadata,
        }
        try:
            request = urllib.request.Request(
                f"{self._url}/events", data=json.dumps(event, default=str).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(request, timeout=1).close()
        except Exception as exc:  # observability must never break the employee
            if not self._warned:
                self._warned = True
                log.warning("agent-activity unreachable", extra={"fields": {"error": str(exc)}})
