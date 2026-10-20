"""What Hermes is asked in each turn."""

import json
from typing import Any


def event_turn(envelope: dict[str, Any]) -> str:
    return f"""A business event arrived in your inbox.

Event type: {envelope.get('eventType')}
Correlation id: {envelope.get('correlationId')}
Event id: {envelope.get('eventId')}
Caused by: {envelope.get('source')}

Payload:
```json
{json.dumps(envelope.get('payload', {}), indent=2, sort_keys=True)}
```

Apply your procedures to this event using your company tools, then reply with one line summarising what you did."""


def resume_turn(agent_id: str) -> str:
    if agent_id == "ceo":
        check = "Call list_pending_decisions and decide every pending escalation following your procedures."
    else:
        check = (
            "Call list_my_tasks (by default it returns your outstanding ASSIGNED and IN_PROGRESS tasks). Finish "
            "each of those tasks following your procedures (work may already be partly done; company tools are "
            "idempotent)."
        )
    return f"""You just started (or were restarted). Before handling new events, recover outstanding work.

{check}

If there is nothing outstanding, do nothing. Reply with one line summarising what you did."""
