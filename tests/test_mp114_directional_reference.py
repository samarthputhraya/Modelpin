"""MP-114: a directional mode must score the candidate against the baseline's REPERTOIRE.

`[M] 2026-09-16` Filed as a reporting defect -- `Arg match` publishing **1.00**, "identical",
over payload sets that demonstrably differ. Reproduction found the larger half: the same
quantity gates the verdict, so it was a false NEGATIVE in the build gate.

The mechanism. Under `subset`/`superset` the statistic was

    tvd = max(0.0, mean(candidate violations) - mean(baseline violations))

where both sides were scored against `modal_sequence(baseline)` -- ONE representative run,
from a function whose own docstring says it is "for human-readable explanations". A baseline
that jitters therefore violates its own mode, and that self-violation rate was subtracted
from the candidate's. Measured, with the candidate calling `delete_account` on 10 of 10 runs,
which `subset` explicitly forbids:

    quiet baseline   -> Tool match 0.00, regression,  exit 1
    jittery baseline -> Tool match 0.60, unchanged,   exit 0

The permutation test had already found it significant (p = 0.046). Only the effect size was
deflated, and only by the baseline's own noise.

The fix moves no calibrated constant. The reference becomes what the relation is actually
about -- the UNION of baseline runs for `subset` (nothing in the candidate may be absent
from anything the baseline ever did) and their INTERSECTION for `superset` (the candidate
must keep what the baseline did on every run).

And each baseline run is scored against a reference built from the OTHER n-1 runs, so
neither side is graded against a reference it helped build. That second half is not
decoration: the version without it was BLOCKED in review for taking `arg_freetext_note`
from 0 of 90 same-model trials flagged to 9 of 90, because a free-text payload is novel on
every run and the candidate alone was being scored out-of-sample.
"""

from __future__ import annotations

from collections import Counter

import pytest

from modelpin.diff import MIN_TOOL_TVD, diff_scenario
from modelpin.diff.structural import relation_arg_reference, relation_reference
from modelpin.models import DiffVerdict, ToolCall, Trace

DIRECTIONAL = ("subset", "superset")


def _name_runs(sequences: list[list[str]]) -> list[Trace]:
    return [
        Trace(
            scenario_id="s",
            model_id="m",
            run_idx=i,
            tool_calls=[ToolCall(name=n, arguments={}) for n in seq],
            final_output="ok",
        )
        for i, seq in enumerate(sequences)
    ]


def _arg_runs(amounts: list[float]) -> list[Trace]:
    return [
        Trace(
            scenario_id="s",
            model_id="m",
            run_idx=i,
            tool_calls=[ToolCall(name="issue_refund", arguments={"amount": a})],
            final_output="ok",
        )
        for i, a in enumerate(amounts)
    ]


# --- the reference itself -------------------------------------------------------------


def test_subset_reference_is_the_union_of_everything_baseline_did() -> None:
    base = _name_runs([["search"], ["search", "a"], ["search", "b"]])
    assert sorted(relation_reference(base, "subset")) == ["a", "b", "search"]


def test_superset_reference_is_only_what_baseline_did_on_every_run() -> None:
    base = _name_runs([["search"], ["search", "a"], ["search", "b"]])
    assert sorted(relation_reference(base, "superset")) == ["search"]


def test_the_reference_keeps_multiplicity() -> None:
    """`subset` compares multisets: two `search` calls in one run is not one call."""
    base = _name_runs([["search"], ["search", "search"]])
    assert sorted(relation_reference(base, "subset")) == ["search", "search"]
    assert sorted(relation_reference(base, "superset")) == ["search"]


def test_an_empty_baseline_has_an_empty_reference() -> None:
    assert relation_reference([], "subset") == ()
    assert relation_arg_reference([], "superset") == ()


def test_a_run_that_called_no_tool_does_not_erase_the_superset_reference() -> None:
    """`[M] 2026-09-16` Found by pricing, not by review. Intersecting over EVERY run let one
    empty baseline run in five collapse the reference to nothing, which silenced the whole
    channel: 2 real detections in the committed corpus went from `regression` to `unchanged`
    (`tc_archive_two_box_request`, `tc_tailoring_consent_log`). A run that called no tool is
    the absence of evidence about which calls are always made, not evidence of absence."""
    base = _name_runs([[], ["request_box", "request_box"], ["request_box", "request_box"]])
    assert sorted(relation_reference(base, "superset")) == ["request_box", "request_box"]


def test_a_baseline_that_never_called_a_tool_still_yields_an_empty_reference() -> None:
    """Dropping empty runs must not turn "no evidence at all" into a confident reference."""
    assert relation_reference(_name_runs([[], [], []]), "superset") == ()


