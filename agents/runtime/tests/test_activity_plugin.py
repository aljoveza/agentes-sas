"""The Hermes plugin turns Company MCP calls into activity events (posted to a stand-in service)."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "company-activity" / "__init__.py"


@pytest.fixture
def collector(monkeypatch):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            events.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(202)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("AGENT_ACTIVITY_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("AGENT_ID", "commercial-manager")
    monkeypatch.setenv("AGENT_ROLE", "Commercial Manager")
    monkeypatch.setenv("DEPARTMENT", "commercial")
    monkeypatch.setenv("REPORTS_TO", "ceo")
    yield events
    server.shutdown()


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location("company_activity", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def messages(events):
    return [(e["from"], e["to"], e["kind"], e["message"]) for e in events if e["eventType"] == "agent_message"]


def test_messages_carry_the_order(collector, plugin):
    plugin._post_tool_call(tool_name="mcp__company__assign_task", status="ok", result="{}",
                           args={"assigned_to": "sales-rep", "task_type": "ValidateCustomer", "correlation_id": "O-7"})
    plugin._post_tool_call(tool_name="mcp__company__approve_decision", status="ok", args={"decision_id": "D-1"},
                           result=json.dumps({"requestedBy": "commercial-manager", "correlationId": "O-8"}))
    assert [e["correlationId"] for e in collector if e["eventType"] == "agent_message"] == ["O-7", "O-8"]


def test_tool_calls_are_published_with_identity(collector, plugin):
    plugin._pre_tool_call(tool_name="mcp__company__get_order", args={"order_id": "O-1"}, session_id="s1")
    plugin._post_tool_call(tool_name="mcp__company__get_order", args={"order_id": "O-1"}, result="{}", status="ok")
    started, finished = collector
    assert (started["eventType"], started["agentId"], started["department"]) == ("tool_started", "commercial-manager", "commercial")
    assert started["metadata"] == {"tool": "mcp__company__get_order", "preview": "O-1"}
    assert finished["metadata"]["status"] == "ok"
    assert messages(collector) == []


def test_delegation_escalation_and_reports_become_messages(collector, plugin):
    plugin._post_tool_call(tool_name="mcp__company__assign_task", status="ok", result="{}",
                           args={"assigned_to": "sales-rep", "task_type": "ValidateCustomer", "correlation_id": "O-1"})
    plugin._post_tool_call(tool_name="mcp__company__request_ceo_approval", status="ok", result="{}",
                           args={"decision_type": "SPECIAL_DISCOUNT", "requested_value": 25, "correlation_id": "O-1"})
    plugin._post_tool_call(tool_name="mcp__company__complete_task", status="ok",
                           args={"task_id": "TASK-X", "result": "Customer ok"},
                           result=json.dumps({"taskId": "TASK-X", "assignedBy": "commercial-manager"}))
    plugin._post_tool_call(tool_name="mcp__company__approve_decision", status="ok", args={"decision_id": "D-1"},
                           result='Tool result: {"decisionId": "D-1", "requestedBy": "commercial-manager"}')
    assert messages(collector) == [
        ("commercial-manager", "sales-rep", "delegate", "ValidateCustomer O-1"),
        ("commercial-manager", "ceo", "escalate", "SPECIAL_DISCOUNT 25 O-1"),
        ("commercial-manager", "commercial-manager", "report", "done: Customer ok"),
        ("commercial-manager", "commercial-manager", "decide", "approved"),
    ]


def test_failed_calls_are_errors_not_messages(collector, plugin):
    plugin._post_tool_call(tool_name="mcp__company__validate_order", status="ok", args={"order_id": "O-1"},
                           result=json.dumps({"errorCode": "ESCALATION_REQUIRED", "retryable": False}))
    plugin._post_tool_call(tool_name="mcp__company__assign_task", status="error", args={"assigned_to": "x"},
                           result="boom", error_message="boom")
    finished = [e for e in collector if e["eventType"] == "tool_finished"]
    assert [e["metadata"]["error"] for e in finished] == ["ESCALATION_REQUIRED", "boom"]
    assert messages(collector) == []


def test_unreachable_collector_never_raises(monkeypatch, plugin):
    monkeypatch.setenv("AGENT_ACTIVITY_URL", "http://127.0.0.1:9")
    plugin._pre_tool_call(tool_name="mcp__company__whoami", args={})
