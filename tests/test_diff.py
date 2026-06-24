"""Golden tests for the behavioral diff engine.

Two jobs:
1. catch the four canonical regressions — (a) no change, (b) tool-call regression,
   (c) refusal spike, (d) format drift;
2. PROVE the north-star property — a low false-positive rate. The engine must NOT
   cry wolf on model non-determinism: a single odd run, or a majority that flips
   between two equally-likely behaviors, is noise, not a regression.

Traces are constructed inline and deterministically — no network, no recorded
fixtures needed, so CI stays cheap and reproducible.
"""

from modelpin.diff import diff_scenario
from modelpin.diff.stats import (
    permutation_pvalue_distribution,
    permutation_pvalue_mean,
)
from modelpin.diff.structural import trajectory_match
from modelpin.models import Assertion, DiffVerdict, Scenario, ToolCall, Trace

RUNS = 5  # spec default is 3-5; 5 gives the permutation test enough power to be decisive.


def trace(seq=(), refused=False, output="ok", run=0):
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=run,
        tool_calls=[ToolCall(name=n) for n in seq],
        refused=refused,
        final_output=output,
    )


def runs(seqs, refused=False, output="ok"):
    """Build a list of traces, one per element of ``seqs`` (each a tool-name list)."""
    return [trace(seq=s, refused=refused, output=output, run=i) for i, s in enumerate(seqs)]


# --------------------------------------------------------------------------- #
# (a)-(d): the four canonical cases the engine MUST catch (or correctly ignore)
# --------------------------------------------------------------------------- #


def test_a_no_change_is_unchanged():
    base = runs([["lookup_order"]] * RUNS)
    cand = runs([["lookup_order"]] * RUNS)
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict == DiffVerdict.unchanged
    assert r.signals.tool_call_match == 1.0
    assert r.confidence == 1.0


def test_b_consistent_tool_call_change_is_regression():
    base = runs([["lookup_order"]] * RUNS)
    cand = runs([["lookup_order", "lookup_order"]] * RUNS)  # now calls it twice, every run
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict == DiffVerdict.regression
    assert r.signals.tool_call_match < 1.0
    assert r.confidence >= 0.9
    assert "tool-call" in r.explanation


def test_c_refusal_spike_is_regression():
    base = runs([["cancel_subscription"]] * RUNS, refused=False)
    cand = runs([["cancel_subscription"]] * RUNS, refused=True)  # now refuses every run
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict == DiffVerdict.regression
    assert r.signals.refusal_delta == 1.0
    assert r.confidence >= 0.9


def test_d_format_drift_is_minor():
    sc = Scenario(
        id="s", name="invoice", input={"messages": []}, assertions=Assertion(must_contain=["$"])
    )
    base = runs([[]] * RUNS, output="Total: $5")
    cand = runs([[]] * RUNS, output="Total: 5")  # consistently drops the "$"
    r = diff_scenario("s", "old", "new", base, cand, scenario=sc)
    assert r.verdict == DiffVerdict.changed_minor
    assert r.signals.format_valid is False


# --------------------------------------------------------------------------- #
# False-positive guards: the whole point of the multi-run statistics
# --------------------------------------------------------------------------- #


def test_single_refusal_blip_is_not_a_regression():
    """One refusing run out of five is sampling noise, not a regression. The old
    'refusal_delta >= 0.3' rule wrongly flagged this — the permutation test must not."""
    base = runs([["cancel_subscription"]] * RUNS, refused=False)
    cand = [trace(["cancel_subscription"], refused=(i == 0), run=i) for i in range(RUNS)]
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict != DiffVerdict.regression


