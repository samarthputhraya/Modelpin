"""MP-39: README's sample of the pre-spend lines is what `modelpin check` actually prints.

`[M] 2026-11-27` README's "Cost" section quotes two lines `check` prints before it spends, with
three numbers a reader budgets from (40 replays, up to 360 judge calls, up to 45 per flagged
scenario). Only one README fence is executed in CI (`<!-- mp:smoke -->`), and none of these
numbers was read from README by any test: `tests/test_first_run_cost.py` pins the same values
against `_replay_plan` itself, so README could drift alone.

This drives the real `check` over the 8-scenario held-out suite at 5 runs against a 5-run
baseline, with no key set: both lines print, then the provider preflight stops the run (exit 4)
before anything is called. The README lines must match what was printed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.models import Trace
from modelpin.scenarios import load_scenarios
from modelpin.storage import save_baseline

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
SUITE = REPO / "examples" / "suite"

runner = CliRunner()


def _flat(text: str) -> str:
    return " ".join(text.split())


def _readme_cost_lines() -> list[str]:
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## Cost") :]
    fence = re.search(r"```text\n(.*?)\n```", section, re.S)
    assert fence, "README's Cost section no longer holds a text fence of the pre-spend lines"
    return [line for line in fence.group(1).splitlines() if line.strip()]


def test_readme_quotes_the_pre_spend_lines_check_prints(tmp_path: Path, monkeypatch) -> None:
    if not SUITE.is_dir():  # pragma: no cover - the suite ships in the sdist
        pytest.skip("examples/suite is not present")
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SUITE, tmp_path / "scenarios", ignore=shutil.ignore_patterns("*.md"))
    (tmp_path / "modelpin.yaml").write_text(
        "models: [gpt-4o-mini]\nproviders: [openai]\njudge_model: gpt-4.1-mini\nruns: 5\n",
        encoding="utf-8",
    )
    # A 5-run baseline for every scenario, written directly: no fixture file is needed for
    # a run that stops before its first call.
    scenarios = load_scenarios(tmp_path / "scenarios")
    assert len(scenarios) == 8, [s.id for s in scenarios]
    save_baseline(
        {
            s.id: [
                Trace(scenario_id=s.id, model_id="gpt-4o-mini", run_idx=i, final_output="ok")
                for i in range(5)
            ]
            for s in scenarios
        },
        "gpt-4o-mini",
        tmp_path / ".modelpin",
    )

    result = runner.invoke(
        app, ["check", "--from", "gpt-4o-mini", "--to", "gpt-4.1", "--provider", "openai"]
    )
    printed = _flat(result.output)
    assert result.exit_code == 4, result.output  # stopped at the key, before any call
    lines = _readme_cost_lines()
    assert len(lines) == 2, lines
    for line in lines:
        assert _flat(line) in printed, (
            f"README's Cost sample says:\n  {line}\n"
            f"but `modelpin check` printed:\n  {result.output}"
        )
