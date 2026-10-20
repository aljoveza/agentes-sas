import dataclasses
import json
import socket
import sys
import urllib.error
import urllib.request

import pytest

from agent_runtime.service import SigningProxy, serve_health

# Stands in for company-mcp-proxy: answers /proxy-health on LISTEN_PORT.
FAKE_PROXY = """
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/proxy-health' else 404); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', int(os.environ['LISTEN_PORT'])), H).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port: int, path: str = "/health") -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


@pytest.fixture
def fake_proxy(settings, tmp_path):
    script = tmp_path / "proxy"
    script.write_text(f"#!{sys.executable}\n{FAKE_PROXY}")
    script.chmod(0o755)
    return dataclasses.replace(settings, mcp_proxy_upstream="https://mcp.example", mcp_proxy_port=free_port(),
                               mcp_proxy_command=str(script))


def test_health_follows_the_signing_proxy(fake_proxy):
    proxy = SigningProxy(fake_proxy)
    proxy.wait_ready()
    port = free_port()
    server = serve_health(port, "fulfillment", proxy)
    try:
        assert get(port) == (200, {"status": "ok", "agentId": "fulfillment"})
        assert get(port, "/other")[0] == 404
        proxy.stop()
        proxy._process.wait(timeout=5)
        assert get(port) == (503, {"status": "mcp proxy down", "agentId": "fulfillment"})
    finally:
        server.shutdown()
        proxy.stop()


def test_a_proxy_that_cannot_start_stops_the_employee(settings, tmp_path):
    broken = dataclasses.replace(settings, mcp_proxy_upstream="https://mcp.example", mcp_proxy_command="/usr/bin/false")
    with pytest.raises(SystemExit, match="exited"):
        SigningProxy(broken).wait_ready(timeout=5)