def test_noisy_but_equivalent_distribution_is_unchanged():
    """Both models exhibit the SAME bimodal behavior (sometimes one call, sometimes
    two); only the per-run majority happens to flip. A naive majority comparison
    would flag a regression — the distributional test must call it unchanged."""
    one, two = ["lookup_order"], ["lookup_order", "lookup_order"]
    base = runs([one, one, one, two, two])  # majority = one call
    cand = runs([two, two, two, one, one])  # majority = two calls, same distribution
    r = diff_scenario("s", "old", "new", base, cand)
    assert r.verdict == DiffVerdict.unchanged


def test_false_positive_rate_is_zero_on_equivalent_pairs():
    """Held-out-style check (spec DoD): across several noisy-but-equivalent pairs,
    the engine must flag ZERO regressions. This is the metric the product lives on."""
    a, b, c = ["t1"], ["t1", "t2"], ["t1", "t2", "t3"]
    equivalent_pairs = [
        (runs([a, a, b, b, c]), runs([b, c, a, b, a])),  # same 3-way mix, reshuffled
        (runs([a, a, a, a, b]), runs([a, b, a, a, a])),  # mostly a, one b, either side
        (runs([a, b, a, b, a]), runs([b, a, b, a, b])),  # alternating, balanced
        (runs([a] * 5), runs([a] * 4 + [b])),  # one stray extra call
    ]
    flagged = sum(
        diff_scenario("s", "old", "new", base, cand).verdict == DiffVerdict.regression
        for base, cand in equivalent_pairs
    )
    assert flagged == 0, f"false positives on equivalent pairs: {flagged}/{len(equivalent_pairs)}"


# --------------------------------------------------------------------------- #
# Match modes
# --------------------------------------------------------------------------- #


def test_unordered_mode_ignores_call_order():
    base = runs([["lookup_order", "issue_refund"]] * RUNS)
    cand = runs([["issue_refund", "lookup_order"]] * RUNS)  # same calls, swapped order
    assert diff_scenario("s", "o", "n", base, cand, mode="strict").verdict == DiffVerdict.regression
    assert (
        diff_scenario("s", "o", "n", base, cand, mode="unordered").verdict == DiffVerdict.unchanged
    )


def test_trajectory_match_modes():
    assert trajectory_match(["a", "b"], ["a", "b"], "strict")
    assert not trajectory_match(["a", "b"], ["b", "a"], "strict")
    assert trajectory_match(["a", "b"], ["b", "a"], "unordered")
    assert trajectory_match(["a", "b"], ["a"], "subset")  # dropped a call: allowed
    assert not trajectory_match(["a"], ["a", "b"], "subset")  # new call: forbidden
    assert trajectory_match(["a"], ["a", "b"], "superset")  # extra call: allowed
    assert not trajectory_match(["a", "b"], ["a"], "superset")  # dropped call: forbidden


# --------------------------------------------------------------------------- #
# Directional match modes THROUGH diff_scenario (regression guards for the FP hole
# where subset/superset silently collapsed to strict and flagged the very change the
# mode permits — a false positive on the cardinal metric).
# --------------------------------------------------------------------------- #


def test_subset_mode_allows_a_dropped_call_unchanged():
    """`--match subset` declares 'the candidate dropping a call is acceptable'. A candidate
    that consistently drops a call must therefore be `unchanged`, not a manufactured
    regression."""
    base = runs([["lookup_order", "issue_refund"]] * RUNS)
    cand = runs([["lookup_order"]] * RUNS)  # drops issue_refund every run -> allowed under subset
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.verdict == DiffVerdict.unchanged
    assert r.signals.tool_call_match == 1.0


def test_superset_mode_allows_an_added_call_unchanged():
    """`--match superset` declares 'the candidate adding a call is acceptable'."""
    base = runs([["lookup_order"]] * RUNS)
    cand = runs([["lookup_order", "issue_refund"]] * RUNS)  # adds a call -> allowed under superset
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert r.verdict == DiffVerdict.unchanged
    assert r.signals.tool_call_match == 1.0


