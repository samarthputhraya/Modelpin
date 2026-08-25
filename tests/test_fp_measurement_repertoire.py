"""MP-75 - the FP rate must publish whether the run measured anything.

`scripts/fp_measurement.py` reported verdicts only. A scenario the model answers identically
on every run scores `unchanged` and was counted `clean`, so a suite of deterministic
scenarios printed `False-positive rate: 0/8 = 0%` - which reads as "the engine did not cry
wolf" but means "there was nothing to cry wolf about".

`[M]` `diff/stats.py:128-129` early-exits at `p=1.0` when the two sides are DISTRIBUTIONALLY
IDENTICAL, so such a scenario scores `unchanged` REGARDLESS of any change to the engine.
Crediting it to the north-star metric credits a trial that could not have failed.

The exclusion predicate itself is the dangerous part, and `test_measurable` below is the
reason this file exists. [M] fp-guardian blocked the first attempt: it decided from PER-SIDE
variance ("each side is unimodal, so nothing varied"), which is a different claim from
"the sides are identical". Two internally-invariant sides that DIFFER are the engine's
highest-confidence firing configuration, so that predicate deleted the most confident false
positives from both numerator and denominator. It also showed the tests were not pinning the
decision at all: replacing it with `if False:` left all 271 green.

No provider is needed for any of this - ADR-0006 forbids a live call from the suite.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.diff import DiffVerdict  # noqa: E402
from modelpin.models import ToolCall, Trace  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    FP_OUTCOMES,
    load_role_sets,
    roles_for_dir,
    select_by_role,
    classify,
    fp_outcome,
    fp_report,
    fp_summary,
    fp_tally,
    measurable,
    repertoire,
    upper_bound_95,
)


def _t(out="ok", tool="lookup", args=None):
    return Trace(
        scenario_id="s",
        model_id="m",
        tool_calls=[ToolCall(name=tool, arguments=args if args is not None else {"q": "x"})],
        final_output=out,
        latency_ms=1,
        tokens_in=1,
        tokens_out=1,
    )


class TestRepertoire:
    def test_identical_runs_count_as_one_behaviour_per_channel(self):
        assert repertoire([_t(), _t(), _t()]) == {"tools": 1, "args": 1, "text": 1}

    def test_text_jitter_is_counted_on_the_text_channel_only(self):
        rep = repertoire([_t(out="a"), _t(out="b"), _t(out="a")])
        assert rep == {"tools": 1, "args": 1, "text": 2}

    def test_argument_jitter_is_counted_even_when_the_prose_is_identical(self):
        # The MP-04 premise: a model can be word-perfect while its ARGUMENTS move. If this
        # channel were folded into `text`, the calibration set MP-54 exists for would read
        # as invariant and be excluded from the very rate it was built to measure.
        rep = repertoire([_t(args={"kg": 3.36}), _t(args={"kg": 3.357}), _t(args={"kg": 3.36})])
        assert rep["args"] == 2, rep
        assert rep["text"] == 1, rep

    def test_tool_trajectory_change_is_its_own_channel(self):
        assert repertoire([_t(tool="a"), _t(tool="b")])["tools"] == 2

    def test_text_is_compared_verbatim_because_a_gating_signal_is_byte_exact(self):
        # [M] fp-guardian: `structural.py:123-126` `violates_text_assertions` does a
        # byte-exact, case-SENSITIVE `s in out`, and flags "Order shipped" vs "order
        # shipped" at confidence 0.996. A canonicalising diagnostic would print `text: 1`
        # for that pair. A diagnostic may be finer than the engine, never coarser.
        assert repertoire([_t(out="Logged"), _t(out="logged")])["text"] == 2
        assert repertoire([_t(out="a b"), _t(out="a  b")])["text"] == 2

    def test_unhashable_argument_values_do_not_crash_the_measurement(self):
        # Arguments are model-authored JSON: nested dicts and lists are ordinary. A crash
        # here would abort a paid run at the last step.
        rep = repertoire([_t(args={"a": [1, {"b": 2}]}), _t(args={"a": [1, {"b": 3}]})])
        assert rep["args"] == 2

    def test_argument_key_order_is_not_mistaken_for_variance(self):
        # Same payload, different serialisation order -> one behaviour, not two.
        assert repertoire([_t(args={"a": 1, "b": 2}), _t(args={"b": 2, "a": 1})])["args"] == 1


class TestMeasurable:
    """The exclusion predicate. Getting this wrong silently deletes false positives."""

    class _R:
        def __init__(self, verdict, confidence):
            self.verdict, self.confidence = verdict, confidence

    def test_a_trial_with_no_measured_effect_is_excluded(self):
        # `unchanged` at confidence 1.0 means min(p) == 1.0 across every signal (ADR-0001):
        # no channel measured anything, so nothing could have fired.
        assert measurable(self._R(DiffVerdict.unchanged, 1.0)) is False

    def test_a_trial_where_something_could_have_fired_is_scored(self):
        assert measurable(self._R(DiffVerdict.unchanged, 0.95)) is True

    def test_a_FLAGGED_verdict_is_never_excluded_however_confident(self):
        """The safety property. THIS is the test that must never be deleted.

        [M] fp-guardian: the rejected per-side predicate excluded `regression` at conf 0.992
        and 0.996 on four separate channels - the engine's most confident false positives,
        removed from the rate that exists to count them. Any future predicate must keep
        every one of these scored.
        """
        for verdict in (DiffVerdict.regression, DiffVerdict.changed_minor):
            for conf in (0.0, 0.5, 0.952, 0.992, 0.996, 1.0):
                assert measurable(self._R(verdict, conf)) is True, (
                    f"{verdict.value} at confidence {conf} was excluded from the "
                    "false-positive rate. A flagged verdict is a false positive in the FP "
                    "arm by construction; removing it deletes the thing being measured."
                )

    def test_two_internally_invariant_but_DIFFERENT_sides_are_scored(self):
        """The exact case the first implementation got wrong.

        Each side is unimodal, so a per-side variance test says "nothing varied" - but the
        sides differ, which is `stats.py`'s lowest p and the engine's highest confidence.
        Building the pools here rather than mocking, so the claim is about real traces.
        """
        base = [_t(tool="lookup") for _ in range(5)]
        cand = [_t(tool="escalate") for _ in range(5)]
        assert repertoire(base) == {"tools": 1, "args": 1, "text": 1}
        assert repertoire(cand) == {"tools": 1, "args": 1, "text": 1}
        # Per-side variance says "measured nothing". The engine says regression at 0.992.
        from modelpin.diff import diff_scenario
        from modelpin.models import Scenario

        scn = Scenario(id="s", name="s", kind="agent", input={"messages": []})
        r = diff_scenario("s", "m", "m", base, cand, scn, "strict", judge=None)
        assert r.verdict is DiffVerdict.regression, r.verdict
        assert measurable(r) is True, (
            "a flagged, internally-invariant pair was excluded from the FP rate - this is "
            "the defect fp-guardian blocked, reintroduced."
        )

    def test_argument_only_jitter_is_excluded_while_the_engine_cannot_see_arguments(self):
        """[M] no gating signal on this branch reads `tc.arguments` (argkey.py is on the
        unmerged MP-04 branch), so an args-only trial is guaranteed `unchanged` at 1.0. It
        must not inflate the denominator with a trial that cannot fail. When MP-04 lands
        this stops being vacuous on its own - the engine starts measuring the channel."""
        from modelpin.diff import diff_scenario
        from modelpin.models import Scenario

        base = [_t(args={"kg": 3.36}) for _ in range(5)]
        cand = [_t(args={"kg": 3.357}) for _ in range(5)]
        assert repertoire(base + cand)["args"] == 2, "the pools really do differ on args"
        scn = Scenario(id="s", name="s", kind="agent", input={"messages": []})
        r = diff_scenario("s", "m", "m", base, cand, scn, "strict", judge=None)
        assert measurable(r) is False, (
            f"args-only jitter was scored as a real trial, but the engine returned "
            f"{r.verdict.value} at {r.confidence} because it reads no argument signal."
        )


class TestFpOutcome:
    """The FP arm's actual decision. [M] fp-guardian: with this logic inline in `main()`,
    replacing it with `if False:` deleted the entire point of MP-75 and 271 tests stayed
    green. These are the tests that would have gone red."""

    class _R:
        def __init__(self, verdict, confidence):
            self.verdict, self.confidence = verdict, confidence

    def test_a_trial_with_no_measured_effect_is_excluded_not_counted_clean(self):
        assert fp_outcome(self._R(DiffVerdict.unchanged, 1.0)) == "no-effect"

    def test_a_real_clean_trial_is_counted(self):
        assert fp_outcome(self._R(DiffVerdict.unchanged, 0.95)) == "clean"

    def test_an_abstention_stays_unmeasured(self):
        # ADR-0018: neither a false alarm nor a detection. Must not be re-routed by MP-75.
        assert fp_outcome(self._R(DiffVerdict.insufficient_evidence, 0.5)) == "unmeasured"

    def test_every_flagged_verdict_reaches_the_rate_at_every_confidence(self):
        """THE safety property, at the decision level rather than the helper level.

        A flagged verdict must reach the numerator no matter how the exclusion is written.
        [M] the predicate fp-guardian blocked excluded `regression` at conf 0.992/0.996 -
        the engine's most confident false positives - from both numerator and denominator.
        """
        for verdict in (DiffVerdict.regression, DiffVerdict.changed_minor):
            for conf in (0.0, 0.5, 0.952, 0.992, 0.996, 1.0):
                assert fp_outcome(self._R(verdict, conf)) == "fp", (
                    f"{verdict.value} at confidence {conf} did not reach the rate. A flagged "
                    "verdict in the FP arm IS a false positive; excluding it deletes the "
                    "measurement."
                )

    def test_the_exclusion_cannot_be_deleted_without_this_going_red(self):
        """Guards the arm wiring, not just the predicate: a `clean` and a `no-effect` trial
        must be distinguishable outcomes. If someone collapses the branch, this fails."""
        outcomes = {
            fp_outcome(self._R(DiffVerdict.unchanged, 1.0)),
            fp_outcome(self._R(DiffVerdict.unchanged, 0.95)),
        }
        assert outcomes == {"no-effect", "clean"}, (
            f"the no-effect exclusion collapsed into {outcomes} - MP-75's whole point is that "
            "these two are not the same trial."
        )


class TestFpTally:
    """The accounting. What lands in the numerator and the denominator IS the metric."""

    def test_only_trials_that_could_have_fired_reach_the_denominator(self):
        t = fp_tally(["clean", "clean", "fp", "no-effect", "no-effect", "unmeasured"])
        assert t == {
            "scored": 3,
            "false_positives": 1,
            "no_effect": 2,
            "unmeasured": 1,
        }, t

    def test_a_run_where_nothing_could_fire_scores_zero_over_zero_not_zero_percent(self):
        # The headline case. Before MP-75 this printed `0/8 = 0%` - a good-looking number
        # over eight trials that could not have failed.
        t = fp_tally(["no-effect"] * 8)
        assert t["scored"] == 0, "invariant trials inflated the denominator"
        assert t["false_positives"] == 0

    def test_a_false_positive_is_in_BOTH_numerator_and_denominator(self):
        t = fp_tally(["fp"])
        assert (t["false_positives"], t["scored"]) == (1, 1), t

    def test_no_outcome_can_reach_the_numerator_without_the_denominator(self):
        """Guards the table directly: a numerator-without-denominator row would let the rate
        exceed 1.0, or divide by zero on a run whose only outcome was that row."""
        for name, (in_denom, in_numer, _bucket, _label) in FP_OUTCOMES.items():
            assert not (in_numer and not in_denom), (
                f"outcome {name!r} counts toward the false-positive numerator but not the "
                "denominator, which makes the published rate meaningless."
            )

    def test_every_outcome_fp_outcome_can_return_is_in_the_table(self):
        """A new outcome that fp_outcome returns but the table lacks would KeyError mid-run,
        after the API calls were paid for."""
        produced = {
            fp_outcome(TestFpOutcome._R(DiffVerdict.unchanged, 1.0)),
            fp_outcome(TestFpOutcome._R(DiffVerdict.unchanged, 0.9)),
            fp_outcome(TestFpOutcome._R(DiffVerdict.regression, 0.99)),
            fp_outcome(TestFpOutcome._R(DiffVerdict.changed_minor, 0.99)),
            fp_outcome(TestFpOutcome._R(DiffVerdict.insufficient_evidence, 0.5)),
        }
        assert produced <= set(FP_OUTCOMES), produced - set(FP_OUTCOMES)


class _Res:
    def __init__(self, verdict, confidence):
        self.verdict, self.confidence = verdict, confidence


_REP = {"tools": 1, "args": 1, "text": 1}


class TestFpReport:
    """The CALL SITE. [M] fp-guardian, second review: with this loop inline in `main()`,
    substituting `classify(r.verdict)` for `fp_outcome(r)` restored the exact pre-MP-75
    accounting - `0/8 = 0%` straight back - and all 281 tests stayed green, because
    `classify` returns three strings that are all valid FP_OUTCOMES keys. Pinning the
    decision function was not enough."""

    def test_an_all_invariant_run_scores_nothing_rather_than_zero_percent(self):
        rows = [(f"s{i}", _Res(DiffVerdict.unchanged, 1.0), _REP, _REP) for i in range(8)]
        t, lines = fp_report(rows)
        assert t["scored"] == 0, (
            f"8 trials that could not have fired reached the denominator ({t}). That is the "
            "pre-MP-75 accounting: it published `0/8 = 0%` for a run that tested nothing."
        )
        assert t["no_effect"] == 8
        assert len(lines) == 8

    def test_swapping_the_decision_for_classify_changes_the_published_numbers(self):
        """Kills the exact mutant that survived the first fix.

        `classify` and `fp_outcome` agree on flagged and abstaining verdicts and DISAGREE on
        exactly the case MP-75 exists for: `unchanged` at confidence 1.0.
        """
        r = _Res(DiffVerdict.unchanged, 1.0)
        assert classify(r.verdict) == "clean"
        assert fp_outcome(r) == "no-effect"
        assert fp_tally([classify(r.verdict)])["scored"] == 1
        assert fp_tally([fp_outcome(r)])["scored"] == 0
        # ...and the arm must use the latter.
        assert fp_report([("s", r, _REP, _REP)])[0]["scored"] == 0

    def test_a_false_positive_survives_the_whole_arm(self):
        rows = [
            ("bad", _Res(DiffVerdict.regression, 0.992), _REP, _REP),
            ("ok", _Res(DiffVerdict.unchanged, 0.9), _REP, _REP),
            ("dead", _Res(DiffVerdict.unchanged, 1.0), _REP, _REP),
        ]
        t, _ = fp_report(rows)
        assert (t["false_positives"], t["scored"]) == (1, 2), t

    def test_provider_errors_are_counted_not_silently_dropped(self):
        """`scored + no_effect + unmeasured` need not equal len(scenarios), and the banner
        used to claim 'every scenario was unmeasured or invariant' - false on an all-error
        run, which would print 0/0 with both coverage counters at zero."""
        t, lines = fp_report([("a", None, None, None), ("b", None, None, None)])
        assert t["errors"] == 2, t
        assert t["scored"] == 0 and lines == []


class TestUpperBound:
    """[M] fp-guardian: `1 - alpha**(1/n)` is the Clopper-Pearson bound ONLY at k=0. At
    k/n above ~0.31 it returns a bound BELOW the observed rate - a self-contradicting
    number about the north-star metric, flattering it. This harness is aimed at the surface
    where k > 0 is the expected outcome."""

    @pytest.mark.parametrize(
        "k,n,expected",
        [(0, 8, 0.312), (1, 8, 0.471), (2, 8, 0.600), (4, 8, 0.807), (1, 6, 0.582), (0, 1, 0.950)],
    )
    def test_matches_the_exact_clopper_pearson_bound(self, k, n, expected):
        assert abs(upper_bound_95(k, n) - expected) < 0.002

    def test_the_bound_is_never_below_the_observed_rate(self):
        """The self-contradiction guard. The old closed form violated this from 3/8 up."""
        for n in range(1, 13):
            for k in range(n + 1):
                assert upper_bound_95(k, n) >= k / n - 1e-9, f"{k}/{n}"

    def test_degenerate_inputs_do_not_explode(self):
        assert upper_bound_95(0, 0) == 1.0
        assert upper_bound_95(8, 8) == 1.0


def test_main_does_not_exclude_inside_the_recall_arm():
    """ADR-0022's invariant AS IT APPLIES TO `main()` only. Read the scope note below before
    trusting this test to cover more than it does.

    **This guard went vestigial when MP-79 extracted the arm, and its scope is now narrow.**
    It slices from the `[ARM:RECALL]` marker, which before MP-79 contained the whole
    recall arm and now contains ~700 characters of `main()`; `recall_outcome` is defined far
    above the marker. [M] mutation-sentinel 2026-08-23 measured the consequence: putting
    `measurable()` or `fp_outcome()` into `recall_outcome` - the two mutations this test was
    written for - leaves it GREEN. Across 28 mutants its assertions never fired once.

    The real guard is now `tests/test_recall_arm.py::test_the_recall_arm_never_adopts_the_fp
    _arms_exclusion`, which walks `co_names` over `recall_outcome` / `recall_report` /
    `recall_tally` / `main` instead of grepping a text window, and which kills both. Kept
    here, renamed and rescoped, because `main()` is still the one layer no unit test reaches
    and a `continue` added inline there would bypass the pure functions entirely.
    """
    src = (Path(__file__).resolve().parent.parent / "scripts" / "fp_measurement.py").read_text(
        encoding="utf-8"
    )
    recall = src[src.index("[ARM:RECALL]") :]
    assert "measurable(" not in recall, (
        "main()'s recall arm now excludes trials the way the FP arm does. A perturbed "
        "scenario that still reads `unchanged` is a MISS, not an unmeasurable trial; "
        "excluding it lets a dead engine report 0/0 recall. See ADR-0022."
    )
    assert "fp_outcome(" not in recall, "main()'s recall arm must not use fp_outcome()"


class TestFpSummary:
    """What actually reaches the operator. [M] fp-guardian killed two mutants living here:
    reverting the interval to the closed form, and deleting the interval line entirely.
    Both left the suite green while the helper itself was fully tested."""

    @staticmethod
    def _t(fp=0, scored=0, no_effect=0, unmeasured=0, errors=0):
        return {
            "false_positives": fp,
            "scored": scored,
            "no_effect": no_effect,
            "unmeasured": unmeasured,
            "errors": errors,
        }

    def test_the_summary_uses_the_exact_bound_not_the_closed_form(self):
        """Kills the mutant that reverts to `1 - 0.05**(1/n)`. At 4/8 that prints 31.2% -
        an upper bound BELOW the 50% observed rate."""
        out = "\n".join(fp_summary(self._t(fp=4, scored=8)))
        assert "80.7%" in out, f"expected the exact Clopper-Pearson bound for 4/8: {out}"
        assert "31.2%" not in out, f"the closed form is back: {out}"

    def test_the_published_bound_is_never_below_the_published_rate(self):
        for fp, scored in [(0, 8), (1, 8), (3, 8), (4, 8), (6, 8), (1, 1)]:
            out = "\n".join(fp_summary(self._t(fp=fp, scored=scored)))
            assert "upper bound" in out, f"the interval line vanished for {fp}/{scored}"
            pct = float(out.split("upper bound on the true rate: ")[1].split("%")[0])
            assert pct >= 100 * fp / scored - 0.1, f"{fp}/{scored}: bound {pct}% < observed"

    def test_a_run_that_measured_nothing_says_so_loudly(self):
        out = "\n".join(fp_summary(self._t(no_effect=8)))
        assert "0/0 = n/a" in out
        assert "THIS RUN MEASURED NOTHING" in out, out
        assert "upper bound" not in out, "an unbounded run must not print a bound"

    def test_the_coverage_counters_are_always_published_beside_the_rate(self):
        out = "\n".join(fp_summary(self._t(fp=1, scored=4, no_effect=3, unmeasured=2)))
        assert "Unmeasured (excluded from the rate): 2" in out
        assert "Could not have fired at ALPHA (excluded from the rate): 3" in out
        assert "1/4 = 25%" in out

    def test_provider_errors_are_surfaced_when_present_and_silent_when_not(self):
        assert any("Provider errors" in x for x in fp_summary(self._t(scored=1, errors=2)))
        assert not any("Provider errors" in x for x in fp_summary(self._t(scored=1)))


def _fp_arm_source() -> str:
    """The FP arm's slice of `main()`. Grepped, because `main()` needs a provider and
    ADR-0006 forbids a live call — the same technique as the recall-arm guard above."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "fp_measurement.py").read_text(
        encoding="utf-8"
    )
    return src[src.index("[ARM:FP]") : src.index("[ARM:RECALL]")]


