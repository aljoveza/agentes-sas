import json
import stat

import boto3
import pytest
from moto import mock_aws

from agent_runtime.__main__ import handle
from agent_runtime.hermes import HermesRunner, TurnResult
from agent_runtime.inbox import Inbox


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        dlq = sqs.create_queue(QueueName="inbox-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        url = sqs.create_queue(
            QueueName="inbox",
            Attributes={"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"})},
        )["QueueUrl"]
        yield sqs, url, dlq


def send_event(sqs, url, event_type="WorkAssigned"):
    envelope = {"eventId": "e-1", "eventType": event_type, "correlationId": "ORD-1", "source": "operations-manager",
                "payload": {"assignedTo": "fulfillment"}}
    sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"detail-type": event_type, "detail": envelope}))


class FakeRunner:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.prompts: list[str] = []

    def run(self, prompt: str) -> TurnResult:
        self.prompts.append(prompt)
        return TurnResult(self.ok, 0 if self.ok else 2, "done", 0.1)


def depth(sqs, url) -> int:
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages",
                                                                    "ApproximateNumberOfMessagesNotVisible"])
    return sum(int(v) for v in attrs["Attributes"].values())


def test_successful_turn_acknowledges_the_event(queue, settings):
    sqs, url, _ = queue
    send_event(sqs, url)
    inbox, runner = Inbox(sqs, url), FakeRunner(ok=True)
    assert handle(inbox, runner, settings, inbox.receive(wait_seconds=0))
    assert "WorkAssigned" in runner.prompts[0]
    assert depth(sqs, url) == 0


def test_failed_turn_is_retried_then_dead_lettered(queue, settings):
    sqs, url, dlq = queue
    send_event(sqs, url)
    inbox, runner = Inbox(sqs, url), FakeRunner(ok=False)
    for _ in range(3):
        delivery = inbox.receive(wait_seconds=0)
        assert delivery is not None
        handle(inbox, runner, settings, delivery)
        inbox.retry_later(delivery, seconds=0)  # skip the 30s back-off in tests
    assert inbox.receive(wait_seconds=0) is None
    assert depth(sqs, dlq) == 1


def test_unreadable_message_never_reaches_hermes(queue, settings):
    sqs, url, _ = queue
    sqs.send_message(QueueUrl=url, MessageBody="not json")
    inbox, runner = Inbox(sqs, url), FakeRunner(ok=True)
    assert not handle(inbox, runner, settings, inbox.receive(wait_seconds=0))
    assert runner.prompts == []


def fake_hermes(tmp_path, script: str):
    path = tmp_path / "hermes"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_runner_passes_the_prompt_and_reports_exit_codes(tmp_path):
    ok = HermesRunner(tmp_path / "w", 5, fake_hermes(tmp_path, 'echo "ran: $1 $2 $3"\n')).run("hello")
    assert ok.ok and ok.output == "ran: -z hello --yolo"
    failed = HermesRunner(tmp_path / "w", 5, fake_hermes(tmp_path, "exit 2\n")).run("x")
    assert not failed.ok and failed.exit_code == 2


def test_runner_times_out(tmp_path):
    result = HermesRunner(tmp_path / "w", 1, fake_hermes(tmp_path, "sleep 5\n")).run("x")
    assert not result.ok and result.exit_code is None
