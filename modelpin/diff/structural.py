"""Structural signals for the behavioral diff. See spec section 6A.

Cheap, deterministic, per-run extractions over recorded traces:
- tool-call trajectory, with strict / unordered / subset / superset match modes;
- refusal flags;
- text/format assertion violations.

These turn each run into a comparable signal. The non-determinism handling that
combines N runs into a calibrated verdict lives in ``stats.py`` + ``__init__.py``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from modelpin.models import Trace

MatchMode = Literal["strict", "unordered", "subset", "superset"]

#: Equivalence modes collapse a sequence to a single hashable key (usable in a
#: distributional test). Directional modes do not — they are relations.
EQUIVALENCE_MODES: frozenset[str] = frozenset({"strict", "unordered"})


def tool_call_sequence(trace: Trace) -> tuple[str, ...]:
    """The ordered tuple of tool names this run invoked."""
    return tuple(tc.name for tc in trace.tool_calls)


def canonical_sequence(seq: Sequence[str], mode: MatchMode = "strict") -> tuple[str, ...]:
    """Map a tool-call sequence to a hashable key under an *equivalence* mode.

    ``strict`` preserves order; ``unordered`` ignores it. Directional modes
    (``subset`` / ``superset``) are not equivalences and must be evaluated with
    :func:`trajectory_match`, not bucketed by this key — we fall back to the
    ordered key for them so callers don't silently get wrong groupings.
    """
    if mode == "unordered":
        return tuple(sorted(seq))
    return tuple(seq)


def trajectory_match(
    base_seq: Sequence[str], cand_seq: Sequence[str], mode: MatchMode = "strict"
) -> bool:
    """Does the candidate trajectory still satisfy the baseline under ``mode``?

    - ``strict``:    identical ordered sequence.
    - ``unordered``: same multiset of calls, any order.
    - ``subset``:    candidate introduces no call absent from baseline (it may make
                     fewer) — allows dropping, forbids new calls.
    - ``superset``:  candidate omits no call present in baseline (it may add more) —
                     forbids dropping, allows new calls.
    """
    base = Counter(base_seq)
    cand = Counter(cand_seq)
    if mode == "strict":
        return tuple(base_seq) == tuple(cand_seq)
    if mode == "unordered":
        return base == cand
    if mode == "subset":
        return not (cand - base)  # nothing in candidate is missing from baseline
    if mode == "superset":
        return not (base - cand)  # nothing in baseline is missing from candidate
    raise ValueError(f"unknown match mode: {mode!r}")


def modal_sequence(traces: list[Trace], mode: MatchMode = "strict") -> tuple[str, ...]:
    """The most common tool-call sequence across runs (for human-readable explanations)."""
    if not traces:
        return ()
    keys = Counter(canonical_sequence(tool_call_sequence(t), mode) for t in traces)
    return keys.most_common(1)[0][0]


def is_degenerate(trace: Trace) -> bool:
    """True when this run recorded NO behavior the diff can read.

    The three clauses are exactly the three verdict-bearing per-run extractions in this
    module -- ``tool_call_sequence``, ``refused_flags``, ``violates_text_assertions`` --
    plus ``semantic.py``'s read of ``final_output``. If all four have nothing to work with,
    the run is not evidence of "no change"; it is an absence of evidence. See ADR-0018.

    ``latency_ms`` and the token counts are deliberately NOT consulted: ADR-0003 makes them
    reported-but-never-gating, and the adapters sum usage with ``or 0``, so a zero there is
    not evidence of anything.

    ``not trace.refused`` is LOAD-BEARING and must not be tidied away. A genuinely empty
    response really does carry ``refused=False`` (``looks_like_refusal("")`` is False), while
    a content-filter refusal can carry empty text and ``refused=True`` -- and that IS a
    complete measurement which must stay in the comparison. Dropping this clause would turn
    every both-sides-refuse scenario from a correct ``unchanged`` into an abstention.
    """
    return not trace.tool_calls and not trace.refused and not (trace.final_output or "").strip()


def degenerate_count(traces: list[Trace]) -> int:
    """How many of these runs recorded nothing at all."""
    return sum(1 for t in traces if is_degenerate(t))


def refused_flags(traces: list[Trace]) -> list[int]:
    """Per-run refusal as 0/1, for the distributional test."""
    return [1 if t.refused else 0 for t in traces]


def refusal_rate(traces: list[Trace]) -> float:
    if not traces:
        return 0.0
    return sum(1 for t in traces if t.refused) / len(traces)


def violates_text_assertions(
    trace: Trace,
    must_contain: Sequence[str] | None,
    must_not_contain: Sequence[str] | None,
) -> bool:
    """A basic stand-in for output-format/schema validity (spec 6A): does this run's
    output break the scenario's must-/must-not-contain assertions?"""
    out = trace.final_output or ""
    if must_contain and not all(s in out for s in must_contain):
        return True
    if must_not_contain and any(s in out for s in must_not_contain):
        return True
    return False


def assertion_violation_flags(
    traces: list[Trace],
    must_contain: Sequence[str] | None,
    must_not_contain: Sequence[str] | None,
) -> list[int]:
    """Per-run assertion violation as 0/1, for the distributional test."""
    return [1 if violates_text_assertions(t, must_contain, must_not_contain) else 0 for t in traces]
