"""MP-79 - the detection arm must not be able to report a number it did not measure.

MP-75 spent four rounds hardening the FALSE-POSITIVE arm and left the DETECTION arm inline in
`main()`, where no test reaches. `[M]` bug repro 2026-08-23 ran the same mutant battery
against it. **Eight of nine mutants survived a full green suite:**

    detected += int(caught)  ->  detected += 1                  302 passed
    caught = kind == "fp"    ->  caught = True                   302 passed
    caught = kind == "fp"    ->  caught = kind != "unmeasured"   302 passed   (same mutant:
                                   the abstention has already `continue`d, so it is always True)
    checked += 1             ->  checked += 2                    302 passed
    checked += 1 hoisted above the abstention `continue`         302 passed
    delete the `unmeasured` exclusion block (ADR-0018)           302 passed
    whole per-scenario body  ->  `continue`  (-20 lines, 0/0)    302 passed
    delete both closing print() calls                            302 passed
    classify(r.verdict)      ->  fp_outcome(r)                   1 FAILED  <- the only kill

The single kill came from a source grep, and it landed on the mutant with the LEAST effect on
the published numbers: after the abstention `continue`, `classify`'s "clean" and `fp_outcome`'s
"no-effect" both score a MISS anyway. Every mutant that actually moved a number survived.

So the harness could have printed `Detection: 3/3 injected regressions caught` for an engine
that flagged nothing at all - and since #46 withdrew the false-positive claim (ADR-0022),
detection is the only half of the DoD the project still asserts.

No provider is needed for any of this: ADR-0006 forbids a live call from the suite, which is
why the arm is a pure function over rows and why `main()` itself is pinned by grep.
"""

import inspect
import re
import sys
from pathlib import Path
from types import CodeType

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.diff import DiffVerdict  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    FP_OUTCOMES,
    PERTURBATIONS,
    RECALL_OUTCOMES,
    build_row,
    fp_outcome,
    main,
    recall_outcome,
    recall_report,
    recall_summary,
    recall_tally,
    upper_bound_95,
)


class _Res:
    """The three fields the recall arm reads off a DiffResult."""

    def __init__(self, verdict, confidence, explanation="why"):
        self.verdict, self.confidence, self.explanation = verdict, confidence, explanation


_REP = {"tools": 1, "args": 1, "text": 1}

#: Confidences spanning the engine's whole observed range, including the two extremes that
#: matter: 1.0 (the FP arm's exclusion trigger) and 0.0.
_CONFIDENCES = (0.0, 0.5, 0.952, 0.992, 0.996, 1.0)


class TestRecallOutcome:
    """The per-scenario decision. Every mutant below lived here and survived."""

    def test_a_flagged_verdict_is_a_detection_at_every_confidence(self):
        for verdict in (DiffVerdict.regression, DiffVerdict.changed_minor):
            for conf in _CONFIDENCES:
                assert recall_outcome(_Res(verdict, conf)) == "detected", (
                    f"{verdict.value} at confidence {conf} was not credited as a detection; "
                    "the injected perturbation was caught and must count."
                )

    def test_a_perturbed_scenario_that_still_reads_unchanged_is_a_miss(self):
        assert recall_outcome(_Res(DiffVerdict.unchanged, 0.95)) == "missed"

    def test_an_INVARIANT_perturbed_scenario_is_a_MISS_not_an_exclusion(self):
        """THE test this file exists for. ADR-0022's closing paragraph, made executable.

        `unchanged` at confidence 1.0 is precisely the trial the FP arm EXCLUDES - nothing
        could have fired. The recall arm must score it as a MISS anyway, because the
        perturbation was a real injected behaviour change and the candidate answered
        identically regardless. Excluding it lets a dead engine post `0/0` recall, which
        reads as "nothing to report" rather than "the engine caught nothing".

        [M] FP review, the MP-75 review recorded in ADR-0022: inserting
        `if not measurable(r): continue` into the recall arm left all 281 tests green.
        """
        r = _Res(DiffVerdict.unchanged, 1.0)
        assert recall_outcome(r) == "missed", (
            "an invariant perturbed trial was excluded from the detection denominator. That "
            "is the FP arm's exclusion adopted by the wrong arm: it converts every miss on a "
            "deterministic model into a non-event, and a dead engine then reports 0/0."
        )
        # ...and the SAME trial is excluded by the FP arm. Both readings are correct; they
        # are different questions. If these two ever agree, one arm has adopted the other's.
        assert fp_outcome(r) == "no-effect"

    def test_an_abstention_is_excluded_rather_than_scored_as_a_miss(self):
        """ADR-0018, which is a different exclusion from ADR-0022's and the only one this
        arm has. A run that reached no verdict measured nothing, so it is neither a
        detection nor a miss. [M] deleting this branch entirely left all 302 green."""
        assert recall_outcome(_Res(DiffVerdict.insufficient_evidence, 0.5)) == "unmeasured"

    def test_the_three_outcomes_are_distinguishable(self):
        """Guards the wiring, not just the predicate: collapse any branch and this fails."""
        outcomes = {
            recall_outcome(_Res(DiffVerdict.regression, 0.99)),
            recall_outcome(_Res(DiffVerdict.unchanged, 1.0)),
            recall_outcome(_Res(DiffVerdict.insufficient_evidence, 0.5)),
        }
        assert outcomes == {"detected", "missed", "unmeasured"}, outcomes


class TestRecallTally:
    """The accounting. [M] `detected += 1` - the mutant the row was filed on - lives here."""

    def test_only_detections_reach_the_numerator(self):
        t = recall_tally(["detected", "missed", "missed", "unmeasured"])
        assert t == {"checked": 3, "detected": 1, "unmeasured": 1}, t

    def test_a_dead_engine_scores_zero_not_the_number_of_scenarios(self):
        """The headline mutant, at the tally level: `detected += 1` would return 3 here."""
        t = recall_tally(["missed", "missed", "missed"])
        assert t["detected"] == 0, (
            f"three missed regressions were credited as {t['detected']} detections. This is "
            "`detected += 1`: the arm reports 100% detection for an engine that flagged "
            "nothing."
        )
        assert t["checked"] == 3, "a miss must stay in the denominator"

    def test_an_abstention_is_in_neither_numerator_nor_denominator(self):
        t = recall_tally(["unmeasured"] * 4)
        assert (t["detected"], t["checked"]) == (0, 0), t
        assert t["unmeasured"] == 4, "the exclusion must still be published"

    def test_no_outcome_reaches_the_numerator_without_the_denominator(self):
        """A numerator-without-denominator row would let detection exceed 1.0, or divide by
        zero on a run whose only outcome was that row."""
        for name, (in_denom, in_numer, _bucket, _label) in RECALL_OUTCOMES.items():
            assert not (in_numer and not in_denom), (
                f"outcome {name!r} counts toward the detection numerator but not the "
                "denominator, which makes the published fraction meaningless."
            )

    def test_every_outcome_recall_outcome_can_return_is_in_the_table(self):
        """A returned outcome the table lacks would KeyError mid-run, after the API calls
        were paid for."""
        produced = {recall_outcome(_Res(v, c)) for v in DiffVerdict for c in (0.5, 1.0)}
        assert produced <= set(RECALL_OUTCOMES), produced - set(RECALL_OUTCOMES)

    def test_the_two_arms_vocabularies_are_not_interchangeable(self):
        """Why swapping one arm's decision function for the other's cannot pass silently.

        [M] the equivalent swap in the FP arm (`classify` for `fp_outcome`) restored the
        withdrawn `0/8 = 0%` with 281 tests green, precisely because every string `classify`
        returned was a valid FP_OUTCOMES key. These two tables must never acquire that
        property: "clean" / "fp" / "no-effect" have to be KeyErrors here.
        """
        assert set(FP_OUTCOMES) & set(RECALL_OUTCOMES) == {"unmeasured"}
        for foreign in ("clean", "fp", "no-effect"):
            with pytest.raises(KeyError):
                recall_tally([foreign])


