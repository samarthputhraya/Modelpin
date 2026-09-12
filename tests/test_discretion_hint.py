"""The tool alarm reports what varied, and refuses to say why.

MP-247. MP-220 is an OPEN, disclosed false positive: an optional tool call the model makes on
4 of 5 baseline runs and 0 of 5 candidate runs clears the gate and publishes `regression` at
confidence 0.952 with exit 1, on a same-model, same-prompt null. README says so in its
limitations, and `tests/test_mp220_tool_channel_false_positive.py` pins it.

That disclosure is in the wrong place for the person it is about. A user meets the defect as a
RED BUILD on their pull request, acts on it, and concludes the tool cried wolf -- all long
before they read a limitations section. So the alarm carries the evidence at the point it
fires.

WHAT THIS MODULE IS REALLY GUARDING, AND WHY IT IS WRITTEN THIS WAY.

`[M] 2026-09-12` The first version asked whether the baseline TRAJECTORY was multimodal, then
printed a sentence claiming the baseline "made this call on some runs and not others". Those
are different predicates, and the FP review measured the gap on this project's own committed
traces: **9 hints, 8 of them on trials labelled real regressions**, and following the advice
turned 7 into `unchanged @ 1.000`. On `tc_sailmaker_cloth_check` the baseline called
`check_cloth_stock` on 5 of 5 runs while the hint said otherwise, because an unrelated tool had
moved position. The tool was asserting a measurable falsehood about the run it was describing.

Two changes came out of that, and both are pinned below:

* the predicate now tests the DROPPED tool's own baseline presence rate, strictly between 0
  and 1 -- so the printed counts are true by construction; and
* the sentence reports those counts and **draws no conclusion**. `[M]` Even with the corrected
  predicate, a replay of 2,528 stored trials still fires on `tc_glazier_survey_check`, a
  labelled real regression whose `confirm_measurements` genuinely ran on 3 of 5 baseline runs.
  An intermittently-called tool can be required-when-applicable, and nothing in the traces
  distinguishes that from discretion. So the tool says what it measured, names both readings,
  and says it cannot tell them apart.

The most important test here remains the one asserting SILENCE on a real regression, because a
hint's failure mode is worse than no hint: `Scenario.match` is persistent and excluded from the
scenario fingerprint, so acting on a wrong hint silences that channel for that scenario with
nothing prompting a revisit.
"""

from __future__ import annotations

import pytest

from modelpin.diff import diff_scenario
from modelpin.models import DiffVerdict, Scenario, Trace

_MARKER = "your baseline was already inconsistent about this"
_SUBSET = '"match": "subset"'

#: The channel markers `scripts/fp_measurement.py` and `scripts/channel_exposure.py` PARSE out
#: of `explanation`. The hint is prose appended to the same string, so it must never contain
#: one -- that would re-attribute a trial to a channel it did not fire on.
_CHANNEL_MARKERS = (
    "tool-call behavior changed",
    "refusal rate",
    "semantic drift",
    "tool-call arguments changed",
    "output format drift",
)


def _trace(idx: int, tools: list[str]) -> Trace:
    return Trace.model_validate(
        {
            "scenario_id": "s",
            "model_id": "m",
            "run_idx": idx,
            "final_output": "done",
            "tool_calls": [{"name": t, "arguments": {}} for t in tools],
            "refused": False,
            "tokens_in": 10,
            "tokens_out": 5,
            "latency_ms": 1,
        }
    )


def _scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "s",
            "name": "s",
            "kind": "agent",
            "input": {"messages": [{"role": "user", "content": "go"}]},
        }
    )


def _run(base: list[list[str]], cand: list[list[str]], mode: str = "strict"):
    return diff_scenario(
        "s",
        "m",
        "m",
        [_trace(i, t) for i, t in enumerate(base)],
        [_trace(i, t) for i, t in enumerate(cand)],
        _scenario(),
        mode=mode,  # type: ignore[arg-type]
        judge=None,
    )


#: MP-220's exact shape: the optional second call on 4 of 5 baseline runs, 0 of 5 candidate.
_UNDECIDED_BASE = [["update_order_status", "notify_customer"]] * 4 + [["update_order_status"]]
_DROPPED_CAND = [["update_order_status"]] * 5


def test_the_alarm_reports_the_measured_rate_and_names_the_mechanism() -> None:
    result = _run(_UNDECIDED_BASE, _DROPPED_CAND)
    assert result.verdict is DiffVerdict.regression, (
        "precondition: this is MP-220's shape and the gate is expected to fire. If it no "
        "longer does, MP-220 has been fixed and this module needs rewriting, not deleting."
    )
    assert _MARKER in result.explanation, result.explanation
    assert "notify_customer on 4 of 5 baseline runs" in result.explanation, (
        "the alarm does not report the rate it measured, so the user cannot check the claim "
        f"against their own runs:\n\n{result.explanation}"
    )
    assert _SUBSET in result.explanation, result.explanation


def test_the_alarm_refuses_to_say_which_reading_is_right() -> None:
    """`[M]` A labelled REAL regression survives the corrected predicate, so it must not claim.

    `tc_glazier_survey_check` drops `confirm_measurements`, genuinely intermittent at 3 of 5
    baseline runs, and is labelled `changed`. An intermittently-called tool can be
    required-when-applicable; the traces do not distinguish that from discretion. If this test
    starts failing because the sentence became decisive, that is a claims defect, not a nit.
    """
    text = _run(_UNDECIDED_BASE, _DROPPED_CAND).explanation
    assert "cannot tell those apart" in text, text
    assert "real regression and the mode should stay" in text, text


