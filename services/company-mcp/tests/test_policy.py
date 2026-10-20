import re
from pathlib import Path

import yaml
import pytest

from company_mcp import policy

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
PROMPT_FILES = {
    "ceo": "ceo.yaml",
    "commercial-manager": "commercial/manager.yaml",
    "sales-rep": "commercial/sales-rep.yaml",
    "operations-manager": "operations/manager.yaml",
    "fulfillment": "operations/fulfillment.yaml",
    "accounting-manager": "accounting/manager.yaml",
    "accountant": "accounting/accountant.yaml",
}


def test_accounting_cannot_ship_and_operations_cannot_invoice():
    assert not policy.can_use("accountant", "create_shipment")
    assert not policy.can_use("fulfillment", "create_invoice")
    assert not policy.can_use(None, "whoami")


def test_org_chart_matches_terraform():
    terraform = (Path(__file__).resolve().parents[3] / "infrastructure/modules/organization/main.tf").read_text()
    declared = dict(re.findall(r'^\s{4}([a-z-]+) = \{\n(?:.*\n)*?\s+reports_to\s+= "([a-z-]*)"', terraform, re.M))
    assert {agent: manager or None for agent, manager in declared.items()} == policy.REPORTS_TO


@pytest.mark.parametrize("agent", PROMPT_FILES)
def test_prompt_tools_match_granted_tools(agent):
    """Prompts describe tools; policy enforces them. They must not drift apart."""
    prompt = yaml.safe_load((PROMPTS / PROMPT_FILES[agent]).read_text())
    assert set(prompt["allowed_tools"]) == policy.TOOL_GRANTS[agent]
