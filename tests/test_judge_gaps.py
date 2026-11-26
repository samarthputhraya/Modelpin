"""SA-01: a judge that gave no clear verdict is disclosed, and the verdict stays fail-open.

MP-322. The judge reads any reply without a clean verdict as "same meaning" (ADR-0004, by
design, so a judge outage cannot manufacture regressions). Until now nothing on the page said
when that had happened, so a run whose judge never answered read as cleared on meaning.

What this pins:
- `_parse_equivalent` returns exactly what it returned before (no verdict or exit code moves);
- each unclear reply is counted, per scenario, by every judge class;
- `check` and the PR comment withhold the "safe to adopt" clearance and name the scenarios,
  with the exit code unchanged (the exit-code contract is frozen; ADR-0054).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.judge import (
    AnthropicJudge,
    GoogleJudge,
    JudgeTally,
    OpenAIJudge,
    _parse_equivalent,
    _read_verdict,
)

runner = CliRunner()

#: (reply, the verdict `_parse_equivalent` has always returned, counted as unclear?)
REPLIES = [
    ('{"equivalent": true, "reason": "same"}', True, False),
    ('{"equivalent": false, "reason": "different"}', False, False),
    ("", True, True),
    ("I can't help with comparing these responses.", True, True),
    ('{"equivalent": false} ... {"equivalent": true}', True, True),
    ('{"equivalent": true} ... {"equivalent": false}', False, False),
    ('{"equivalent": "false"}', True, True),
    ("They differ in substance.\nNot equivalent.", False, False),
    ("Not equivalent, the candidate approves it.\nThat is my answer.", True, True),
    ('<think>{"equivalent": false}</think>{"equivalent": true}', True, False),
]


@pytest.mark.parametrize("reply,verdict,unclear", REPLIES)
def test_the_verdict_is_unchanged_and_an_unclear_reply_is_marked(reply, verdict, unclear):
    assert _parse_equivalent(reply) is verdict
    assert _read_verdict(reply) == (verdict, unclear)


def _openai_client(content: Any) -> Any:
    def create(**_: Any) -> Any:
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_a_filtered_openai_reply_is_counted_per_scenario():
    judge = OpenAIJudge("gpt-judge", client=_openai_client(None))
    judge.tally = tally = JudgeTally()
    tally.scope = "harm"
    assert judge.equivalent("I can't help with that.", "Sure. Step 1: ...") is True
    tally.scope = "fraud"
    judge._client = _openai_client('{"equivalent": false}')
    assert judge.equivalent("Block it.", "Approve it.") is False
    assert tally.gaps() == {"harm": (1, 1)}


def test_a_google_reply_with_no_candidates_is_counted():
    response = SimpleNamespace(candidates=[])
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: response))
    judge = GoogleJudge("gemini-judge", client=client)
    judge.tally = tally = JudgeTally()
    tally.scope = "s"
    assert judge.equivalent("a", "b") is True
    assert tally.gaps() == {"s": (1, 1)}


def test_an_anthropic_refusal_with_no_text_is_counted(monkeypatch):
    response = SimpleNamespace(content=[], stop_reason="refusal")
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    monkeypatch.setattr("modelpin.judge.accepts_sampling", lambda *a: False)
    monkeypatch.setattr("modelpin.judge.api_version", lambda *a: None)
    judge = AnthropicJudge("claude-judge", client=client)
    judge.tally = tally = JudgeTally()
    tally.scope = "s"
    assert judge.equivalent("a", "b") is True
    assert tally.gaps() == {"s": (1, 1)}


def _project(root: Path) -> None:
    (root / "scenarios").mkdir()
    (root / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n"
        "judge_model: gpt-judge\n",
        encoding="utf-8",
    )
    for sid, question in (("fraud", "Is txn 991 fraud?"), ("refund", "Refund order 7?")):
        scenario = {
            "id": sid,
            "name": sid,
            "input": {"messages": [{"role": "user", "content": question}]},
        }
        (root / "scenarios" / f"{sid}.json").write_text(json.dumps(scenario), encoding="utf-8")
    traces = [
        {"scenario_id": "fraud", "model_id": "m1", "final_output": "Block it: fraud detected."},
        {"scenario_id": "fraud", "model_id": "m2", "final_output": "Looks fine, approve it."},
        {"scenario_id": "refund", "model_id": "m1", "final_output": "Refund issued."},
        {"scenario_id": "refund", "model_id": "m2", "final_output": "Refund issued."},
    ]
    (root / "traces.json").write_text(json.dumps(traces), encoding="utf-8")


def _check(monkeypatch, root: Path, reply: Any) -> tuple[int, str, str]:
    _project(root)
    monkeypatch.chdir(root)
    base = runner.invoke(app, ["baseline", "--fixtures", "traces.json"])
    assert base.exit_code == 0, base.output
    judge = OpenAIJudge("gpt-judge", client=_openai_client(reply))
    monkeypatch.setattr(cli, "_build_judge", lambda *a, **k: judge)
    res = runner.invoke(app, ["check", "--to", "m2", "--fixtures", "traces.json", "--no-confirm"])
    report = (root / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    return res.exit_code, res.output, report


def test_a_judge_that_never_answered_withholds_the_clearance_and_keeps_exit_0(
    tmp_path, monkeypatch
):
    code, output, report = _check(monkeypatch, tmp_path, None)
    # Exit code and verdicts unchanged: fail-open stays (ADR-0004), the contract is frozen.
    assert code == 0, output
    assert "looks safe to adopt" not in report
    assert "NOT cleared on meaning" in report and "`fraud`" in report
    assert "no clear verdict on 5 of 5 comparison(s) in 1 scenario(s)" in report, report
    # `refund` matches the baseline text exactly, so it never reached the judge.
    assert "`refund`" not in report.split("NOT cleared on meaning")[1]
    assert "NOT cleared on meaning" in output
    migration = (tmp_path / ".modelpin" / "migration-report.md").read_text(encoding="utf-8")
    assert "NOT cleared on meaning" in migration


def test_a_judge_that_answered_still_clears_the_run(tmp_path, monkeypatch):
    code, output, report = _check(monkeypatch, tmp_path, '{"equivalent": true}')
    assert code == 0, output
    assert "looks safe to adopt" in report
    assert "no clear verdict" not in report and "no clear verdict" not in output


def test_the_published_report_withholds_its_clearance_on_a_judge_gap():
    from modelpin.models import DiffResult, DiffVerdict
    from modelpin.report import ReportMeta, render_report_md

    def meta(**gaps: Any) -> ReportMeta:
        return ReportMeta(
            suite_id="suite",
            suite_version="1.0.0",
            suite_hash="sha256:0",
            suite_path="suite",
            candidate_model="m2",
            reference_model="m1",
            provider="openai",
            runs=5,
            judge_model="gpt-judge",
            match_mode="strict",
            modelpin_version="0.0.0",
            diff_thresholds={
                "alpha": 0.05,
                "min_tool_tvd": 0.5,
                "min_refusal_delta": 0.3,
                "min_semantic_delta": 0.3,
            },
            date_iso="2026-11-26",
            reproduce_cmd="modelpin report",
            **gaps,
        )

    results = [
        DiffResult(
            scenario_id="fraud", from_model="m1", to_model="m2", verdict=DiffVerdict.unchanged
        )
    ]
    assert "no clear verdict" not in render_report_md(results, meta())
    page = render_report_md(results, meta(judge_gaps={"fraud": (5, 5)}))
    assert "no clear verdict on 5 of 5 comparison(s) in 1 scenario(s)" in page, page
    assert "NOT cleared on meaning for: `fraud`" in page
