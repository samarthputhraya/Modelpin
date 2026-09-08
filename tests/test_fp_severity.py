"""MP-223 - the published false-positive rate pooled two consequences that are not comparable.

`scripts/fp_measurement.py`'s `_FLAGGED` counts a `changed_minor` on a same-model null against
the north-star metric exactly like a red build, and **that classifier is correct and is not
touched here.** If `changed_minor` were reclassified `clean`, any future channel could escape
the metric permanently by shipping as "advisory", and ADR-0029's argument gate is already that
shape. The defect was the REPORTING.

`[M] 2026-09-08` On the run of record, **30 of the 39 scored trials could only ever have fired
on the advisory argument gate**, which by ADR-0029 escalates to `changed_minor` at most and can
never exit 1 (`cli.py` gates the CI-failing code on `regression` alone). A bound 77% carried by
a channel a user's CI never sees is not a statement about *"if Modelpin says it broke, it
broke"*. The split publishes each severity with its OWN denominator - the trials in which a
channel of that severity could have fired at all, which is ADR-0022's predicate applied one
severity at a time and ADR-0038 D3's per-channel shape.

Everything here is offline (ADR-0006): synthetic traces, a fake judge, no provider, no key.
"""

from __future__ import annotations

import pytest

from modelpin.diff import diff_scenario
from modelpin.models import Assertion, DiffVerdict, Scenario, ToolCall, Trace
from scripts.fp_measurement import (
    ADVISORY_CHANNELS,
    HARD_CHANNELS,
    _FLAGGED,
    channels_that_fired,
    classify,
    semantic_pvalue,
    severity_exposure,
    severity_fired,
    structural_channel_pvalues,
    verify_against_published,
)

RUNS = 5


def trace(seq=(), args=None, refused=False, output="ok", run=0):
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=run,
        tool_calls=[ToolCall(name=n, arguments=(args or {})) for n in seq],
        refused=refused,
        final_output=output,
    )


def runs(seqs, args=None, refused=False, output="ok"):
    return [trace(s, args, refused, output, i) for i, s in enumerate(seqs)]


class _FakeJudge:
    def __init__(self, rule):
        self.rule = rule

    def equivalent(self, reference, candidate, task=None):
        return self.rule(reference, candidate)


def _scenario(assertions=None):
    return Scenario(
        id="s",
        name="s",
        input={"messages": [{"role": "user", "content": "hi"}]},
        assertions=assertions,
    )


# --------------------------------------------------------------------------------------
# The classifier MP-223 must not loosen.
# --------------------------------------------------------------------------------------


def test_changed_minor_still_counts_against_the_north_star_metric() -> None:
    """The one thing this row is forbidden to do, pinned so a later "simplification" cannot.

    Splitting the REPORT by severity is safe. Splitting the CLASSIFIER would let any channel
    leave the metric permanently by shipping as advisory -- and ADR-0029's argument gate is
    already exactly that shape, so this is not hypothetical.
    """
    assert DiffVerdict.changed_minor in _FLAGGED
    assert DiffVerdict.regression in _FLAGGED
    assert classify(DiffVerdict.changed_minor) == "fp"
    assert classify(DiffVerdict.regression) == "fp"
    assert classify(DiffVerdict.unchanged) == "clean"
    assert classify(DiffVerdict.insufficient_evidence) == "unmeasured"


def test_the_two_severities_partition_every_channel_the_engine_can_name() -> None:
    """A channel in neither tuple would vanish from both rates without failing anything."""
    from scripts.fp_measurement import SEVERITY_REASONS

    named = {c.split("_", 1)[0] for c in SEVERITY_REASONS}
    assert named == set(HARD_CHANNELS) | set(ADVISORY_CHANNELS)
    assert not set(HARD_CHANNELS) & set(ADVISORY_CHANNELS)


# --------------------------------------------------------------------------------------
# Each channel is classified by what the ENGINE does with it, not by a list someone wrote.
# --------------------------------------------------------------------------------------


def test_a_tool_trajectory_change_is_a_hard_channel_and_fails_a_build() -> None:
    base = runs([["lookup_order"]] * RUNS)
    cand = runs([["lookup_order", "lookup_order"]] * RUNS)
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict is DiffVerdict.regression, "the premise of calling `tool` hard"
    assert "tool" in channels_that_fired(r)
    assert severity_fired(r) == {"hard": True, "advisory": False}


