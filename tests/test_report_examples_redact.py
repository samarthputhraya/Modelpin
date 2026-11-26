"""Examples quoted in reports redact key-shaped strings, and `check` warns about them (MP-327).

The console, `last-report.md` (the PR comment), `migration-report.md` and the published Report
quote one example run per side for every flagged scenario. Those quotes now pass through
`scrub_secrets`, as provider error messages always did. The recorded runs themselves are never
rewritten (report, don't rewrite): `check` warns instead, as `baseline` already did, and the
secret scan reads strings at any depth.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.models import ToolCall, Trace
from modelpin.report.evidence import Example
from modelpin.storage import secret_bearing_scenarios

runner = CliRunner()
KEY = "sk-proj-" + "FAKE" + "a1B2c3D4" * 5


def test_an_example_redacts_keys_in_output_arguments_and_tool_names() -> None:
    trace = Trace(
        scenario_id="s",
        model_id="m",
        final_output=f"Debug: OPENAI_API_KEY={KEY}",
        tool_calls=[
            ToolCall(name="lookup", arguments={"token": KEY, "nested": {"auth": [KEY]}}),
            ToolCall(name=f"call_{KEY}", arguments={}),
        ],
    )
    text = Example.of(trace).describe()
    assert KEY[:12] not in text, text
    assert "[redacted]" in text


def test_the_secret_scan_reads_nested_arguments_parts_and_tool_names() -> None:
    def run(**kw) -> Trace:
        return Trace(scenario_id="s", model_id="m", final_output="ok", **kw)

    cases = {
        "nested dict": run(tool_calls=[ToolCall(name="f", arguments={"a": {"b": KEY}})]),
        "list": run(tool_calls=[ToolCall(name="f", arguments={"a": ["x", KEY]})]),
        "tool name": run(tool_calls=[ToolCall(name=f"f_{KEY}", arguments={})]),
        "gemini parts": run(messages=[{"role": "user", "parts": [{"text": f"use {KEY}"}]}]),
    }
    for label, trace in cases.items():
        assert secret_bearing_scenarios({"s": [trace]}) == {"s": 1}, label
    clean = run(tool_calls=[ToolCall(name="f", arguments={"a": {"b": ["fine"]}})])
    assert secret_bearing_scenarios({"s": [clean]}) == {}


def test_check_redacts_the_quotes_keeps_the_run_and_warns(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    scenario = {
        "id": "refund",
        "name": "refund",
        "input": {"messages": [{"role": "user", "content": "Refund order 7?"}]},
    }
    (tmp_path / "scenarios" / "refund.json").write_text(json.dumps(scenario), encoding="utf-8")
    (tmp_path / "traces.json").write_text(
        json.dumps(
            [
                {"scenario_id": "refund", "model_id": "m1", "final_output": "Refund issued."},
                {
                    "scenario_id": "refund",
                    "model_id": "m2",
                    "final_output": f"I can't do that. Debug: {KEY}",
                    "refused": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["baseline", "--fixtures", "traces.json"]).exit_code == 0
    res = runner.invoke(app, ["check", "--to", "m2", "--fixtures", "traces.json"])
    assert res.exit_code == 1, res.output  # a refusal regression; the verdict is untouched
    console = " ".join(res.output.split())  # Rich wraps at 80 columns
    assert "key-shaped string appears in m2's runs for: refund (" in console, console
    assert KEY[:12] not in res.output
    store = tmp_path / ".modelpin"
    for name in ("last-report.md", "migration-report.md"):
        text = (store / name).read_text(encoding="utf-8")
        assert KEY[:12] not in text, name
        assert "[redacted]" in text, name
    runs = [p for p in (store / "runs").glob("*.json")]
    assert runs and any(KEY in p.read_text(encoding="utf-8") for p in runs), "run was rewritten"
