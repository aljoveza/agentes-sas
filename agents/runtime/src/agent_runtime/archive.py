"""Archives each turn's Hermes session to S3, so what an employee thought outlives its container.

Hermes keeps sessions in its local (ephemeral) SQLite store. After a turn, the session is exported
through the supported CLI (Markdown, secrets redacted) and stored at
sessions/archives/<agent-id>/<correlation-id>/<session-id>.md, where Nexo reads an order's
transcripts. Observability only: a failed archive is logged and never fails the turn.
"""

import logging
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("agent_runtime")

_SESSION_ID = re.compile(r'^session_id: "([^"]+)"', re.MULTILINE)
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class SessionArchive:
    def __init__(self, s3, bucket: str, agent_id: str, command: str = "hermes", timeout: int = 120) -> None:
        self._s3 = s3
        self._bucket = bucket
        self._agent_id = agent_id
        self._command = command
        self._timeout = timeout
        self._archived: set[str] = set()
        # One export at a time, off the inbox loop: the next event does not wait for it.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive")

    def archive_later(self, started_at: datetime, correlation_id: str | None) -> None:
        self._worker.submit(self.archive, started_at, correlation_id)

    def archive(self, started_at: datetime, correlation_id: str | None) -> int:
        """Upload the sessions that started since started_at (UTC); returns how many."""
        folder = _UNSAFE.sub("_", correlation_id or "") or "no-order"
        # Hermes filters by minute; sessions already archived are skipped below.
        after = (started_at - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        try:
            with tempfile.TemporaryDirectory() as directory:
                exported = subprocess.run(
                    [self._command, "sessions", "export", directory, "--format", "md", "--after", after,
                     "--min-messages", "0", "--yes", "--redact"],
                    capture_output=True, text=True, timeout=self._timeout,
                )
                if exported.returncode:
                    raise RuntimeError((exported.stderr or exported.stdout).strip()[-500:])
                count = 0
                for path in sorted(Path(directory).rglob("*.md")):
                    text = path.read_text()
                    match = _SESSION_ID.search(text)
                    session_id = match.group(1) if match else path.stem
                    if session_id in self._archived:
                        continue
                    self._s3.put_object(
                        Bucket=self._bucket,
                        Key=f"sessions/archives/{self._agent_id}/{folder}/{session_id}.md",
                        Body=text.encode(),
                        ContentType="text/markdown; charset=utf-8",
                    )
                    self._archived.add(session_id)
                    count += 1
        except Exception as exc:  # observability only
            log.warning("session archive failed", extra={"fields": {"agentId": self._agent_id,
                                                                     "correlationId": correlation_id, "error": str(exc)}})
            return 0
        log.info("sessions archived", extra={"fields": {"agentId": self._agent_id, "correlationId": correlation_id,
                                                        "sessions": count}})
        return count
