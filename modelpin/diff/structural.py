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
from typing import Any, Literal

from modelpin.diff.argkey import canonical_arguments
from modelpin.models import Trace

MatchMode = Literal["strict", "unordered", "subset", "superset"]

#: Equivalence modes collapse a sequence to a single hashable key (usable in a
#: distributional test). Directional modes do not — they are relations.
EQUIVALENCE_MODES: frozenset[str] = frozenset({"strict", "unordered"})


def tool_call_sequence(trace: Trace) -> tuple[str, ...]:
    """The ordered tuple of tool names this run invoked."""
    return tuple(tc.name for tc in trace.tool_calls)


def canonical_sequence(seq: Sequence[Any], mode: MatchMode = "strict") -> tuple[Any, ...]:
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
    base_seq: Sequence[Any], cand_seq: Sequence[Any], mode: MatchMode = "strict"
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


# --- the ARGUMENT signal (MP-04) --------------------------------------------------------
# Deliberately a SECOND extraction rather than a richer tool_call_sequence. [M] Folding
# arguments into the existing key does not sharpen the name signal, it DESTROYS it: the
# permutation test is relabeling-invariant, so once per-run argument jitter makes every key
# distinct, a total tool swap (web_search on all 5 baseline runs -> sql_query on all 5
# candidate runs) reads `unchanged` at confidence 1.0 where names-only reads `regression` at
# 0.992. Measured across the 286-pool enumeration, 42.98% of today's name-gate firings go
# silent. Keeping the two signals separate is what makes this fix additive.


def tool_arg_sequence(trace: Trace) -> tuple[tuple[str, str], ...]:
    """The ordered tuple of (tool name, canonical argument payload) this run invoked.

    Anchored to the NAME so that ``f(x=1), g(y=2)`` can never compare equal to
    ``f(y=2), g(x=1)``.
    """
    return tuple((tc.name, canonical_arguments(tc.arguments)) for tc in trace.tool_calls)


def modal_arg_sequence(traces: list[Trace], mode: MatchMode = "strict") -> tuple[Any, ...]:
    """The most common (name, args) key across runs — for explanations, never for gating."""
    if not traces:
        return ()
    keys = Counter(canonical_sequence(tool_arg_sequence(t), mode) for t in traces)
    return keys.most_common(1)[0][0]


def has_tool_arguments(traces: list[Trace]) -> bool:
    """Did EVERY run on this side record at least one non-empty argument payload?

    ANY is the wrong quantifier and it cost a 136-verdict false-positive sweep. ``{}`` is not
    a payload VALUE, it is three different things wearing one key: a genuine no-argument call,
    ``providers/openai.py``'s "partial/malformed args -- record the call, drop the payload",
    and ``providers/google.py``'s non-dict fallback. ``ToolCall.arguments`` defaults to ``{}``,
    so "not measured" and "measured empty" are INDISTINGUISHABLE in the data model.

    That matters because ``{}`` against a populated payload is disjoint BY CONSTRUCTION -- it
    scores TVD 1.0 exactly, clears the floor, and fires at maximum confidence. And because the
    artifact is systematic per ADAPTER rather than random per run, the permutation test offers
    literally no protection against it.

    [M] Replaying the tracked corpus (2,240 comparisons, both trace caches x all ordered model
    pairs x 4 match modes) with ANY: 34 argument-gate firings, 136 verdict flips, every one
    `unchanged` -> `regression`, 136 of 136 CROSS-vendor and 0 same-vendor, firing in BOTH
    directions on the same pair. With EVERY (this function): 0 firings, 0 flips. The MP-04
    headline bug still fires either way. The cost is a false NEGATIVE when a payload is
    dropped entirely, which is the safe direction. See ADR-0018 on "not measured" != "equal".
    """
    return bool(traces) and all(any(tc.arguments for tc in t.tool_calls) for t in traces)


def name_trajectory_is_stable(
    baseline_traces: list[Trace], candidate_traces: list[Trace], mode: MatchMode = "strict"
) -> bool:
    """Is the tool-NAME trajectory unimodal on each side AND identical across them?

    The precondition for running the argument gate at all. When the name trajectory is itself
    jittery, the NAME gate is already the responsible signal and the argument key is only
    refining noise — letting both gates fire on the same pool is what turns two tests into a
    raised error rate.

    [M] Exhaustive enumeration, 286 pool shapes x C(10,5) split-halves = 72,072 relabelings
    under a true null (2 names x 2 payloads):
        names only (status quo)         912/72072 = 1.2654%   worst pool 12/252
        + argument gate, no precondition           raises both the rate and the 12/252 ceiling
        + argument gate, THIS precondition  916/72072 = 1.2710%   worst pool 12/252
    +0.0056 percentage points, and the pre-existing worst-case ceiling is unchanged.

    RETRACTED 2026-08-25 (MP-105 / fp-guardian): this docstring previously called the
    precondition "the whole reason this fix is false-positive-neutral". **The fix is not
    false-positive-neutral, and the enumeration above structurally cannot show that it is**
    -- its null carries TWO payloads, while [M] the gate fires only when the two sides are
    payload-disjoint AND each side is internally concentrated, so a repertoire fixed at 2
    prices a different signal from the one that ships. [M] `__init__.py` sets
    `verdict = regression` on `arg_regressed` with no conjunction with `tool_regressed`: this
    is an INDEPENDENT verdict-raising path that adds false-positive mass to exactly the pools
    the ceiling argument below leaves untouched -- pools that were 0/252 before.

    What the precondition DOES buy is narrower, verified more strongly than the retracted
    claim, and still load-bearing:

    1. [M] The two gates are mutually exclusive PER POOL, not merely per split. This function
       requires all 2N runs to share ONE name key, which is split-independent, so `tool_tvd`
       is exactly 0.0 whenever `args_compared` holds -- 0 counterexamples over 191,808 pool
       pairs x 4 match modes. Without it the two channels could stack inside one pool.
    2. [M] The argument channel does not raise the name gate's worst-case PER-POOL CEILING,
       and this does NOT depend on the 2-payload null. Exhaustive over every payload multiset,
       equivalence modes: N=3 0/20 vs 0/20; N=4 2/70 vs 2/70; N=5 12/252 vs 12/252; N=6 44/924
       vs 44/924 -- equal at every N enumerated, with the N=5 argument worst pool reached at
       repertoire 6-7. Directional modes at N=5: 2/252 vs 12/252, strictly lower. (N>=7 not
       enumerated.) The ceiling comes from ALPHA's discreteness capping each channel; the
       precondition is what stops them stacking.

    CEILING IS NOT RATE, and conflating them is how the retracted claim was born. The
    per-pool ceiling is genuinely unchanged while the aggregate RATE rises from 0/0 on `main`
    to percent-scale. See `scripts/arg_gate_price.py`.
    """
    base_names = {canonical_sequence(tool_call_sequence(t), mode) for t in baseline_traces}
    cand_names = {canonical_sequence(tool_call_sequence(t), mode) for t in candidate_traces}
    return len(base_names) == 1 and base_names == cand_names


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
