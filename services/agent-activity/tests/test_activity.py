from agent_activity.activity import Activity, order_from_reference


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def ev(agent, kind, **meta):
    return {"agentId": agent, "agentName": agent.title(), "department": "operations",
            "reportsTo": "ceo", "eventType": kind, "metadata": meta}


def by_id(view):
    return {a["id"]: a for a in view["agents"]}


def test_tool_calls_belong_to_the_order_of_the_current_turn():
    activity = Activity()
    activity.ingest(ev("fulfillment", "turn_started", eventType="WorkAssigned", correlationId="O-1"))
    activity.ingest(ev("fulfillment", "tool_started", tool="mcp__company__reserve_inventory"))
    me = by_id(activity.order("O-1"))["fulfillment"]
    assert (me["onThisOrder"], me["status"], me["tool"]) == (True, "working", "reserve_inventory")

    activity.ingest(ev("fulfillment", "tool_finished", tool="mcp__company__reserve_inventory", status="ok"))
    activity.ingest(ev("fulfillment", "turn_finished", summary="Reserved and shipped"))
    view = activity.order("O-1")
    me = by_id(view)["fulfillment"]
    assert (me["onThisOrder"], me["status"], me["lastSummary"], me["involved"]) == (False, "idle", "Reserved and shipped", True)
    assert [e["kind"] for e in view["timeline"]] == ["turn_started", "tool_started", "tool_finished", "turn_finished"]


def test_an_agent_busy_with_another_order_is_not_shown_as_working_on_this_one():
    activity = Activity()
    activity.ingest(ev("fulfillment", "turn_started", eventType="WorkAssigned", correlationId="O-2"))
    me = by_id(activity.order("O-1"))["fulfillment"]
    assert (me["onThisOrder"], me["busyElsewhere"], me["involved"]) == (False, True, False)


def test_escalation_shows_the_manager_waiting_on_that_order():
    activity = Activity()
    activity.ingest(ev("commercial-manager", "turn_started", eventType="WorkCompleted", correlationId="O-1"))
    activity.ingest({"eventType": "agent_message", "from": "commercial-manager", "to": "ceo", "kind": "escalate",
                     "correlationId": "O-1", "message": "SPECIAL_DISCOUNT 25 O-1"})
    activity.ingest(ev("commercial-manager", "turn_finished", summary="Escalated"))
    me = by_id(activity.order("O-1"))["commercial-manager"]
    assert (me["status"], me["waitingFor"]) == ("waiting", "ceo")
    message = [e for e in activity.order("O-1")["timeline"] if e["kind"] == "message"][0]
    assert (message["messageKind"], message["to"]) == ("escalate", "ceo")


def test_failed_tools_and_turns_are_kept():
    activity = Activity()
    activity.ingest(ev("fulfillment", "turn_started", eventType="WorkAssigned", correlationId="O-3"))
    activity.ingest(ev("fulfillment", "tool_finished", tool="mcp__company__reserve_inventory", status="error",
                       error="INVENTORY_UNAVAILABLE"))
    activity.ingest(ev("fulfillment", "turn_failed", summary="Bedrock timeout", attempt=1))
    kinds = [(e["kind"], e.get("ok"), e.get("error")) for e in activity.order("O-3")["timeline"]]
    assert ("tool_finished", False, "INVENTORY_UNAVAILABLE") in kinds
    assert kinds[-1][0] == "turn_failed"
    assert by_id(activity.order("O-3"))["fulfillment"]["status"] == "error"


def test_recovery_turns_attribute_work_by_reference():
    activity = Activity()
    activity.ingest(ev("fulfillment", "turn_started", eventType="Recovery", correlationId="outstanding work"))
    activity.ingest({**ev("fulfillment", "tool_started", tool="mcp__company__start_task",
                          preview="TASK-FulfillOrder-O-9"), "taskId": "TASK-FulfillOrder-O-9"})
    assert [e["tool"] for e in activity.order("O-9")["timeline"]] == ["start_task"]
    assert activity.order("outstanding work")["timeline"] == []


def test_order_references():
    assert order_from_reference("TASK-FulfillOrder-DEVOPS-2026-001") == "DEVOPS-2026-001"
    assert order_from_reference("DECISION-SPECIAL-DISCOUNT-MKT-1-02") == "MKT-1-02"
    assert order_from_reference("TIENDA-ABC") == "TIENDA-ABC"


def test_stale_turns_do_not_look_busy_forever():
    clock = Clock()
    activity = Activity(clock)
    activity.ingest(ev("accountant", "turn_started", eventType="WorkAssigned", correlationId="O-1"))
    clock.now += 600
    me = by_id(activity.order("O-1"))["accountant"]
    assert (me["status"], me["onThisOrder"]) == ("idle", False)


def test_heartbeat_restores_desks_and_stats_count_events():
    activity = Activity()
    activity.ingest(ev("ceo", "heartbeat"))
    assert by_id(activity.state())["ceo"]["status"] == "idle"
    assert activity.stats() == {"heartbeat": 1}
