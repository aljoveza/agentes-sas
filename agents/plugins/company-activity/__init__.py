"""company-activity: publishes one Hermes employee's activity to the agent-activity service.

Based on the hooks of Hermes Pixel Office (https://github.com/teknium1/hermes-pixel-office,
MIT, Copyright (c) 2026 Teknium / Nous Research; see LICENSE-hermes-pixel-office). Upstream appends
events to a local file; our employees run in separate containers, so each hook POSTs the event to
$AGENT_ACTIVITY_URL instead, tagged with the employee's identity ($AGENT_ID, $AGENT_ROLE,
$DEPARTMENT, $REPORTS_TO). Nexo shows it live, per order.

From Company MCP calls it also derives `agent_message` events, the arrows of the org chart:
    assign_task                       manager  -> worker    delegate
    complete_task / fail_task         worker   -> manager   report
    request_ceo_approval              manager  -> ceo       escalate
    approve_decision / reject_decision ceo     -> manager   decide

Observers only: every failure is swallowed (fail-open), nothing touches the conversation, and
business correctness never depends on these events.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 0.5
# Hermes names MCP tools mcp__<server>__<tool> (observed in 0.21); tolerate single underscores too.
_PREFIX = re.compile(r"^mcp_+company_+")
_warned = False


def _identity() -> Dict[str, Any]:
    return {
        "agentId": os.environ.get("AGENT_ID", ""),
        "agentName": os.environ.get("AGENT_ROLE", ""),
        "department": os.environ.get("DEPARTMENT", ""),
        "reportsTo": os.environ.get("REPORTS_TO", ""),
    }


def _publish(event: Dict[str, Any]) -> None:
    global _warned
    url = os.environ.get("AGENT_ACTIVITY_URL", "").rstrip("/")
    if not url:
        return
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    try:
        request = urllib.request.Request(
            f"{url}/events",
            data=json.dumps(event, default=str).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=_TIMEOUT).close()
    except Exception as exc:  # observers must never break the agent loop
        if not _warned:
            _warned = True
            logger.warning("company-activity: service unreachable (%s: %s)", type(exc).__name__, exc)


def _short(text: Any, limit: int = 80) -> str:
    value = " ".join(str(text or "").split())
    return value[: limit - 1] + "…" if len(value) > limit else value


def _result_field(result: Any, name: str) -> Optional[str]:
    """Find a field in a tool result, which may be a dict, JSON text, or text wrapping JSON."""
    if isinstance(result, dict):
        if name in result and isinstance(result[name], str):
            return result[name]
        for value in result.values():
            found = _result_field(value, name)
            if found:
                return found
        return None
    if isinstance(result, list):
        for value in result:
            found = _result_field(value, name)
            if found:
                return found
        return None
    if isinstance(result, str):
        try:
            return _result_field(json.loads(result), name)
        except ValueError:
            match = re.search(rf'"{name}"\s*:\s*"([^"]+)"', result)
            return match.group(1) if match else None
    return None


def _preview(tool: str, args: Dict[str, Any]) -> str:
    for key in ("order_id", "task_id", "decision_id", "correlation_id", "product_id", "customer_id"):
        if args.get(key):
            return str(args[key])
    return ""


def _pre_tool_call(**kw: Any) -> None:
    tool = str(kw.get("tool_name") or "")
    args = kw.get("args") if isinstance(kw.get("args"), dict) else {}
    _publish({
        **_identity(),
        "eventType": "tool_started",
        "sessionId": kw.get("session_id"),
        "taskId": args.get("task_id"),
        "metadata": {"tool": tool, "preview": _preview(tool, args)},
    })
    return None  # observer — never blocks


def _post_tool_call(**kw: Any) -> None:
    tool = str(kw.get("tool_name") or "")
    args = kw.get("args") if isinstance(kw.get("args"), dict) else {}
    result = kw.get("result")
    ok = (kw.get("status") or "ok") == "ok" and not _result_field(result, "errorCode")
    me = os.environ.get("AGENT_ID", "")
    _publish({
        **_identity(),
        "eventType": "tool_finished",
        "sessionId": kw.get("session_id"),
        "taskId": args.get("task_id"),
        "metadata": {
            "tool": tool,
            "status": "ok" if ok else "error",
            "error": None if ok else (_result_field(result, "errorCode") or kw.get("error_message")),
            "durationMs": kw.get("duration_ms"),
        },
    })
    if not ok or not _PREFIX.match(tool):
        return
    name = _PREFIX.sub("", tool)
    message: Optional[Dict[str, Any]] = None
    correlation = args.get("correlation_id") or args.get("order_id") or _result_field(result, "correlationId")
    if name == "assign_task":
        message = {"to": args.get("assigned_to"), "kind": "delegate",
                   "message": f"{args.get('task_type', 'task')} {args.get('correlation_id', '')}".strip()}
    elif name in ("complete_task", "fail_task"):
        verdict = "done" if name == "complete_task" else "failed"
        message = {"to": _result_field(result, "assignedBy"), "kind": "report",
                   "message": f"{verdict}: {_short(args.get('result') or args.get('error_code'), 50)}"}
    elif name == "request_ceo_approval":
        message = {"to": "ceo", "kind": "escalate",
                   "message": f"{args.get('decision_type', 'decision')} {args.get('requested_value', '')} "
                              f"{args.get('correlation_id', '')}".strip()}
    elif name in ("approve_decision", "reject_decision"):
        message = {"to": _result_field(result, "requestedBy"), "kind": "decide",
                   "message": "approved" if name == "approve_decision" else "rejected"}
    if message and message.get("to"):
        _publish({"eventType": "agent_message", "from": me, "taskId": args.get("task_id"),
                  "correlationId": correlation, **message})


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    logger.info("company-activity registered for %s", os.environ.get("AGENT_ID", "?"))
