"""The employee's SQS inbox. Messages are EventBridge events whose detail is the business envelope."""

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Delivery:
    receipt_handle: str
    message_id: str
    receive_count: int
    envelope: dict[str, Any] | None


class Inbox:
    def __init__(self, client: Any, queue_url: str, visibility_timeout: int = 300) -> None:
        self._sqs = client
        self._url = queue_url
        self._visibility = visibility_timeout

    def receive(self, wait_seconds: int = 20) -> Delivery | None:
        response = self._sqs.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=self._visibility,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        try:
            envelope = json.loads(message["Body"])["detail"]
            if not isinstance(envelope, dict) or "eventType" not in envelope:
                envelope = None
        except (ValueError, KeyError, TypeError):
            envelope = None
        return Delivery(
            receipt_handle=message["ReceiptHandle"],
            message_id=message["MessageId"],
            receive_count=int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            envelope=envelope,
        )

    def done(self, delivery: Delivery) -> None:
        self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=delivery.receipt_handle)

    def retry_later(self, delivery: Delivery, seconds: int = 30) -> None:
        """Make the message visible again soon; after maxReceiveCount the queue moves it to the DLQ."""
        self._sqs.change_message_visibility(
            QueueUrl=self._url, ReceiptHandle=delivery.receipt_handle, VisibilityTimeout=seconds
        )

    @contextmanager
    def working_on(self, delivery: Delivery, every: int = 60) -> Iterator[None]:
        """Keep the message hidden while a long turn runs, so no other consumer picks it up."""
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(every):
                try:
                    self._sqs.change_message_visibility(
                        QueueUrl=self._url, ReceiptHandle=delivery.receipt_handle, VisibilityTimeout=self._visibility
                    )
                except Exception:  # noqa: BLE001 - heartbeat failures only shorten the lease
                    log.warning("could not extend message visibility", exc_info=True)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