def test_the_fp_arm_publishes_only_through_the_pinned_helpers():
    """ADR-0022's own lesson, applied one level further up.

    [M] fp-guardian, third review: `main()` is the caller now, and it was not pinned.
    Inlining `fp_tally([classify(r.verdict) ...])` there restored the pre-MP-75 accounting
    (`0/8 = 0%`) with 299 tests green; replacing the summary print-loop with `pass` made
    every published number vanish, also with 299 green. Extracting a pure function does not
    pin the code that calls it — this test is the third and last place that lesson applies.
    """
    arm = _fp_arm_source()
    assert "fp_report(" in arm, "the FP arm stopped going through fp_report()"
    assert "fp_summary(" in arm, "the FP arm stopped publishing through fp_summary()"
    # BOTH loops, by name and by count. [M] mutation-sentinel 2026-08-23, found while
    # measuring MP-79's recall arm: this hole is SYMMETRIC and it was here first. Deleting
    # `for line in lines_out: print(line)` - every per-scenario false-positive line - left all
    # 332 tests green, because the surviving fp_summary loop satisfies a bare `"print(line)"
    # in arm`. The per-scenario content is covered by TestFpReport; its consumption was not.
    assert "lines_out = fp_report(" in arm, "fp_report's per-scenario lines are no longer bound"
    assert "for line in lines_out:" in arm, "the per-scenario FP lines are no longer printed"
    assert "for line in fp_summary(" in arm, "fp_summary's lines are no longer printed"
    assert arm.count("print(line)") == 2, (
        f"the FP arm has {arm.count('print(line)')} print loops, expected 2 (per-scenario "
        "evidence, then summary). One of them has been dropped."
    )
    assert "classify(" not in arm, (
        "the FP arm calls classify() directly. classify() has no exclusion, so this is the "
        "pre-MP-75 accounting restored: invariant trials return to the denominator and the "
        "rate reads 0/8 = 0% again."
    )
    assert "fp_tally(" not in arm, "the FP arm bypasses fp_report() to tally directly"


