"""Event adapter: inbox -> one Hermes turn per business event -> ack or retry."""

import logging
import shutil
import signal
import threading
import time
from datetime import UTC, datetime

import boto3

from .config import Settings
from .hermes import HermesRunner
from .identity import ACTIVITY_PLUGIN, BEDROCK_PROFILE, render_aws_config, render_hermes_config, render_soul, validate
from .inbox import Inbox
from .logs import configure_logging
from .activity import ActivityPublisher
from .archive import SessionArchive
from .prompts import load_prompt
from .service import SigningProxy, serve_health
from .turns import event_turn, resume_turn

log = logging.getLogger("agent_runtime")


def tail(text: str, limit: int = 500) -> str:
    """The end of a long reply (where the summary is), cut at a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    return "…" + (cut.split(" ", 1)[1] if " " in cut else cut)
HEARTBEAT_SECONDS = 30


def prepare_hermes(settings: Settings) -> dict:
    prompt = load_prompt(settings)
    validate(prompt, settings.agent_id)
    settings.hermes_home.mkdir(parents=True, exist_ok=True)
    (settings.hermes_home / "SOUL.md").write_text(render_soul(prompt, settings))
    (settings.hermes_home / "config.yaml").write_text(render_hermes_config(settings))
    plugin_source = settings.plugins_dir / ACTIVITY_PLUGIN
    if settings.activity_url and plugin_source.is_dir():
        shutil.copytree(plugin_source, settings.hermes_home / "plugins" / ACTIVITY_PLUGIN, dirs_exist_ok=True)
    return prompt


def hermes_environment(settings: Settings) -> dict[str, str]:
    """Environment only Hermes gets: its Bedrock profile when inference runs in another account."""
    aws_config = render_aws_config(settings)
    if aws_config is None:
        return {}
    path = settings.hermes_home / "aws-config"
    path.write_text(aws_config)
    return {"AWS_CONFIG_FILE": str(path), "AWS_PROFILE": BEDROCK_PROFILE}


def handle(inbox: Inbox, runner: HermesRunner, settings: Settings, delivery, activity: ActivityPublisher | None = None,
           archive: SessionArchive | None = None) -> bool:
    activity = activity or ActivityPublisher(settings)
    envelope = delivery.envelope
    if envelope is None:
        log.error("unreadable message; leaving it for the DLQ", extra={"fields": {"messageId": delivery.message_id}})
        inbox.retry_later(delivery, seconds=0)
        return False

    fields = {
        "agentId": settings.agent_id,
        "department": settings.department,
        "eventId": envelope.get("eventId"),
        "eventType": envelope.get("eventType"),
        "correlationId": envelope.get("correlationId"),
        "attempt": delivery.receive_count,
    }
    log.info("turn started", extra={"fields": fields})
    activity.publish("turn_started", eventType=envelope.get("eventType"), correlationId=envelope.get("correlationId"),
                   eventId=envelope.get("eventId"), attempt=delivery.receive_count)
    started_at = datetime.now(UTC)
    with inbox.working_on(delivery):
        result = runner.run(event_turn(envelope))
    if archive:
        archive.archive_later(started_at, envelope.get("correlationId"))
    fields |= {"seconds": round(result.seconds, 1), "exitCode": result.exit_code, "summary": tail(result.output)}
    if result.ok:
        inbox.done(delivery)
        log.info("turn completed", extra={"fields": fields})
        activity.publish("turn_finished", eventType=envelope.get("eventType"), summary=tail(result.output),
                       seconds=round(result.seconds, 1))
    else:
        inbox.retry_later(delivery)
        log.error("turn failed; will retry", extra={"fields": fields})
        activity.publish("turn_failed", eventType=envelope.get("eventType"), summary=tail(result.output),
                       attempt=delivery.receive_count)
    return result.ok


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    prompt = prepare_hermes(settings)
    log.info(
        "employee ready",
        extra={"fields": {"agentId": settings.agent_id, "role": prompt["role"], "promptVersion": prompt["version"]}},
    )

    proxy = SigningProxy(settings) if settings.mcp_proxy_upstream else None
    if proxy:
        proxy.wait_ready()
    if settings.health_port:
        serve_health(settings.health_port, settings.agent_id, proxy)

    activity = ActivityPublisher(settings)
    activity.publish("employee_ready", promptVersion=prompt["version"])
    runner = HermesRunner(settings.workdir, settings.turn_timeout, env=hermes_environment(settings))
    sqs = boto3.client(
        "sqs",
        region_name=settings.region,
        endpoint_url=settings.queue_endpoint_url,
        # LocalStack ignores credentials; real ones would map to a different emulated account.
        **({"aws_access_key_id": "test", "aws_secret_access_key": "test"} if settings.queue_endpoint_url else {}),
    )
    inbox = Inbox(sqs, settings.queue_url)
    archive = (SessionArchive(boto3.client("s3", region_name=settings.region), settings.session_archive_bucket,
                              settings.agent_id) if settings.session_archive_bucket else None)

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())

    if settings.resume_on_start:
        activity.publish("turn_started", eventType="Recovery", correlationId="outstanding work")
        started_at = datetime.now(UTC)
        result = runner.run(resume_turn(settings.agent_id))
        if archive:
            archive.archive_later(started_at, "recovery")
        activity.publish("turn_finished", eventType="Recovery", summary=tail(result.output))
        log.info(
            "recovery turn finished",
            extra={"fields": {"agentId": settings.agent_id, "ok": result.ok, "summary": tail(result.output)}},
        )

    # One event at a time: an employee has one identity and one desk.
    last_heartbeat = time.monotonic()
    while not stopping.is_set():
        if proxy and not proxy.alive():
            # Without its proxy the employee cannot act; let ECS replace the task.
            log.error("MCP signing proxy exited; stopping", extra={"fields": {"agentId": settings.agent_id}})
            raise SystemExit(1)
        delivery = inbox.receive()
        if delivery is not None:
            handle(inbox, runner, settings, delivery, activity, archive)
        if time.monotonic() - last_heartbeat > HEARTBEAT_SECONDS:
            # Lets the (in-memory) agent-activity service rebuild itself after a restart.
            activity.publish("heartbeat", promptVersion=prompt["version"])
            last_heartbeat = time.monotonic()
    log.info("stopping", extra={"fields": {"agentId": settings.agent_id}})
    if proxy:
        proxy.stop()


if __name__ == "__main__":
    main()
