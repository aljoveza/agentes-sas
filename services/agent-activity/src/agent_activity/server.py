"""HTTP surface: POST /events (from agents); GET /state, /orders/<id>/activity, /stats, /health."""

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .activity import Activity

log = logging.getLogger("agent_activity")
MAX_BODY = 64 * 1024
ORDER_PATH = re.compile(r"^/orders/(?P<order>[A-Za-z0-9_-]{1,120})/activity$")
# Debugging aid: log every received event (ACTIVITY_LOG_EVENTS=1).
LOG_EVENTS = os.environ.get("ACTIVITY_LOG_EVENTS") == "1"


def make_handler(activity: Activity) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            match = ORDER_PATH.match(path)
            if match:
                self._send(200, activity.order(match.group("order")))
            elif path == "/state":
                self._send(200, activity.state())
            elif path == "/stats":
                self._send(200, activity.stats())
            elif path in ("/", "/health"):
                self._send(200, {"status": "ok", "service": "agent-activity"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?")[0] != "/events":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self._send(413 if length > MAX_BODY else 400, {"error": "bad size"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except ValueError:
                self._send(400, {"error": "invalid json"})
                return
            for event in payload if isinstance(payload, list) else [payload]:
                if isinstance(event, dict):
                    if LOG_EVENTS:
                        log.info("event %s", json.dumps(event, default=str)[:2000])
                    activity.ingest(event)
            self._send(202, {})

    return Handler


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8113"))
    server = ThreadingHTTPServer((host, port), make_handler(Activity()))
    log.info("agent activity on http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
