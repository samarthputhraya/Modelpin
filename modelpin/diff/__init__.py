"""Behavioral diff orchestrator. See spec section 6.

Combines structural per-run signals (``structural.py``) with the distributional
permutation test (``stats.py``) into a single per-scenario verdict + confidence.

Decision rule (tuned for a low FALSE-POSITIVE rate — the north-star metric):
a signal counts as a regression only when the candidate distribution differs from
baseline at p <= ALPHA *and* the effect clears a minimum size. A single odd run,
or a majority that merely flips between two equally-likely behaviors, is NOT a
regression — the permutation test treats it as noise. The semantic LLM-judge
(``semantic.py``, spec 6B) is optional and injected: with ``judge=None`` this layer
stays purely structural + statistical and never makes a network call; with a judge it
adds a calibrated semantic-divergence signal through the same distributional test.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from modelpin.diff.stats import (
    permutation_pvalue_distribution,
    permutation_pvalue_mean,
    total_variation_distance,
)
from modelpin.diff.semantic import Judge, semantic_divergence_flags
from modelpin.diff.argkey import describe_argument_change
from modelpin.diff.structural import (
    EQUIVALENCE_MODES,
    MatchMode,
    assertion_violation_flags,
    canonical_sequence,
    degenerate_count,
    has_tool_arguments,
    modal_arg_sequence,
    modal_sequence,
    name_trajectory_is_stable,
    refusal_rate,
    refused_flags,
    tool_arg_sequence,
    tool_call_sequence,
    trajectory_match,
)
from modelpin.models import DiffResult, DiffSignals, DiffVerdict, Scenario, Trace

#: Significance threshold for the permutation test. Lower = fewer false positives.
ALPHA = 0.05

#: A side is UNUSABLE when AT LEAST HALF of its runs recorded nothing: ``2 * d >= n``.
#:
#: The boundary is not arbitrary and it is not a fitted constant. Two of the engine's own
#: quantities break exactly at ``d/n = 0.5``:
#:   * ``modal_sequence`` and the semantic reference output are both ``most_common(1)``, so at
#:     or above half the mode IS "nothing" — the reference every other run is compared against.
#:   * When a healthy baseline meets a candidate with d silent runs, the tool TVD and the
#:     semantic delta are each EXACTLY ``d/n``. Since MIN_TOOL_TVD and MIN_SEMANTIC_DELTA are
#:     both 0.5, ``d/n = 0.5`` is precisely where pure measurement failure begins clearing the
#:     effect-size floors on its own.
#:
#: An earlier draft used a STRICT majority (``>``) on the reasoning that a 50/50 split is
#: noise. [M] That was falsified: at n=8, d=4 the pre-gate engine publishes `regression` at
#: confidence 0.962 about a candidate that recorded nothing on half its runs — the p-gate is
#: the only thing holding the tie, and it stops holding at N>=8. Inclusive `>=` is the only
#: variant that closes that manufactured regression.
#:
#: [A] not [M]: no run in this repo contains a degenerate trace ([M] 0 of 360 real traces in
#: docs/reports/data/), so the boundary cannot be falsified here. MP-54's live calls produce
#: the first d/n distribution via the counters on DiffSignals. ADR-0018 holds the revisit
#: trigger. FP review protected.
DEGENERATE_SIDE_NUMERATOR = 2
#: A tool-call distribution must shift by at least this total-variation distance to
#: count — guards against trivially-significant jitter once N grows large.
MIN_TOOL_TVD = 0.5
#: The ARGUMENT distributions must be fully DISJOINT before the argument gate fires (MP-04).
#:
#: 1.0 is not a fitted dial, it is a structural rule: "no run on the candidate side used a
#: payload any baseline run used." It was chosen over the obvious alternative -- halving ALPHA
#: for the second gate -- because [M] that alternative's meaning is NOT STABLE IN N, and this
#: engine has already shipped two comments stating guarantees that held only at the default N:
#:     N=3  equivalence modes: a fully disjoint change fires at neither (p = 0.100000);
#:          DIRECTIONAL modes differ -- they route through permutation_pvalue_mean, whose
#:          floor is 1/C(2N,N) not 2/C(2N,N), so `--match subset --runs 3` fires at exactly
#:          p = ALPHA. The dead zone is mode-dependent: equivalence N<=3, directional N<=2.
#:     N=4  a fully disjoint change does NOT fire at ALPHA/2 at all (p = 0.028571 > 0.025)
#:          -- ALPHA/2 silently disables this entire signal at a legal `runs` setting
#:     N=5  ALPHA/2 coincides exactly with disjointness (the default, hence the trap)
#:     N>=6 ALPHA/2 fires on NON-disjoint changes (N=6, 5-of-6: p = 0.015152)
#: [M] The floor stated here is exactly "disjoint and significant" at every N in 3..8, and it
#: costs nothing AGAINST THAT ALTERNATIVE (halving ALPHA): over 72,072 exhaustive relabelings
#: under a true null both land on 916/72072 = 1.2710% (status quo 912/72072 = 1.2654%), both
#: hold the worst pool at 12/252, and both give 0.0722% / 0.1804% on the pure argument-jitter
#: channel. That is a comparison of two candidate floors, NOT a statement that the signal is
#: free: this null carries two payloads and cannot price a real repertoire.
#:
#: [A] DISCHARGED 2026-08-25 -- its own falsifier fired. This previously read: "no corpus in
#: this repo contains RUN-TO-RUN argument variance ... so the REAL-WORLD false-positive rate
#: of this signal is unmeasured, and every number above is synthetic. Falsifier: MP-54's live
#: tool scenarios at temperature > 0 flag one scenario whose argument change a human calls
#: equivalent." [M] MP-105 committed measured repertoires under examples/calibration/results/
#: that DO carry run-to-run argument variance (arg_numeric_rounding: 10 distinct payloads in
#: 24 runs on gpt-4.1-mini), and pricing the gate against them gives a peak added
#: false-positive rate near 3.5% at the shipped `runs: 5`, against 0.0000% before this signal
#: existed. The numbers above are still synthetic; they are no longer the only ones.
#: [M] The equality is exact for every disjoint shape through N=21; at N=22 a genuinely
#: disjoint pool scores 0.9999999999999999 and the gate silently does NOT fire (0 of 199,836
#: non-disjoint pools ever reached 1.0, so the drift is one-directional and false-NEGATIVE).
#:
#: THIS FLOOR IS UNCALIBRATED, so the signal it gates is ADVISORY: it escalates only to
#: `changed_minor` and can never fail a build on its own (ADR-0029, and the verdict block
#: below). Do NOT read the pricing above as a licence to promote it, and do NOT tune
#: `DEFAULT_RUNS` against it -- the cost is non-monotone in N and `arg_*` is the fitted-on
#: set (ADR-0025).
MIN_TOOL_ARG_TVD = 1.0
#: Ignore refusal-rate rises smaller than this even if "significant" (one run in three).
MIN_REFUSAL_DELTA = 0.34
#: Candidate semantic-divergence rate must exceed the baseline's by at least this much.
#: CALIBRATED on examples/calibration/ (labeled set distinct from the held-out suite; see
#: docs/fp-measurement.md). On the INDEPENDENT-CANDIDATE run of record
#: (`examples/calibration/results/result-independent-judge.json`, candidate gpt-3.5-turbo, judge
#: gpt-4o-mini): equivalent pairs land at delta 0.0-0.20 and the meaning changes that actually
#: took effect at 0.60-1.0, so 0.5 sits in that gap. [M] But 5 of those 6 equivalent pairs score
#: p=1.00 and could not have fired, so the evidence is 0/1 (95% upper bound 95.0%), NOT 0/6 at
#: 39.3% -- that figure was the pre-MP-75 accounting. The "0.0 versus >=0.8" separation quoted here previously
#: is the SELF-JUDGE run, which an adversarial audit demoted as circular -- do not cite it as the
#: justification. [M] `explain_concept` scores 0.20 equivalent / 0.60 changed on the independent
#: run, and `define_term`'s CHANGED pair scores 0.0. The held-out re-validation moved no verdict
#: with the promotion live, but contributed 0 SCORED trials (MP-75/ADR-0022), so it is not
#: evidence for this floor either. NOTE
#: the calibration set is still small and the perturbations synthetic — see docs/fp-measurement.md for
#: the limitations and the planned expansion to real migration traces + an independent judge.
MIN_SEMANTIC_DELTA = 0.5


def _scenario_task(scenario: Optional[Scenario]) -> Optional[str]:
    """The user's request from a scenario (last user message) — context for the judge."""
    if not scenario:
        return None
    for message in reversed(scenario.input.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _side_is_unusable(traces: list[Trace]) -> bool:
    """A side with no runs, or whose modal run recorded nothing, cannot be compared."""
    n = len(traces)
    if n == 0:
        return True
    return DEGENERATE_SIDE_NUMERATOR * degenerate_count(traces) >= n


def _abstention_explanation(n_base: int, d_base: int, n_cand: int, d_cand: int) -> str:
    """Name the failing side AND its remedy — they differ, and a wrong remedy loops the user."""
    if n_base == 0 or n_cand == 0:
        return (
            "insufficient evidence: need at least one recorded run per side "
            f"(baseline {n_base}, candidate {n_cand}); re-record with `modelpin baseline`"
        )
    parts: list[str] = []
    if DEGENERATE_SIDE_NUMERATOR * d_base >= n_base:
        parts.append(
            f"{d_base}/{n_base} baseline run(s) recorded no output, no tool call and no "
            "refusal - re-record with `modelpin baseline`"
        )
    if DEGENERATE_SIDE_NUMERATOR * d_cand >= n_cand:
        parts.append(
            f"{d_cand}/{n_cand} candidate run(s) recorded no output, no tool call and no "
            "refusal - re-run, or inspect the provider response"
        )
    return "insufficient evidence: " + "; ".join(parts) + "; nothing to compare"


def diff_scenario(
    scenario_id: str,
    from_model: str,
    to_model: str,
    baseline_traces: list[Trace],
    candidate_traces: list[Trace],
    scenario: Optional[Scenario] = None,
    mode: MatchMode = "strict",
    judge: Optional[Judge] = None,
) -> DiffResult:
    """Compare baseline vs candidate trace distributions for one scenario.

    With ``judge`` set, the semantic LLM-judge signal is evaluated (spec 6B); with it
    ``None`` the diff is purely structural + statistical and makes no network call.
    """
    # --- input-validity gate: did we measure anything at all? --------------------
    # Runs BEFORE every signal, and outranks every verdict INCLUDING regression, because each
    # downstream statistic (permutation p, TVD, modal_sequence, the judge's reference output)
    # is computed over the same contaminated sample. Abstaining is not a softer regression;
    # it is the honest answer when a side recorded no behavior to compare. ADR-0018.
    d_base, d_cand = degenerate_count(baseline_traces), degenerate_count(candidate_traces)
    n_base, n_cand = len(baseline_traces), len(candidate_traces)
    if _side_is_unusable(baseline_traces) or _side_is_unusable(candidate_traces):
        return DiffResult(
            scenario_id=scenario_id,
            from_model=from_model,
            to_model=to_model,
            verdict=DiffVerdict.insufficient_evidence,
            # 0.0, never min(p). ADR-0001 governs how sure we are of a COMPARISON; here there
            # was no comparison. "Confident abstention" is the exact confusion MP-49 was.
            confidence=0.0,
            signals=DiffSignals(
                degenerate_baseline=d_base,
                degenerate_candidate=d_cand,
                baseline_runs=n_base,
                candidate_runs=n_cand,
            ),
            explanation=_abstention_explanation(n_base, d_base, n_cand, d_cand),
        )

    # --- tool-call trajectory --------------------------------------------------
    # Equivalence modes (strict/unordered) collapse each run to a hashable key and compare
    # the two DISTRIBUTIONS. Directional modes (subset/superset) are relations, NOT
    # equivalences: bucketing them by a key would flag the very change the mode permits (a
    # dropped call under subset, an added call under superset) as a regression — a false
    # positive on the cardinal metric. For those, score each run by whether it VIOLATES the
    # baseline relation and gate a *rise* in the violation rate (one-sided, like refusal).
    if mode in EQUIVALENCE_MODES:
        base_keys = [canonical_sequence(tool_call_sequence(t), mode) for t in baseline_traces]
        cand_keys = [canonical_sequence(tool_call_sequence(t), mode) for t in candidate_traces]
        tool_tvd = total_variation_distance(base_keys, cand_keys)
        tool_p = permutation_pvalue_distribution(base_keys, cand_keys)
    else:
        ref_seq = modal_sequence(baseline_traces, mode)
        base_viol = [
            0 if trajectory_match(ref_seq, tool_call_sequence(t), mode) else 1
            for t in baseline_traces
        ]
        cand_viol = [
            0 if trajectory_match(ref_seq, tool_call_sequence(t), mode) else 1
            for t in candidate_traces
        ]
        # Reuse MIN_TOOL_TVD as the violation-rate floor (both are 0..1 "fraction" scales);
        # clamp the rise at >= 0 so the reported tool_call_match never exceeds 1.0.
        tool_tvd = max(0.0, _mean(cand_viol) - _mean(base_viol))
        tool_p = permutation_pvalue_mean(base_viol, cand_viol)
    tool_regressed = tool_p <= ALPHA and tool_tvd >= MIN_TOOL_TVD

    # --- tool-call ARGUMENTS (MP-04; spec 6A defines a call as "name + args") --------
    # A SECOND signal over the same machinery, never a richer key for the first. [M] Folding
    # arguments into the name key does not sharpen that signal, it DESTROYS it: the
    # permutation test is relabeling-invariant, so once per-run argument jitter makes every
    # key distinct, a total tool swap (web_search on all 5 baseline runs -> sql_query on all
    # 5 candidate runs) flips from `regression` at 0.992 to `unchanged` at 1.0.
    #
    # [A] SYNTHETIC, and labelled as such because it was briefly mismarked [M]: across the
    # 286-pool ENUMERATION 42.98% of name-gate firings go silent under the folded key. On the
    # REAL corpus that figure is 0.00% (0 of 10), because no tracked scenario carries the
    # run-to-run argument variance the enumeration assumes. The folded key is still correctly
    # rejected, but on a different [M] number: it fires on 44 corpus cells against 10.
    #
    # The gate runs ONLY when the name trajectory is stable on both sides and identical
    # across them. When names are themselves jittery the NAME gate is already the
    # responsible signal, and letting both fire on one pool is what turns two tests into a
    # raised error rate. [M] That precondition holds the rate at 1.2654% -> 1.2710% over
    # 72,072 exhaustive relabelings, worst pool unchanged at 12/252 -- but see the retraction
    # in `name_trajectory_is_stable`: that null has TWO payloads and cannot see the channel
    # this gate actually opens. The fix is NOT false-positive-neutral. [M] Priced on the
    # committed repertoires it peaks near 3.5% at the shipped `runs: 5`.
    #
    # It also requires EQUAL runs per side, which is reachable: cli.py replays the candidate at
    # the current `runs` against a PERSISTED baseline, so `baseline --runs 5` then
    # `check --runs 2` gives 5 vs 2. [M] The 72,072-relabeling figure above is conditioned on a
    # fixed pool shape and structurally cannot see that regime; the UNCONDITIONAL true-null
    # rate there is far worse -- 0.1953% at 5v5, but 1.9204% at 5v2 and 2.2968% at 8v2 (1 in
    # 44), against a status quo of 0.0000% because arguments were not diffed at all. Everything
    # stays under ALPHA, as a permutation test must, but "bounded by 5%" is not
    # "false-positive-neutral". Until MP-54 prices that, the argument gate simply declines.
    arg_tvd = 0.0
    arg_p = 1.0
    arg_regressed = False
    # AND, never OR: one side alone having arguments means the other side's `{}` becomes a
    # comparable payload, and `{}` vs a populated payload is disjoint by construction. [M] The
    # OR form fired on 34 corpus cells and flipped 136 verdicts, 136/136 cross-vendor.
    args_compared = (
        len(baseline_traces) == len(candidate_traces)
        and has_tool_arguments(baseline_traces)
        and has_tool_arguments(candidate_traces)
        and name_trajectory_is_stable(baseline_traces, candidate_traces, mode)
    )
    if args_compared:
        if mode in EQUIVALENCE_MODES:
            base_akeys = [canonical_sequence(tool_arg_sequence(t), mode) for t in baseline_traces]
            cand_akeys = [canonical_sequence(tool_arg_sequence(t), mode) for t in candidate_traces]
            arg_tvd = total_variation_distance(base_akeys, cand_akeys)
            arg_p = permutation_pvalue_distribution(base_akeys, cand_akeys)
        else:
            # Mirror the name signal's own dispatch. Bucketing a directional mode by a key
            # would flag the very change the mode PERMITS: under `subset` a dropped whole
            # call must stay legal even though dropping it changes the argument key too.
            ref_aseq = modal_arg_sequence(baseline_traces, mode)
            base_aviol = [
                0 if trajectory_match(ref_aseq, tool_arg_sequence(t), mode) else 1
                for t in baseline_traces
            ]
            cand_aviol = [
                0 if trajectory_match(ref_aseq, tool_arg_sequence(t), mode) else 1
                for t in candidate_traces
            ]
            arg_tvd = max(0.0, _mean(cand_aviol) - _mean(base_aviol))
            arg_p = permutation_pvalue_mean(base_aviol, cand_aviol)
        arg_regressed = arg_p <= ALPHA and arg_tvd >= MIN_TOOL_ARG_TVD

    # --- refusal rate ----------------------------------------------------------
    refusal_delta = refusal_rate(candidate_traces) - refusal_rate(baseline_traces)
    refusal_p = permutation_pvalue_mean(
        refused_flags(baseline_traces), refused_flags(candidate_traces)
    )
    refusal_regressed = refusal_p <= ALPHA and refusal_delta >= MIN_REFUSAL_DELTA

    # --- output format / assertion drift (soft signal) -------------------------
    fmt_p = 1.0
    fmt_drift = False
    if scenario and scenario.assertions:
        a = scenario.assertions
        base_v = assertion_violation_flags(baseline_traces, a.must_contain, a.must_not_contain)
        cand_v = assertion_violation_flags(candidate_traces, a.must_contain, a.must_not_contain)
        fmt_delta = _mean(cand_v) - _mean(base_v)
        fmt_p = permutation_pvalue_mean(base_v, cand_v)
        fmt_drift = fmt_p <= ALPHA and fmt_delta > 0

    # --- semantic equivalence (LLM-as-judge; optional, only when a judge is given) ---
    semantic_score: Optional[float] = None
    semantic_p = 1.0
    semantic_diverged = False
    if judge is not None:
        base_sem, cand_sem, semantic_score = semantic_divergence_flags(
            baseline_traces, candidate_traces, judge, _scenario_task(scenario)
        )
        semantic_delta = _mean(cand_sem) - _mean(base_sem)
        semantic_p = permutation_pvalue_mean(base_sem, cand_sem)
        semantic_diverged = semantic_p <= ALPHA and semantic_delta >= MIN_SEMANTIC_DELTA

    # --- cheap deltas (informational; not part of the verdict) -----------------
    latency_delta = _mean([t.latency_ms for t in candidate_traces]) - _mean(
        [t.latency_ms for t in baseline_traces]
    )
    token_delta = round(
        _mean([t.tokens_out for t in candidate_traces])
        - _mean([t.tokens_out for t in baseline_traces])
    )

    signals = DiffSignals(
        # The tool-NAME trajectory distance, and ONLY that. It was briefly the worse of the
        # two tool sub-signals -- `max(tool_tvd, arg_tvd)` -- on the Spec-6A reading that a
        # tool call is name + args, so publishing 1.0 over a changed argument would be a
        # false claim of sameness. That reading is right about the CLAIM and wrong about the
        # COLUMN, and MP-112 is what made the difference: arguments now have their own
        # published field (`tool_arg_match`, the report's `Arg match` column), so the
        # composite no longer adds a claim -- it only removes one.
        #
        # [M] MP-74, re-reproduced 2026-08-27 on `arg_freetext_note` (5 runs/side, SAME model
        # both sides): tool names identical on all 10 runs -> `tool_tvd` 0.0, free-text
        # payloads disjoint -> `arg_tvd` 1.0, and the report published `Tool match 0.00`
        # beside `unchanged @ 1.00`. <=0.1.2 read 1.00 there. Sharper still, and NOT in the
        # row: a refusal-only `regression` ("refusal rate 0% -> 100%", tool names identical
        # throughout) published `Tool match 0.00` too, which reads as the tool trajectory
        # having broken. It had not.
        #
        # [M] `structural.py:162` records `tool_tvd` == 0.0 whenever `args_compared` holds --
        # 0 counterexamples over 191,808 pool pairs x 4 match modes, re-verified 2026-08-27
        # over 120,000. So under `max()` this column silently SWITCHED MEANING between rows:
        # the name signal where no arguments were compared, the argument signal where they
        # were, under one header. Each column now measures the thing its name says.
        #
        # Precisely stated, because the loose version oversells it: this column is now
        # identically 1.0 wherever `tool_arg_match` is populated, so it carries no bits in
        # that regime. What the split recovers is the PAIR -- under `max()`, `tool_tvd` was
        # unrecoverable from the published fields whenever `arg_tvd >= tool_tvd`; it is
        # recoverable now. And the contradiction is narrowed, not abolished: `unchanged @
        # 1.00` beside `Arg match 0.00` is the same shape one column over. That one is
        # deliberate (ADR-0029 decision 5 publishes the number an advisory rests on) and
        # pre-dates this change -- see the `Arg match` legend row on the backlog.
        #
        # [M] Verdict-neutral by construction: `tool_regressed` above reads `tool_p`/`tool_tvd`
        # directly and never this field. Nothing in `diff/` consumes it; it is rendered
        # (`report/__init__.py`), persisted in the report sidecar, and carried wholesale into
        # `scripts/drift_map.py` output via `model_dump()`.
        tool_call_match=round(1.0 - tool_tvd, 3),  # 1.0 == identical tool-NAME trajectory
        tool_arg_match=round(1.0 - arg_tvd, 3) if args_compared else None,
        format_valid=not fmt_drift,
        refusal_delta=round(refusal_delta, 3),
        semantic_score=semantic_score,
        latency_delta_ms=round(latency_delta, 3),
        token_delta=int(token_delta),
        # Populated on EVERY result, not just abstentions: degradation below the majority
        # threshold (e.g. 2 of 5 runs silent) still produces a verdict, and a reader must be
        # able to see that it did. This residual band is the largest gap ADR-0018 leaves open.
        degenerate_baseline=d_base,
        degenerate_candidate=d_cand,
        baseline_runs=n_base,
        candidate_runs=n_cand,
    )

    # --- verdict ---------------------------------------------------------------
    reasons: list[str] = []
    hard_pvalues: list[float] = []
    verdict = DiffVerdict.unchanged

    if tool_regressed:
        verdict = DiffVerdict.regression
        hard_pvalues.append(tool_p)
        if mode in EQUIVALENCE_MODES:
            reasons.append(
                f"tool-call behavior changed: {list(modal_sequence(baseline_traces, mode))} "
                f"-> {list(modal_sequence(candidate_traces, mode))}"
            )
        else:
            reasons.append(
                f"tool-call trajectory now violates the '{mode}' relation vs baseline "
                f"{list(modal_sequence(baseline_traces, mode))}"
            )
    if refusal_regressed:
        verdict = DiffVerdict.regression
        hard_pvalues.append(refusal_p)
        reasons.append(
            f"refusal rate {refusal_rate(baseline_traces):.0%} -> {refusal_rate(candidate_traces):.0%}"
        )
    minor_pvalues: list[float] = []
    if arg_regressed:
        # ADVISORY, not CI-failing (ADR-0029). `MIN_TOOL_ARG_TVD` has no labelled calibration
        # set behind it -- it is a guess, exactly as `MIN_SEMANTIC_DELTA` was before `37b63a1`,
        # and this is that commit's patch shape run in reverse. [M] Uncapped, the gate fails a
        # stranger's build ALONE: same model both sides, identical tool names, one optional
        # field jittering -> `regression` @ 0.992 at the shipped `runs: 5`, where `main`
        # @ `13a715c` reads `unchanged` @ 1.0. [M 2026-08-25 @ 0e81392] Priced offline over the
        # committed repertoires, the worst of 48 cells is 66/1580 = 4.18% of scored trials
        # (arg_optional_fields, gpt-4.1-mini, --match subset, N=3), 26 cells non-zero over
        # 0.08%-4.18%, against 0/0 before this signal existed. Method, artifact and the
        # uncertainty that binds it: docs/fp-measurement.md, "No argument threshold may be
        # fitted here".
        #
        # PROMOTE to a hard regression only on a labelled argument set under
        # `examples/calibration/` (behaviour change vs provider jitter), scored under ADR-0025,
        # re-priced across every match mode and N=3..6 -- the cost is NON-MONOTONE in N, so no
        # `runs` value buys this down and `runs` must NOT be tuned here. See ADR-0029 for the
        # falsifiable promotion condition. Tracked as MP-54.
        if verdict != DiffVerdict.regression:
            verdict = DiffVerdict.changed_minor
        minor_pvalues.append(arg_p)
        reasons.append(
            "tool-call arguments changed: "
            + describe_argument_change(
                modal_arg_sequence(baseline_traces, mode),
                modal_arg_sequence(candidate_traces, mode),
            )
        )
    if fmt_drift:
        if verdict != DiffVerdict.regression:
            verdict = DiffVerdict.changed_minor
        minor_pvalues.append(fmt_p)
        reasons.append("output format drift: violates the scenario's text assertions")
    if semantic_diverged:
        # Calibrated (examples/calibration/): a consistent semantic divergence beyond the
        # baseline's own spread, clearing MIN_SEMANTIC_DELTA at p <= ALPHA, is a hard,
        # CI-failing regression. The labeled sweep separated equivalent (delta 0.0) from real
        # meaning changes (delta >= 0.8) with 0 false positives at this floor, so this no
        # longer over-fires on reworded-but-equivalent answers. A single divergent run, or a
        # minority below the floor, still reads as noise (the permutation test + the floor).
        verdict = DiffVerdict.regression
        hard_pvalues.append(semantic_p)
        reasons.append(
            f"semantic drift: candidate answers diverge in meaning from baseline "
            f"(equivalence {semantic_score:.0%})"
        )

    # confidence = how sure we are of the verdict.
    #   regression/minor -> 1 - p of the firing signal (small p => high confidence);
    #   unchanged        -> smallest p across signals (1.0 when distributions match,
    #                       lower when something was a borderline near-miss).
    if verdict == DiffVerdict.regression:
        confidence = round(1.0 - min(hard_pvalues), 3)
    elif verdict == DiffVerdict.changed_minor:
        confidence = round(1.0 - min(minor_pvalues), 3)
    else:
        confidence = round(min(tool_p, arg_p, refusal_p, fmt_p, semantic_p), 3)

    explanation = "; ".join(reasons) if reasons else "no statistically significant behavior change"

    return DiffResult(
        scenario_id=scenario_id,
        from_model=from_model,
        to_model=to_model,
        verdict=verdict,
        signals=signals,
        confidence=confidence,
        explanation=explanation,
    )
