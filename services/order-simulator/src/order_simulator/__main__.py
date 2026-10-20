"""
    order-simulator run demo/scenarios/01-happy-path.json [--run-id 2]
    order-simulator market [--orders 5] [--seed 42] [--dry-run]
    order-simulator trace DEVOPS-2026-001

`run` places the scenario's orders through the Company MCP as `order-simulator`.
`market` places a stream of random but replayable orders (the seed is printed; --seed replays it).
`trace` prints the order's event timeline (audit log) and its current records (DynamoDB).

Environment: COMPANY_MCP_URL (default http://localhost:8080/mcp), SIMULATOR_AUTH=header|iam,
MCP_AUDIENCE (iam), AWS_REGION, TABLE_NAME and AUDIT_LOG_GROUP (trace), AWS_ENDPOINT_URL (LocalStack).
"""

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr, Key
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from company_mcp.iam import TokenSigner

from .market import PRODUCT_IDS, MarketPlanner, Product


def identity_headers() -> dict[str, str]:
    if os.environ.get("SIMULATOR_AUTH", "header") == "iam":
        region = os.environ.get("AWS_REGION", "us-east-1")
        return TokenSigner(boto3.Session(region_name=region), region, os.environ["MCP_AUDIENCE"]).headers()
    return {"X-Agent-Id": "order-simulator"}


async def run(scenario_path: Path, run_id: str | None) -> int:
    scenario = json.loads(scenario_path.read_text())
    url = os.environ.get("COMPANY_MCP_URL", "http://localhost:8080/mcp")
    print(f"▶ {scenario['name']}: {scenario['description']}")
    print(f"  expect: {scenario['expect']}")
    async with Client(StreamableHttpTransport(url, headers=identity_headers())) as mcp:
        for order in scenario["orders"]:
            order_id = order["order_id"] + (f"-R{run_id}" if run_id else "")
            arguments = {
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "lines": order["lines"],
                "payment_reference": f"PAY-{order_id}",
                "payment_amount": order["payment_amount"],
                "requested_discount_percent": order.get("requested_discount_percent", "0"),
            }
            for attempt in range(order.get("repeat", 1)):
                result = await mcp.call_tool("place_order", arguments, raise_on_error=False)
                if result.is_error:
                    print(f"  ✗ {order_id}: {result.content[0].text}")
                    return 1
                placed = result.structured_content
                print(f"  ✓ {order_id} (submission {attempt + 1}): {placed['status']}, total {placed['total']}")
            print(f"    follow it: order-simulator trace {order_id}")
    return 0


async def market(orders: int, seed: int | None, min_wait: float, max_wait: float, dry_run: bool) -> int:
    seed = seed if seed is not None else random.randrange(1_000_000)
    rng = random.Random(seed)
    url = os.environ.get("COMPANY_MCP_URL", "http://localhost:8080/mcp")
    async with Client(StreamableHttpTransport(url, headers=identity_headers())) as mcp:
        catalog = {}
        for pid in PRODUCT_IDS:
            item = (await mcp.call_tool("get_inventory", {"product_id": pid})).structured_content
            catalog[pid] = Product(pid, item["name"], Decimal(str(item["unitPrice"])), int(item["available"]))
        planner = MarketPlanner(catalog, rng)
        prefix = f"MKT-{datetime.now():%m%d%H%M%S}"
        plan = [planner.plan(f"{prefix}-{n:02d}") for n in range(1, orders + 1)]
        waits = [0.0] + [rng.uniform(min_wait, max_wait) for _ in plan[1:]]

        print(f"▶ market: {orders} orders, seed {seed} (replay with --seed {seed})")
        for order, wait in zip(plan, waits):
            if dry_run:
                print(f"  [{order.kind}] {order.order_id}: {order.describe(catalog)} {' · '.join(order.notes)}")
                continue
            if wait:
                print(f"  … next customer in {wait:.0f}s")
                await asyncio.sleep(wait)
            for _ in range(order.repeat):
                result = await mcp.call_tool("place_order", order.arguments(), raise_on_error=False)
                status = "✗ " + result.content[0].text if result.is_error else "✓"
                print(f"  {status} {order.order_id}: {order.describe(catalog)}", flush=True)
    return 0


def trace(order_id: str) -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    logs = boto3.client("logs", region_name=region)
    table = boto3.resource("dynamodb", region_name=region).Table(os.environ["TABLE_NAME"])

    print(f"Events for {order_id}")
    events = []
    paginator = logs.get_paginator("filter_log_events")
    for page in paginator.paginate(logGroupName=os.environ["AUDIT_LOG_GROUP"], filterPattern=f'"{order_id}"'):
        for entry in page["events"]:
            event = json.loads(entry["message"])
            envelope = event.get("detail", {})
            if envelope.get("correlationId") == order_id:
                events.append(envelope)
    for envelope in sorted(events, key=lambda e: e["timestamp"]):
        payload = envelope.get("payload", {})
        hint = payload.get("assignedTo") or payload.get("decisionId") or payload.get("errorCode") or ""
        print(f"  {envelope['timestamp']}  {envelope['eventType']:<22} by {envelope['source']:<20} {hint}")
    if not events:
        print("  (none yet)")

    print(f"\nRecords for {order_id}")
    for item in table.query(KeyConditionExpression=Key("pk").eq(f"ORDER#{order_id}"))["Items"]:
        status = item.get("status", "")
        total = item.get("total")
        extra = f"{total:.2f}" if total is not None else item.get("trackingNumber") or item.get("invoiceId") or ""
        print(f"  {item['sk']:<12} {status:<12} {extra}")
    # Tasks and decisions are keyed by their own ids; a scan is fine for a development tool.
    others = table.scan(FilterExpression=Attr("correlationId").eq(order_id))["Items"]
    for item in sorted(others, key=lambda i: i["pk"]):
        who = f"{item.get('assignedBy', item.get('requestedBy', ''))} → {item.get('assignedTo', 'ceo')}"
        print(f"  {item['pk'].split('#')[1]:<48} {item['status']:<12} {who}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="place a scenario's orders")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--run-id", help="suffix order ids to replay a scenario with new orders")
    market_parser = commands.add_parser("market", help="place a stream of random, replayable orders")
    market_parser.add_argument("--orders", type=int, default=5)
    market_parser.add_argument("--seed", type=int, help="replay a previous run")
    market_parser.add_argument("--min-wait", type=float, default=30, help="seconds between orders (min)")
    market_parser.add_argument("--max-wait", type=float, default=90, help="seconds between orders (max)")
    market_parser.add_argument("--dry-run", action="store_true", help="print the plan, place nothing")
    trace_parser = commands.add_parser("trace", help="show an order's events and records")
    trace_parser.add_argument("order_id")
    args = parser.parse_args()

    if args.command == "run":
        sys.exit(asyncio.run(run(args.scenario, args.run_id)))
    if args.command == "market":
        sys.exit(asyncio.run(market(args.orders, args.seed, args.min_wait, args.max_wait, args.dry_run)))
    sys.exit(trace(args.order_id))


if __name__ == "__main__":
    main()
