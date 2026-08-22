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


def test_a_refusal_is_a_measurement_not_a_degenerate_run():
    """LOAD-BEARING. A content-filter refusal can carry empty text and refused=True; that is
    a complete observation and must stay in the comparison. Dropping the `not refused` clause
    would turn every both-sides-refuse scenario from a correct `unchanged` into an abstention."""
    assert not is_degenerate(Trace(scenario_id="s", model_id="m", refused=True))
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
    [(5, 2, False), (5, 3, True), (4, 2, False), (4, 3, True), (3, 2, True), (1, 1, True)],
)
def test_the_threshold_is_a_strict_majority(n: int, d: int, expect_abstain: bool):
    """2*d > n, never >=. A tie stays usable, matching the engine's standing bias that a
    50/50 flip is noise. The residual band (d below the threshold) is disclosed, not gated --
    see ADR-0018's non-goals."""
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
