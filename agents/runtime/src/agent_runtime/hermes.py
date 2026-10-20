"""Runs one Hermes turn as a subprocess (`hermes -z`): prompt in, final text out."""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    ok: bool
    exit_code: int | None
    output: str
    seconds: float


class HermesRunner:
    def __init__(self, workdir: Path, timeout: int, command: str = "hermes", env: dict[str, str] | None = None) -> None:
        self._workdir = workdir
        self._timeout = timeout
        self._command = command
        # Extra environment for Hermes only (e.g. its Bedrock profile); the runtime and the
        # signing proxy keep the task role.
        self._env = os.environ | env if env else None

    def run(self, prompt: str) -> TurnResult:
        self._workdir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [self._command, "-z", prompt, "--yolo"],
                cwd=self._workdir,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=self._env,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return TurnResult(False, None, output.strip(), time.monotonic() - started)
        if completed.returncode != 0 and completed.stderr:
            log.warning("hermes stderr", extra={"fields": {"stderr": completed.stderr.strip()[-2000:]}})
        return TurnResult(
            completed.returncode == 0, completed.returncode, completed.stdout.strip(), time.monotonic() - started
        )
