"""The migration report: the same `check` told to the person deciding whether to switch models.

Pinned here: a plain-English bottom line that follows the verdicts (never clearer than the run
was); each flagged scenario with what the current model did and what the replacement did; what
the run did not cover; the facts of the run; model output escaped; no ranking words (ADR-0009);
and that writing it never changes what `check` decides or exits with.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import _CountingJudge, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, write_demo
from modelpin.models import DiffResult, DiffVerdict
from modelpin.report import ChannelCensus
from modelpin.report.evidence import Example
from modelpin.report.migration import (
    MIGRATION_REPORT_FILENAME,
    MigrationFacts,
    plain_reasons,
    render_migration_report,
)

_BANNED = re.compile(
    r"(?i)\b(better|worse|best|beats|wins|loses|superior|inferior|upgrade|downgrade)\b"
)


def _facts(**over) -> MigrationFacts:
    base = dict(
        date_iso="2026-10-04 12:00 UTC",
        from_model="gpt-4o-2024-05-13",
        to_model="gpt-5.6-sol",
        provider="openai",
        runs=5,
        match_mode="strict",
        modelpin_version="0.5.1",
        judge_model="gemini-3.5-flash",
        baseline_runs=40,
        candidate_runs=45,
        judge_calls=123,
        tokens_in=12345,
        tokens_out=6789,
    )
    base.update(over)
    return MigrationFacts(**base)


def _r(sid: str, verdict: DiffVerdict, explanation: str = "", confidence: float = 0.99):
    return DiffResult(
        scenario_id=sid,
        from_model="gpt-4o-2024-05-13",
        to_model="gpt-5.6-sol",
        verdict=verdict,
        explanation=explanation or "no statistically significant behavior change",
        confidence=confidence,
    )


TOOLS = "tool-call behavior changed: ['lookup_order', 'issue_refund'] -> ['lookup_order']"
REFUSAL = "refusal rate 0% -> 100%"


def _mixed():
    return [
        _r("refund_request", DiffVerdict.regression, TOOLS),
        _r("angry_customer", DiffVerdict.regression, f"{TOOLS}; {REFUSAL}"),
        _r(
            "invoice_parse",
            DiffVerdict.changed_minor,
            "output format drift: violates the scenario's text assertions",
        ),
        _r("greeting", DiffVerdict.unchanged, confidence=1.0),
    ]


def _examples():
    before = Example(("lookup_order(id=7)", "issue_refund(amount=20)"), "Refund issued.", False)
    after = Example(("lookup_order(id=7)",), "I can't do that.", True)
    return {"refund_request": (before, after)}


def test_a_regression_puts_hold_the_switch_in_the_bottom_line() -> None:
    md = render_migration_report(_mixed(), _facts(), examples=_examples())
    bottom = md.splitlines()[2]
    assert bottom.startswith("**Bottom line: Hold the switch to `gpt-5.6-sol`")
    assert "2 scenarios that changed" in bottom


def test_the_verdict_paragraph_names_both_models_and_every_bucket() -> None:
    md = render_migration_report(_mixed(), _facts())
    para = md.splitlines()[4]
    assert "4 scenarios from your app 5 times each" in para
    assert "`gpt-5.6-sol`, the model you are moving to" in para
    assert "`gpt-4o-2024-05-13`, the model you use today" in para
    assert "2 scenarios behaved differently in a way that matters" in para
    assert "`refund_request`, `angry_customer`" in para
    assert "1 changed in a smaller way" in para and "1 behaved the same" in para


def test_each_flagged_scenario_has_plain_reasons_and_before_after() -> None:
    md = render_migration_report(_mixed(), _facts(), examples=_examples())
    assert "### refund\\_request: changed in a way that matters" in md or (
        "### refund_request: changed in a way that matters" in md
    )
    assert "It takes different actions in your app" in md
    assert "It declines requests at a different rate" in md
    assert "Its answers no longer pass the text checks" in md
    assert "**Before** (`gpt-4o-2024-05-13`, a typical run)" in md
    assert "**After** (`gpt-5.6-sol`, a run showing the change)" in md
    assert "issue_refund(amount=20)" in md
    assert "Confidence: 0.99" in md


def test_regressions_come_before_minor_changes() -> None:
    md = render_migration_report(list(reversed(_mixed())), _facts())
    assert md.index("refund") < md.index("invoice")


def test_the_run_table_carries_date_models_runs_calls_and_tokens() -> None:
    md = render_migration_report(_mixed(), _facts())
    for needle in (
        "| Date | 2026-10-04 12:00 UTC |",
        "| Model in use | `gpt-4o-2024-05-13` |",
        "| Model moving to | `gpt-5.6-sol` |",
        "| Runs per scenario | 5 on each model |",
        "| Recorded runs of `gpt-4o-2024-05-13` compared | 40 |",
        "| Runs of `gpt-5.6-sol` made by this check | 45,",
        "`gemini-3.5-flash`, 123 calls",
        "12,345 in, 6,789 out",
    ):
        assert needle in md, needle


def test_a_clean_run_with_full_coverage_says_the_switch_looks_safe_for_those_scenarios() -> None:
    results = [_r("a", DiffVerdict.unchanged, confidence=1.0)]
    census = ChannelCensus(
        tools_exercised=True, assertions_declared=True, judge_enabled=True, compared=1
    )
    md = render_migration_report(results, _facts(), census=census)
    assert "looks safe for them" in md.splitlines()[2]


def test_no_judge_withholds_the_all_clear_and_says_meaning_was_not_compared() -> None:
    results = [_r("a", DiffVerdict.unchanged, confidence=1.0)]
    md = render_migration_report(results, _facts(judge_model=None, judge_calls=None))
    assert "looks safe" not in md
    assert "could not see every kind of change" in md.splitlines()[2]
    assert "Meaning was not compared" in md
    assert "| Judge (compares meaning) | none configured |" in md


def test_an_underpowered_run_is_never_called_clear() -> None:
    results = [_r("a", DiffVerdict.unchanged, confidence=1.0)]
    md = render_migration_report(results, _facts(), underpowered=["a"])
    assert "looks safe" not in md
    assert "could not have reported a regression" in md


def test_unmeasured_and_skipped_scenarios_make_it_incomplete() -> None:
    results = [
        _r("a", DiffVerdict.unchanged, confidence=1.0),
        _r("b", DiffVerdict.insufficient_evidence, "candidate returned no usable output"),
    ]
    md = render_migration_report(results, _facts(), skipped=["c"])
    assert md.splitlines()[2].startswith("**Bottom line: Incomplete")
    assert "could not be measured, which is not a pass" in md
    assert "1 more scenario was not compared at all" in md
    assert "had no USABLE baseline" in md


def test_the_reader_is_told_a_partial_change_can_go_unnoticed() -> None:
    results = [_r("a", DiffVerdict.unchanged, confidence=1.0)]
    md = render_migration_report(results, _facts())
    assert "Each scenario ran 5 times on each model." in md
    assert "only some of those runs can go unnoticed" in md
    assert "(s)" not in md
    assert "We ran 1 scenario from your app" in md


def test_nothing_compared_clears_nothing() -> None:
    md = render_migration_report([], _facts(), skipped=["a"])
    assert "clears nothing" in md.splitlines()[2]


def test_model_output_cannot_inject_markdown() -> None:
    hostile = Example(("escalate[Build passed](https://evil.example)",), "x", False)
    md = render_migration_report(
        _mixed(), _facts(), examples={"refund_request": (hostile, hostile)}
    )
    assert "](https://evil.example)" not in md.replace("`", "") or "`" in md
    line = next(ln for ln in md.splitlines() if "evil.example" in ln)
    assert line.count("`") >= 2, "model-derived text must stay inside a code span"


def test_no_ranking_words_on_any_shape() -> None:
    for results, kw in (
        (_mixed(), {}),
        ([_r("a", DiffVerdict.unchanged, confidence=1.0)], {}),
        ([], {"skipped": ["a"]}),
    ):
        md = render_migration_report(results, _facts(), **kw)
        hit = _BANNED.search(md)
        assert not hit, hit.group(0)


def test_plain_reasons_glosses_each_engine_clause_once() -> None:
    assert plain_reasons(f"{TOOLS}; {REFUSAL}; {TOOLS}") == [
        "It takes different actions in your app: it calls different tools, or calls them in "
        "a different order.",
        "It declines requests at a different rate than the model you use today.",
    ]
    assert plain_reasons("tool-call arguments changed: x -> y")[0].startswith("It calls the same")
    assert plain_reasons("not confirmed: flagged as a regression ...")[0].startswith("It looked")
    assert plain_reasons("something new the engine says") == []


def test_the_counting_judge_counts_and_delegates_without_changing_answers() -> None:
    class Inner:
        parallel_safe = True
        model = "j"

        def equivalent(self, reference, candidate, task=None):
            return reference == candidate

    j = _CountingJudge(Inner())
    threads = [threading.Thread(target=lambda: j.equivalent("a", "a")) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert j.calls == 20
    assert j.equivalent("a", "b") is False and j.calls == 21
    assert j.parallel_safe is True and j.model == "j"


def test_check_writes_the_migration_report_and_keeps_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_demo(tmp_path)
    demo = tmp_path / DEMO_DIRNAME
    monkeypatch.chdir(demo)
    runner = CliRunner()
    assert runner.invoke(app, ["baseline", "--fixtures", DEMO_FIXTURES]).exit_code == 0
    r = runner.invoke(app, ["check", "--to", "demo-model-v2", "--fixtures", DEMO_FIXTURES])
    assert r.exit_code == 1, r.output  # the demo's documented regressions, unchanged
    report = demo / ".modelpin" / MIGRATION_REPORT_FILENAME
    assert report.exists(), r.output
    md = report.read_text(encoding="utf-8")
    assert md.startswith("# Migration check: `demo-model-v1` to `demo-model-v2`")
    assert "**Bottom line: Hold the switch to `demo-model-v2`" in md
    assert "refund_request" in md and "angry_customer" in md
    assert "**Before**" in md and "**After**" in md
    assert "| Runs per scenario | 5 on each model |" in md
    assert "Migration report for a non-engineering reader" in r.output
