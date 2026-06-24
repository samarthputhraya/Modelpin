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

from typing import Optional

from modelpin.diff.stats import (
    permutation_pvalue_distribution,
    permutation_pvalue_mean,
    total_variation_distance,
)
from modelpin.diff.semantic import Judge, semantic_divergence_flags
from modelpin.diff.structural import (
    EQUIVALENCE_MODES,
    MatchMode,
    assertion_violation_flags,
    canonical_sequence,
    modal_sequence,
    refusal_rate,
    refused_flags,
    tool_call_sequence,
    trajectory_match,
)
from modelpin.models import DiffResult, DiffSignals, DiffVerdict, Scenario, Trace

#: Significance threshold for the permutation test. Lower = fewer false positives.
ALPHA = 0.05
#: A tool-call distribution must shift by at least this total-variation distance to
#: count — guards against trivially-significant jitter once N grows large.
MIN_TOOL_TVD = 0.5
#: Ignore refusal-rate rises smaller than this even if "significant" (one run in three).
MIN_REFUSAL_DELTA = 0.34
#: Candidate semantic-divergence rate must exceed the baseline's by at least this much.
#: CALIBRATED on examples/calibration/ (labeled set distinct from the held-out suite; see
#: docs/STATUS.md): equivalent-but-reworded pairs land at delta 0.0, and the meaning changes
#: that actually took effect land at delta >= 0.8 (one perturbation the model resisted scored
#: 0.0 — a failed perturbation, not a missed regression), so 0.5 sits in the empty gap with 0
#: false positives. The held-out suite re-validation stayed 0/8 with the promotion live. NOTE
#: the calibration set is still small and the perturbations synthetic — see docs/STATUS.md for
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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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
    if not baseline_traces or not candidate_traces:
        return DiffResult(
            scenario_id=scenario_id,
            from_model=from_model,
            to_model=to_model,
            verdict=DiffVerdict.unchanged,
            confidence=0.0,
            explanation="insufficient data: need baseline and candidate runs",
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
        tool_call_match=round(1.0 - tool_tvd, 3),  # 1.0 == identical distributions
        format_valid=not fmt_drift,
        refusal_delta=round(refusal_delta, 3),
        semantic_score=semantic_score,
        latency_delta_ms=round(latency_delta, 3),
        token_delta=int(token_delta),
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
        confidence = round(min(tool_p, refusal_p, fmt_p, semantic_p), 3)

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