class TestRecallReport:
    """The CALL SITE - the layer MP-75 needed three separate rounds to learn to pin.
    [M] replacing this loop's inline body with `continue` left all 302 tests green."""

    def test_an_engine_that_flags_nothing_reports_zero_over_three(self):
        """The whole point. Three injected regressions, engine silent on all three."""
        rows = [(f"s{i}", _Res(DiffVerdict.unchanged, 1.0), _REP, _REP) for i in range(3)]
        t, lines = recall_report(rows)
        assert (t["detected"], t["checked"]) == (0, 3), (
            f"a dead engine reported {t['detected']}/{t['checked']} detection. Either the "
            "numerator is unconditional or the invariant trials were excluded; both let the "
            "harness certify an engine that caught nothing."
        )
        assert t["unmeasured"] == 0, "a miss is not an exclusion"
        assert len(lines) == 3

    def test_a_working_engine_scores_every_flag(self):
        rows = [("a", _Res(DiffVerdict.regression, 0.99), _REP, _REP)]
        rows.append(("b", _Res(DiffVerdict.changed_minor, 0.97), _REP, _REP))
        t, _ = recall_report(rows)
        assert (t["detected"], t["checked"]) == (2, 2), t

    def test_a_mixed_run_reports_the_real_fraction(self):
        rows = [
            ("caught", _Res(DiffVerdict.regression, 0.99), _REP, _REP),
            ("missed", _Res(DiffVerdict.unchanged, 0.9), _REP, _REP),
            ("invariant", _Res(DiffVerdict.unchanged, 1.0), _REP, _REP),
            ("abstained", _Res(DiffVerdict.insufficient_evidence, 0.5), _REP, _REP),
        ]
        t, lines = recall_report(rows)
        assert (t["detected"], t["checked"], t["unmeasured"]) == (1, 3, 1), t
        assert len(lines) == 4, "every scenario gets a line, exclusions included"

    def test_the_per_scenario_lines_name_what_happened(self):
        """Kills the body-to-`continue` mutant at the line level: a silent arm prints
        nothing, and an operator watching a paid run sees no per-scenario evidence."""
        _, lines = recall_report([("refund_request", _Res(DiffVerdict.unchanged, 1.0), _REP, _REP)])
        assert "refund_request" in lines[0]
        assert "MISSED" in lines[0], lines[0]

    def test_provider_errors_are_counted_not_silently_dropped(self):
        """`main()` used to `continue` past these, so a run where every perturbed scenario
        failed to reach the API printed `Detection: 0/0` with nothing explaining why."""
        t, lines = recall_report([("a", None, None, None), ("b", None, None, None)])
        assert t["errors"] == 2, t
        assert (t["checked"], t["detected"]) == (0, 0)
        assert lines == []

    def test_swapping_in_the_fp_arms_decision_cannot_pass_quietly(self):
        """The mutant the old source-grep guard caught, now caught behaviourally.

        `recall_outcome` and `fp_outcome` disagree on exactly the trial ADR-0022 is about,
        and the disagreement is a KeyError rather than a wrong number - so this cannot decay
        into a silently-plausible substitution the way the FP arm's did.
        """
        r = _Res(DiffVerdict.unchanged, 1.0)
        assert recall_outcome(r) == "missed" and fp_outcome(r) == "no-effect"
        assert recall_tally([recall_outcome(r)])["checked"] == 1
        with pytest.raises(KeyError):
            recall_tally([fp_outcome(r)])


