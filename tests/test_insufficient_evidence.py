"""A run that measured nothing must never read as "no change" (MP-49, ADR-0018).

`FakeProvider` fabricating a trace was MP-28. This is its live-path twin: a real provider
returning an empty, truncated, filtered or rate-limited response produced a perfectly valid
`Trace`, and two such traces compared as `unchanged` at confidence **1.00** -- the engine's
least-evidence case scoring as its most confident.

The suite had NO test feeding a degenerate trace into `diff_scenario` before this file, which
is why the bug shipped. Every test here fails on the pre-fix engine.
"""

from __future__ import annotations

import pytest

from modelpin.diff import diff_scenario
from modelpin.diff.structural import degenerate_count, is_degenerate
from modelpin.models import DiffVerdict, IncompleteReason, Scenario, ToolCall, Trace


def _runs(model: str, n: int = 5, **kw) -> list[Trace]:
    return [Trace(scenario_id="s", model_id=model, run_idx=i, **kw) for i in range(n)]


def _empty(model: str, n: int = 5) -> list[Trace]:
    return _runs(model, n)


def _text(model: str, n: int = 5, out: str = "Paris is the capital.") -> list[Trace]:
    return _runs(model, n, final_output=out)


def _tools(model: str, n: int = 5) -> list[Trace]:
    return _runs(model, n, final_output="done", tool_calls=[ToolCall(name="lookup_order")])


# --- the predicate ---------------------------------------------------------------------


def test_a_run_with_nothing_recorded_is_degenerate():
    assert is_degenerate(Trace(scenario_id="s", model_id="m"))
    assert is_degenerate(Trace(scenario_id="s", model_id="m", final_output="   "))


def test_a_refusal_with_no_text_is_not_degenerate():
    """LOAD-BEARING. A content-filter refusal carries empty text AND refused=True -- that is a
    complete observation, not an absence of one."""
    assert not is_degenerate(Trace(scenario_id="s", model_id="m", refused=True))


def test_both_sides_refusing_still_compares_as_unchanged():
    """The consequence of the clause above, asserted separately so a mutation that drops
    `not refused` fails on BOTH the predicate and the verdict it protects."""
    base, cand = _runs("o", refused=True), _runs("n", refused=True)
    assert diff_scenario("s", "o", "n", base, cand).verdict == DiffVerdict.unchanged


def test_output_or_a_tool_call_means_the_run_recorded_something():
    assert not is_degenerate(Trace(scenario_id="s", model_id="m", final_output="hi"))
    assert not is_degenerate(Trace(scenario_id="s", model_id="m", tool_calls=[ToolCall(name="x")]))


def test_degenerate_count_counts_only_silent_runs():
    assert degenerate_count(_empty("m", 3) + _text("m", 2)) == 3


# --- the verdict -----------------------------------------------------------------------


def test_both_sides_silent_is_not_unchanged():
    """The headline: pre-fix this was `unchanged` at confidence 1.00."""
    r = diff_scenario("s", "o", "n", _empty("o"), _empty("n"))
    assert r.verdict == DiffVerdict.insufficient_evidence
    assert r.confidence == 0.0


def test_a_silent_candidate_against_a_text_baseline_is_not_unchanged():
    """The case nothing caught. With tools the tool signal fired incidentally; on a plain
    Q&A scenario a candidate losing 100% of its output read `unchanged` at 1.00."""
    r = diff_scenario("s", "o", "n", _text("o"), _empty("n"))
    assert r.verdict == DiffVerdict.insufficient_evidence
    assert "candidate" in r.explanation


def test_abstention_outranks_a_regression_it_cannot_substantiate():
    """Pre-fix this published `tool-call behavior changed: ['lookup_order'] -> []` at 0.992
    about a candidate that recorded no behavior at all. The verdict is demoted deliberately;
    CI still fails, because insufficient_evidence exits 3."""
    r = diff_scenario("s", "o", "n", _tools("o"), _empty("n"))
    assert r.verdict == DiffVerdict.insufficient_evidence
    assert "tool-call behavior changed" not in r.explanation


def test_the_explanation_names_the_failing_side_and_its_remedy():
    """A wrong remedy loops the user: a bad baseline needs re-recording, a bad candidate a re-run."""
    base_bad = diff_scenario("s", "o", "n", _empty("o"), _text("n"))
    assert "baseline" in base_bad.explanation and "modelpin baseline" in base_bad.explanation
    cand_bad = diff_scenario("s", "o", "n", _text("o"), _empty("n"))
    assert "candidate" in cand_bad.explanation and "re-run" in cand_bad.explanation


@pytest.mark.parametrize(
    ("n", "d", "expect_abstain"),
    [
        (5, 2, False),  # 0.40 - below half, verdict still computed
        (5, 3, True),  # 0.60
        (4, 2, True),  # 0.50 - the TIE abstains
        (6, 3, True),  # 0.50
        (8, 4, True),  # 0.50 - pre-gate this published `regression` @ 0.962
        (8, 3, False),  # 0.375
        (3, 2, True),
        (1, 1, True),
    ],
)
def test_a_side_is_unusable_at_or_above_half_degenerate(n: int, d: int, expect_abstain: bool):
    """``2*d >= n``, inclusive.

    The tie is load-bearing, not a rounding preference. The tool TVD and the semantic delta
    are each exactly ``d/n`` when a healthy baseline meets d silent runs, and both floors are
    0.5 -- so d/n == 0.5 is precisely where measurement failure starts clearing the floors by
    itself. An earlier strict-majority draft let n=8,d=4 publish `regression` at 0.962.
    """
    cand = _empty("n", d) + _text("n", n - d)
    r = diff_scenario("s", "o", "n", _text("o", n), cand)
    assert (r.verdict == DiffVerdict.insufficient_evidence) is expect_abstain