@pytest.mark.parametrize(
    ("label", "base"),
    [
        # A plain unimodal baseline. This is what the FIRST version of this test used -- and it
        # exited on the trajectory clause without ever reaching the predicate it claimed to
        # test. Kept as the trivial case.
        ("unimodal baseline", [["lookup", "refund"]] * 5),
        # `[M]` The two shapes that defeated the first predicate. `refund` is on 5/5 baseline
        # runs in BOTH; only an unrelated retry or reorder makes the TRAJECTORY multimodal.
        ("a retry elsewhere", [["lookup", "refund"]] * 4 + [["lookup", "lookup", "refund"]]),
        ("a reorder elsewhere", [["lookup", "refund"]] * 4 + [["refund", "lookup"]]),
    ],
)
def test_a_tool_the_baseline_always_called_gets_no_hint(label: str, base: list) -> None:
    """THE important one: a real regression must stay silent, whatever else varied.

    Suggesting `subset` here would convert a true regression into a permanently silenced one.
    """
    result = _run(base, [["lookup"]] * 5)
    assert result.verdict is DiffVerdict.regression, f"{label}: expected the gate to fire"
    assert _SUBSET not in result.explanation, (
        f"{label}: `refund` ran on EVERY baseline run and the candidate stopped. That is a "
        f"true regression, and the hint must not appear:\n\n{result.explanation}"
    )
    assert _MARKER not in result.explanation, result.explanation


def test_a_count_change_with_nothing_dropped_gets_no_hint() -> None:
    """Nothing was dropped, so a sentence about a dropped call would be false.

    `[M] 2026-09-12` The FP review caught the first version of this test being VACUOUS: its
    baseline produced `unchanged @ 0.167`, so the tool channel never fired and the assertion
    passed whatever the predicate did. This baseline fires (`regression @ 0.992`) and was
    hinted on by the OLD predicate, so the test now catches a revert. The reorder/count family
    was 82% of the old firings and had no non-vacuous test at all.
    """
    base = [["check_balance", "transfer"]] * 4 + [["check_balance", "check_balance", "transfer"]]
    result = _run(base, [["transfer", "check_balance"]] * 5)
    assert result.verdict is DiffVerdict.regression, (
        "precondition: this shape must FIRE, or the assertion below proves nothing. "
        f"got {result.verdict.value} @ {result.confidence}"
    )
    assert _SUBSET not in result.explanation, (
        "the candidate dropped nothing -- it reordered. The hint claims a dropped call.\n\n"
        f"{result.explanation}"
    )


def test_a_candidate_that_invents_a_call_gets_no_hint() -> None:
    """A new tool is a plan change whatever the baseline did. Asserted unconditionally."""
    result = _run([["lookup", "refund"]] * 4 + [["lookup"]], [["lookup", "wire_transfer"]] * 5)
    assert result.verdict is DiffVerdict.regression
    assert _SUBSET not in result.explanation, result.explanation


def test_the_hint_contains_no_channel_marker_the_scripts_parse() -> None:
    """`explanation` is a PARSED interface, not only prose.

    `scripts/channel_exposure.py` attributes a trial to a channel by substring-matching these
    markers. A hint containing one would re-attribute the trial and move a published count --
    a false-positive rate changed by a help message.
    """
    full = _run(_UNDECIDED_BASE, _DROPPED_CAND).explanation
    hint = full[full.index(_MARKER) :]
    for marker in _CHANNEL_MARKERS:
        assert marker not in hint, (
            f"the hint text contains the {marker!r} channel marker, which "
            f"scripts/channel_exposure.py parses:\n\n{hint}"
        )


@pytest.mark.parametrize("mode", ["strict", "unordered"])
def test_the_hint_is_purely_additive_in_every_equivalence_mode(mode: str) -> None:
    """Strip the hint and the explanation must equal the un-hinted one, byte for byte.

    An exact comparison, not a loose bound: the previous version asserted `confidence > 0.9`
    against an actual of 0.9520, so a change moving it to 0.9100 would have passed.
    """
    hinted = _run(_UNDECIDED_BASE, _DROPPED_CAND, mode=mode)
    plain = _run([["lookup", "refund"]] * 5, [["lookup"]] * 5, mode=mode)

    assert _MARKER in hinted.explanation, f"{mode}: expected the hint here"
    assert _MARKER not in plain.explanation, f"{mode}: expected no hint here"

    stripped = hinted.explanation[: hinted.explanation.index("  (" + _MARKER[:8])]
    # `unordered` canonicalises the trajectory, so the modal sequence renders sorted. The
    # expected text is therefore mode-dependent -- that is the engine behaving correctly, and
    # hard-coding one spelling made this assertion fail for a reason unrelated to the hint.
    first = (
        "['update_order_status', 'notify_customer']"
        if mode == "strict"
        else "['notify_customer', 'update_order_status']"
    )
    expected = f"tool-call behavior changed: {first} -> ['update_order_status']"
    assert stripped == expected, (
        f"{mode}: the hint changed the explanation beyond appending itself.\n"
        f"got:      {stripped!r}\nexpected: {expected!r}"
    )
    assert hinted.verdict is DiffVerdict.regression
    assert hinted.confidence == pytest.approx(0.952, abs=0.0005)
    assert hinted.signals.tool_call_match == pytest.approx(0.2, abs=0.0005)