def test_classify_refuses_a_verdict_it_has_no_bucket_for():
    """[M] the `raise` is unreachable today (all four DiffVerdict members are covered), which
    is exactly why it needs a test: nothing else would notice it being softened back to a
    `return "clean"` fallthrough, which silently scores an unknown verdict as a passed trial."""
    with pytest.raises(ValueError, match="no false-positive bucket"):
        classify("a_fifth_verdict")


def test_the_exclusion_label_does_not_claim_no_effect_was_measured():
    """[M] fp-guardian: `unchanged` at confidence 1.0 is BROADER than "nothing moved" —
    golden pairs 3 and 4 (tests/test_diff.py:117-119) have genuinely different tool
    distributions and still score p=1.00 everywhere. A label saying "no effect measured"
    would be false about them, in operator-facing output."""
    label = FP_OUTCOMES["no-effect"][3]
    assert "could not have fired" in label, label
    assert "no effect" not in label.lower(), (
        f"the exclusion label claims no effect was measured: {label!r}. Effects can be "
        "measured and still be unable to fire — say that instead."
    )


# --- MP-89: role refusal + repeats -----------------------------------------------------
#
# [M] The documented FP command (`--scenarios-dir examples/calibration`) collected all 13
# files: the 7 `arg_*` authored to PRICE a false-positive rate AND the 6 semantic scenarios
# `MIN_SEMANTIC_DELTA` was FITTED on. A rate over that denominator is in-sample for 6 of 13 --
# ADR-0025's "fitted on or scored on, never both", violated by the tool rather than by a human.