class TestRecallSummary:
    """What reaches the operator. [M] deleting both closing print() calls left 302 green."""

    @staticmethod
    def _t(detected=0, checked=0, unmeasured=0, errors=0):
        return {
            "detected": detected,
            "checked": checked,
            "unmeasured": unmeasured,
            "errors": errors,
        }

    def test_the_detection_fraction_reaches_the_operator(self):
        out = "\n".join(recall_summary(self._t(detected=1, checked=3)))
        assert "Detection: 1/3 injected perturbations caught" in out, out
        # SINGULAR, deliberately: it subsumes the plural. [M] FP review 2026-08-23 -
        # the first version of this pin read "injected regressions" and the CHECKED-NOTHING
        # banner ("no injected regression reached a verdict") walked straight past it. A
        # guard that reads as a vocabulary pin and is really a plural-string pin.
        for tally in (self._t(detected=1, checked=3), self._t(), self._t(errors=2)):
            body = " ".join(recall_summary(tally))
            assert "injected regression" not in body, (
                f"the arm calls the injected instructions `regressions`, asserting the very "
                f"premise it exists to test: {body!r}. That is the overclaim FP review "
                "blocked in the MISSED note."
            )

    def test_a_run_that_checked_nothing_says_so_loudly(self):
        """`0/0` is the number a dead engine plus a wrong exclusion produces together. It
        must never read as a quiet pass."""
        out = "\n".join(recall_summary(self._t(unmeasured=3)))
        assert "Detection: 0/0" in out
        assert "CHECKED NOTHING" in out, out
        # [M] MP-82: `1 - upper_bound_95(0, 0)` is 0.0 via the helper's `n <= 0` guard, so an
        # ungated interval prints `0.0%` - a measured-looking floor directly above a banner
        # saying nothing was measured. This is the one MP-82 shape the doc's verbatim quote
        # provably cannot see: patching the bound in on `checked == 0` ALONE left 356 green.
        assert "lower bound" not in out, (
            f"a run that checked nothing published an interval: {out!r}. 0/0 has no floor to "
            "report; the FP arm gates its bound on `scored` for the same reason."
        )

    def test_an_all_error_run_blames_the_network_not_the_engine(self):
        out = "\n".join(recall_summary(self._t(errors=3)))
        assert "REACHED NO VERDICT" in out, out
        assert "connectivity" in out
        assert "lower bound" not in out, (
            f"a run where every scenario errored published an interval: {out!r}. The bound "
            "would describe the network, not the engine."
        )

    def test_a_miss_is_named_without_claiming_the_behaviour_changed(self):
        """Operator-facing wording carries ADR-0022's distinction, because the number alone
        does not: `2/3` looks like a rounding problem unless the miss is named.

        [M] FP review 2026-08-23 BLOCKED the first version of this note, which read "the
        perturbation did change the behaviour, whatever the candidate did about it". The
        perturbation changes the INSTRUCTION; whether behaviour changed is the thing being
        measured. `decline_pii` is 1 of the 3 entries in PERTURBATIONS and on the run of
        record returned `unchanged` because the model RESISTED it - so that sentence printed
        "real failure to detect" over a correct true negative.

        Why this is a test and not merely a careful author: that sentence is direct pressure
        to loosen MIN_SEMANTIC_DELTA until the "miss" converts into a detection, and MP-80
        records that the semantic sweep is FLAT from 0.1 to 0.9 - the slack is there to take.
        The accounting must not change; only the claim about what a miss proves.
        """
        out = "\n".join(recall_summary(self._t(detected=1, checked=3)))
        assert "2 perturbation(s) MISSED" in out, out
        assert "never" in out and "excluded" in out, out
        assert "ignored the injected instruction" in out, (
            f"the note no longer offers the resisted-instruction reading: {out!r}. The miss "
            "is counted either way, but the operator must not be told behaviour changed."
        )
        for overclaim in ("did change the behaviour", "real failure to detect"):
            assert overclaim not in out, (
                f"the note asserts {overclaim!r} as fact. The perturbation changes the "
                "INSTRUCTION; whether behaviour changed is what this arm is measuring."
            )

    def test_the_exclusion_counter_is_always_published_beside_the_fraction(self):
        out = "\n".join(recall_summary(self._t(detected=2, checked=2, unmeasured=5)))
        assert "Unmeasured (excluded): 5" in out, out

    def test_provider_errors_are_surfaced_when_present_and_silent_when_not(self):
        assert any("Provider errors" in x for x in recall_summary(self._t(checked=1, errors=2)))
        assert not any("Provider errors" in x for x in recall_summary(self._t(checked=1)))

    def test_a_full_run_does_not_print_a_percentage(self):
        """[M] ADR-0022 withdrew `0/8 = 0%` because a rate over a handful of trials reads as
        a property of the engine. `3/3 = 100%` is the same error mirrored. Print the fraction
        and its lower bound; never the point estimate.

        [M] FP review computed what the withheld interval would say, which is why MP-82
        exists rather than nothing: `3/3` bounds the true detection rate at only 36.8%, and
        `2/3` - the actual run of record - at 13.5%. The bare fraction is weak evidence too;
        it is just not weak in a way that reads as a claim. MP-82 took the first branch this
        test's own failure message offered; the ban on the percentage survives it unchanged.
        """
        out = "\n".join(recall_summary(self._t(detected=3, checked=3)))
        assert "100%" not in out, (
            f"the detection arm published a percentage over 3 trials: {out!r}. Print the "
            "interval with the fraction (MP-82); the point estimate is the withdrawn claim."
        )
        assert "36.8%" in out, f"the interval MP-82 added is gone from a 3/3 run: {out!r}"

        # [M] MP-82 adversary: the substring ban above went BLIND the moment this arm gained a
        # `{:.1%}`-formatted line, because `"100%" in "100.0%"` is False. It was total when the
        # arm printed no percentage at all. This is the general form it has to be now: the
        # point estimate must not appear as ANY published percentage. At d>0 the exact floor is
        # strictly below d/checked, so equality means the point estimate got published wearing
        # the interval's label - ADR-0022's withdrawn `0/8 = 0%` mirrored. (At d=0 floor and
        # observed are both 0.0% and genuinely coincide, which is why that case is exempt.)
        for detected, checked in [(3, 3), (2, 3), (5, 5), (1, 2), (4, 6), (7, 8)]:
            body = "\n".join(recall_summary(self._t(detected=detected, checked=checked)))
            observed = 100 * detected / checked
            for pct in re.findall(r"(\d+\.?\d*)%", body):
                assert abs(float(pct) - observed) > 1e-9, (
                    f"{detected}/{checked} published {pct}%, which IS the observed rate. A "
                    f"lower bound is strictly below it at d>0 (here {observed:.1f}% observed, "
                    "floor should be lower). The point estimate is wearing the bound's label."
                )

    def test_the_summary_uses_the_exact_bound_not_the_closed_form(self):
        """The recall arm's copy of `tests/test_fp_measurement_repertoire.py:386`, which is
        the ONLY guard that pins a summary to USE `upper_bound_95` rather than merely pinning
        the helper in isolation. [M] MP-75's lesson, and the landmine `ops/NOW.md` records:
        pinning a pure function does not pin its CALLER.

        Hardcoded numerals, deliberately, exactly as the FP arm's template does: a literal
        only the exact helper can produce survives any refactor of the call and cannot be
        satisfied by the closed form. Deriving the expectation from `upper_bound_95` here
        would make the test agree with a mutated helper.

        [M] THE COLLISION THAT MAKES THIS GUARD NECESSARY: the closed form `alpha**(1/n)` is
        the exact lower bound ONLY when nothing was missed. At 2/3 it prints 36.8% - which is
        also the CORRECT bound at 3/3. So the closed-form mutant produces a wrong number that
        looks like a right one, and only a tally with a miss in it can tell them apart.
        """
        out = "\n".join(recall_summary(self._t(detected=2, checked=3)))
        assert "13.5%" in out, f"expected the exact Clopper-Pearson floor for 2/3: {out!r}"
        assert "36.8%" not in out, (
            f"the closed form is back: {out!r}. 36.8% is `0.05**(1/3)`, the floor for a run "
            "that missed NOTHING - published here over a run that missed one of three."
        )
        # The WHOLE line, verbatim. [M] mutation testing: the label, the confidence level and
        # the `n=` were pinned by nothing but the hand-copied block in docs/fp-measurement.md,
        # and that guard's own failure message tells a maintainer to re-copy the doc when the
        # wording changes - so dropping `95%` from a published statistic, or publishing a wrong
        # n, went green in two edits. A literal here cannot be laundered by a doc re-copy.
        assert "  95% lower bound on the true rate: 13.5% (one-sided Clopper-Pearson, n=3)" in out

    def test_the_published_n_is_the_denominator_the_bound_was_computed_at(self):
        """[M] mutation testing: three single-token mutants of the published `n=` survived a
        green suite - `n={len(PERTURBATIONS)}`, `n={checked + unmeasured}`, and
        `n={checked + errors}`. Every asserting tally in this class used checked==3, which is
        ALSO len(PERTURBATIONS), so all three candidates coincided and nothing separated them.

        An inflated n overstates the evidence base behind the floor, and does it precisely on
        the runs that HAVE abstentions or provider errors - the runs whose coverage counters
        this arm publishes because the denominator moved. Flattering direction, on the exact
        shape the arm exists to be honest about.

        Denominators deliberately != 3, and literals rather than derived values, for the same
        reason the sibling test above hardcodes: a derived expectation agrees with a mutated
        helper. [M] verified against the shipped helper.
        """
        for expected, t in [
            ("2.5% (one-sided Clopper-Pearson, n=2)", self._t(1, 2, unmeasured=1)),
            ("0.0% (one-sided Clopper-Pearson, n=1)", self._t(0, 1)),
            ("7.6% (one-sided Clopper-Pearson, n=5)", self._t(2, 5, unmeasured=4, errors=2)),
            ("27.1% (one-sided Clopper-Pearson, n=6)", self._t(4, 6, errors=3)),
            ("54.9% (one-sided Clopper-Pearson, n=5)", self._t(5, 5)),
            ("52.9% (one-sided Clopper-Pearson, n=8)", self._t(7, 8, unmeasured=2)),
        ]:
            out = "\n".join(recall_summary(t))
            assert f"95% lower bound on the true rate: {expected}" in out, out

    def test_the_published_bound_is_never_above_the_published_fraction(self):
        """The mirror of the FP arm's `:393`. A LOWER bound above the observed rate is the
        same self-contradiction as an upper bound below it, pointing the other way - and
        `ops/decisions/ADR-0022` records that the shipped closed form once did exactly that.

        Also the only guard asserting the line EXISTS at all: [M] deleting it left every
        other recall test green except the doc's verbatim quote, which sees the 2/3 tally
        only and only while the doc holds a hand-copied duplicate.
        """
        for detected, checked in [(0, 3), (1, 3), (2, 3), (3, 3), (1, 1), (5, 5), (2, 8)]:
            out = "\n".join(recall_summary(self._t(detected=detected, checked=checked)))
            assert "lower bound" in out, f"the interval line vanished for {detected}/{checked}"
            pct = float(out.split("lower bound on the true rate: ")[1].split("%")[0])
            assert pct <= 100 * detected / checked + 0.05, (
                f"{detected}/{checked}: floor {pct}% exceeds the observed rate. A lower "
                "bound above what was measured overstates the engine."
            )

    def test_the_bound_never_travels_without_its_caveat(self):
        """[M] Both doc surfaces (`README.md`, `docs/fp-measurement.md`) print this caveat
        beside this number. A tool that published the bound bare would assert a
        Clopper-Pearson interval MORE confidently than the documents it was aligned to -
        MP-82's own defect, re-created pointing the other way.

        The perturbations are chosen one per signal (tool/refusal, format/PII,
        classification), so they are exactly NOT the exchangeable draws a binomial interval
        assumes. The bound is still the honest direction to err in; the caveat is what keeps
        it from reading as a characterisation.
        """
        for detected, checked in [(3, 3), (2, 3), (0, 3), (1, 2), (4, 6)]:
            out = "\n".join(recall_summary(self._t(detected=detected, checked=checked)))
            assert "exchangeable" in out, (
                f"{detected}/{checked} published a bound with no exchangeability caveat: "
                f"{out!r}. `docs/fp-measurement.md` and `README.md` both carry it."
            )
            # BOTH SIDES. [M] mutation testing: a mutant inverting the caveat to "which by
            # construction they ARE" was killed only by the doc's hand-copied block, so a
            # re-copy laundered it - turning the hedge into an assertion that three
            # hand-picked injections ARE exchangeable trials, in the tool and the doc at once.
            # Asserting the word alone cannot tell the caveat from its negation.
            assert (
                "which by construction they are" in out and "not - each targets" in out
            ), f"{detected}/{checked}: the caveat no longer NEGATES exchangeability: {out!r}"
            # ADR-0023: the rate's antecedent must stay on the page. Without it "the true
            # rate" reads as the rate at which real regressions are caught, which three
            # synthetic injections do not measure.
            assert "over perturbations APPLIED, not over behaviour changes" in out, (
                f"{detected}/{checked}: the bound no longer says what it is a rate OVER: "
                f"{out!r}. The denominator includes a perturbation the model resisted."
            )
            # ORDER, not just presence. "That rate" needs an antecedent immediately above it,
            # and a caveat printed before the number it qualifies reads as a caveat on the
            # fraction instead. [M] mutation testing: reordering was killed ONLY by the doc's
            # hand-copied block, which a re-copy launders.
            lines = recall_summary(self._t(detected=detected, checked=checked))
            bound_at = next(i for i, x in enumerate(lines) if "lower bound" in x)
            caveat_at = next(i for i, x in enumerate(lines) if "That rate is over" in x)
            assert bound_at < caveat_at, (
                f"{detected}/{checked}: the caveat precedes the bound it qualifies, so "
                f'"That rate" points at the fraction instead: {lines!r}'
            )

    def test_a_corrupt_tally_cannot_publish_a_flattering_bound(self):
        """[M] MP-82 arithmetic sweep: `detected > checked` is the ONE input in the whole
        space that makes the complement overstate. `1 - upper_bound_95(-1, 3)` reaches
        neither guard (`-1 >= 3` is False), leaves `range(k+1)` empty so the CDF is
        identically 0, and the bisection collapses to ~3.1e-61 - printing **100.0%**,
        certainty of perfect detection, from a tally that cannot be right.

        Unreachable through `recall_tally` today: no `RECALL_OUTCOMES` row reaches the
        numerator without the denominator. That is a property of the TABLE, not of the
        formula, and nothing asserted the formula was safe if the table ever changed. Raising
        beats clamping: a clamp would publish a plausible number for corrupt input.
        """
        with pytest.raises(ValueError, match="corrupt tally"):
            recall_summary(self._t(detected=4, checked=3))
        # and the table genuinely cannot produce it, which is why this is a tripwire
        t = recall_tally(["detected", "detected", "missed", "unmeasured"])
        assert t["detected"] <= t["checked"], t


