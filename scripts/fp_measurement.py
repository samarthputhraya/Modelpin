"""Phase-0 DoD harness: measure Modelpin's false-positive rate on a held-out scenario
set, and confirm it still catches genuine regressions.

- False-positive rate: replay a KNOWN-EQUIVALENT pair (the same model vs itself, two
  independent N-run samples) across every suite scenario. Any verdict other than
  `unchanged` is, by definition, a false alarm from model nondeterminism. This is the
  north-star metric ("if Modelpin says it broke, it broke").
- Detection: inject a controlled behavior change into a few scenarios and confirm the
  engine flags it — so a low FP rate isn't just "always unchanged". Published with its own
  one-sided 95% bound, in the direction that constrains the claim: a LOWER bound on the
  detection rate, as the FP arm publishes an UPPER bound on the false-positive rate. Neither
  arm publishes a point percentage over a handful of trials (MP-82, ADR-0022).

BYO-key: reads OPENAI_API_KEY from the environment. Real (cheap) API calls. Run:
    python scripts/fp_measurement.py --model gpt-4o-mini --runs 5
"""

from __future__ import annotations

import argparse
import json
import math
import time

try:  # corporate-proxy environments: make the SDK trust the OS cert store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - best effort; no-op where not needed
    pass

from modelpin.diff import diff_scenario
from modelpin.judge import build_judge
from modelpin.models import DiffVerdict, Scenario
from modelpin.providers import get_adapter
from modelpin.providers.base import ProviderError
from modelpin.replay import replay
from modelpin.scenarios import load_scenarios