def test_a_refusal_spike_is_a_hard_channel_and_fails_a_build() -> None:
    base = runs([[]] * RUNS)
    cand = runs([[]] * RUNS, refused=True)
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict is DiffVerdict.regression
    assert "refusal" in channels_that_fired(r)
    assert severity_fired(r) == {"hard": True, "advisory": False}


def test_semantic_drift_is_a_hard_channel_and_fails_a_build() -> None:
    base = [trace(output="the refund was issued", run=i) for i in range(RUNS)]
    cand = [trace(output="I cannot help with that", run=i) for i in range(RUNS)]
    judge = _FakeJudge(lambda ref, c: ref == c)
    r = diff_scenario("s", "old", "new", base, cand, _scenario(), "strict", judge=judge)
    assert r.verdict is DiffVerdict.regression
    assert "semantic" in channels_that_fired(r)
    assert severity_fired(r) == {"hard": True, "advisory": False}


def test_an_argument_change_is_ADVISORY_and_cannot_fail_a_build() -> None:
    """ADR-0029: `MIN_TOOL_ARG_TVD` has no labelled set behind it, so the gate is capped."""
    base = runs([["issue_refund"]] * RUNS, args={"amount": 49.99})
    cand = runs([["issue_refund"]] * RUNS, args={"amount": 4999.00})
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict is DiffVerdict.changed_minor, "the premise of calling `argument` advisory"
    assert "argument" in channels_that_fired(r)
    assert severity_fired(r) == {"hard": False, "advisory": True}


def test_assertion_drift_is_ADVISORY_and_cannot_fail_a_build() -> None:
    """ADR-0032: the format gate is the one verdict signal with no effect-size floor."""
    scn = _scenario(Assertion(must_contain=["INV-"]))
    base = [trace(output="INV-1", run=i) for i in range(RUNS)]
    cand = [trace(output="nope", run=i) for i in range(RUNS)]
    r = diff_scenario("s", "old", "new", base, cand, scn)
    assert r.verdict is DiffVerdict.changed_minor
    assert "assertion" in channels_that_fired(r)
    assert severity_fired(r) == {"hard": False, "advisory": True}


def test_an_advisory_misfire_is_counted_even_when_a_hard_channel_masks_the_verdict() -> None:
    """Counting by VERDICT alone would hide it, and in the flattering direction.

    A trial can fire the advisory argument gate AND a hard channel; the verdict block sets
    `regression` and the advisory misfire becomes invisible in it. On a same-model null that
    is still an advisory false alarm and the advisory rate must see it.
    """
    base = runs([["a"]] * RUNS, args={"x": 1}) + []
    cand = runs([["a"]] * RUNS, args={"x": 2}, refused=True)
    r = diff_scenario("s", "old", "new", base, cand)
    fired = severity_fired(r)
    assert r.verdict is DiffVerdict.regression
    assert (
        fired["hard"] and fired["advisory"]
    ), f"both severities fired; the explanation was {r.explanation!r}"


# --------------------------------------------------------------------------------------
# The recomputation is exact, or it raises. It never quietly re-attributes a bound.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base,cand,assertions",
    [
        (runs([["a"]] * RUNS), runs([["a"]] * RUNS), None),
        (runs([["a"]] * RUNS), runs([["a", "a"]] * RUNS), None),
        (runs([["a"]] * RUNS, args={"x": 1}), runs([["a"]] * RUNS, args={"x": 2}), None),
        (runs([[]] * RUNS), runs([[]] * RUNS, refused=True), None),
        (
            [trace(output="INV-1", run=i) for i in range(RUNS)],
            [trace(output="nope", run=i) for i in range(RUNS)],
            Assertion(must_contain=["INV-"]),
        ),
        # The MP-220 shape: a mixed baseline against a unimodal candidate.
        (runs([["a", "b"], ["a", "b"], ["a", "b"], ["a"], ["a", "b"]]), runs([["a"]] * RUNS), None),
    ],
)
def test_the_recomputed_channel_pvalues_reproduce_the_engines_own_confidence(
    base, cand, assertions
) -> None:
    """`verify_against_published` is a TOTAL check, not a spot check.

    For an `unchanged` trial the engine's confidence IS `round(min(all five p), 3)`, so if
    `structural_channel_pvalues` ever drifts from `diff/__init__.py` -- a new match mode, a
    changed statistic, a reordered conjunction in `args_compared` -- the severity attribution
    under it would silently re-label a published bound. This raises instead.
    """
    scn = _scenario(assertions)
    r = diff_scenario("s", "m", "m", base, cand, scn)
    channel_p = structural_channel_pvalues(base, cand, scn, "strict")
    channel_p["semantic"] = semantic_pvalue(r, channel_p)
    verify_against_published(r, channel_p)  # raises SystemExit on drift


