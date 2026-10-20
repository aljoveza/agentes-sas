# Agent activity

Live activity of every employee, company-wide and per order. Observability only: in memory,
rebuilt by the agents' heartbeats after a restart, and never required for business correctness.
Nexo (`services/nexo`) reads it to show who is working on an order right now.

```
agent runtime   ── employee_ready / heartbeat / turn_started / turn_finished / turn_failed ─┐
Hermes plugin   ── tool_started / tool_finished / agent_message (delegate, report,          ├─► POST /events
(company-activity)  escalate, decide)                                                       ┘
```

An employee handles one business event at a time, so every tool call and message between its
`turn_started` and `turn_finished` belongs to that turn's order (`correlationId`).

| Endpoint | Returns |
|---|---|
| `GET /orders/<id>/activity` | each employee's part in the order (`onThisOrder`, `tool`, `waitingFor`, `busyElsewhere`, `lastSummary`) and its timeline |
| `GET /state` | every employee's live status and a short feed |
| `GET /stats` | events received by type (are publishers wired?) |
| `GET /health` | liveness |

`ACTIVITY_LOG_EVENTS=1` logs every received event. Stdlib Python, no dependencies.
Tests: `uv run pytest`.