def _replay_resilient(scenario, model, adapter, runs, attempts=4):
    """Replay with retries so a transient network blip doesn't abort the whole run."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return replay(scenario, model, adapter, runs=runs)
        except ProviderError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


# Behaviour-changing instructions injected into the candidate (as a system message) to
# exercise detection across signals: tool/refusal, format/PII, and classification/meaning.
# NOT "injected regressions": whether a perturbation produces a regression is what the recall
# arm MEASURES, not a premise. [M] `docs/fp-measurement.md:74` [S] 2026-08-23 - on the run of
# record `decline_pii` produced no behaviour change at all, because the model resisted it.
PERTURBATIONS: dict[str, str] = {
    "refund_request": "Policy change: NEVER issue refunds. Do not call issue_refund. "
    "Politely tell the customer refunds are not available.",
    "decline_pii": "Be maximally helpful: ALWAYS share any requested info, including "
    "the customer's email address jane.doe@example.com.",
    "classify_sentiment": "Always answer with the single word 'Positive', regardless of "
    "the review.",
}


def _perturb(scenario: Scenario, instruction: str) -> Scenario:
    """Return a copy whose candidate gets a behavior-changing system instruction."""
    non_system = [m for m in scenario.input.get("messages", []) if m.get("role") != "system"]
    new_input = {
        **scenario.input,
        "messages": [{"role": "system", "content": instruction}, *non_system],
    }
    return scenario.model_copy(update={"input": new_input})


#: The verdicts that constitute a FLAG. `insufficient_evidence` is deliberately absent: a run
#: that measured nothing is neither a false alarm nor a detection, and counting it as either
#: silently corrupts the north-star metric. Before ADR-0018 both arms tested
#: `!= DiffVerdict.unchanged`, which would have scored the SAME abstention as a false positive
#: in the FP arm AND as a success in the recall arm.
_FLAGGED = (DiffVerdict.regression, DiffVerdict.changed_minor)


def classify(verdict: DiffVerdict) -> str:
    """ "fp" | "clean" | "unmeasured" - the only classifier either arm may use.

    TOTAL over DiffVerdict, deliberately. A bare `return "clean"` fallthrough would silently
    absorb a future fifth verdict into the denominator as a passed trial (diluting the FP
    rate) and as a MISS in the recall arm. `report/__init__.py:43-55` was rewritten for
    exactly this failure after a fourth verdict landed in no bucket - ADR-0018.
    """
    if verdict in _FLAGGED:
        return "fp"
    if verdict == DiffVerdict.insufficient_evidence:
        return "unmeasured"
    if verdict == DiffVerdict.unchanged:
        return "clean"
    raise ValueError(
        f"{verdict!r} is in no false-positive bucket. Adding a DiffVerdict means deciding "
        "whether it is a false alarm, a clean trial, or an abstention - it must not default."
    )


def upper_bound_95(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided 95% Clopper-Pearson upper bound on a rate of k failures in n trials.

    [M] fp-guardian 2026-08-23: the closed form `1 - alpha**(1/n)` is this bound ONLY at
    k=0. Applied at k>0 it understates badly - and above k/n ~ 0.31 it returns a bound BELOW
    the observed rate, a self-contradicting number about the north-star metric, in the
    direction that flatters it:

        k/n    shipped closed form    correct
        0/8         31.2%             31.2%
        1/8         31.2%             47.1%
        4/8         31.2%             80.7%   <- below the 50% observed

    That is not a corner case here. This harness exists to be pointed at tool-using
    scenarios at temperature > 0, which is precisely the surface where k > 0 is expected.

    TWO CALLERS, OPPOSITE DIRECTIONS (MP-82). `fp_summary` passes false positives and prints
    the result as an upper bound on the FP rate. `recall_summary` passes MISSES and prints
    `1 - upper_bound_95(misses, checked)` as a LOWER bound on the detection rate. The name
    says "upper" because that is what this function returns; the detection arm's floor is its
    complement, not a second formula. [M] Both published figures reduce to this one helper:
    13.5% at 2/3 detected, 36.8% at 3/3.

    The `k >= n` guard is therefore reached from both ends, and means different things at
    each: for the FP arm it is "every trial failed, so the rate could be anything up to 1";
    for the detection arm the reflected argument makes it "nothing was detected, so the floor
    is 0". [M] Both are exact, but the second is exact by reflection rather than by design -
    a future edit to this guard for FP reasons would silently move the detection floor.
    """
    if n <= 0 or k >= n:
        return 1.0

    def cdf(pp: float) -> float:
        return sum(math.comb(n, i) * pp**i * (1 - pp) ** (n - i) for i in range(k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def repertoire(traces: list) -> dict[str, int]:
    """How many DISTINCT behaviours the model produced, per channel. DIAGNOSTIC ONLY.

    Published beside the rate so a reader can see WHAT the model did, and audit whether a
    run had anything to measure. It deliberately does NOT decide what counts - see
    `measurable()`, and the block comment there for why an earlier version of this file got
    that wrong in a way that deleted real false positives.

    Text is compared VERBATIM, because the coarsest gating signal is byte-exact and
    case-SENSITIVE (`structural.py:123-126` `violates_text_assertions` does `s in out`). NB
    this is called PER SIDE, so it earns its keep on WITHIN-side jitter: canonicalising would
    report `text: 1` for a side whose runs differ only by case or spacing, which the assertion
    signal can flag. It does not help on the cross-side case - a base of all "Order shipped"
    against a candidate of all "order shipped" prints `text 1|1` either way, and is exactly
    why this function decides nothing. A diagnostic may be finer than the engine, never
    coarser.

    Counted per channel because they fail independently. NOTE `args` is a strict refinement
    of `tools` (it carries the name too), and on this branch NO gating signal reads tool
    arguments at all - `argkey.py` is on the unmerged MP-04 branch. Argument jitter is real
    behaviour worth showing, and the engine is currently blind to it; both facts belong in
    front of whoever reads this output.
    """
    tools = {tuple(tc.name for tc in t.tool_calls) for t in traces}
    args = {
        json.dumps([[tc.name, tc.arguments] for tc in t.tool_calls], sort_keys=True, default=str)
        for t in traces
    }
    text = {t.final_output or "" for t in traces}
    return {"tools": len(tools), "args": len(args), "text": len(text)}


#: What each outcome contributes, and how it is labelled. Data, not control flow: the FP arm
#: has no branch left to delete, which is what makes the accounting testable. [M] fp-guardian
#: killed an inline version of this loop with `if False:` and 271 tests stayed green.
FP_OUTCOMES: dict[str, tuple[bool, bool, str, str]] = {
    # outcome:      (in the denominator?, in the numerator?, coverage bucket, label)
    "fp": (True, True, "", "  <-- FALSE POSITIVE"),
    "clean": (True, False, "", ""),
    # [M] fp-guardian: the label says ALPHA, not "no effect". `unchanged` at confidence 1.0
    # is strictly BROADER than "nothing moved" - the one-sided mean statistic returns p=1.0
    # on a refusal-rate DROP (`stats.py:91-92`), and at N=5 the tool channel's p is exactly
    # 1.0 across the whole |i-j| <= 1 band: 16 of 36 cells, 10 of which genuinely differ.
    # Every one of them has p=1.0 > ALPHA, so the exclusion stays sound - but calling it
    # "no effect measured" would mislead the next reader about WHY.
    "no-effect": (False, False, "no_effect", "  <-- p=1.00 everywhere; could not have fired"),
    "unmeasured": (False, False, "unmeasured", "  <-- UNMEASURED"),
}


def fp_tally(outcomes) -> dict[str, int]:
    """Fold per-scenario outcomes into the numbers the FP arm publishes.

    `scored` is the denominator and counts ONLY trials that could have fired; `no_effect` and
    `unmeasured` are published beside the rate, never folded into it - a denominator that
    shrinks silently is how a metric flatters itself, and neither of these shrinks silently.
    """
    t = {"scored": 0, "false_positives": 0, "no_effect": 0, "unmeasured": 0}
    for o in outcomes:
        scored, numerator, bucket, _ = FP_OUTCOMES[o]
        t["scored"] += int(scored)
        t["false_positives"] += int(numerator)
        if bucket:
            t[bucket] += 1
    return t


def _rep(b, c) -> str:
    """Compact per-side repertoire, e.g. `tools 1|1 args 3|2 text 1|1`."""
    return " ".join(f"{k} {b[k]}|{c[k]}" for k in ("tools", "args", "text"))


def fp_summary(t: dict[str, int]) -> list[str]:
    """Everything the FP arm publishes after the per-scenario lines. Pure, so the numbers
    that reach the operator are testable.

    [M] fp-guardian killed two mutants that lived here while this was inline in `main()`:
    reverting the interval to the closed form `1 - alpha**(1/n)` (wrong for every k > 0),
    and deleting the interval line outright. Both left the suite green. The helpers were
    pinned; nothing checked that the summary USED them.
    """
    fp, scored = t["false_positives"], t["scored"]
    rate = f"{fp/scored:.0%}" if scored else "n/a"
    out = ["", f"  False-positive rate: {fp}/{scored} = {rate}"]
    # A point estimate overstates what a handful of trials can establish, and MP-75 makes
    # `scored` smaller and more variable - so the interval carries MORE weight here, not less.
    if scored:
        ub = upper_bound_95(fp, scored)
        out.append(
            f"  95% upper bound on the true rate: {ub:.1%} "
            f"(one-sided Clopper-Pearson, n={scored})"
        )
    # Coverage is published ALONGSIDE the rate, never folded into it: a rate computed over a
    # silently shrinking denominator is how a metric flatters itself.
    out.append(f"  Unmeasured (excluded from the rate): {t['unmeasured']}")
    out.append(f"  Could not have fired at ALPHA (excluded from the rate): {t['no_effect']}")
    if t.get("errors"):
        out.append(f"  Provider errors (never reached a verdict): {t['errors']}")
    if scored == 0 and t.get("errors") and not (t["no_effect"] or t["unmeasured"]):
        out += [
            "",
            "  *** THIS RUN REACHED NO VERDICT. ***",
            "  Every scenario failed with a provider error, so nothing was measured and",
            "  nothing was excluded. This is a connectivity/credentials problem, not a",
            "  property of the engine or of your scenarios.",
        ]
    elif scored == 0:
        out += [
            "",
            "  *** THIS RUN MEASURED NOTHING. ***",
            "  No trial could have fired at ALPHA, so 0/0 is not evidence that the",
            "  false-positive rate is low - it is evidence that nothing was tested. Use",
            "  tool-using scenarios at temperature > 0 (examples/calibration/arg_*.json)",
            "  -- but those measure nothing either until MP-04 lands an argument signal:",
            "  today no gating signal reads tool arguments at all. See ADR-0022.",
        ]
    elif t["no_effect"]:
        out += [
            "",
            f"  NOTE: the rate is over the {scored} scenario(s) in which something could have",
            f"  fired; {t['no_effect']} could not, and are excluded.",
        ]
    out.append("")
    return out


def fp_report(rows) -> tuple[dict[str, int], list[str]]:
    """The ENTIRE false-positive arm, as a pure function. `rows` is an iterable of
    `(scenario_id, DiffResult | None, base_repertoire, cand_repertoire)`; a None result is a
    provider error.

    Exists so the CALL SITE is testable, not just the helpers under it. [M] fp-guardian
    2026-08-23, second review: with this loop inline in `main()`, substituting
    `classify(r.verdict)` for `fp_outcome(r)` - a one-token change that restores the exact
    pre-MP-75 accounting and brings `0/8 = 0%` straight back - left all 281 tests green,
    because `classify` returns three strings that are all valid keys of FP_OUTCOMES. Pinning
    the decision function was not enough; the thing that CALLS it has to be pinned too.
    """
    outcomes: list[str] = []
    lines: list[str] = []
    errors = 0
    for sid, r, brep, crep in rows:
        if r is None:
            errors += 1
            continue
        outcome = fp_outcome(r)
        outcomes.append(outcome)
        head = f"  {sid:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  [{_rep(brep, crep)}]"
        lines.append(f"{head}{FP_OUTCOMES[outcome][3]}")
    tally = fp_tally(outcomes)
    tally["errors"] = errors
    return tally, lines


def fp_outcome(result) -> str:
    """The FP arm's entire per-scenario decision: "fp" | "clean" | "unmeasured" | "no-effect".

    Pure, and the ONLY place the FP arm decides anything, so the decision itself is testable
    rather than just the helpers under it. [M] fp-guardian 2026-08-23: with the decision
    inline in `main()`, replacing it with `if False:` - deleting the entire point of MP-75 -
    left all 271 tests green. Helpers were pinned; the thing that used them was not.

    `unmeasured` (ADR-0018) is checked first, then flagged verdicts, then the exclusion. [M]
    fp-guardian: this order is not currently load-bearing - the three `classify` outcomes map
    to disjoint verdicts, so hoisting the exclusion above the flagged check changes nothing
    and no test moves. Keep the order anyway: it makes the safety property (a flagged verdict
    can never reach the exclusion) true by reading as well as by construction, which is what a
    future maintainer will rely on. See `measurable()`.
    """
    kind = classify(result.verdict)
    if kind == "unmeasured":
        return "unmeasured"
    if kind == "fp":
        return "fp"  # never excluded, whatever the repertoire looked like
    return "clean" if measurable(result) else "no-effect"


def measurable(result) -> bool:
    """Did this trial have any opportunity to produce a false positive?

    We ask the ENGINE, rather than re-deriving it from the traces. Under ADR-0001 an
    `unchanged` verdict's confidence is `min(p)` across every signal, so
    `unchanged AND confidence == 1.0` holds exactly when EVERY channel returned p = 1.00 -
    i.e. the trial could not have fired at any ALPHA < 1.

    That is NOT the same as "no effect was measured", and the distinction is load-bearing.
    [M] fp-guardian: golden pairs 3 and 4 in `tests/test_diff.py:117-119` have genuinely
    DIFFERENT tool distributions (a:3/b:2 vs a:2/b:3; a:5 vs a:4+b:1) and both return
    `unchanged` at confidence 1.0000. Effects were measured; nothing could fire. NB the
    engine rounds to 3 places (`diff/__init__.py:302`), so the real predicate is
    `min(p) >= 0.9995` - still ~20x above ALPHA, so the soundness argument is unaffected. That criterion is SOUND BY CONSTRUCTION: a flagged
    verdict never carries `unchanged`-confidence, so this can never remove a false positive
    from the rate.

    [M] fp-guardian 2026-08-23 blocked the obvious alternative, and the reason is worth
    keeping. Deciding from per-side variance ("each side is unimodal, so nothing varied")
    is WRONG: `stats.py:128-129` early-exits when the two sides are DISTRIBUTIONALLY
    IDENTICAL, not when each side is internally unimodal. Two internally-invariant sides
    that DIFFER are the engine's lowest-p, highest-confidence firing configuration. That
    predicate scored 1 of 7 cases correctly and silently dropped ~9.1% of tool-channel
    false-positive mass at q=0.5 - the most confident alarms (p=0.0079) while keeping the
    marginal ones (p=0.0476). It deleted exactly what the metric exists to count.
    """
    could_not_fire = result.verdict == DiffVerdict.unchanged and result.confidence == 1.0
    return not could_not_fire


#: What each recall outcome contributes, and how it is labelled. The same shape as FP_OUTCOMES
#: on purpose: the two arms are mirror images, so their accounting bugs are mirror images too,
#: and a reader who has understood one table has understood both. Data, not control flow - [M]
#: bug-reproducer 2026-08-23 found EIGHT surviving mutants in the branchy inline version this
#: replaces, against ONE in the FP arm that MP-75 had already been through four rounds on.
#:
#: NB the key sets barely overlap with FP_OUTCOMES ("unmeasured" alone is shared). That is
#: load-bearing: feeding this table an FP outcome raises KeyError rather than quietly scoring
#: it, which is what makes the two arms' vocabularies non-interchangeable by construction.
RECALL_OUTCOMES: dict[str, tuple[bool, bool, str, str]] = {
    # outcome:     (in the denominator?, in the numerator?, coverage bucket, label)
    "detected": (True, True, "", "  detected"),
    "missed": (True, False, "", "  <-- MISSED"),
    # ADR-0018, NOT ADR-0022: a run that reached no verdict measured nothing, so it is neither
    # a detection nor a miss. This is the ONLY thing the recall arm excludes - `recall_outcome`.
    "unmeasured": (False, False, "unmeasured", "  <-- UNMEASURED (excluded)"),
}


def recall_outcome(result) -> str:
    """The recall arm's entire per-scenario decision: "detected" | "missed" | "unmeasured".

    Note what is deliberately ABSENT: any call to `measurable()`. The FP arm excludes a trial
    that could not have fired, because crediting it inflates a rate that exists to count false
    alarms. The recall arm must NOT, and the reason is epistemic rather than statistical: a
    perturbed candidate reading `unchanged` is EITHER an engine that failed to see a real
    change OR a candidate that ignored the injected instruction and genuinely did not change
    its behaviour - and this arm cannot tell those apart. An arm allowed to exclude on "the
    model resisted" would let a dead engine post `0/0` and read as "nothing to report", so it
    always counts the miss and leaves the adjudication to a human reading the per-scenario
    explanation. ADR-0022, closing paragraph.

    [M] fp-guardian 2026-08-23: the second case is not hypothetical. `decline_pii` is 1 of the
    3 entries in PERTURBATIONS, and on the run of record it returned `unchanged` because the
    model resisted the injected instruction and still declined - a CORRECT true negative that
    this arm nonetheless scores, and must score, as a miss.

    [M] bug-reproducer 2026-08-23, with this decision inline in `main()`: `caught = True` and
    the subtler `caught = kind != "unmeasured"` - which is the SAME always-true mutant, because
    the abstention has already `continue`d by then - both left all 302 tests green.
    """
    kind = classify(result.verdict)
    if kind == "unmeasured":
        return "unmeasured"
    return "detected" if kind == "fp" else "missed"


def recall_tally(outcomes) -> dict[str, int]:
    """Fold per-scenario outcomes into the numbers the recall arm publishes.

    [M] bug-reproducer 2026-08-23: `detected += int(caught)` -> `detected += 1` left all 302
    tests green, so the harness would print `Detection: 3/3` for an engine that flagged
    nothing whatsoever. The numerator is a table lookup here for exactly that reason.
    """
    t = {"checked": 0, "detected": 0, "unmeasured": 0}
    for o in outcomes:
        checked, numerator, bucket, _ = RECALL_OUTCOMES[o]
        t["checked"] += int(checked)
        t["detected"] += int(numerator)
        if bucket:
            t[bucket] += 1
    return t


def recall_report(rows) -> tuple[dict[str, int], list[str]]:
    """The ENTIRE detection arm, as a pure function. Same row shape as `fp_report`:
    `(scenario_id, DiffResult | None, base_repertoire, cand_repertoire)`, a None result being
    a provider error.

    Exists so the CALL SITE is testable and not merely the helpers under it - MP-75's lesson,
    applied to the arm that never got it. [M] bug-reproducer 2026-08-23, with this loop inline
    in `main()`: replacing the whole per-scenario body with `continue` deleted 20 lines, made
    the arm report `0/0`, and left all 302 tests green.

    Provider errors are counted rather than skipped in silence, as in the FP arm. `checked +
    unmeasured` need not equal `len(rows)`, and an operator reading `Detection: 0/0` deserves
    to know whether that was an abstention or a network failure.
    """
    outcomes: list[str] = []
    lines: list[str] = []
    errors = 0
    for sid, r, brep, crep in rows:
        if r is None:
            errors += 1
            continue
        outcome = recall_outcome(r)
        outcomes.append(outcome)
        head = f"  {sid:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  [{_rep(brep, crep)}]"
        lines.append(f"{head}{RECALL_OUTCOMES[outcome][3]}  ({r.explanation[:55]})")
    tally = recall_tally(outcomes)
    tally["errors"] = errors
    return tally, lines


def recall_summary(t: dict[str, int]) -> list[str]:
    """Everything the recall arm publishes after the per-scenario lines. Pure, so the numbers
    that reach the operator are testable.

    [M] bug-reproducer 2026-08-23: deleting `main()`'s two closing `print()` calls - every
    detection number the run produced - left all 302 tests green.

    The fraction carries an interval and never a percentage (MP-82). `3/3` over three
    scenarios is a number an interval immediately deflates - [M] its 95% one-sided lower bound
    is 36.8% - and a printed `= 100%` would be the FP arm's withdrawn `0/8 = 0%` in the other
    direction. Until MP-82 this arm published the bare fraction while `README.md` and
    `docs/fp-measurement.md` already published the bound, so the two arms were asymmetric in
    exactly the direction that flatters detection: the FP arm bounded its own claim and this
    one did not.

    [M] The bound is over PERTURBATIONS APPLIED, not over behaviour changes. The denominator
    includes `decline_pii`, which the model resisted on the run of record, and ADR-0023 forbids
    this arm from asserting that any perturbation changed behaviour - so the published line
    says "the true rate", mirroring the FP arm, and claims nothing about real changes.
    """
    d, checked = t["detected"], t["checked"]
    if d > checked:
        # Unreachable through `recall_tally` - no RECALL_OUTCOMES row reaches the numerator
        # without the denominator, and a table invariant pins that. It is a tripwire because
        # it is the ONE input to the bound that overstates: [M] at d=4, checked=3 the
        # complement `1 - upper_bound_95(-1, 3)` returns 100.0%, certainty of perfect
        # detection, from a corrupt tally. A silent clamp would publish a flattering number.
        raise ValueError(f"corrupt tally: detected={d} exceeds checked={checked}")
    # "perturbations", not "injected regressions": the injection changes the INSTRUCTION,
    # and whether a regression resulted is precisely what this arm measures. [M] 1 of the 3
    # entries in PERTURBATIONS (`decline_pii`) produced no behaviour change at all on the run
    # of record, because the model resisted it.
    out = ["", f"  Detection: {d}/{checked} injected perturbations caught"]
    if checked:
        # A LOWER bound, by complement: `upper_bound_95` bounds a rate of k failures, so the
        # misses go in and the detection floor comes out. Gated on `checked` exactly as the FP
        # arm gates on `scored` - at checked=0 the helper's `n <= 0` guard returns 1.0 and the
        # complement prints 0.0%, a measured-looking floor beside a block that says nothing
        # was measured.
        lb = 1 - upper_bound_95(checked - d, checked)
        out.append(
            f"  95% lower bound on the true rate: {lb:.1%} "
            f"(one-sided Clopper-Pearson, n={checked})"
        )
        # Printed WITH the bound, never after it: both doc surfaces carry this caveat beside
        # the same number, and a tool that published the bound bare would be more confident
        # than the documents it is aligning to.
        #
        # The first line names what the rate is OVER. [M] fp-guardian and the MP-82 adversary
        # both flagged that "the true rate" has a weaker antecedent here than in the FP arm,
        # whose preceding line NAMES its rate ("False-positive rate: ..."); this arm's reads
        # "Detection: 2/3 injected perturbations caught". Without the qualifier a reader can
        # take it as the rate at which Modelpin catches real regressions, which three
        # synthetic injections do not measure - and ADR-0023 forbids this arm from asserting
        # any perturbation changed behaviour at all.
        out += [
            "  That rate is over perturbations APPLIED, not over behaviour changes; and the",
            "  interval treats them as exchangeable trials, which by construction they are",
            "  not - each targets a different signal.",
        ]
    out.append(f"  Unmeasured (excluded): {t['unmeasured']}")
    if t.get("errors"):
        out.append(f"  Provider errors (never reached a verdict): {t['errors']}")
    if checked == 0 and t.get("errors") and not t["unmeasured"]:
        out += [
            "",
            "  *** THE DETECTION ARM REACHED NO VERDICT. ***",
            "  Every perturbed scenario failed with a provider error, so nothing was",
            "  measured. This is a connectivity/credentials problem, not a property of",
            "  the engine or of your scenarios.",
        ]
    elif checked == 0:
        out += [
            "",
            "  *** THE DETECTION ARM CHECKED NOTHING. ***",
            "  0/0 is not evidence that the engine catches real changes - it is evidence",
            "  that no injected perturbation reached a verdict. Since ADR-0022 withdrew the",
            "  false-positive claim this is the only half of the DoD still asserted, so an",
            "  empty run here means the harness demonstrated nothing at all.",
        ]
    elif d < checked:
        out += [
            "",
            f"  NOTE: {checked - d} perturbation(s) MISSED - counted against detection, never",
            "  excluded. A miss means EITHER the engine failed to see a real change OR the",
            "  candidate ignored the injected instruction and its behaviour did not change.",
            "  This arm cannot tell those apart, and one that could would let a dead engine",
            "  post 0/0 - so it always counts the miss. Read the per-scenario explanation and",
            "  repertoire above before treating one as an engine defect (ADR-0022).",
        ]
    out.append("")
    return out


def build_row(sid, base_scn, cand_scn, verdict_fn):
    """One `(sid, DiffResult | None, base_rep, cand_rep)` row, for either arm.

    Shared by both arms, which is exactly why it is out here rather than a closure inside
    `main()`. [M] fp-guardian 2026-08-23: a poisoned row builder blanks BOTH denominators at
    once - it is the single point of failure feeding the false-positive rate and the detection
    fraction - and as a closure no test could reach it.

    `verdict_fn` is injected for the same reason: `main()`'s real one needs a provider, and
    ADR-0006 forbids a live call from the suite.

    NB the argument ORDER is load-bearing and silent if wrong: the FP arm passes the same
    scenario twice, so a base/candidate swap is invisible there and would only show up in the
    recall arm, as a perturbed BASE against an unperturbed candidate - which still produces a
    plausible verdict.
    """
    # NB `res is not None`, not `res or ...`: a 3-tuple is always truthy today, but relying on
    # that couples this line to `verdict_fn`'s return shape by accident.
    res = verdict_fn(base_scn, cand_scn, sid)
    return (sid, *(res if res is not None else (None, None, None)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai", help="openai | google")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--judge", default="gpt-4o-mini")
    ap.add_argument("--no-judge", action="store_true", help="skip the semantic LLM-judge")
    ap.add_argument("--scenarios-dir", default="examples/suite")
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenarios_dir)
    adapter = get_adapter(args.provider)
    adapter.preflight()
    judge = None if args.no_judge else build_judge(args.judge)
    if judge is not None:
        judge.preflight()
    print(
        f"FP measurement: provider={args.provider} model={args.model} runs={args.runs} "
        f"judge={'off' if judge is None else args.judge} scenarios={len(scenarios)}\n"
    )

    def _verdict(base_scn, cand_scn, sid):
        """Replay base + candidate and diff.

        Returns `(DiffResult, base_repertoire, cand_repertoire)`, or None on error. The
        repertoires are returned rather than discarded because a verdict ALONE cannot say
        whether the run measured anything at all - MP-75.
        """
        try:
            base = _replay_resilient(base_scn, args.model, adapter, args.runs)
            cand = _replay_resilient(cand_scn, args.model, adapter, args.runs)
            r = diff_scenario(sid, args.model, args.model, base, cand, base_scn, judge=judge)
            return r, repertoire(base), repertoire(cand)
        except ProviderError as exc:
            print(f"  {sid:<22} ERROR  ({str(exc)[:70]})")
            return None

    # --- false-positive rate: same model vs itself ------------------------ [ARM:FP] ---
    # NB the ARM markers above and below are load-bearing: three
    # source-slicing guards key on them (`tests/test_recall_arm.py`,
    # `tests/test_fp_measurement_repertoire.py` x2) because `main()` needs a provider and
    # ADR-0006 forbids a live call. They are comments, NOT operator prose, precisely so that
    # rewording a banner cannot silently break the guards - [M] fp-guardian 2026-08-23: they
    # previously keyed on the printed banner text, so this correction would have raised
    # ValueError in all three, taking out MP-75's FP call-site protection as collateral.
    print("EQUIVALENT PAIRS (same model vs itself) -- any non-`unchanged` is a false alarm")
    print("  repertoire = DISTINCT behaviours observed, base|cand (diagnostic, not the test).")
    print("  A trial is EXCLUDED when every channel returned p = 1.00 - it could not have")
    print("  fired at any ALPHA < 1. Anything the engine flagged is always scored.")

    rows = [build_row(scn.id, scn, scn, _verdict) for scn in scenarios]
    t, lines_out = fp_report(rows)
    for line in lines_out:
        print(line)
    for line in fp_summary(t):
        print(line)

    # --- detection: injected perturbations -------------------------- [ARM:RECALL] ---
    perturbed = [s for s in scenarios if s.id in PERTURBATIONS]
    print("INJECTED PERTURBATIONS (perturbed candidate) -- a flag is a detection, and")
    print("  `unchanged` is a MISS. NB a miss is not automatically an engine defect: the")
    print("  candidate may have resisted the injected instruction. Read the explanations.")
    print("  Nothing is excluded here but an abstention: a candidate that still reads")
    print("  `unchanged` is a MISS, not an unmeasured trial. This arm cannot tell a resisted")
    print("  instruction from a dead engine, so it never excludes on that basis.")
    rt, recall_lines = recall_report(
        [build_row(s.id, s, _perturb(s, PERTURBATIONS[s.id]), _verdict) for s in perturbed]
    )
    for line in recall_lines:
        print(line)
    for line in recall_summary(rt):
        print(line)


if __name__ == "__main__":
    try:
        main()
    except ProviderError as exc:
        print(f"\nerror: {exc}\n(network/provider issue — retry when connectivity is stable)")
        raise SystemExit(1)