class TestBuildRow:
    """The one path feeding BOTH denominators.

    [M] FP review 2026-08-23: as a closure inside `main()` this was unreachable by any test,
    and a poisoned version blanks the false-positive rate and the detection fraction at once
    while leaving the suite green. `verdict_fn` is injected so it can be exercised without a
    provider (ADR-0006).
    """

    def test_a_verdict_is_passed_through_with_its_repertoires(self):
        assert build_row("s", "B", "C", lambda b, c, sid: ("R", "BR", "CR")) == (
            "s",
            "R",
            "BR",
            "CR",
        )

    def test_the_base_and_candidate_reach_the_verdict_in_that_order(self):
        """A base/candidate swap is SILENT in the FP arm, which passes the same scenario
        twice. It would surface only in the recall arm, as a perturbed base against an
        unperturbed candidate - which still produces an entirely plausible verdict."""
        seen = []
        build_row("sid", "BASE", "CAND", lambda b, c, sid: seen.append((b, c, sid)) or None)
        assert seen == [("BASE", "CAND", "sid")], seen

    def test_a_provider_error_becomes_a_row_both_arms_read_as_an_error(self):
        row = build_row("s", "B", "C", lambda b, c, sid: None)
        assert row == ("s", None, None, None)
        # ...and both reporters must count it as an error rather than score it.
        assert recall_report([row])[0]["errors"] == 1
        assert recall_report([row])[0]["checked"] == 0


