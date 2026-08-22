"""Non-determinism handling for the behavioral diff. See spec section 6C.

Models are noisy: identical input yields slightly different runs. We therefore
compare the candidate *distribution* (N runs) against the baseline *distribution*
and only call something changed when the gap is unlikely to be sampling noise.
This is the core defense of the north-star metric: the FALSE-POSITIVE rate.

The test is an EXACT two-sample permutation test — no SciPy, fully deterministic:
enumerate every way to relabel the pooled runs into two groups of the original
sizes, and measure how often chance alone produces a gap at least as large as the
one observed. Small N (5 runs/side by default, see ``config.DEFAULT_RUNS``) is enumerated
exactly; large pools fall back to a fixed-seed random sample so the cost stays bounded and
reproducible.

Note the hard floor this creates: the smallest p attainable at N runs/side is
``2/C(2N, N)`` for the tool signal, so below N=4 it cannot reach ``p <= ALPHA`` on ANY
data. N is therefore a correctness input, not a cost dial. See MP-03.

Two statistics:
- a difference of means, for binary per-run signals (refusal rate, assertion-failure
  rate) — one-sided, because we only care when the *bad* rate rises;
- total-variation distance between categorical distributions, for tool-call
  sequence keys — two-sided, because any distributional shift is interesting.

A higher p-value means "more consistent with no real change". We flag only when
p is small AND the observed effect clears a minimum size, so trivial-but-significant
jitter at large N never trips an alarm.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Hashable, Sequence
from itertools import combinations
from math import comb

#: Pools at or below this many total runs are enumerated exactly; larger pools are
#: sampled. 16 total covers the spec's 3-8 runs-per-side range with exact p-values.
MAX_EXACT_RUNS = 16
SAMPLED_PERMUTATIONS = 5000
_SAMPLING_SEED = 0
_EPS = 1e-12


def _splits(n: int, k: int):
    """Yield candidate-group index tuples (size ``k``) over a pool of ``n`` runs.

    Exact (all ``C(n, k)`` combinations) for small pools; a deterministic,
    de-duplicated random sample for large ones. BOTH paths include the
    actually-observed labeling — the candidate occupies the last ``k`` pooled
    indices, so ``observed`` is yielded first on the sampled path — which keeps
    the permutation p-value valid (strictly positive: hits/total >= 1/total).
    Omitting it would let a clean spike report p=0.0, biasing toward false
    positives — the one thing this engine must never do.
    """
    if n <= MAX_EXACT_RUNS:
        yield from combinations(range(n), k)  # combinations() includes the observed split
        return
    observed = tuple(range(n - k, n))
    seen: set[tuple[int, ...]] = {observed}
    yield observed
    rng = random.Random(_SAMPLING_SEED)
    pool = list(range(n))
    target = min(SAMPLED_PERMUTATIONS, comb(n, k))
    while len(seen) < target:
        pick = tuple(sorted(rng.sample(pool, k)))
        if pick not in seen:
            seen.add(pick)
            yield pick


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def permutation_pvalue_mean(baseline: Sequence[float], candidate: Sequence[float]) -> float:
    """One-sided p-value for ``mean(candidate) > mean(baseline)``.

    Use for binary per-run rates (refusal, assertion failure) where only an
    increase is a regression. Returns 1.0 (no evidence) when the observed
    increase is non-positive or a group is empty.
    """
    nb, nc = len(baseline), len(candidate)
    if nb == 0 or nc == 0:
        return 1.0
    observed = _mean(candidate) - _mean(baseline)
    if observed <= 0:
        return 1.0
    pool = list(baseline) + list(candidate)
    n = nb + nc
    hits = total = 0
    for cand_idx in _splits(n, nc):
        cand_sum = sum(pool[i] for i in cand_idx)
        base_sum = sum(pool) - cand_sum
        stat = cand_sum / nc - base_sum / nb
        total += 1
        if stat >= observed - _EPS:
            hits += 1
    return hits / total if total else 1.0


def _frequencies(keys: Sequence[Hashable]) -> dict[Hashable, float]:
    n = len(keys)
    return {k: v / n for k, v in Counter(keys).items()}


def total_variation_distance(a_keys: Sequence[Hashable], b_keys: Sequence[Hashable]) -> float:
    """0 (identical distributions) .. 1 (disjoint) over two categorical samples."""
    fa, fb = _frequencies(a_keys), _frequencies(b_keys)
    keys = set(fa) | set(fb)
    return 0.5 * sum(abs(fa.get(k, 0.0) - fb.get(k, 0.0)) for k in keys)


def permutation_pvalue_distribution(
    base_keys: Sequence[Hashable], cand_keys: Sequence[Hashable]
) -> float:
    """Two-sided p-value that two categorical samples share one distribution, using
    total-variation distance as the statistic. Returns 1.0 when the samples are
    identical or a group is empty."""
    nb, nc = len(base_keys), len(cand_keys)
    if nb == 0 or nc == 0:
        return 1.0
    observed = total_variation_distance(base_keys, cand_keys)
    if observed <= 0:
        return 1.0
    pool = list(base_keys) + list(cand_keys)
    n = nb + nc
    hits = total = 0
    for cand_idx in _splits(n, nc):
        cand_set = set(cand_idx)
        cand_group = [pool[i] for i in cand_idx]
        base_group = [pool[i] for i in range(n) if i not in cand_set]
        total += 1
        if total_variation_distance(base_group, cand_group) >= observed - _EPS:
            hits += 1
    return hits / total if total else 1.0
