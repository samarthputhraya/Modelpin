"""Semantic signal (LLM-as-judge). See spec section 6B.

When a candidate's output differs *textually* from the baseline, ask a judge model
whether the two answers accomplish the same task / mean the same thing. The judge is
OPTIONAL and injected: with no judge the diff stays purely structural (and fully
offline). The signal is wired into the verdict through the SAME distributional
permutation test as the other signals (see ``diff/__init__.py``), so a single divergent
run is noise — only a *consistent* candidate-side semantic drift, beyond the baseline's
own natural spread, is flagged. This protects the false-positive north-star: reworded
but equivalent answers must not raise an alarm.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional, Protocol

from modelpin.models import Trace


class Judge(Protocol):
    """An equivalence oracle. Implementations call an LLM; tests inject a fake."""

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool:
        """True if ``candidate`` accomplishes the same task / means the same as ``reference``."""
        ...


_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip().lower())


def reference_output(traces: list[Trace]) -> str:
    """The modal ``final_output`` across runs — the representative 'expected' answer."""
    outputs = [t.final_output or "" for t in traces]
    if not outputs:
        return ""
    return Counter(outputs).most_common(1)[0][0]


def _equivalent_to_any(
    output: str,
    pool: list[str],
    judge: Judge,
    task: Optional[str] = None,
) -> bool:
    """Is ``output`` equivalent to AT LEAST ONE of ``pool``?

    An EMPTY pool returns True — nothing to disagree with is not divergence. That is the
    single-run baseline (``runs: 1``), where leave-one-out leaves nothing: flagging it would
    make every such run divergent by construction and manufacture a regression out of a
    configuration the CLI already refuses for other reasons (``MIN_RUNS``).

    Two short-circuits, in cost order. Text that normalises to a pool member's is equivalent
    with no judge call at all — the same fast path the single-reference version had, widened
    from one string to the pool. Otherwise the judge is asked run by run and the first
    equivalence wins, so the common case (a candidate matching the baseline's first recorded
    run) costs one call rather than ``len(pool)``.

    `[M]` The short-circuit makes the result order-dependent if the judge is not transitive,
    and MP-208 measured two judges disagreeing on 5 of 24 semantic scores over identical
    traces — so that is a live possibility, not a hypothetical. ADR-0040's third falsifier is
    exactly this: if non-transitivity shows up, the short-circuit goes and the comparison
    becomes exhaustive.
    """
    if not pool:
        return True
    norm = _normalize(output)
    if any(norm == _normalize(p) for p in pool):
        return True
    return any(judge.equivalent(p, output, task) for p in pool)


def semantic_divergence_flags(
    baseline_traces: list[Trace],
    candidate_traces: list[Trace],
    judge: Judge,
    task: Optional[str] = None,
) -> tuple[list[int], list[int], float]:
    """Per-run semantic-divergence flags (0/1) for baseline and candidate, and the mean
    candidate equivalence score (1.0 == every candidate run matches the baseline meaning).

    A run is divergent when it is equivalent to **no** baseline run. It is NOT divergent
    merely for differing from one arbitrarily chosen baseline run, which is what this
    function used to ask (MP-206, ADR-0040).

    `[M] 2026-09-07` Why that mattered. The old rule compared every run, both sides, against
    the MODAL baseline output — and for free text at temperature 1.0 the five baseline runs
    are five distinct strings, so the "mode" was simply baseline run 0, chosen arbitrarily. A
    strict judge then called some of the OTHER baseline runs non-equivalent to that arbitrary
    reference, and those flags landed on the baseline side of a one-sided test. On the run of
    record (`reports/fp-runs/2026-09-07/`, `recall:triage_ticket_json`) that made a candidate
    answering `other`/`low` on 5 of 5 runs, against a baseline answering `bug`/`high` on 5 of
    5, read **`unchanged` @ 0.083**: the judge had flagged all five candidate runs, and the
    baseline's own noise ate the signal. Recomputed offline, `base_flags` of 2/5 and 4/5
    reproduce the recorded 0.083 and 0.50 exactly; at 0/5 the same candidate scores p = 0.004.

    The baseline side is scored LEAVE-ONE-OUT — run *i* against every baseline run but
    itself. That is not symmetry for its own sake: comparing a baseline run against a pool
    containing itself would make ``base_flags`` identically zero by construction and delete
    the false-positive protection this function otherwise provides, turning any candidate
    rewording into a flag. The baseline's genuine spread has to stay in the comparison.

    Text identical to any pool member (after whitespace/case normalization) still skips the
    judge call and counts as equivalent — the same fast path as before, widened from one
    reference to the pool.
    """
    base_outputs = [t.final_output or "" for t in baseline_traces]

    base_flags = [
        0 if _equivalent_to_any(out, base_outputs[:i] + base_outputs[i + 1 :], judge, task) else 1
        for i, out in enumerate(base_outputs)
    ]
    cand_flags = [
        0 if _equivalent_to_any(t.final_output or "", base_outputs, judge, task) else 1
        for t in candidate_traces
    ]
    cand_score = 1.0 - (sum(cand_flags) / len(cand_flags)) if cand_flags else 1.0
    return base_flags, cand_flags, round(cand_score, 3)