def _called_names(fn) -> set[str]:
    """Every global name a function's compiled code can reference, nested scopes included.

    Compiled names rather than source text. `recall_outcome`'s docstring has to MENTION
    `measurable()` in order to explain why it must never call it, and a grep cannot tell the
    two apart - [M] the first version of the guard below failed on its own explanation.
    """
    out, stack = set(), [fn.__code__]
    while stack:
        code = stack.pop()
        out |= set(code.co_names)
        stack += [c for c in code.co_consts if isinstance(c, CodeType)]
    return out


def _recall_arm_source() -> str:
    """`main()`'s recall slice. Grepped, because `main()` calls `adapter.preflight()` and
    ADR-0006 forbids a live call from the suite - the same technique the FP arm's call-site
    guard uses. `inspect.getsource(main)` rather than the whole file, so this cannot be
    fooled by moving a function above or below the banner."""
    src = inspect.getsource(main)
    return src[src.index("[ARM:RECALL]") :]


def test_each_arm_marker_occurs_exactly_once():
    """The slice markers are load-bearing for three guards across two files, and a SECOND
    occurrence silently truncates a slice to nothing rather than erroring.

    [M] 2026-08-23: introducing these markers, the explanatory comment beside them contained
    the literal tokens, so `src.index("[ARM:RECALL]")` matched the COMMENT - inside the FP
    arm - and cut the FP arm's slice down to 40 characters. It surfaced only because MP-75's
    call-site guard went red. A future edit that mentions a marker in prose gets caught here
    instead.
    """
    src = inspect.getsource(main)
    for marker in ("[ARM:FP]", "[ARM:RECALL]"):
        assert src.count(marker) == 1, (
            f"{marker} occurs {src.count(marker)} times in main(). Every source-slicing "
            "guard keys on the FIRST occurrence, so a duplicate silently shrinks a slice "
            "and the guard passes over an empty window."
        )


def test_the_recall_arm_publishes_only_through_the_pinned_helpers():
    """MP-75's landmine, applied to the other arm BEFORE it costs four rounds.

    Pinning a pure function does not pin its caller, and every extraction creates a new
    caller. `recall_report` and `recall_summary` are fully covered above - and all of that
    coverage is worth nothing if `main()` stops calling them, or recomputes the numbers
    inline beside them.
    """
    arm = _recall_arm_source()
    assert "recall_report(" in arm, "the recall arm stopped going through recall_report()"
    assert "recall_summary(" in arm, "the recall arm stopped publishing through recall_summary()"
    # BOTH loops, by name and by count. [M] mutation testing 2026-08-23: a bare
    # `assert "print(line)" in arm` is satisfied by the SUMMARY loop alone, so deleting the
    # per-scenario loop left all 332 green - and the MISSED note tells the operator to "read
    # the per-scenario explanation above", which under that mutant does not exist. The
    # content of those lines was pinned; their CONSUMPTION was not. MP-75's lesson, one notch
    # further out than MP-75 took it.
    # The BINDING as well as the loop: [M] pinning only the loop left `rt, _discard =
    # recall_report(...)` alive - the per-scenario lines silently discarded, and `main()` then
    # raising NameError at the end of a PAID run. No test runs main() (it needs a provider,
    # ADR-0006), so the producer/consumer wiring has to be asserted as a pair.
    assert "recall_lines = recall_report(" in arm, "recall_report's lines are no longer bound"
    assert "for line in recall_lines:" in arm, "the per-scenario lines are no longer printed"
    assert "for line in recall_summary(" in arm, "the summary lines are no longer printed"
    assert arm.count("print(line)") == 2, (
        f"the recall arm has {arm.count('print(line)')} print loops, expected 2 (per-scenario "
        "evidence, then summary). One of them has been dropped."
    )
    # [M] mutation testing: the banner is operator-facing stdout that no test read, so the
    # vocabulary X2/X3 forbid stayed freely reachable here - including `expect regression/
    # changed_minor`, which announces the arm's conclusion before the arm runs. That is a
    # worse overclaim than either string already pinned, and ADR-0023 exists to refuse it.
    assert "injected regression" not in arm.lower(), (
        "the recall arm's printed banner calls the injected instructions `regressions`. The "
        "summary was pinned against this and the banner was not."
    )
    assert (
        "expect regression" not in arm
    ), "the banner tells the operator what to expect before the arm has measured anything."
    assert "INJECTED PERTURBATIONS" in arm, "the arm's header no longer names itself"
    for inline in ("detected +=", "checked +="):
        assert inline not in arm, (
            f"`{inline}` is back inline in main()'s recall arm. That is the shape [M] "
            "bug repro found eight surviving mutants in; nothing below main() can pin it."
        )
    # The SELECTION expression, over the whole of main(): [M] FP review 2026-08-23 -
    # asserting bare "PERTURBATIONS" inside the sliced window does NOT close this, because
    # the selection sits above the marker and the assertion is satisfied by the unrelated
    # `PERTURBATIONS[s.id]` lookup inside the comprehension. `perturbed = []` survived all
    # 72 tests in FP review's mirror.
    assert "for s in scenarios if s.id in PERTURBATIONS" in inspect.getsource(main), (
        "main() no longer selects the perturbed scenarios from PERTURBATIONS. An empty "
        "selection is the one remaining way to make this arm report 0/0 - loud rather than "
        "silent (the CHECKED NOTHING banner fires), but nothing below main() can see it."
    )
    assert "_perturb(" in arm, "the candidate side is no longer perturbed at all"
    names = _called_names(main)
    assert "recall_tally" not in names, "main() bypasses recall_report() to tally directly"
    assert "classify" not in names, (
        "main() classifies verdicts itself again. Both arms must go through their own "
        "outcome function; classify() alone knows about neither exclusion."
    )


def test_the_recall_arm_never_adopts_the_fp_arms_exclusion():
    """ADR-0022's stated invariant, guarded across every layer that could reintroduce it.

    The behavioural test above proves `recall_outcome` returns "missed" for an invariant
    trial. This proves nobody added a second, earlier exclusion on top of it - a
    `if not measurable(r): continue` in the reporter or the caller would drop the row before
    the decision function ever saw it, and no assertion about `recall_outcome` would move.
    """
    for fn in (recall_outcome, recall_report, recall_tally, main):
        names = _called_names(fn)
        assert "measurable" not in names, (
            f"{fn.__name__} consults measurable(). A perturbed scenario that still reads "
            "`unchanged` is a MISS, not an unmeasurable trial; excluding it lets a dead "
            "engine report 0/0 recall. See ADR-0022."
        )
    for fn in (recall_outcome, recall_report, recall_tally):
        assert "fp_outcome" not in _called_names(fn), (
            f"{fn.__name__} uses the FP arm's decision function, whose vocabulary this arm "
            "does not share."
        )


# --------------------------------------------------------------------------------------
# MP-81 - the DOC must publish what the harness prints, not a hand-adjusted number.
# --------------------------------------------------------------------------------------

