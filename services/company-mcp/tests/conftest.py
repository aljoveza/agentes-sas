import itertools
import socket
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

import boto3
import pytest
import uvicorn
from moto import mock_aws

from company_mcp.artifacts import ArtifactStore
from company_mcp.errors import DependencyFailure
from company_mcp.models import OrderLineRequest
from company_mcp.repository import Repository
from company_mcp.seed import seed
from company_mcp.service import CompanyService
from company_mcp.table import create_table

BUCKET = "company-test"


class RecordingPublisher:
    """Collects published events; can simulate EventBridge outages."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.failures_left = 0

    def publish(self, event: dict) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise DependencyFailure("EventBridge unavailable (simulated)")
        self.events.append(event)

    def types(self) -> list[str]:
        return [event["eventType"] for event in self.events]

    def of_type(self, event_type: str) -> list[dict]:
        return [event for event in self.events if event["eventType"] == event_type]


@pytest.fixture
def aws(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        yield


@pytest.fixture
def repo(aws) -> Repository:
    repo = Repository(create_table(boto3.resource("dynamodb"), "company"))
    seed(repo)
    return repo


@pytest.fixture
def s3(aws):
    client = boto3.client("s3")
    client.create_bucket(Bucket=BUCKET)
    return client


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def service(repo, s3, publisher) -> CompanyService:
    ticks = itertools.count()
    return CompanyService(
        repo, publisher, ArtifactStore(s3, BUCKET), clock=lambda: f"2026-10-01T10:{next(ticks) % 60:02d}:00Z"
    )


def place(service: CompanyService, order_id: str = "DEVOPS-2026-001", *, quantity: int = 3,
          customer_id: str = "CUST-ACME", product_id: str = "LAPTOP-PRO",
          payment_amount: str = "9000.00", requested_discount: str = "0"):
    return service.place_order(
        "order-simulator",
        order_id=order_id,
        customer_id=customer_id,
        lines=[OrderLineRequest(productId=product_id, quantity=quantity)],
        payment_reference=f"PAY-{order_id}",
        payment_amount=Decimal(payment_amount),
        requested_discount_percent=Decimal(requested_discount),
    )


@contextmanager
def serve(app):
    """Run an ASGI app on a free local port for the duration of the block; yields its base URL."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join()