def test_subset_mode_does_not_flag_an_allowed_drop_even_when_distribution_shifts():
    """Even when the candidate's trajectory *distribution* differs from baseline, if every
    candidate trajectory is permitted by the subset relation the verdict stays unchanged —
    the strict-collapse bug fired a regression here."""
    full, dropped = ["lookup_order", "issue_refund"], ["lookup_order"]
    base = runs([full] * RUNS)
    cand = runs(
        [dropped, dropped, dropped, dropped, full]
    )  # all subset-allowed; distribution shifts
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.verdict == DiffVerdict.unchanged


def test_subset_mode_flags_a_new_call_as_regression():
    """subset still FORBIDS new calls: a candidate that consistently introduces a call
    absent from baseline must regress (the mode loosens drops, not additions)."""
    base = runs([["lookup_order"]] * RUNS)
    cand = runs([["lookup_order", "issue_refund"]] * RUNS)  # new call -> forbidden under subset
    r = diff_scenario("s", "old", "new", base, cand, mode="subset")
    assert r.verdict == DiffVerdict.regression
    assert "tool-call" in r.explanation


def test_superset_mode_flags_a_dropped_call_as_regression():
    """superset FORBIDS dropping calls."""
    base = runs([["lookup_order", "issue_refund"]] * RUNS)
    cand = runs([["lookup_order"]] * RUNS)  # dropped a call -> forbidden under superset
    r = diff_scenario("s", "old", "new", base, cand, mode="superset")
    assert r.verdict == DiffVerdict.regression


# --------------------------------------------------------------------------- #
# Permutation-test sanity (the engine's foundation)
# --------------------------------------------------------------------------- #


def test_permutation_mean_blip_vs_spike():
    # one-in-five blip: not significant
    assert permutation_pvalue_mean([0, 0, 0, 0, 0], [1, 0, 0, 0, 0]) > 0.05
    # full spike: decisively significant
    assert permutation_pvalue_mean([0, 0, 0, 0, 0], [1, 1, 1, 1, 1]) < 0.05
    # a drop in the bad rate is never a regression (one-sided)
    assert permutation_pvalue_mean([1, 1, 1, 1, 1], [0, 0, 0, 0, 0]) == 1.0


def test_permutation_distribution_shift_vs_noise():
    a, b = ("t",), ("t", "t")
    # identical distributions
    assert permutation_pvalue_distribution([a] * 5, [a] * 5) == 1.0
    # clean shift
    assert permutation_pvalue_distribution([a] * 5, [b] * 5) < 0.05
    # same balanced mix, reshuffled -> not significant
    assert permutation_pvalue_distribution([a, a, a, b, b], [b, b, b, a, a]) > 0.05


# --------------------------------------------------------------------------- #
# Large-N sampling path (>16 total runs): the fallback must stay statistically
# valid — a regression guard for the observed-split bug an audit caught, where a
# clean spike wrongly returned p == 0.0 (impossible) and biased toward FALSE
# POSITIVES.
# --------------------------------------------------------------------------- #


def test_large_n_sampling_pvalue_is_positive_and_significant():
    # 20 total runs -> sampling fallback. A clean 0->100% spike must be flagged,
    # with a SMALL BUT STRICTLY POSITIVE p (a valid permutation p is never 0.0).
    p = permutation_pvalue_mean([0] * 10, [1] * 10)
    assert 0.0 < p < 0.05


def test_large_n_sampling_does_not_false_positive_on_equivalent():
    a, b = ("t",), ("t", "t")
    base = [a] * 6 + [b] * 4
    cand = [a] * 4 + [b] * 6  # same two behaviors, modest mix shift; 20 total -> sampling
    assert permutation_pvalue_distribution(base, cand) > 0.05


# --------------------------------------------------------------------------- #
# Semantic LLM-judge signal (spec 6B): optional, injected, and FP-safe. The judge
# is a fake here so these stay offline/deterministic — no network, no key.
# --------------------------------------------------------------------------- #


