"""Folds observability events from every agent into live state, company-wide and per order.

Publishers (one pair per employee container):
  * agent runtime:   employee_ready, heartbeat, turn_started, turn_finished, turn_failed
  * Hermes plugin:   tool_started, tool_finished, agent_message (delegate, report, escalate, decide)

An employee handles one business event at a time, so every tool call and message between its
turn_started and turn_finished belongs to that turn's order (correlationId).

Visualisation state only: kept in memory, rebuilt by heartbeats after a restart, and nothing in the
business workflow depends on it.
"""

import re
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import UTC, datetime
from typing import Any

FEED_SIZE = 40
ORDER_TIMELINE_SIZE = 400
ORDERS_KEPT = 500
STALE_TURN_SECONDS = 300
NOT_AN_ORDER = {"", "Recovery", "outstanding work"}
TASK_ID = re.compile(r"^TASK-[A-Za-z0-9]+-(?P<order>.+)$")
DECISION_ID = re.compile(r"^DECISION-(?:SPECIAL-DISCOUNT|REFUND|EXCEPTION)-(?P<order>.+)$")


def tool_label(tool: str) -> str:
    # Hermes names MCP tools mcp__<server>__<tool>.
    return re.sub(r"^mcp_+company_+", "", tool or "")


def short(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value[: limit - 1] + "…" if len(value) > limit else value


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")


def order_from_reference(reference: Any) -> str:
    """ORDER-1, TASK-FulfillOrder-ORDER-1 and DECISION-SPECIAL-DISCOUNT-ORDER-1 all mean ORDER-1."""
    value = str(reference or "")
    for pattern in (TASK_ID, DECISION_ID):
        match = pattern.match(value)
        if match:
            return match.group("order")
    return value


class Activity:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._agents: dict[str, dict[str, Any]] = {}
        self._feed: deque[dict[str, Any]] = deque(maxlen=FEED_SIZE)
        self._orders: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._summaries: dict[tuple[str, str], str] = {}
        self._received: Counter[str] = Counter()

    # ------------------------------------------------------------------ ingest

    def ingest(self, event: dict[str, Any]) -> None:
        kind = str(event.get("eventType"))
        now = self._clock()
        with self._lock:
            self._received[kind] += 1
            agent_id = event.get("agentId") if kind != "agent_message" else event.get("from")
            if not agent_id or not isinstance(agent_id, str):
                return
            agent = self._agent(agent_id, event, now)
            handler = getattr(self, f"_on_{kind}", None)
            if handler:
                handler(agent, event, now)
            agent["updatedAt"] = now

    def _agent(self, agent_id: str, event: dict[str, Any], now: float) -> dict[str, Any]:
        agent = self._agents.setdefault(agent_id, {
            "id": agent_id, "name": agent_id, "department": "", "reportsTo": "",
            "status": "idle", "tool": "", "order": "", "event": "", "detail": "", "awaiting": "",
            "updatedAt": now,
        })
        for source, target in (("agentName", "name"), ("department", "department"), ("reportsTo", "reportsTo")):
            if event.get(source):
                agent[target] = str(event[source])
        return agent

    def _record(self, order: str, entry: dict[str, Any]) -> None:
        if order in NOT_AN_ORDER:
            return
        timeline = self._orders.get(order)
        if timeline is None:
            timeline = self._orders[order] = deque(maxlen=ORDER_TIMELINE_SIZE)
            while len(self._orders) > ORDERS_KEPT:
                self._orders.popitem(last=False)
        else:
            self._orders.move_to_end(order)
        timeline.append(entry)

    def _entry(self, now: float, agent: dict[str, Any], kind: str, **fields: Any) -> dict[str, Any]:
        return {"at": iso(now), "agentId": agent["id"], "kind": kind, **fields}

    def _on_employee_ready(self, agent, event, now) -> None:
        agent.update(status="idle", tool="", order="", event="", awaiting="", detail="ready")
        self._feed.append({"at": iso(now), "agentId": agent["id"], "text": "started"})

    def _on_heartbeat(self, agent, event, now) -> None:
        pass  # identity refreshed by _agent(); a desk unknown after a restart appears idle

    def _on_turn_started(self, agent, event, now) -> None:
        meta = event.get("metadata") or {}
        order = str(meta.get("correlationId") or "")
        business_event = str(meta.get("eventType") or "")
        agent.update(status="thinking", tool="", awaiting="", detail="",
                     order="" if order in NOT_AN_ORDER else order, event=business_event)
        self._record(order, self._entry(now, agent, "turn_started", event=business_event))
        if agent["order"]:
            self._feed.append({"at": iso(now), "agentId": agent["id"], "text": f"picks up {business_event} {order}"})

    def _order_of(self, agent: dict[str, Any], *references: Any) -> str:
        for reference in references:
            if reference:
                return order_from_reference(reference)
        return agent["order"]

    def _on_tool_started(self, agent, event, now) -> None:
        meta = event.get("metadata") or {}
        tool = tool_label(str(meta.get("tool") or ""))
        agent.update(status="working", tool=tool)
        order = agent["order"] or self._order_of(agent, meta.get("preview"), event.get("taskId"))
        self._record(order, self._entry(now, agent, "tool_started", tool=tool))

    def _on_tool_finished(self, agent, event, now) -> None:
        meta = event.get("metadata") or {}
        tool = tool_label(str(meta.get("tool") or ""))
        ok = meta.get("status") != "error"
        agent.update(status="thinking", tool="")
        if not ok:
            agent["detail"] = short(meta.get("error"), 80)
        order = agent["order"] or self._order_of(agent, event.get("taskId"))
        self._record(order, self._entry(now, agent, "tool_finished", tool=tool, ok=ok,
                                        error=None if ok else short(meta.get("error"), 200)))

    def _on_agent_message(self, agent, event, now) -> None:
        kind = str(event.get("kind") or "delegate")
        receiver = str(event.get("to") or "")
        if kind == "escalate":
            agent["awaiting"] = receiver
        order = self._order_of(agent, event.get("correlationId"), event.get("taskId"))
        text = short(event.get("message"), 200)
        self._record(order, self._entry(now, agent, "message", messageKind=kind, to=receiver, text=text))
        self._feed.append({"at": iso(now), "agentId": agent["id"], "text": f"{kind} → {receiver}: {text}"})

    def _on_turn_finished(self, agent, event, now) -> None:
        summary = short((event.get("metadata") or {}).get("summary"), 400)
        order = agent["order"]
        if order:
            self._summaries[(order, agent["id"])] = summary
            self._record(order, self._entry(now, agent, "turn_finished", summary=summary))
        agent.update(status="waiting" if agent["awaiting"] else "idle", tool="", order="", detail=summary)

    def _on_turn_failed(self, agent, event, now) -> None:
        meta = event.get("metadata") or {}
        summary = short(meta.get("summary"), 400)
        if agent["order"]:
            self._record(agent["order"], self._entry(now, agent, "turn_failed", summary=summary,
                                                     attempt=meta.get("attempt")))
        agent.update(status="error", tool="", order="", detail=summary)

    # ------------------------------------------------------------------ read

    def _live(self, agent: dict[str, Any], now: float) -> dict[str, Any]:
        view = dict(agent)
        if view["status"] in ("working", "thinking") and now - view["updatedAt"] > STALE_TURN_SECONDS:
            view.update(status="idle", tool="", order="")  # e.g. the container died mid-turn
        view["updatedAt"] = iso(view["updatedAt"])
        return view

    def state(self) -> dict[str, Any]:
        """Company-wide: every employee's live status and a short feed."""
        now = self._clock()
        with self._lock:
            return {"agents": [self._live(a, now) for a in self._agents.values()],
                    "feed": list(self._feed), "at": iso(now)}

    def order(self, order_id: str) -> dict[str, Any]:
        """One order: each employee's part in it and its activity timeline."""
        now = self._clock()
        with self._lock:
            timeline = list(self._orders.get(order_id, ()))
            involved = {entry["agentId"] for entry in timeline}
            agents = []
            for agent in self._agents.values():
                live = self._live(agent, now)
                agents.append({
                    **{k: live[k] for k in ("id", "name", "department", "reportsTo", "status", "updatedAt")},
                    "involved": agent["id"] in involved,
                    "onThisOrder": live["order"] == order_id,
                    "tool": live["tool"] if live["order"] == order_id else "",
                    "busyElsewhere": bool(live["order"]) and live["order"] != order_id,
                    "waitingFor": live["awaiting"] if live["status"] == "waiting" and agent["id"] in involved else "",
                    "lastSummary": self._summaries.get((order_id, agent["id"]), ""),
                })
        return {"orderId": order_id, "agents": agents, "timeline": timeline, "at": iso(now)}

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._received)
