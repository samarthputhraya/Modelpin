"""`mp check` writes a machine-readable run record beside its archived report.

Before 0.4.1 the candidate runs a check paid for were discarded when the process exited, and the
only record of the verdicts was Markdown. A reviewer could not inspect what the candidate said
beyond one example, and a script could not read a verdict without parsing prose. Offline.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()


def _demo_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "modelpin-demo")
    assert runner.invoke(app, ["baseline", "--fixtures", "traces.json"]).exit_code == 0
    return runner.invoke(app, ["check", "--to", "demo-model-v2", "--fixtures", "traces.json"])


def test_the_run_record_holds_every_verdict_and_candidate_run(tmp_path, monkeypatch):
    result = _demo_check(tmp_path, monkeypatch)
    assert result.exit_code == 1, result.output
    records = sorted((tmp_path / "modelpin-demo" / ".modelpin" / "runs").glob("check-*.json"))
    assert len(records) == 1, records
    record = json.loads(records[0].read_text(encoding="utf-8"))

    assert record["exit_code"] == 1
    assert (record["from_model"], record["to_model"]) == ("demo-model-v1", "demo-model-v2")
    verdicts = {r["scenario_id"]: r["verdict"] for r in record["results"]}
    assert verdicts == {
        "greeting": "unchanged",
        "refund_request": "regression",
        "angry_customer": "regression",
        "invoice_parse": "changed_minor",
    }
    runs = record["candidate_runs"]
    assert set(runs) == set(verdicts)
    # A confirmed regression carries both samples: 5 first runs + 5 fresh runs.
    assert len(runs["refund_request"]) == 10 and len(runs["greeting"]) == 5
    assert all("messages" not in t for side in runs.values() for t in side), "no prompts"


def test_the_record_is_next_to_its_report_and_ignored_by_git(tmp_path, monkeypatch):
    _demo_check(tmp_path, monkeypatch)
    runs = tmp_path / "modelpin-demo" / ".modelpin" / "runs"
    (md,) = runs.glob("check-*.md")
    assert md.with_suffix(".json").is_file()
    assert "!baseline-*.json" in (runs.parent / ".gitignore").read_text(encoding="utf-8")