_FP_DOC = Path(__file__).resolve().parent.parent / "docs" / "fp-measurement.md"

#: Where the harness quote begins. Indexing the section's fenced blocks positionally would
#: silently retarget the moment a block is added above it - claims review 2026-08-24.
_QUOTE_SENTINEL = "That is what the harness prints, verbatim and unadjusted:"


def _doc_text() -> str:
    return _FP_DOC.read_text(encoding="utf-8")


def _doc_detection_section() -> str:
    """The Results-section detection block, ending before `### Corroborating evidence`."""
    text = _doc_text()
    start = text.index("**Detection:")
    return text[start : text.index("### Corroborating evidence", start)]


def _doc_live_prose(section: str) -> str:
    """The text minus the WITHDRAWAL notes - not minus every blockquote.

    Three iterations, each hole found by RUNNING the battery rather than by reading the test:

    1. `split("> **Corrected")[0]` was positional, so re-asserting a withdrawn claim BELOW the
       note passed, and moving the note to the top of the section would have disabled the
       guard entirely (`[M]` FP review 2026-08-24).
    2. Section-scoped, so the same sentence one line ABOVE the `**Detection:` headline passed
       all 37 tests (`[M]` found re-running the battery after fixing 1).
    3. Stripping EVERY blockquote line, this version's predecessor: `[M]` FP review
       2026-08-24 inserted a `> **In brief.**` pull-quote directly under `## Results` carrying
       all three withdrawn claims, in the most-read position on the page, and got 37 green.
       Blockquotes are how this repo withdraws a claim, but nothing MAKES a blockquote a
       withdrawal - so only a quote opening with the `**Corrected` marker is exempt.
    """
    out: list[str] = []
    in_withdrawal = False
    for ln in section.splitlines():
        if not ln.strip():
            # `[M]` FP review 2026-08-24, third pass: this used to `continue` BEFORE
            # resetting, so a pull-quote separated from a withdrawal note by a blank line
            # inherited its exemption - the same `> **In brief.**` bypass, green again one
            # paragraph lower. A blank line ends the note.
            in_withdrawal = False
            continue
        if ln.lstrip().startswith(">"):
            if "**Corrected" in ln:
                in_withdrawal = True
            if not in_withdrawal:
                out.append(ln)
            continue
        in_withdrawal = False
        out.append(ln)
    return "\n".join(out)


def _scannable(text: str) -> str:
    """Live prose with markdown emphasis and line wrapping normalised away.

    `[M]` FP review 2026-08-24, third pass: the banned-claim scan missed *"a miss is
    a false *negative*, not a false alarm"* sitting 65 lines below its own correction, for
    TWO independent reasons - the emphasis markers (`*negative*`, not the `_negative_` the
    ban listed) and a hard wrap between "is" and "a false". A substring scan over raw
    markdown is a guard that reads as coverage and provides none against ordinary editing.
    """
    return re.sub(r"[*_`]", "", " ".join(text.split()))


def _doc_run_of_record(section: str) -> list[str]:
    """The outcomes the doc's own verdict table records, in `recall_outcome` vocabulary."""
    rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
    assert rows, "the detection table vanished; MP-81 restored it deliberately"
    outcomes = []
    for row in rows:
        outcome = row.split("|")[3]
        assert ("MISSED" in outcome) != ("detected" in outcome), (
            f"table row is neither a detection nor a miss, so the arm's vocabulary has "
            f"drifted out of the doc: {row!r}"
        )
        outcomes.append("missed" if "MISSED" in outcome else "detected")
    return outcomes


def test_the_doc_quotes_the_harness_verbatim_for_the_run_it_publishes():
    """MP-81's regression guard: the published block must be the block the harness prints.

    `[M]` 2026-08-23. `docs/fp-measurement.md:68,76` published `Detection: 2/2` and *"every
    perturbation that actually changed behavior was caught"* for a run the harness scored
    **2/3** - `decline_pii` removed from the denominator by the argument ADR-0023 rejects.
    Nothing caught it for the length of two ADRs, because no test connected the document to
    the function whose output it claimed to be reporting. This is that connection.

    WHAT THIS DOES NOT PROVE, stated because it reads stronger than it is: both sides come
    from the document, so this pins the doc's INTERNAL consistency and its fidelity to
    `recall_summary`'s wording - not that the table matches a run that actually happened.
    `[M]` FP review 2026-08-24: a self-consistent two-edit forgery (flip the table row to
    `detected`, edit the block to 3/3, drop the NOTE) survives this assertion alone. The
    scenario-id check below closes it against `PERTURBATIONS`; closing it against the run
    itself needs a committed artifact of the run, which the repo does not have (MP-83).
    """
    section = _doc_detection_section()
    outcomes = _doc_run_of_record(section)

    expected = "\n".join(ln for ln in recall_summary(recall_tally(outcomes)) if ln.strip())
    quoted = section.split(_QUOTE_SENTINEL, 1)[1].split("```")[1]
    got = "\n".join(ln for ln in quoted.splitlines() if ln.strip())

    assert got == expected, (
        "docs/fp-measurement.md quotes a detection block the harness does not print.\n"
        f"--- harness ---\n{expected}\n--- doc ---\n{got}\n"
        "Publish what the harness prints (MP-81); if recall_summary's wording changed, "
        "re-copy it here rather than paraphrasing."
    )


def test_the_docs_table_covers_exactly_the_perturbations_the_harness_injects():
    """Anchors the doc's table to CODE, not to itself.

    Without this, dropping the inconvenient row and editing the quoted fraction to match is a
    self-consistent edit every other assertion here accepts - a mechanised version of the
    exact defect MP-81 fixed.
    """
    ids = {
        row.split("`")[1] for row in _doc_detection_section().splitlines() if row.startswith("| `")
    }
    assert ids == set(PERTURBATIONS), (
        f"the doc's detection table lists {sorted(ids)} but the harness injects "
        f"{sorted(PERTURBATIONS)}. A row dropped here shrinks the published denominator, "
        "which is precisely how this document came to publish 2/2 for a 2/3 run."
    )


def test_the_prose_headline_matches_the_fraction_the_harness_printed():
    """`[M]` claims review 2026-08-24: the headline `2 of 3` could be edited to `3 of 3`
    while the fenced block still said `2/3`, and the suite stayed green. The headline is the
    sentence a skimmer reads; it may not disagree with the block beneath it.
    """
    section = _doc_detection_section()
    t = recall_tally(_doc_run_of_record(section))
    headline = section.split("\n", 1)[0]
    assert f"{t['detected']} of {t['checked']}" in headline, (
        f"the detection headline {headline!r} does not state the "
        f"{t['detected']}/{t['checked']} the harness computes for the table below it."
    )


