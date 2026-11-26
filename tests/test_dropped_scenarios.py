"""A baselined scenario missing from the run is named, never silently dropped (MP-333).

`[M] 2026-11-26` audit: deleting, renaming or moving a baselined scenario's file (into
`scenarios/.archive/`, say) took a run from two regressions, exit 1, to "looks safe to adopt",
exit 0. Nothing on the console or in the PR comment mentioned the scenarios that had gone, so
a reviewer saw a clean check over a suite that had lost exactly the cases that were failing.
The exit code stays as it is (the exit-code contract is frozen; ADR-0054); the clearance is
withheld and the scenarios are named.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()


def _project(root: Path) -> None:
    (root / "scenarios").mkdir(parents=True)
    (root / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    traces = []
    for sid, base, cand in (
        ("kept", "Refund issued.", "Refund issued."),
        ("refund_policy", "Refund issued.", "I can't help with refunds."),
    ):
        scenario = {
            "id": sid,
            "name": sid,
            "input": {
                "messages": [{"role": "user", "content": "Refund order 7?"}],
                "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            },
        }
        (root / "scenarios" / f"{sid}.json").write_text(json.dumps(scenario), encoding="utf-8")
        call = [{"name": "lookup", "arguments": {}}]
        traces.append(
            {"scenario_id": sid, "model_id": "m1", "final_output": base, "tool_calls": call}
        )
        traces.append(
            {
                "scenario_id": sid,
                "model_id": "m2",
                "final_output": cand,
                "refused": cand.startswith("I can't"),
                "tool_calls": call,
            }
        )
    (root / "traces.json").write_text(json.dumps(traces), encoding="utf-8")


def _check(root: Path, monkeypatch) -> tuple[int, str, str]:
    monkeypatch.chdir(root)
    res = runner.invoke(app, ["check", "--to", "m2", "--fixtures", "traces.json"])
    report = (root / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    return res.exit_code, " ".join(res.output.split()), report


def _baselined(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    _project(root)
    monkeypatch.chdir(root)
    assert runner.invoke(app, ["baseline", "--fixtures", "traces.json"]).exit_code == 0
    return root


def test_a_deleted_baselined_scenario_is_named_and_the_run_is_not_cleared(
    tmp_path: Path, monkeypatch
) -> None:
    root = _baselined(tmp_path, monkeypatch)
    assert _check(root, monkeypatch)[0] == 1, "the control run must see the regression"
    (root / "scenarios" / "refund_policy.json").unlink()
    code, console, report = _check(root, monkeypatch)
    assert code == 0, "the exit code is unchanged by this fix"
    assert "looks safe to adopt" not in report, report
    assert "not in this run, so `m2` was NOT checked on them: `refund_policy`" in report
    assert "in the baseline for m1 are not in this run and were NOT checked: refund_policy" in (
        console
    )


def test_moving_it_into_an_archive_folder_is_named_too(tmp_path: Path, monkeypatch) -> None:
    root = _baselined(tmp_path, monkeypatch)
    (root / "scenarios" / ".archive").mkdir()
    shutil.move(root / "scenarios" / "refund_policy.json", root / "scenarios" / ".archive")
    _, _, report = _check(root, monkeypatch)
    assert "`refund_policy`" in report and "looks safe to adopt" not in report


def test_a_run_with_every_baselined_scenario_still_clears(tmp_path: Path, monkeypatch) -> None:
    root = _baselined(tmp_path, monkeypatch)
    traces = json.loads((root / "traces.json").read_text(encoding="utf-8"))
    for t in traces:
        if t["model_id"] == "m2":
            t["final_output"], t["refused"] = "Refund issued.", False
    (root / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    code, console, report = _check(root, monkeypatch)
    assert code == 0 and "looks safe to adopt" in report, report
    assert "were NOT checked" not in console
