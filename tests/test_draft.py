"""`modelpin draft <file>` writes reviewable scenario drafts, and keeps them honest.

Offline: the model is a scripted adapter returning the JSON a real model would.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.models import Trace
from modelpin.providers.base import ProviderAdapter
from modelpin.scenarios import load_scenarios

runner = CliRunner()

APP = '''
SYSTEM = """You are the refunds desk. Always call lookup_order before issue_refund."""
TOOLS = [lookup_order, issue_refund]
OPENAI_API_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": SYSTEM}])
'''


class _Drafter(ProviderAdapter):
    def __init__(self, reply: dict | str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def run(self, scenario, model_id, run_idx=0):
        self.prompts.append(scenario.input["messages"][0]["content"])
        text = self.reply if isinstance(self.reply, str) else "Sure:\n" + json.dumps(self.reply)
        return Trace(scenario_id=scenario.id, model_id=model_id, final_output=text)


GOOD = {
    "system_prompt": "You are the refunds desk. Always call lookup_order before issue_refund.",
    "tools": [
        {"type": "function", "function": {"name": "lookup_order", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "cancel_account", "parameters": {"type": "object"}}},
    ],
    "scenarios": [
        {"id": "refund damaged", "name": "Damaged item", "user_message": "Order A-1 arrived broken.",
         "why": "main path", "suggested_must_contain": ["A-1"]},
        {"id": "refund_late", "name": "Late", "user_message": "Refund order B-2, it's late."},
    ],
}  # fmt: skip


def _run(tmp_path, monkeypatch, reply, *extra):
    src = tmp_path / "app.py"
    src.write_text(APP, encoding="utf-8")
    drafter = _Drafter(reply)
    monkeypatch.setattr(cli, "_adapter", lambda provider, fixtures: drafter)
    result = runner.invoke(
        app,
        ["draft", str(src), "--model", "gpt-4o-mini", "--provider", "openai",
         "--scenarios-dir", str(tmp_path / "scenarios"), *extra],
    )  # fmt: skip
    return result, drafter


def test_drafts_are_written_where_baseline_cannot_see_them(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, GOOD)
    assert result.exit_code == 0, result.output
    drafts = sorted((tmp_path / "scenarios" / ".drafts").glob("*.json"))
    assert [p.name for p in drafts] == ["refund_damaged.json", "refund_late.json"]
    assert load_scenarios(tmp_path / "scenarios") == [], "a draft must never be baselined"


def test_what_the_file_says_is_copied_and_what_the_model_invented_is_labelled(
    tmp_path, monkeypatch
):
    _run(tmp_path, monkeypatch, GOOD)
    doc = json.loads(
        (tmp_path / "scenarios" / ".drafts" / "refund_damaged.json").read_text("utf-8")
    )
    assert doc["input"]["messages"][0] == {"role": "system", "content": GOOD["system_prompt"]}
    assert [t["function"]["name"] for t in doc["input"]["tools"]] == ["lookup_order"]
    assert "assertions" not in doc, "an invented expectation must not become a live assertion"
    assert doc["_draft"]["suggested_must_contain"] == ["A-1"]
    assert "user message" in doc["_draft"]["invented"]


def test_a_system_prompt_not_in_the_file_is_dropped_and_said(tmp_path, monkeypatch):
    reply = dict(GOOD, system_prompt="You are a friendly assistant who loves refunds.")
    result, _ = _run(tmp_path, monkeypatch, reply)
    doc = json.loads((tmp_path / "scenarios" / ".drafts" / "refund_late.json").read_text("utf-8"))
    assert doc["input"]["messages"][0]["role"] == "user"
    flat = " ".join(result.output.split())
    assert "left out" in flat and "cancel_account" in flat


def test_secrets_are_redacted_before_the_file_is_sent(tmp_path, monkeypatch):
    result, drafter = _run(tmp_path, monkeypatch, GOOD)
    assert "sk-proj-abcdef" not in drafter.prompts[0]
    assert "redacted" in " ".join(result.output.split())


def test_a_reply_that_is_not_the_json_format_writes_nothing(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, "I cannot help with that.")
    assert result.exit_code == 4, result.output
    assert not (tmp_path / "scenarios" / ".drafts").exists() or not list(
        (tmp_path / "scenarios" / ".drafts").glob("*.json")
    )


def test_a_promoted_draft_loads_as_a_scenario(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, GOOD)
    drafts = tmp_path / "scenarios" / ".drafts"
    (drafts / "refund_late.json").rename(tmp_path / "scenarios" / "refund_late.json")
    (loaded,) = load_scenarios(tmp_path / "scenarios")
    assert loaded.id == "refund_late" and loaded.input["tools"]


def test_the_offline_provider_is_refused(tmp_path):
    src = tmp_path / "app.py"
    src.write_text(APP, encoding="utf-8")
    result = runner.invoke(app, ["draft", str(src), "--model", "m", "--provider", "fake"])
    assert result.exit_code == 4


def test_canned_tool_results_are_kept_only_for_tools_the_file_offers(tmp_path, monkeypatch):
    reply = json.loads(json.dumps(GOOD))
    reply["scenarios"][0]["tool_results"] = {
        "lookup_order": {"order_id": "A-1", "status": "delivered"},
        "cancel_account": {"ok": True},
    }
    _run(tmp_path, monkeypatch, reply)
    doc = json.loads(
        (tmp_path / "scenarios" / ".drafts" / "refund_damaged.json").read_text("utf-8")
    )
    assert doc["input"]["tool_results"] == {
        "lookup_order": {"order_id": "A-1", "status": "delivered"}
    }
    assert "tool_results" in doc["_draft"]["invented"]