def test_the_published_lower_bound_is_the_one_the_repos_own_helper_computes():
    """`[M]` claims review 2026-08-24: `13.5%` could be edited to a flattering-and-wrong
    `66.7%` with the suite green, because the doc was this bound's only surface.

    Since MP-82 the tool prints the bound too, and the verbatim-quote guard above compares
    that line against this same document - so the doc's prose and the doc's quoted block are
    now pinned to the same helper from two directions. This test still earns its place: it is
    the only one that reads the PROSE figure, which the quoted block does not contain.

    Derived from the doc's own tally rather than hardcoded, so it follows `PERTURBATIONS`
    when that grows.
    """
    section = _doc_detection_section()
    t = recall_tally(_doc_run_of_record(section))
    bound = f"{1 - upper_bound_95(t['checked'] - t['detected'], t['checked']):.1%}"
    assert f"**{bound}**" in section, (
        f"docs/fp-measurement.md must publish the exact one-sided 95% lower bound "
        f"{bound} for {t['detected']}/{t['checked']} - "
        f"`1 - upper_bound_95({t['checked'] - t['detected']}, {t['checked']})`."
    )


def test_the_correction_note_and_its_limits_cannot_be_quietly_deleted():
    """`[M]` claims review 2026-08-24: deleting the entire `> **Corrected (MP-81)**` note,
    and separately deleting the interval sentence together with "not characterised", both
    left the suite green. Silently dropping a disclosure is the failure mode this project
    rates worst - removing one must be a red build, not an edit nobody notices.
    """
    section = _doc_detection_section()
    for required in (
        "> **Corrected 2026-08-24 (MP-81)",
        "adjusted by hand",
        "not characterised",
    ):
        assert required in section, (
            f"docs/fp-measurement.md no longer contains {required!r}. This document published "
            "a hand-adjusted detection number for the length of two ADRs; the note recording "
            "that, and the limits on what the corrected number supports, are not optional."
        )


def test_the_doc_never_asserts_that_a_perturbation_changed_behaviour():
    """ADR-0023 consequence 2, applied to the public surface rather than the operator string.

    The withdrawn sentences are quotable inside a `> **Corrected**` blockquote - that is how
    this repo withdraws a claim - so blockquote lines are excluded and everything else is
    live prose. The flattering direction is NOT covered here: `[M]` FP review 2026-08-24
    noted the original ban was one-directional, and R2 moved that check to
    `test_a_perfect_detection_score_is_a_tripwire_not_a_silent_publish`, which asserts over
    the tally rather than over the characters `3/3`. This test bans only WITHDRAWN claims.

    Scoped to the WHOLE FILE, not the detection section. `[M]` 2026-08-24, re-running the
    mutant battery after this guard was rewritten: re-asserting *"So 2/2 real behavior changes
    were flagged"* one line ABOVE the `**Detection:` headline left all 37 green, because the
    section slice starts at that headline. A withdrawn claim is withdrawn everywhere on the
    page, so there is no reason to scope this narrowly - and a guard whose blind spot is
    "one line earlier" is the kind that reads as coverage and provides none.
    """
    live = _scannable(_doc_live_prose(_doc_text()))
    for banned in (
        "actually changed behavior",
        "real behavior changes were flagged",
        "Not a false negative",
        "2/2",
        # `[M]` FP review 2026-08-24: MP-81's own fix introduced this one. "A miss here IS a
        # false negative" entails "the behaviour changed and the engine missed it" - ADR-0023
        # consequence 2, in the unflattering direction, and the same sentence FP review
        # blocked from the operator string during MP-79.
    ):
        assert banned not in live, (
            f"docs/fp-measurement.md asserts {banned!r} outside the correction note. The "
            "harness cannot tell a resisted instruction from a dead engine, so it may not "
            "claim which perturbations changed behaviour. See ADR-0023."
        )

    # Bound to the SUBJECT, not the phrase. `[M]` FP review 2026-08-24 found this assertion
    # live 65 lines below its own correction, and a bare `"is a false negative"` ban cannot be
    # used because the compliant sentence DISCLAIMS it in those very words ("whether any given
    # one is a false negative or a correct true negative is what this arm cannot tell").
    # SENTENCE-LEVEL, because neither a spelling ban nor a whole-document presence check
    # survives contact. `[M]` 2026-08-24 reword battery: a literal ban policed grammar, not
    # meaning - "every miss is a false negative" was caught while "misses are false
    # negatives", "a miss here means a false negative" and "the miss is, in truth, a false
    # negative" all walked through. A document-wide positive check failed differently and
    # worse: the disclaimer can sit in one section while the assertion sits in another,
    # which is precisely the shape of the defect MP-81 exists to fix.
    #
    # So the rule is per-sentence: tie a miss to "false negative" and you must disclaim it
    # in the same breath. Any phrasing that does is fine; any phrasing that does not is red.
    for sentence in re.split(r"(?<=[.!?])\s+", live):
        if not re.search(r"miss(?:es)?\b", sentence, re.I):
            continue
        if "false negative" not in sentence.lower():
            continue
        assert any(
            d in sentence.lower()
            for d in ("cannot tell", "either", "or a correct", "never a false alarm")
        ), (
            f"docs/fp-measurement.md ties a miss to a false negative without disclaiming it "
            f"in the same sentence: {sentence!r}\n"
            "A perturbed pair reading `unchanged` is EITHER a real change the engine missed "
            "OR a candidate that ignored the injected instruction, and this arm cannot tell "
            "which. Asserting the first is ADR-0023 consequence 2, and it is the standing "
            "pressure to loosen a floor until the miss converts."
        )


def test_a_perfect_detection_score_is_a_tripwire_not_a_silent_publish():
    """A TRIPWIRE, not a permanent prohibition, and the ONLY guard that catches a fully
    self-consistent forgery (`[M]` FP review, twice).

    An all-detected score is `0/8 = 0%` mirrored - `recall_summary`'s docstring refuses to
    print a percentage for exactly this reason. It is also the outcome ADR-0023 names as the
    threshold-pressure endpoint: converting `decline_pii`'s miss by moving a floor lands
    precisely here, and `[M]` the semantic sweep is flat from 0.1 to 0.9, so the slack to do
    it exists.

    Asserted over the doc's TALLY, never over the characters `"3/3"`. `[M]` FP review
    2026-08-24 showed the substring form was (a) unsatisfiable in conjunction with the
    verbatim guard - a legitimate all-detected run makes the harness quote itself contain
    `3/3`, so no document could pass both, and the cheapest repair would have been to stop
    scanning fenced blocks, silently gutting the banned-claims guard - and (b) a false alarm
    on ordinary fractions: `13/30`, `23/30`, `3/31` all matched, and this document's own
    stated next step is ">=30 labeled pairs".

    If the miss converts LEGITIMATELY - a perturbation whose compliance an independent oracle
    can verify - that is ADR-0023's stated falsifier: revisit the ADR and this guard together.
    Deleting this line alone is the erosion it exists to catch.
    """
    t = recall_tally(_doc_run_of_record(_doc_detection_section()))
    assert not (t["checked"] > 0 and t["detected"] == t["checked"]), (
        f"docs/fp-measurement.md publishes a perfect {t['detected']}/{t['checked']} detection "
        "score. If a threshold moved to get there, that is the erosion ADR-0023 exists to "
        "catch. If the perturbation design changed instead, revisit ADR-0023's falsifier and "
        "update this guard deliberately - it is the only one that catches a self-consistent "
        "rewrite of the table, the fraction and the note together."
    )


