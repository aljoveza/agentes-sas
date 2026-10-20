import configparser
import dataclasses
import os
import sys

import yaml

from agent_runtime.__main__ import hermes_environment, prepare_hermes
from agent_runtime.hermes import HermesRunner
from agent_runtime.identity import render_hermes_config
from agent_runtime.turns import event_turn, resume_turn


def test_soul_comes_from_the_job_description(settings):
    prepare_hermes(settings)
    soul = (settings.hermes_home / "SOUL.md").read_text()
    assert "# Fulfillment" in soul
    version = yaml.safe_load(settings.prompt_file.read_text())["version"]
    assert f"version {version}" in soul
    assert "reserve_inventory" in soul  # procedures are rendered
    assert "ESCALATION_REQUIRED" in soul  # operating rules are rendered


def test_hermes_only_gets_the_company_mcp(settings):
    config = yaml.safe_load(render_hermes_config(settings))
    assert config["platform_toolsets"] == {"cli": ["mcp-company"]}
    assert config["mcp_servers"]["company"]["url"] == "http://127.0.0.1:9000/mcp"
    assert "headers" not in config["mcp_servers"]["company"]  # identity is added by the proxy
    assert config["model"] == {"default": "us.anthropic.example-model", "provider": "bedrock"}
    assert config["memory"]["memory_enabled"] is False
    assert config["tools"]["tool_search"]["enabled"] == "off"


def test_event_turn_carries_the_envelope():
    turn = event_turn({"eventType": "WorkAssigned", "eventId": "e-1", "correlationId": "ORD-1",
                       "source": "operations-manager", "payload": {"taskId": "TASK-FulfillOrder-ORD-1"}})
    assert "WorkAssigned" in turn and "ORD-1" in turn and "TASK-FulfillOrder-ORD-1" in turn


def test_resume_turn_depends_on_role():
    assert "list_pending_decisions" in resume_turn("ceo")
    assert "list_my_tasks" in resume_turn("fulfillment")


def test_activity_plugin_is_installed_only_when_the_service_is_configured(settings):
    from dataclasses import replace

    prepare_hermes(settings)
    assert "plugins" not in yaml.safe_load((settings.hermes_home / "config.yaml").read_text())
    assert not (settings.hermes_home / "plugins").exists()

    with_activity = replace(settings, activity_url="http://agent-activity:8113")
    prepare_hermes(with_activity)
    config = yaml.safe_load((settings.hermes_home / "config.yaml").read_text())
    assert config["plugins"] == {"enabled": ["company-activity"]}
    assert (settings.hermes_home / "plugins" / "company-activity" / "plugin.yaml").exists()


def test_long_summaries_are_cut_at_a_word_boundary():
    from agent_runtime.__main__ import tail

    assert tail("short reply") == "short reply"
    cut = tail("word " * 200 + "Final summary of the turn.")
    assert cut.startswith("…word") and cut.endswith("Final summary of the turn.") and len(cut) <= 501


def test_bedrock_in_another_account_is_only_hermes_concern(settings):
    assert hermes_environment(settings) == {}

    role = "arn:aws:iam::111111111111:role/org-bedrock-invoker"
    settings = dataclasses.replace(settings, bedrock_role_arn=role)
    settings.hermes_home.mkdir(parents=True)
    env = hermes_environment(settings)
    assert env["AWS_PROFILE"] == "bedrock"
    config = configparser.ConfigParser()
    config.read(env["AWS_CONFIG_FILE"])
    assert dict(config["profile bedrock"]) == {
        "role_arn": role,
        "credential_source": "EcsContainer",
        "role_session_name": "fulfillment",
        "region": "us-east-1",
    }


def test_hermes_extra_environment_reaches_only_the_hermes_process(tmp_path, monkeypatch):
    script = tmp_path / "hermes"
    script.write_text(f"#!{sys.executable}\nimport os\nprint(os.environ.get('AWS_PROFILE', '-'), os.environ['KEEP'])\n")
    script.chmod(0o755)
    monkeypatch.setenv("KEEP", "kept")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    assert HermesRunner(tmp_path, 10, str(script), env={"AWS_PROFILE": "bedrock"}).run("hi").output == "bedrock kept"
    assert HermesRunner(tmp_path, 10, str(script)).run("hi").output == "- kept"
    assert "AWS_PROFILE" not in os.environ