class TestRoleRefusal:
    @staticmethod
    def _scn(*ids):
        return [SimpleNamespace(id=i) for i in ids]

    def test_a_multi_role_directory_is_REFUSED_not_filtered(self):
        """The row's own wording: refuse, do NOT filter. A silent filter leaves the operator
        believing they measured the directory they named; a refusal makes them say which role
        they meant on the COMMAND LINE -- the surface they read, where a README cannot reach."""
        rm = {"fit": ["a"], "score": ["b"]}
        with pytest.raises(SystemExit) as exc:
            select_by_role(self._scn("a", "b"), rm, None)
        msg = str(exc.value)
        assert "2 roles" in msg and "fit" in msg and "score" in msg, msg
        assert "ADR-0025" in msg, "the refusal must cite the invariant it protects"
        assert "--role" in msg, "a refusal that does not say how to proceed is a dead end"

    def test_a_single_role_directory_runs_untouched(self):
        """Guards the direction that would break every existing caller: `examples/suite` is
        one role, and adding a manifest must not start demanding a flag for it."""
        picked, note = select_by_role(self._scn("a", "b"), {"score": ["a", "b"]}, None)
        assert [s.id for s in picked] == ["a", "b"]
        assert "score" in note

    def test_an_undeclared_directory_is_not_blocked(self):
        """A user pointing this at their OWN scenarios has no manifest entry and must not be
        held hostage by one."""
        picked, note = select_by_role(self._scn("x"), {}, None)
        assert [s.id for s in picked] == ["x"]
        assert "undeclared" in note

    def test_naming_a_role_selects_exactly_the_declared_ids(self):
        picked, note = select_by_role(
            self._scn("a", "b", "c"), {"fit": ["a"], "score": ["b", "c"]}, "score"
        )
        assert [s.id for s in picked] == ["b", "c"]
        assert "2 of 3" in note, note

    def test_an_empty_declared_role_raises_rather_than_measuring_nothing(self):
        """[M] THE bug this suite exists to have caught. The first cut read `ids` from the
        manifest, whose real key is `scenarios`, so every role resolved EMPTY: the refusal
        still fired correctly and `--role score` silently selected 0 scenarios, which the run
        would then have reported as `0/0` -- indistinguishable from ADR-0022 excluding
        everything. A partial success that looks like a working feature."""
        with pytest.raises(SystemExit, match="names no scenarios"):
            select_by_role(self._scn("a"), {"score": []}, "score")

    def test_an_undeclared_role_name_is_rejected_with_what_is_available(self):
        with pytest.raises(SystemExit) as exc:
            select_by_role(self._scn("a"), {"fit": ["a"]}, "score")
        assert "fit" in str(exc.value)

    def test_a_declared_id_missing_from_disk_is_an_error_not_a_smaller_n(self):
        """Silently scoring 6 of 7 would shrink the denominator without saying so -- the
        exact failure mode `fp_summary` publishes coverage counters to prevent."""
        with pytest.raises(SystemExit, match="absent from the directory"):
            select_by_role(self._scn("a"), {"score": ["a", "b"]}, "score")


class TestRolesManifestIsReadCorrectly:
    def test_the_real_manifest_resolves_calibrations_two_roles(self):
        """Reads the SHIPPED examples/roles.json, not a fixture. [M] This is what catches a
        key rename: a fixture-only test agreed with the wrong key name `ids` and passed."""
        rm = roles_for_dir(load_role_sets(), "examples/calibration")
        assert set(rm) == {"fit", "score"}, rm
        assert len(rm["score"]) == 7, rm["score"]
        assert len(rm["fit"]) == 6, rm["fit"]
        assert all(i.startswith("arg_") for i in rm["score"]), rm["score"]

    def test_the_directory_matches_however_the_path_is_spelled(self):
        sets = load_role_sets()
        base = roles_for_dir(sets, "examples/calibration")
        for spelling in ("examples/calibration/", "calibration", "./examples/calibration"):
            assert roles_for_dir(sets, spelling) == base, spelling

    def test_a_missing_manifest_is_empty_not_an_exception(self):
        assert load_role_sets("no/such/roles.json") == []