def test_the_whole_document_uses_perturbation_vocabulary_not_regression():
    """ADR-0023 consequence 3, over the WHOLE FILE rather than the Results slice.

    `[M]` Both gates, 2026-08-24, independently: MP-81's first commit corrected the Results
    section and left `## Detection (control)` 60 lines below still calling the injections
    "three controlled regressions" and asserting `decline_pii` *"(comply -> ... semantic
    change)"* - the forbidden partition, stated as fact, on the one scenario the run shows
    the model RESISTED. A section-scoped guard is how that survived a commit whose entire
    subject was this invariant.
    """
    live = _scannable(_doc_live_prose(_doc_text()))
    for banned in ("controlled regression", "injected regression"):
        assert banned not in live, (
            f"docs/fp-measurement.md calls a perturbation a {banned!r}. Whether a perturbation "
            "yields a regression is what this arm MEASURES, never a premise it may assert "
            "(ADR-0023 consequence 3)."
        )


_README = Path(__file__).resolve().parent.parent / "README.md"


def test_the_readme_publishes_the_same_detection_fraction_as_the_harness():
    """`README.md` is the surface most readers meet, and nothing guarded it.

    `[M]` claims review 2026-08-24, second pass: seven separate edits to `README.md` each
    left the whole suite green - including `2 of 3` -> `3 of 3`, restoring the withdrawn
    `22.4% at 2/2` interval, deleting the withdrawal paragraph outright, and a `299 tests
    passing` line that was 40 short of the real count. Two of those were real staleness this
    change had to fix by hand, which is the argument for pinning at least the number the whole
    correction is about.

    Scoped deliberately to the detection fraction and the withdrawal's existence. Guarding
    every claim in a README from a unit test is the wrong shape; MP-85 covers the rest.
    """
    text = _README.read_text(encoding="utf-8")
    t = recall_tally(_doc_run_of_record(_doc_detection_section()))

    assert f"**{t['detected']} of {t['checked']}** injected perturbations were flagged" in text, (
        f"README.md must publish the same {t['detected']} of {t['checked']} the harness "
        "computes and docs/fp-measurement.md quotes. It is the project's only surviving "
        "quantitative DoD claim and the surface most readers meet first."
    )
    bound = f"{1 - upper_bound_95(t['checked'] - t['detected'], t['checked']):.1%}"
    assert (
        f"**{bound}**" in text
    ), f"README.md must publish the exact one-sided 95% lower bound {bound}."
    assert "is **withdrawn**" in text, (
        "README.md no longer withdraws the 2/2 reading. A withdrawal that can be deleted "
        "silently is not a withdrawal."
    )


def test_the_doc_states_the_recall_arms_one_exclusion_correctly():
    """The recall arm excludes exactly one thing, and the doc must say which.

    `[M]` FP review 2026-08-24, third pass: `## Detection (control)` claimed *"anything
    else, `unchanged` included, scores a miss and is never excluded"*. That is false in the
    LOOSENING direction - an abstention IS excluded (ADR-0018) - and this file's own header
    records that deleting the `unmeasured` block left 302 tests green. A reader reconciling
    the harness to a doc that says "never excluded" deletes exactly that block.

    Asserted positively (ADR-0018 is named) and negatively (the false absolute is absent).
    The harness's own MISSED note says "counted against detection, never excluded" of a
    MISS, which is correct and must keep passing - hence the conjunction, not the phrase.
    """
    text = _doc_text()
    assert "ADR-0018" in text, (
        "docs/fp-measurement.md no longer names the recall arm's one exclusion. An arm "
        "described as excluding nothing invites deleting the abstention block, and a run "
        "that reached no verdict would then be scored a miss."
    )
    # Positive and SECTION-SCOPED. A literal ban on "never excluded" cannot work: the
    # harness's own MISSED note says exactly that of a miss, correctly, inside the verbatim
    # quote. And `[M]` a literal ban on the full sentence was trivially evaded - the mutant
    # produced "scores a miss; is never excluded", which no fixed phrase catches. Requiring
    # the citation where the rule is stated is robust to how the sentence is worded.
    start = text.index("## Detection (control)")
    section = text[start : text.index(chr(10) + "## ", start + 1)]
    assert "excludes nothing" not in _scannable(section), (
        "the `## Detection (control)` section says the arm excludes nothing. `[M]` A "
        "citation-present check alone passes this: the reword battery landed "
        "'excludes nothing whatsoever, not even an abstention (contra ADR-0018)' green. The "
        "arm excludes an abstention that reached no verdict, and only that."
    )
    assert "ADR-0018" in section, (
        "the `## Detection (control)` section no longer names the arm's one exclusion. It "
        "excludes an abstention that reached no verdict (ADR-0018) - and only that. A "
        "section that says the arm excludes NOTHING is false in the loosening direction, "
        "and invites deleting the abstention block that this file's header records "
        "surviving 302 green tests."
    )


def test_the_readmes_test_count_is_the_real_one():
    """`[M]` This number drifted 42 in production - `README.md` published `299 tests passing`
    against an actual 341, across multiple releases, and MP-81 had to correct it by hand.

    It is the cheapest possible claim for a reader to check and the one most likely to be
    quietly wrong, which is exactly the combination that costs credibility.

    `[M] 2026-08-26` It went wrong a second way, in the flattering direction, and this guard
    permitted it: the README said `461 tests passing` while `pytest -q` reported **458 passed,
    3 xfailed**. Three `xfail(strict=True)` markers pin an OPEN defect (the MP-05 scenario-id
    collision) -- they are not passes, and publishing them as passes overstates the suite by
    exactly the count of the bugs it has conceded. Comparing the claim to `collected` could
    never see that. The README now publishes BOTH numbers and this guard checks the
    arithmetic between them, without re-running the suite inside the suite.
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
    ).stdout
    collected = int(re.search(r"(\d+) tests? collected", out).group(1))

    text = (root / "README.md").read_text(encoding="utf-8")
    claimed = re.search(r"\*\*(\d+) tests passing\*\*", text)
    assert claimed, "README.md no longer states a test count"
    xfailed = re.search(r"\+(\d+) `xfail`", text)
    assert xfailed, (
        "README.md states a passing count but no longer states the xfail count. Both are "
        "required: an xfail pins an OPEN defect and must never be published as a pass."
    )
    assert int(claimed.group(1)) + int(xfailed.group(1)) == collected, (
        f"README.md claims {claimed.group(1)} passing + {xfailed.group(1)} xfailed = "
        f"{int(claimed.group(1)) + int(xfailed.group(1))}; pytest collects {collected}. "
        "This exact number was 42 short for several releases before MP-81 caught it, and "
        "3 too high in the flattering direction before MP-112 caught it."
    )
