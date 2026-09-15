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


def test_a_baseline_keeps_every_scenario_it_could_record(tmp_path, monkeypatch):
    """One scenario the provider rejects no longer throws away the others (exit 3, not 4)."""
    from modelpin import cli
    from modelpin.models import Trace
    from modelpin.providers.base import ProviderAdapter, ProviderError
    from modelpin.storage import load_baseline

    class _RejectsOne(ProviderAdapter):
        def run(self, scenario, model_id, run_idx=0):
            if scenario.id == "bad":
                raise ProviderError("blocked by the provider")
            return Trace(
                scenario_id=scenario.id, model_id=model_id, run_idx=run_idx, final_output="ok"
            )

    scen = tmp_path / "scenarios"
    scen.mkdir()
    for sid in ("good", "bad"):
        (scen / f"{sid}.json").write_text(
            json.dumps(
                {"id": sid, "name": sid, "input": {"messages": [{"role": "user", "content": sid}]}}
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(cli, "_adapter", lambda provider, fixtures: _RejectsOne())
    result = runner.invoke(
        app, ["baseline", "--model", "m", "--provider", "openai", "--scenarios-dir", str(scen),
              "--store-dir", str(tmp_path / "store")],
    )  # fmt: skip
    assert result.exit_code == 3, result.output
    assert set(load_baseline("m", tmp_path / "store")) == {"good"}
    assert "NOT recorded" in " ".join(result.output.split())


def test_a_baseline_that_records_nothing_is_a_setup_error(tmp_path, monkeypatch):
    from modelpin import cli
    from modelpin.providers.base import ProviderAdapter, ProviderError

    class _RejectsAll(ProviderAdapter):
        def run(self, scenario, model_id, run_idx=0):
            raise ProviderError("model not found [404]")

    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "s.json").write_text(
        json.dumps(
            {"id": "s", "name": "s", "input": {"messages": [{"role": "user", "content": "x"}]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_adapter", lambda provider, fixtures: _RejectsAll())
    result = runner.invoke(
        app, ["baseline", "--model", "m", "--provider", "openai", "--scenarios-dir", str(scen),
              "--store-dir", str(tmp_path / "store")],
    )  # fmt: skip
    assert result.exit_code == 4, result.output
    assert "404" in result.output
