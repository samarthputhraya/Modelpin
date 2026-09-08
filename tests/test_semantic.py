"""Unit tests for the semantic divergence-flag computation (no network)."""

from __future__ import annotations

from modelpin.diff.semantic import _normalize, reference_output, semantic_divergence_flags
from modelpin.models import Trace


class FakeJudge:
    """Records calls; returns equivalence per an injected rule(reference, candidate)."""

    def __init__(self, rule):
        self.rule = rule
        self.calls: list[tuple] = []

    def equivalent(self, reference, candidate, task=None):
        self.calls.append((reference, candidate, task))
        return self.rule(reference, candidate)


def _tr(out, run=0):
    return Trace(scenario_id="s", model_id="m", run_idx=run, final_output=out)


def _traces(outputs):
    return [_tr(o, i) for i, o in enumerate(outputs)]


def test_reference_is_modal_output():
    assert reference_output(_traces(["A", "A", "B"])) == "A"


def test_identical_text_skips_the_judge_entirely():
    judge = FakeJudge(lambda r, c: False)  # would flag everything if ever called
    base = _traces(["same"] * 3)
    cand = _traces(["same"] * 3)
    base_f, cand_f, score = semantic_divergence_flags(base, cand, judge)
    assert judge.calls == []  # never invoked — text matched the reference
    assert base_f == [0, 0, 0] and cand_f == [0, 0, 0]
    assert score == 1.0


def test_consistently_divergent_candidate_is_flagged():
    judge = FakeJudge(lambda r, c: False)  # judge says different whenever asked
    base = _traces(["Approved."] * 3)
    cand = _traces(["Request denied.", "We cannot proceed.", "No."])
    base_f, cand_f, score = semantic_divergence_flags(base, cand, judge)
    assert base_f == [0, 0, 0]  # baseline identical to its own mode
    assert cand_f == [1, 1, 1]  # all candidate runs judged non-equivalent
    assert score == 0.0


def test_reworded_but_equivalent_candidate_is_not_flagged():
    judge = FakeJudge(lambda r, c: True)  # different words, same meaning
    base = _traces(["The total is $5."] * 3)
    cand = _traces(["Total: 5 dollars.", "It comes to five dollars.", "$5 total."])
    base_f, cand_f, score = semantic_divergence_flags(base, cand, judge)
    assert cand_f == [0, 0, 0]  # equivalence -> no divergence
    assert score == 1.0
    assert len(judge.calls) == 3  # judge consulted once per differing candidate run


def test_baseline_natural_spread_is_measured():
    # A baseline run that differs from the mode and is judged non-equivalent counts as
    # baseline spread, so the candidate is compared against that natural variance.
    judge = FakeJudge(lambda r, c: False)
    base = _traces(["A", "A", "Z"])  # mode "A"; the "Z" run differs
    cand = _traces(["A", "A", "A"])
    base_f, cand_f, _ = semantic_divergence_flags(base, cand, judge)
    assert base_f == [0, 0, 1]
    assert cand_f == [0, 0, 0]


def test_normalize_ignores_whitespace_and_case():
    assert _normalize("  Total:  $5 ") == _normalize("total: $5")


# --- ADR-0040: the candidate is compared to EVERY baseline run -------------------------------
#
# `[M] 2026-09-07 FP review` Everything above this line uses a textually HOMOGENEOUS baseline
# (`["same"]*3`, `["Approved."]*3`), which is exactly the regime where the old modal-reference
# rule and the new any-equivalent rule agree. So the whole of ADR-0040 D1 was unpinned: a
# mutation reverting the candidate side to `base_outputs[:1]` -- one arbitrary reference, the
# entire defect the ADR exists to fix -- left all 956 tests green. These use a HETEROGENEOUS
# baseline, which is the only shape that can tell the two rules apart.


class _StrictExcept:
    """Equivalent only for the exact pairs listed. Records the (reference, candidate) order it
    was asked in, because the judge prompt is deliberately asymmetric."""

    def __init__(self, pairs):
        self.pairs = set(pairs)
        self.asked: list[tuple[str, str]] = []

    def equivalent(self, reference, candidate, task=None):
        self.asked.append((reference, candidate))
        return (reference, candidate) in self.pairs


def test_a_candidate_matching_a_NON_modal_baseline_run_is_not_divergent():
    """The load-bearing assertion of ADR-0040 D1, and the one that kills the revert.

    The baseline says A twice and B once, so the MODAL output is A. The candidate says B --
    a meaning the baseline genuinely produced. Under the old rule B is compared only to A,
    judged non-equivalent, and flagged. Under ADR-0040 it is equivalent to a baseline run and
    is not.

    `[M]` Mutating the candidate pool to `base_outputs[:1]` reverts exactly this and was
    invisible to every other test in the repo.
    """
    judge = _StrictExcept([])  # nothing is equivalent unless the TEXT matches
    base_flags, cand_flags, score = semantic_divergence_flags(
        _traces(["A", "A", "B"]), _traces(["B", "B"]), judge
    )
    assert cand_flags == [0, 0], (
        "a candidate run reproducing a meaning the BASELINE ITSELF produced is not divergence; "
        f"got {cand_flags}. This is the modal-reference defect (MP-206)."
    )
    assert score == 1.0
    # ...and the baseline's own spread is still measured: `B` appears once, and leave-one-out
    # compares it against two `A`s, neither of which matches.
    assert base_flags == [0, 0, 1], base_flags


def test_the_judge_is_asked_baseline_first_candidate_second():
    """`[M]` Swapping the two arguments left all 956 tests green. It is not cosmetic: the judge
    prompt names one side "baseline" and the other "candidate" and asks specifically about "a
    refusal where the baseline helped (or vice versa)", so the orientation IS the question. The
    baseline side now issues up to N*(N-1) oriented calls per trial instead of N-1."""
    judge = _StrictExcept([])
    semantic_divergence_flags(_traces(["A", "A"]), _traces(["Z"]), judge)
    assert judge.asked, "the judge was never consulted"
    for reference, candidate in judge.asked:
        assert candidate == "Z" or candidate == "A", judge.asked
    assert (
        "A",
        "Z",
    ) in judge.asked, (
        f"the judge must be asked equivalent(<baseline>, <candidate>); got {judge.asked}"
    )
    assert ("Z", "A") not in judge.asked, "the arguments are swapped"


def test_an_empty_leave_one_out_pool_is_not_divergence():
    """A single-run baseline leaves nothing to compare against. `[M]` Returning False there
    would flag it by construction; it also reproduces the pre-ADR-0040 answer, since a 1-run
    baseline IS its own mode. Reachable: `MIN_RUNS` guards `--runs`, but `check` reads its
    reference side off disk and a 1-trace baseline passes `_side_is_unusable`."""
    judge = _StrictExcept([])
    base_flags, cand_flags, _ = semantic_divergence_flags(_traces(["A"]), _traces(["Z"]), judge)
    assert base_flags == [0], base_flags
    assert cand_flags == [1], cand_flags