def test_a_drifted_recomputation_raises_rather_than_publishing_a_wrong_attribution() -> None:
    base = runs([["a"]] * RUNS)
    cand = runs([["a"]] * RUNS)
    r = diff_scenario("s", "m", "m", base, cand, _scenario())
    assert r.verdict is DiffVerdict.unchanged and r.confidence == 1.0
    with pytest.raises(SystemExit, match="has drifted from"):
        verify_against_published(
            r, {"tool": 0.01, "argument": 1.0, "refusal": 1.0, "assertion": 1.0, "semantic": 1.0}
        )


# --------------------------------------------------------------------------------------
# The semantic channel is the one that cannot be recomputed, so its recovery is pinned.
# --------------------------------------------------------------------------------------


def test_a_perfect_equivalence_score_pins_the_semantic_p_at_exactly_one() -> None:
    """`semantic_score` is `1 - mean(cand_flags)`, and the test is ONE-SIDED on a rise.

    So a score of 1.0 means every candidate flag is 0, a candidate mean of 0 cannot rise
    above a baseline mean of >= 0, and `p` is exactly 1.0. This is an identity, not an
    estimate -- which is why 31 of the run of record's 39 scored trials are settled by it.
    """
    base = runs([["a"]] * RUNS)
    cand = runs([["a"]] * RUNS)
    judge = _FakeJudge(lambda ref, c: True)
    r = diff_scenario("s", "m", "m", base, cand, _scenario(), "strict", judge=judge)
    assert r.signals.semantic_score == 1.0
    assert (
        semantic_pvalue(r, {"tool": 1.0, "argument": 1.0, "refusal": 1.0, "assertion": 1.0}) == 1.0
    )


def test_no_judge_means_the_semantic_channel_could_not_have_fired() -> None:
    base = runs([["a"]] * RUNS)
    cand = runs([["a"]] * RUNS)
    r = diff_scenario("s", "m", "m", base, cand, _scenario())  # judge=None
    assert r.signals.semantic_score is None
    assert (
        semantic_pvalue(r, {"tool": 1.0, "argument": 1.0, "refusal": 1.0, "assertion": 1.0}) == 1.0
    )


def test_an_undetermined_semantic_p_is_excluded_from_the_hard_denominator_not_guessed() -> None:
    """A smaller denominator is a WEAKER upper bound. That is the conservative direction.

    Guessing "exposed" would shrink the published bound on evidence nobody has; guessing
    "not exposed" would shrink the denominator on the same non-evidence. Reporting it
    separately is the only option that cannot flatter the number.
    """
    base = runs([["a"]] * RUNS)
    cand = runs([["a"]] * RUNS)
    judge = _FakeJudge(lambda ref, c: ref == c or "x" in c)
    r = diff_scenario("s", "m", "m", base, cand, _scenario(), "strict", judge=judge)
    # Force the ambiguous branch: a sub-1.0 score with a structural channel already at the
    # published minimum, so the arithmetic cannot isolate the semantic residual.
    r = r.model_copy(update={"confidence": 0.5})
    r.signals.semantic_score = 0.8
    channel_p = {"tool": 0.5, "argument": 1.0, "refusal": 1.0, "assertion": 1.0}
    channel_p["semantic"] = semantic_pvalue(r, channel_p)
    assert channel_p["semantic"] is None
    exposure = severity_exposure(r, channel_p)
    assert exposure["hard"] is None, "undetermined, so out of the hard denominator entirely"
    assert exposure["advisory"] is False


def test_a_channel_that_FIRED_is_exposed_by_definition_and_never_undetermined() -> None:
    """A flagged trial can never fall out of its own denominator - that would delete a false
    alarm from the numerator's own base and is the one direction that flatters a rate."""
    base = runs([[]] * RUNS)
    cand = runs([[]] * RUNS, refused=True)
    r = diff_scenario("s", "m", "m", base, cand, _scenario())
    assert r.verdict is DiffVerdict.regression
    exposure = severity_exposure(r, {"tool": 1.0, "refusal": 1.0, "semantic": None})
    assert exposure["hard"] is True