def test_a_superset_run_that_dropped_an_always_present_call_is_caught_despite_an_empty_run() -> (
    None
):
    base = _name_runs([[], ["log_consent", "record"], ["log_consent", "record"],
                       ["log_consent", "record"], ["log_consent", "record"]])  # fmt: skip
    cand = _name_runs([["get_garment", "record"]] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert r.verdict is DiffVerdict.regression, r.explanation


def test_a_superset_signal_that_required_nothing_is_not_published_as_identical() -> None:
    """MP-114 as filed, the vacuous-1.00 half: a baseline that shares no call across its runs
    requires nothing, so no candidate run can violate it. `None` renders as `—`, not `1.00`."""
    base = _name_runs([["a"], ["b"], ["c"], ["d"], ["e"]])
    cand = _name_runs([["z"]] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert relation_reference(base, "superset") == ()
    assert r.signals.tool_call_match is None, r.signals


@pytest.mark.parametrize("mode", DIRECTIONAL)
def test_a_baseline_run_is_never_scored_against_a_reference_it_helped_build(mode: str) -> None:
    """The property the whole fix rests on, and the one an earlier version got wrong.

    Scoring the baseline against the full envelope makes its violation rate 0 by
    construction while the candidate is scored out-of-sample -- which is what fires the
    gate on novelty alone. Each baseline run must see a reference built from the OTHERS.
    """
    from modelpin.diff.structural import leave_one_out_references

    base = _name_runs([["search"], ["search", "a"], ["search", "b"], ["search", "a", "b"]])
    refs = leave_one_out_references(base, mode)
    assert len(refs) == len(base)
    for i, ref in enumerate(refs):
        others = [t for j, t in enumerate(base) if j != i]
        assert ref == relation_reference(others, mode), i
        assert Counter(ref) == Counter(relation_reference(others, mode))


def test_a_behaviour_seen_once_is_not_treated_as_the_baseline_repertoire() -> None:
    """What leave-one-out buys, stated as behaviour: a call the baseline made on exactly
    one run does not survive its own exclusion, so it is not part of what the candidate is
    licensed to do. Seen twice, it is."""
    from modelpin.diff.structural import leave_one_out_references

    once = _name_runs([["search", "rare"], ["search"], ["search"], ["search"]])
    twice = _name_runs([["search", "rare"], ["search", "rare"], ["search"], ["search"]])
    assert "rare" not in leave_one_out_references(once, "subset")[0]
    assert "rare" in leave_one_out_references(twice, "subset")[0]


def test_a_free_text_argument_that_is_novel_every_run_is_not_a_false_positive() -> None:
    """THE regression test for the blocked version of this fix.

    `[M] 2026-09-16` measured on the committed same-model corpus: without leave-one-out,
    `arg_freetext_note` went from 0 of 90 trials flagged to **9 of 90** at confidence 0.996.
    A free-text payload is different on every run on BOTH sides, so scoring the candidate
    against a reference the baseline built makes a violation rate of 1.0 the ordinary
    outcome under a true null. The earlier north-star test could not see this: its
    repertoire had two elements, which five runs almost surely cover.
    """
    notes = [
        "customer says the item arrived damaged",
        "the buyer reports damage on arrival",
        "item was damaged when it arrived, per the customer",
        "customer reported the package arrived damaged",
        "damage on arrival reported by the customer",
        "the customer notes the item came damaged",
        "arrived damaged according to the buyer",
        "buyer says the goods were damaged in transit",
        "reported damaged on delivery by the customer",
        "the item arrived in a damaged state, customer says",
    ]

    def runs(texts: list[str]) -> list[Trace]:
        return [
            Trace(
                scenario_id="s",
                model_id="m",
                run_idx=i,
                tool_calls=[ToolCall(name="log_note", arguments={"note": text})],
                final_output="ok",
            )
            for i, text in enumerate(texts)
        ]

    # Same model both sides: every run paraphrases, nothing behavioural changed.
    base, cand = runs(notes[:5]), runs(notes[5:])
    for mode in DIRECTIONAL:
        r = diff_scenario("s", "old", "new", base, cand, mode=mode)
        assert r.verdict is DiffVerdict.unchanged, (
            f"mode={mode}: a paraphrase on every run is not a behaviour change "
            f"({r.verdict.value}, Arg match {r.signals.tool_arg_match}): {r.explanation}"
        )


# --- the false negative, which is the part that failed a real build ------------------


def test_baseline_jitter_no_longer_hides_a_forbidden_call() -> None:
    """The headline reproduction. Identical candidate; only the baseline's noise differs."""
    candidate = _name_runs([["search", "delete_account"]] * 10)
    quiet = _name_runs([["search"]] * 10)
    jittery = _name_runs([["search"]] * 4 + [["search", "a"]] * 3 + [["search", "b"]] * 3)

    for label, base in (("quiet", quiet), ("jittery", jittery)):
        r = diff_scenario("s", "old", "new", base, candidate, mode="subset")
        assert r.verdict is DiffVerdict.regression, f"{label} baseline: {r.verdict} {r.signals}"
        assert r.signals.tool_call_match == 0.0, label


def test_the_published_arg_match_no_longer_reads_identical_over_differing_payloads() -> None:
    """MP-114 as originally filed: the number a reviewer reads."""
    base = _arg_runs([49.99, 49.99, 49.99, 50.00, 50.00])
    cand = _arg_runs([49.99, 49.99, 49.99, 4999.00, 4999.00])
    for mode in DIRECTIONAL:
        r = diff_scenario("s", "old", "new", base, cand, mode=mode)
        assert r.signals.tool_arg_match != 1.0, (
            f"mode={mode}: published 'identical' over payload sets that differ "
            f"(baseline had no 4999.00 run): {r.signals.tool_arg_match}"
        )


def test_a_candidate_that_narrows_to_the_baseline_mode_is_not_scored_as_a_rise() -> None:
    """The clamp case in the original row: raw rise was negative and clamped to 0, which
    read as `1.00`. Under `subset` a candidate that only ever uses a payload the baseline
    used is genuinely legal -- so here `1.00` is CORRECT, and must stay."""
    base = _arg_runs([49.99, 49.99, 49.99, 50.00, 50.00])
    cand = _arg_runs([49.99] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.signals.tool_arg_match == 1.0
    assert r.verdict is DiffVerdict.unchanged


# --- what must NOT have changed: the false-positive ceiling ---------------------------


@pytest.mark.parametrize("mode", DIRECTIONAL)
def test_a_candidate_drawn_from_the_same_repertoire_does_not_fire(mode: str) -> None:
    """The north-star guard. Both sides sample the same jittery behaviour, so nothing in
    the candidate is outside the baseline's repertoire and nothing required is dropped."""
    base = _name_runs([["search"], ["search", "a"], ["search"], ["search", "a"], ["search"]])
    cand = _name_runs([["search", "a"], ["search"], ["search"], ["search"], ["search", "a"]])
    r = diff_scenario("s", "old", "new", base, cand, mode=mode)
    assert r.verdict is DiffVerdict.unchanged, f"{r.verdict}: {r.explanation}"


def test_subset_still_permits_dropping_a_call() -> None:
    """The whole point of `subset`: fewer calls is legal. A fix that broke this would be
    trading a false negative for exactly the false positive the mode exists to prevent."""
    base = _name_runs([["search", "fetch"]] * 5)
    cand = _name_runs([["search"]] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.verdict is DiffVerdict.unchanged, r.explanation


def test_superset_still_permits_adding_a_call() -> None:
    base = _name_runs([["search"]] * 5)
    cand = _name_runs([["search", "fetch"]] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert r.verdict is DiffVerdict.unchanged, r.explanation


def test_superset_still_catches_a_dropped_call() -> None:
    base = _name_runs([["search", "fetch"]] * 5)
    cand = _name_runs([["search"]] * 5)
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert r.verdict is DiffVerdict.regression, r.explanation


def test_one_odd_candidate_run_is_still_noise() -> None:
    """MIN_TOOL_TVD is unmoved, so a single deviating run cannot clear it."""
    base = _name_runs([["search"]] * 5)
    cand = _name_runs([["search"], ["search"], ["search"], ["search"], ["search", "new"]])
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.signals.tool_call_match == round(1.0 - 0.2, 3)
    assert 0.2 < MIN_TOOL_TVD
    assert r.verdict is DiffVerdict.unchanged, r.explanation


def test_the_equivalence_modes_are_untouched() -> None:
    """`strict`/`unordered` never used the modal reference; this fix must not reach them."""
    base = _arg_runs([49.99, 49.99, 49.99, 50.00, 50.00])
    cand = _arg_runs([49.99, 49.99, 49.99, 4999.00, 4999.00])
    for mode in ("strict", "unordered"):
        r = diff_scenario("s", "old", "new", base, cand, mode=mode)
        assert r.signals.tool_arg_match == 0.6, f"mode={mode}: {r.signals.tool_arg_match}"
