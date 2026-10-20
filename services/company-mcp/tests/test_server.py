"""End to end over streamable HTTP with header identity (local mode): tool visibility, error shape."""

import json

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from company_mcp import policy
from company_mcp.iam import HeaderAuthenticator
from company_mcp.server import create_server
from conftest import place, serve


@pytest.fixture
def url(service):
    app = create_server(service, HeaderAuthenticator()).http_app(path="/mcp", stateless_http=True)
    with serve(app) as base:
        yield f"{base}/mcp"


def client(url: str, agent: str | None) -> Client:
    headers = {"X-Agent-Id": agent} if agent else {}
    return Client(StreamableHttpTransport(url, headers=headers))


async def test_agents_only_see_their_tools(url):
    for agent in policy.TOOL_GRANTS:
        async with client(url, agent) as c:
            assert {tool.name for tool in await c.list_tools()} == policy.TOOL_GRANTS[agent]


async def test_forbidden_tool_is_rejected(url):
    async with client(url, "accountant") as c:
        result = await c.call_tool("create_shipment", {"order_id": "DEVOPS-2026-001"}, raise_on_error=False)
    assert result.is_error
    assert json.loads(result.content[0].text)["errorCode"] == "FORBIDDEN"


async def test_missing_identity_is_rejected(url):
    async with client(url, None) as c:
        assert await c.list_tools() == []
        result = await c.call_tool("whoami", raise_on_error=False)
    assert result.is_error


async def test_business_errors_are_structured(url, service):
    place(service, "DEVOPS-2026-002", payment_amount="6750.00", requested_discount="25")
    async with client(url, "commercial-manager") as c:
        result = await c.call_tool(
            "validate_order", {"order_id": "DEVOPS-2026-002", "discount_percent": 25}, raise_on_error=False
        )
    error = json.loads(result.content[0].text)
    assert error["errorCode"] == "ESCALATION_REQUIRED"
    assert error["retryable"] is False


async def test_invalid_arguments_are_rejected(url):
    async with client(url, "sales-rep") as c:
        result = await c.call_tool("get_order", {"order_id": "not a valid id"}, raise_on_error=False)
    assert result.is_error


async def test_tool_call_round_trip(url, service):
    place(service)
    async with client(url, "sales-rep") as c:
        identity = (await c.call_tool("whoami")).structured_content
        order = (await c.call_tool("get_order", {"order_id": "DEVOPS-2026-001"})).structured_content
    assert identity["reportsTo"] == "commercial-manager"
    assert order["orderId"] == "DEVOPS-2026-001"
    assert order["customerId"] == "CUST-ACME"


@pytest.mark.parametrize("status", [None, ["ASSIGNED", "IN_PROGRESS"], '["ASSIGNED", "IN_PROGRESS"]', "assigned, in_progress"])
async def test_list_my_tasks_accepts_how_models_phrase_statuses(url, service, status):
    service.assign_task("operations-manager", "fulfillment", "FulfillOrder", "DEVOPS-2026-001", "ship it")
    arguments = {} if status is None else {"status": status}
    async with client(url, "fulfillment") as c:
        result = await c.call_tool("list_my_tasks", arguments)
    assert [t["taskId"] for t in result.structured_content["result"]] == ["TASK-FulfillOrder-DEVOPS-2026-001"]


async def test_invalid_arguments_are_rejected_not_crashes(url, caplog):
    async with client(url, "fulfillment") as c:
        result = await c.call_tool("list_my_tasks", {"status": "SHIPPED"}, raise_on_error=False)
    assert result.is_error
    assert "tool call failed" not in caplog.text