def test_empty_trace_lists_abstain_rather_than_reporting_a_verdict():
    for base, cand in ((_text("o"), []), ([], _text("n")), ([], [])):
        r = diff_scenario("s", "o", "n", base, cand)
        assert r.verdict == DiffVerdict.insufficient_evidence, (base, cand)
        assert r.confidence == 0.0


def test_a_healthy_comparison_is_untouched():
    """The gate must not fire on real data -- this is the false-positive guard."""
    r = diff_scenario("s", "o", "n", _text("o"), _text("n"))
    assert r.verdict == DiffVerdict.unchanged
    assert r.confidence == 1.0


def test_the_gate_runs_before_the_judge_so_garbage_costs_no_tokens():
    """The judge is a paid call. An abstaining run must never reach it."""
    calls: list[tuple[str, str]] = []

    class _CountingJudge:
        def equivalent(self, task: str, reference: str, candidate: str) -> bool:
            calls.append((reference, candidate))
            return True

    scenario = Scenario(id="s", name="s", input={"messages": []})
    diff_scenario("s", "o", "n", _empty("o"), _empty("n"), scenario, "strict", _CountingJudge())
    assert calls == [], f"judge was billed {len(calls)} call(s) on a run that measured nothing"


def test_the_counters_are_populated_so_degradation_below_the_gate_is_visible():
    r = diff_scenario("s", "o", "n", _text("o"), _empty("n", 2) + _text("n", 3))
    assert r.verdict == DiffVerdict.unchanged  # below the threshold, deliberately
    assert r.signals.degenerate_candidate == 2
    assert r.signals.candidate_runs == 5


# --- the recorded reason (Group B) -------------------------------------------------------


def test_incomplete_reason_defaults_to_none_and_gates_nothing():
    """Recording only, ADR-0003 shape. A truncated-but-answering run still gets a verdict."""
    base = _runs("o", final_output="Paris.")
    cand = _runs("n", final_output="Paris.", incomplete_reason=IncompleteReason.max_tokens)
    assert diff_scenario("s", "o", "n", base, cand).verdict == DiffVerdict.unchanged


def test_an_unknown_reason_degrades_instead_of_corrupting_a_baseline():
    """A newer Modelpin's baseline must not read as CORRUPT to an older one -- storage turns
    a ValidationError into "delete it and re-run", i.e. the user loses their recording."""
    t = Trace(scenario_id="s", model_id="m", incomplete_reason="invented_in_a_later_version")
    assert t.incomplete_reason == IncompleteReason.provider_other


def test_a_pre_existing_baseline_still_loads():
    assert Trace(**{"scenario_id": "s", "model_id": "m"}).incomplete_reason is None


# --- the paths the "CI recall is unchanged" claim rests on (fp-guardian C3) --------------


def test_check_exits_three_when_a_scenario_could_not_be_measured(tmp_path):
    """The whole "CI still fails" argument is this one exit code.

    3, not 1 (a regression is a different claim) and not 2 (Click already returns 2 for a
    usage error). `action.yml` gates on `code != '0'`, so CI fails either way -- but a caller
    that cannot tell "it broke" from "we could not tell" is back at MP-49.
    """
    import json

    from typer.testing import CliRunner

    from modelpin.cli import EXIT_UNMEASURED, app

    assert EXIT_UNMEASURED == 3
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "s.json").write_text(
        '{"id":"cap","name":"Cap","input":{"messages":[{"role":"user","content":"hi"}]}}',
        encoding="utf-8",
    )
    fx = tmp_path / "fx.json"
    fx.write_text(
        json.dumps(
            [{"scenario_id": "cap", "model_id": "old", "final_output": "Paris."}] * 5
            + [{"scenario_id": "cap", "model_id": "new", "final_output": ""}] * 5
        ),
        encoding="utf-8",
    )
    common = [
        "--provider",
        "fake",
        "--fixtures",
        str(fx),
        "--scenarios-dir",
        str(scen),
        "--store-dir",
        str(tmp_path / ".s"),
        "--runs",
        "5",
    ]
    runner = CliRunner()
    base = runner.invoke(app, ["baseline", "--model", "old", *common])
    assert base.exit_code == 0, base.output
    chk = runner.invoke(app, ["check", "--to", "new", "--from", "old", *common])
    assert chk.exit_code == 3, f"expected EXIT_UNMEASURED, got {chk.exit_code}: {chk.output}"


def test_a_report_containing_an_abstention_never_says_safe_to_adopt():
    """MP-49's actual harm was this sentence rendering over a run that measured nothing.

    `test_report.py` asserts the line is PRESENT for an all-unchanged run; nothing asserted it
    was ABSENT here, so a refactor could restore the false claim silently.
    """
    from modelpin.report import render_pr_comment

    results = [diff_scenario("s", "o", "n", _text("o"), _empty("n"))]
    md = render_pr_comment(results, "o", "n", 5, provider="openai")
    assert "safe to adopt" not in md
    assert "NOT cleared" in md
    assert "could not measure" in md.lower()