class _FakeJudge:
    """Equivalence oracle driven by a rule(reference, candidate) -> bool."""

    def __init__(self, rule):
        self.rule = rule

    def equivalent(self, reference, candidate, task=None):
        return self.rule(reference, candidate)


def out_runs(outputs):
    """Traces with no tool calls and the given per-run outputs."""
    return [trace(output=o, run=i) for i, o in enumerate(outputs)]


def test_no_judge_leaves_semantic_signal_unevaluated():
    base = out_runs(["ok"] * RUNS)
    cand = out_runs(["totally different"] * RUNS)
    r = diff_scenario("s", "old", "new", base, cand)  # judge=None
    assert r.signals.semantic_score is None
    assert r.verdict == DiffVerdict.unchanged  # structural-only sees identical (empty) tools


def test_consistent_semantic_drift_is_regression():
    # Calibrated promotion (examples/calibration/): a consistent meaning change beyond the
    # baseline's spread is a hard, CI-failing regression, not merely changed_minor. The
    # labeled sweep showed real meaning changes land at delta >= 0.8 vs equivalent at 0.0,
    # so this fires with margin and 0 false positives on the reworded-but-equivalent cases
    # guarded by the tests below.
    judge = _FakeJudge(lambda r, c: False)  # candidate consistently means something else
    base = out_runs(["Your refund was approved."] * RUNS)
    cand = out_runs(["I cannot process that.", "Denied.", "No refund.", "Rejected.", "Nope."])
    r = diff_scenario("s", "old", "new", base, cand, judge=judge)
    assert r.verdict == DiffVerdict.regression
    assert r.signals.semantic_score == 0.0
    assert "semantic" in r.explanation.lower()
    assert r.confidence >= 0.9  # small p on a consistent 5/5 divergence -> high confidence


def test_reworded_but_equivalent_output_is_not_flagged():
    """The north-star case the live smoke run exposed: same meaning, different words
    must NOT raise an alarm just because the text differs."""
    judge = _FakeJudge(lambda r, c: True)  # different wording, same meaning
    base = out_runs(["The total is $5."] * RUNS)
    cand = out_runs(
        ["Total: 5 dollars.", "It's five dollars.", "$5.", "Five dollars total.", "5 USD."]
    )
    r = diff_scenario("s", "old", "new", base, cand, judge=judge)
    assert r.verdict == DiffVerdict.unchanged
    assert r.signals.semantic_score == 1.0


def test_single_semantic_blip_is_not_flagged():
    """One divergent run in five is noise, not a regression — same FP guard as the
    structural signals, applied to the judge."""
    judge = _FakeJudge(lambda r, c: False)
    base = out_runs(["Approved."] * RUNS)
    cand = out_runs(["Approved.", "Approved.", "Approved.", "Approved.", "Denied."])
    r = diff_scenario("s", "old", "new", base, cand, judge=judge)
    assert r.verdict == DiffVerdict.unchanged


def test_minority_judge_divergence_below_floor_is_not_flagged():
    """A noisy judge that disagrees on a MINORITY of candidate runs (here 2/5, below the
    MIN_SEMANTIC_DELTA floor) must not raise an alarm — realistic judge noise on an
    otherwise-equivalent migration stays 'unchanged'. Guards the effect-size floor, not
    just the p-gate."""
    judge = _FakeJudge(lambda r, c: False)  # flags any run whose text differs
    base = out_runs(["Approved."] * RUNS)
    # 3/5 candidate runs match the baseline text (skip judge), 2/5 differ (flagged) ->
    # divergence rate 0.4 < 0.5 floor.
    cand = out_runs(["Approved.", "Approved.", "Approved.", "Sure thing.", "Done deal."])
    r = diff_scenario("s", "old", "new", base, cand, judge=judge)
    assert r.signals.semantic_score == 0.6
    assert r.verdict == DiffVerdict.unchanged
