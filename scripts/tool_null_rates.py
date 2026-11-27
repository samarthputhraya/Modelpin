"""Exact same-model false-alarm rates of the tool-call channel, computed, not sampled. No API key.

    python scripts/tool_null_rates.py            # 5 candidate runs, 5 baseline runs, `strict`
    python scripts/tool_null_rates.py --baseline-runs 10

The model is the same on both sides, every answer has identical text and there is no judge, so
only the tool-call trajectory can fire and every `regression` is a false alarm by construction.
Each run independently follows one of 2 or 3 trajectories (`lookup`; `lookup` then `notify`;
no tool call) with probabilities `p`. The real `diff_scenario` is evaluated once per pair of
(baseline counts, candidate counts), which is all it depends on, and the pairs are weighted by
their multinomial probabilities, so every number below is exact for the stated `p`.

With confirmation (the shipped default, `modelpin/diff/confirm.py`) a regression stands only
when a fresh candidate sample, scored alone against the SAME baseline, also regresses on the
same channel. Writing q(B) for the chance a candidate sample flags against baseline B:

    per check, first sample      sum_B P(B) q(B)
    per check, after confirming  sum_B P(B) q(B)^2
    one recorded baseline B      q(B) before confirming, q(B)^2 after, on EVERY check against B

The worst `p` is found on a grid (steps of 0.05), so a worst case is a lower bound on the true
supremum by at most the grid's resolution. Results are per scenario: a suite with several
scenarios whose tool use is genuinely random adds their chances.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from functools import lru_cache
from math import comb, factorial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.diff import diff_scenario  # noqa: E402
from modelpin.models import DiffVerdict, MatchModeName as MatchMode, ToolCall, Trace  # noqa: E402

SHAPES = (
    [ToolCall(name="lookup")],
    [ToolCall(name="lookup"), ToolCall(name="notify")],
    [],
)


def compositions(n: int, k: int):
    for c in itertools.product(range(n + 1), repeat=k):
        if sum(c) == n:
            yield c


def multinomial(counts: tuple[int, ...], probs: tuple[float, ...]) -> float:
    coef = factorial(sum(counts))
    pr = 1.0
    for c, p in zip(counts, probs):
        coef //= factorial(c)
        pr *= p**c
    return coef * pr


def _side(counts: tuple[int, ...]) -> list[Trace]:
    out: list[Trace] = []
    for shape, c in zip(SHAPES, counts):
        out.extend(
            Trace(
                scenario_id="s",
                model_id="m",
                run_idx=len(out) + i,
                tool_calls=shape,
                final_output="ok",
            )
            for i in range(c)
        )
    return out


@lru_cache(maxsize=None)
def flags(base: tuple[int, ...], cand: tuple[int, ...], mode: MatchMode = "strict") -> bool:
    """Does the real engine call this pair of samples a regression?"""
    r = diff_scenario("s", "m", "m", _side(base), _side(cand), mode=mode)
    return r.verdict is DiffVerdict.regression


def rates(probs: tuple[float, ...], n: int = 5, b: int = 5, mode: MatchMode = "strict") -> dict:
    """Per-check rates (first sample, confirmed) and the worst single baseline, for `probs`."""
    k = len(probs)
    first = confirmed = 0.0
    worst = (0.0, (), 0.0)  # (q, baseline counts, probability of recording that baseline)
    for base in compositions(b, k):
        pb = multinomial(base, probs)
        q = sum(multinomial(c, probs) for c in compositions(n, k) if flags(base, c, mode))
        first += pb * q
        confirmed += pb * q * q
        if q > worst[0]:
            worst = (q, base, pb)
    return {"first": first, "confirmed": confirmed, "worst_baseline": worst}


def grid(k: int, step: int = 20) -> list[tuple[float, ...]]:
    if k == 2:
        return [(i / step, 1 - i / step) for i in range(1, step)]
    return [
        (a / step, c / step, (step - a - c) / step)
        for a in range(step + 1)
        for c in range(step + 1 - a)
    ]


def pool_ceiling(n: int = 5, mode: MatchMode = "strict") -> tuple[int, int]:
    """The most splits of one pooled set of 2n runs, over 2 trajectories, that flag."""

    def counts(pool: list[int], idx) -> tuple[int, int]:
        picked = [pool[i] for i in idx]
        return (picked.count(0), picked.count(1))

    worst = 0
    for a in range(2 * n + 1):
        pool = [0] * a + [1] * (2 * n - a)
        hits = 0
        for cand in itertools.combinations(range(2 * n), n):
            rest = [i for i in range(2 * n) if i not in set(cand)]
            hits += flags(counts(pool, rest), counts(pool, cand), mode)
        worst = max(worst, hits)
    return worst, comb(2 * n, n)


def summary(n: int = 5, b: int = 5, mode: MatchMode = "strict") -> dict:
    out: dict = {}
    for k in (2, 3):
        results = [(rates(p, n, b, mode), p) for p in grid(k)]
        f = max(results, key=lambda x: x[0]["first"])
        c = max(results, key=lambda x: x[0]["confirmed"])
        even = rates(tuple(1 / k for _ in range(k)), n, b, mode)
        out[k] = {
            "worst_first": (f[0]["first"], f[1]),
            "worst_confirmed": (c[0]["confirmed"], c[1]),
            "even": even,
        }
    out["pool_ceiling"] = pool_ceiling(n, mode)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=5, help="candidate runs per check")
    ap.add_argument("--baseline-runs", type=int, default=5, help="recorded baseline runs")
    ap.add_argument(
        "--match", default="strict", choices=["strict", "unordered", "subset", "superset"]
    )
    a = ap.parse_args(argv)
    s = summary(a.runs, a.baseline_runs, a.match)
    print(f"runs={a.runs} baseline-runs={a.baseline_runs} match={a.match}")
    for k in (2, 3):
        r = s[k]
        q, base, pb = r["even"]["worst_baseline"]
        print(f"  {k} trajectories:")
        print(
            f"    worst per check, first sample:    {r['worst_first'][0]:.2%} at p={r['worst_first'][1]}"
        )
        print(
            f"    worst per check, after confirming: {r['worst_confirmed'][0]:.2%} at p={r['worst_confirmed'][1]}"
        )
        print(
            f"    even split, worst recorded baseline {base}: {q:.2%} of checks flag, "
            f"{q * q:.2%} after confirming; chance of recording it {pb:.2%}"
        )
    hits, total = s["pool_ceiling"]
    print(
        f"  worst pooled split ({a.runs} + {a.runs} runs, 2 trajectories): {hits}/{total} = {hits / total:.2%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
