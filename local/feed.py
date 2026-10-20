"""Formats Company MCP and agent JSON logs (stdin) into one readable company feed."""

import json
import sys

DIM, BOLD, RED, GREEN, CYAN, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[36m", "\033[0m"
if not sys.stdout.isatty():
    DIM = BOLD = RED = GREEN = CYAN = RESET = ""

QUIET_TOOLS = {"whoami"}
REF_FIELDS = ("order_id", "task_id", "decision_id", "correlation_id", "invoice_id", "shipment_id")


def first_line(text: str | None, limit: int = 140) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:limit] if lines else ""


def render(entry: dict) -> str | None:
    time = f"{DIM}{entry.get('timestamp', '')[11:19]}{RESET}"
    message = entry.get("message")
    agent = entry.get("agentId") or "?"

    if message in ("tool call", "tool call rejected", "tool call denied"):
        tool = entry.get("tool", "")
        if tool in QUIET_TOOLS:
            return None
        ref = next((entry[f] for f in REF_FIELDS if entry.get(f)), "")
        if entry.get("outcome") == "ok":
            return f"{time}  {GREEN}✓{RESET} {agent:<19} {tool:<22} {ref}"
        error = first_line(entry.get("error") or message, 120)
        return f"{time}  {RED}✗ {agent:<19} {tool:<22} {ref}  {error}{RESET}"
    if message == "turn started":
        return f"{time}  {CYAN}▶ {agent:<19} handles {entry.get('eventType')} ({entry.get('correlationId')}){RESET}"
    if message in ("turn completed", "recovery turn finished"):
        return f"{time}  {BOLD}■ {agent:<19} {first_line(entry.get('summary'))}{RESET}"
    if message == "turn failed; will retry":
        return f"{time}  {RED}■ {agent:<19} FAILED (attempt {entry.get('attempt')}): {first_line(entry.get('summary'))}{RESET}"
    return None


for line in sys.stdin:
    try:
        entry = json.loads(line)
    except ValueError:
        continue
    rendered = render(entry)
    if rendered:
        print(rendered, flush=True)
