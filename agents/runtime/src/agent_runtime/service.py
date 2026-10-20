"""What the container needs around the inbox loop when it runs as an ECS Express Mode service.

Express Mode runs a single container per task, so the signing proxy that is a sidecar locally
runs here as a child process on 127.0.0.1, and the load balancer's health check gets /health.
Locally (compose) neither is configured: the proxy is its own container and nothing probes us.
"""

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Settings

log = logging.getLogger("agent_runtime")


class SigningProxy:
    """company-mcp-proxy as a child process: adds this task role's IAM token to every MCP call."""

    def __init__(self, settings: Settings) -> None:
        env = os.environ | {
            "UPSTREAM_URL": settings.mcp_proxy_upstream,
            "LISTEN_PORT": str(settings.mcp_proxy_port),
            "AWS_REGION": settings.region,
        }
        self._health_url = f"http://127.0.0.1:{settings.mcp_proxy_port}/proxy-health"
        self._process = subprocess.Popen([settings.mcp_proxy_command], env=env)

    def alive(self) -> bool:
        return self._process.poll() is None

    def wait_ready(self, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                raise SystemExit(f"MCP signing proxy exited with code {self._process.returncode}")
            try:
                with urllib.request.urlopen(self._health_url, timeout=2):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        raise SystemExit("MCP signing proxy did not become ready")

    def stop(self) -> None:
        self._process.terminate()


def serve_health(port: int, agent_id: str, proxy: SigningProxy | None) -> ThreadingHTTPServer:
    """GET /health: 200 while the employee can work (its proxy is up), 503 otherwise."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            healthy = proxy is None or proxy.alive()
            if self.path != "/health":
                status, body = 404, {"error": "not found"}
            else:
                status, body = (200, {"status": "ok"}) if healthy else (503, {"status": "mcp proxy down"})
            body = json.dumps(body | {"agentId": agent_id}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # health checks every 30 s would drown the turn logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server
