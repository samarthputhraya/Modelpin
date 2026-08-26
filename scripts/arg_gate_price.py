"""Price the tool-call ARGUMENT gate's added false positives — offline, no API key.

MP-04 shipped a second structural gate defended by an exhaustive enumeration over a
constructed null: `+0.0056 percentage points`, 1.2654% -> 1.2710%. `[M]` **That null carries
two payloads.** The gate fires only when the two sides are payload-disjoint AND each side is
internally concentrated, so a null that fixes the repertoire at 2 prices a different signal
from the one that ships. This script prices it against repertoires this repo has measured
(`examples/calibration/results/arg-repertoire-*.json`). No key, no network call, so it answers
condition 6's dominant term before the gate is ever run live (ADR-0006).

WHAT MAKES THE GATE FIRE — the premise an earlier draft of this file got wrong
------------------------------------------------------------------------------
Disjointness is necessary, NOT sufficient, and it is not the dominant term. `[M]` At N=5:

    base a,a,a,a,a  / cand b,b,b,b,b   disjoint, p = 0.0079   FIRES
    base a,a,a,b,b  / cand c,c,c,d,d   disjoint, p = 0.0159   FIRES
    base a,a,a,b,c  / cand d,d,e,f,g   disjoint, p = 0.0873   does NOT fire
    ten pooled distinct payloads       disjoint, p = 1.0      does NOT fire, and is EXCLUDED

`permutation_pvalue_distribution` is relabeling-invariant: at maximum jitter every relabeling
reproduces the observed TVD, p is exactly 1.0, and the gate goes silent. So "more argument
variance is more dangerous" is FALSE at the top end, and the risk lives in the middle — sides
concentrated enough to look decisive, diverse enough across sides to be disjoint.

TWO ESTIMATORS. THE HEADLINE IS THE ONE THAT ASSUMES LEAST
-----------------------------------------------------------
`split_half`  **HEADLINE.** Deal the ACTUAL observed runs into two disjoint sides of N,
              without replacement. No plug-in distribution, no invented tail. Its
              finite-population dependence errs toward OVER-reporting, which is the safe
              direction for a false-positive claim.
`plug_in`     With-replacement resampling from the observed frequencies. Reported for
              contrast only. `[M]` The two disagree on where the N-curve peaks, which is why
              neither may be quoted alone.

`[M]` Both drive the REAL `diff_scenario` and classify with `fp_measurement.classify` /
`measurable` — the repo's own ADR-0022 predicate, never a second copy of it.

WHAT THIS DOES NOT MEASURE. READ BEFORE QUOTING ANY NUMBER
-----------------------------------------------------------
1. `[M]` **One false-positive mode only.** Both sides are drawn from ONE model's own runs, so
   this prices sampling noise against a same-model null. It structurally cannot see the mode
   this repo has already recorded — two different models emitting different-but-equivalent
   payloads (`3.36` vs `3.357` kg, `regression` @ 0.992). That mode is strictly additional and
   remains unpriced.
2. `[M]` **The argument channel in isolation**, not `mp check` as shipped: the synthetic traces
   carry no assertions, no refusal and no judge, so every other channel returns p = 1.0. The
   shipped verdict is an OR across five channels, so the gate's MARGINAL contribution there is
   smaller; the shipped `confidence = min(p)` also moves the ADR-0022 denominator.
3. `[M]` **Three scenarios, not seven.** Four of the seven `arg_*` shapes emit a single payload
   in 16 runs on every model measured and contribute 0 to both numerator and denominator.
   Replicates buy Monte-Carlo resolution, never scenario coverage — MP-105's exchangeability
   caveat carries forward unchanged.
4. `[A]` **Exchangeability is untestable from the committed artifacts.** They store frequencies;
   `arg_repertoire.py` now also stores `payload_sequence` so a future run can test for drift,
   but the runs already committed cannot be checked for it.
5. `[M]` **The plug-in tail is truncated and the bias is NOT sign-determined.** Good-Turing puts
   31-44% of the mass on payloads the 16 runs never saw. Truncation raises within-side
   concentration (helps firing) while lowering cross-side disjointness (hurts firing); the net
   moves the rate by x0.4 to x2.0 depending on the scenario. Nothing here may be called
   conservative.

THE INTERVAL THIS SCRIPT REFUSES TO PUBLISH
--------------------------------------------
No binomial interval over replicates. `[M]` Replicates are draws from an assumed population:
a Clopper-Pearson bound over them shrinks with compute, so it measures spend rather than
uncertainty. Held at a fixed 2.8% point estimate, `upper_bound_95` returns 3.82% at 1,000
replicates and 3.27% at 4,000 — the "bound" tightens because you bought CPU, not because you
learned anything about the world. `[M]` `upper_bound_95` physically refuses the
question — it raises `OverflowError` above n ~= 3,000 — because its `n` is a count of REAL
trials. The uncertainty that matters is the OUTER one: sampling error in the n=16 estimate of
the payload distribution, which does not shrink with replicates. `--outer` measures it by
double bootstrap.

WEIGHTING IS A CHOICE, NOT A MEASUREMENT
-----------------------------------------
`[M]` On one model at N=5 the same data reads 2.70% (replicate-pooled), 2.27% (scenario-mean
over the varying shapes), 3.26% (dropping `arg_optional_fields`) and 0.97% (scenario-mean over
all seven, crediting the four invariant shapes with 0). This script publishes **per scenario**
and prints every weighting, so the choice is visible rather than baked in.

ROLE DISCIPLINE (ADR-0025)
--------------------------
`examples/calibration/arg_*` is role `score`. SCORING the gate against these repertoires is
what they are for. Using them to MOVE `MIN_TOOL_ARG_TVD` would make the threshold in-sample
for the only surface that can measure it. This script imports the constants and never sweeps
them.

    python scripts/arg_gate_price.py --reps 4000 --out examples/calibration/results/arg-gate-price.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modelpin.diff import ALPHA, MIN_TOOL_ARG_TVD, diff_scenario  # noqa: E402
from modelpin.diff.stats import MAX_EXACT_RUNS  # noqa: E402
from modelpin.models import Scenario, ToolCall, Trace  # noqa: E402

from fp_measurement import classify, measurable  # noqa: E402

#: N=3 is the smallest run count where any mode can fire. The sweep stops at 6 by DEFAULT for
#: cost, not for evidence: the permutation test is exact, so a replicate costs C(2N,N), and
#: `[M]` 1000 replicates take ~3s at N=5 but ~239s at N=8. Extend with `--ns` when the tail
#: matters. `[M]` It is bounded at 8 regardless: above `MAX_EXACT_RUNS` the pooled size forces
#: `stats.py` into a fixed-seed 5,000-permutation approximation that every replicate shares
#: identically, so the error is not averaged away by more replicates.
DEFAULT_NS = (3, 4, 5, 6)
MAX_SWEEP_N = MAX_EXACT_RUNS // 2

#: One equivalence mode and one directional mode. `[M]` They are different statistics with
#: different p-floors and they invert: at N=3 `strict` cannot fire at all while `subset` fires,
#: and at N=5 the ordering reverses. Quoting one mode alone publishes a false zero.
DEFAULT_MODES = ("strict", "subset")

_SCENARIO = Scenario.model_validate(
    {"id": "priced", "name": "priced", "kind": "agent", "input": {"messages": []}}
)


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def good_turing_unseen(counts: list[int]) -> float:
    """Good-Turing estimate of the probability mass on payloads never observed.

    `[M]` 31-44% across the committed repertoires, which is why the plug-in estimator's tail
    truncation is a first-order effect and not a rounding detail.
    """
    total = sum(counts)
    singletons = sum(1 for c in counts if c == 1)
    return singletons / total if total else 0.0


def _traces(values: list[int]) -> list[Trace]:
    return [
        Trace(
            scenario_id="priced",
            model_id="m",
            run_idx=i,
            tool_calls=[ToolCall(name="f", arguments={"v": v})],
            final_output="done",
        )
        for i, v in enumerate(values)
    ]


def _verdict_tally(pairs, mode: str) -> dict[str, Any]:
    tally: Counter[str] = Counter()
    for base, cand in pairs:
        result = diff_scenario(
            "priced", "m", "m", _traces(base), _traces(cand), _SCENARIO, mode, None
        )
        kind = classify(result.verdict)
        if kind == "unmeasured":
            tally["unmeasured"] += 1
        elif kind == "fp":
            tally["fp"] += 1
        else:
            tally["clean" if measurable(result) else "excluded"] += 1
    scored = tally["fp"] + tally["clean"]
    return {
        "false_positives": tally["fp"],
        "scored": scored,
        "excluded_adr0022": tally["excluded"],
        "attempted": sum(tally.values()),
        "fp_rate_of_scored": (tally["fp"] / scored) if scored else None,
        "fp_rate_of_attempted": (tally["fp"] / sum(tally.values())) if tally else None,
    }


def split_half(observed: list[int], n: int, reps: int, rng: random.Random, mode: str):
    """HEADLINE estimator: deal the ACTUAL runs into two disjoint sides of n.

    Without replacement, so it invents no payload the model never emitted. Requires 2n <= the
    number of observed runs; returns None when the repertoire is too small to support it,
    which is a real limit of the evidence and not something to paper over.
    """
    if 2 * n > len(observed):
        return None
    pairs = []
    for _ in range(reps):
        deal = rng.sample(observed, 2 * n)
        pairs.append((deal[:n], deal[n:]))
    return _verdict_tally(pairs, mode)


def plug_in(probs: list[float], n: int, reps: int, rng: random.Random, mode: str):
    """Contrast estimator: with-replacement draws from the observed frequencies."""
    values = list(range(len(probs)))
    pairs = [
        (rng.choices(values, weights=probs, k=n), rng.choices(values, weights=probs, k=n))
        for _ in range(reps)
    ]
    return _verdict_tally(pairs, mode)


def load_repertoires(results_dir: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted(results_dir.glob("arg-repertoire-*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for scenario_id, row in doc["scenarios"].items():
            counts = list(row.get("payload_counts", {}).values())
            if not counts:
                continue
            observed = [i for i, c in enumerate(counts) for _ in range(c)]
            out.append(
                {
                    "artifact": path.name,
                    "model": doc["model"],
                    "scenario": scenario_id,
                    "observed_runs": len(observed),
                    "distinct_payloads": len(counts),
                    "counts": counts,
                    "good_turing_unseen": good_turing_unseen(counts),
                    "_observed": observed,
                }
            )
    return out


def outer_interval(counts: list[int], n: int, refits: int, inner: int, seed: int, mode: str):
    """The uncertainty that actually matters: sampling error in the n=16 repertoire itself.

    Double bootstrap — resample the observed runs to get a new repertoire, then price that.
    Unlike an interval over replicates, this does NOT shrink as you spend more compute.
    """
    rng = random.Random(seed)
    observed = [i for i, c in enumerate(counts) for _ in range(c)]
    rates = []
    for _ in range(refits):
        refit = Counter(rng.choices(observed, k=len(observed)))
        probs = [refit.get(i, 0) / len(observed) for i in range(len(counts))]
        if sum(1 for p in probs if p > 0) < 2:
            rates.append(0.0)
            continue
        row = plug_in(probs, n, inner, rng, mode)
        if row["fp_rate_of_scored"] is not None:
            rates.append(row["fp_rate_of_scored"])
    if not rates:
        return None
    rates.sort()
    lo = rates[max(0, int(0.025 * len(rates)) - 1)]
    hi = rates[min(len(rates) - 1, int(0.975 * len(rates)))]
    return {
        "refits": len(rates),
        "inner_reps": inner,
        "p2_5": lo,
        "p97_5": hi,
        "median": rates[len(rates) // 2],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--reps", type=int, default=3000, help="replicates per (scenario, N, mode)")
    ap.add_argument("--seed", type=int, default=20260825)
    ap.add_argument("--results-dir", default="examples/calibration/results")
    ap.add_argument("--modes", default=",".join(DEFAULT_MODES))
    ap.add_argument(
        "--ns",
        default=",".join(str(n) for n in DEFAULT_NS),
        help=f"run counts to sweep; capped at {MAX_EXACT_RUNS // 2} (exact permutations)",
    )
    ap.add_argument("--outer", action="store_true", help="also run the double bootstrap (slow)")
    ap.add_argument("--outer-refits", type=int, default=40)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    if a.reps < 200:
        raise SystemExit("error: --reps must be >= 200; below that the resolution is noise.")
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    ns = tuple(int(x) for x in a.ns.split(",") if x.strip())
    if any(n > MAX_SWEEP_N for n in ns):
        raise SystemExit(
            f"error: --ns above {MAX_SWEEP_N} would push 2N past MAX_EXACT_RUNS="
            f"{MAX_EXACT_RUNS}, where stats.py returns a fixed-seed approximation that every "
            "replicate shares identically. The error would not average away."
        )

    reps_all = load_repertoires(Path(a.results_dir))
    if not reps_all:
        raise SystemExit(f"error: no arg-repertoire-*.json in {a.results_dir}.")

    print(
        f"argument-gate pricing (OFFLINE)  reps={a.reps}/cell  seed={a.seed}  modes={modes}\n"
        f"ALPHA={ALPHA}  MIN_TOOL_ARG_TVD={MIN_TOOL_ARG_TVD}  N sweep {ns} "
        f"(capped: 2N <= MAX_EXACT_RUNS={MAX_EXACT_RUNS})\n"
        f"On `main` this channel scores 0/0 and the harness prints its "
        f"*** THIS RUN MEASURED NOTHING *** banner: nothing under modelpin/diff/ reads "
        f".arguments.\n"
    )

    artifact: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelpin_version": version("modelpin"),
        "git_sha": _git_sha(),
        "alpha": ALPHA,
        "min_tool_arg_tvd": MIN_TOOL_ARG_TVD,
        "reps_per_cell": a.reps,
        "seed": a.seed,
        "modes": modes,
        "n_sweep": list(ns),
        "headline_estimator": "split_half",
        "notes": {
            "no_binomial_interval": (
                "Replicates are draws from an assumed population. A Clopper-Pearson bound over "
                "them shrinks with compute and says nothing about the world; upper_bound_95 "
                "raises OverflowError above n~3000 by design, because its n counts REAL trials."
            ),
            "status_quo": (
                "main scores 0/0 on this channel and the harness reports MEASURED NOTHING. The "
                "comparison is an absolute ADDITION of false-positive mass, not a ratio."
            ),
            "one_mode_only": (
                "Prices same-model sampling noise. The recorded cross-model mode (3.36 vs 3.357 "
                "kg scoring regression @ 0.992) is strictly additional and unpriced."
            ),
            "channel_isolation": (
                "Judge off, no assertions: this is the argument channel alone, not mp check."
            ),
        },
        "cells": [],
    }

    for rep in reps_all:
        total = sum(rep["counts"])
        probs = [c / total for c in rep["counts"]]
        label = f"{rep['scenario']} [{rep['model']}]"
        if rep["distinct_payloads"] == 1:
            # ASCII only on stdout: MP-33 is an open row about a U+2014 in a provider error
            # garbling on a cp1252 console. A harness that crashes while printing its own
            # result is worse than one that prints a plain hyphen.
            print(f"{label}: one payload in {total} runs -- cannot be disjoint at any N, 0.00%\n")
            artifact["cells"].append(
                {k: v for k, v in rep.items() if k != "_observed"} | {"invariant": True, "by_n": []}
            )
            continue

        print(
            f"{label}  {rep['distinct_payloads']} payloads / {total} runs   "
            f"Good-Turing unseen mass {100 * rep['good_turing_unseen']:.1f}%"
        )
        by_n = []
        for mode in modes:
            print(f"   mode={mode}")
            print(f"      {'N':>3} {'split-half (headline)':>22} {'plug-in (contrast)':>20}")
            for n in ns:
                # [M] `hash(str)` is salted per PROCESS (PEP 456) unless PYTHONHASHSEED is
                # pinned, so the recorded `seed` did NOT make this reproducible: two identical
                # invocations disagreed on every cell. sha256 is stable across processes, so a
                # published figure from this script is now re-derivable from `seed` alone.
                mode_salt = int(hashlib.sha256(mode.encode()).hexdigest()[:6], 16) % 1000
                rng_s = random.Random(a.seed + n + mode_salt)
                rng_p = random.Random(a.seed + 7919 + n + mode_salt)
                sh = split_half(rep["_observed"], n, a.reps, rng_s, mode)
                pi = plug_in(probs, n, a.reps, rng_p, mode)
                by_n.append({"mode": mode, "n": n, "split_half": sh, "plug_in": pi})
                fmt = lambda r: (  # noqa: E731
                    "  2N>runs"
                    if r is None
                    else (
                        "n/a"
                        if r["fp_rate_of_scored"] is None
                        else f"{100 * r['fp_rate_of_scored']:.2f}% ({r['false_positives']}/{r['scored']})"
                    )
                )
                print(f"      {n:>3} {fmt(sh):>22} {fmt(pi):>20}")
        cell = {k: v for k, v in rep.items() if k != "_observed"}
        cell.update({"invariant": False, "by_n": by_n})
        if a.outer:
            peak = max(
                (c for c in by_n if c["split_half"] and c["split_half"]["fp_rate_of_scored"]),
                key=lambda c: c["split_half"]["fp_rate_of_scored"],
                default=None,
            )
            if peak:
                cell["outer_interval_at_peak"] = {
                    "n": peak["n"],
                    "mode": peak["mode"],
                    **(
                        outer_interval(
                            rep["counts"],
                            peak["n"],
                            a.outer_refits,
                            max(250, a.reps // 6),
                            a.seed,
                            peak["mode"],
                        )
                        or {}
                    ),
                }
                oi = cell["outer_interval_at_peak"]
                if "p2_5" in oi:
                    print(
                        f"   outer 95% (double bootstrap over the n={total} repertoire) at "
                        f"N={oi['n']} {oi['mode']}: "
                        f"[{100 * oi['p2_5']:.2f}%, {100 * oi['p97_5']:.2f}%]"
                    )
        artifact["cells"].append(cell)
        print()

    # Weighting is a choice; print every one of them rather than picking silently.
    varying = [c for c in artifact["cells"] if not c.get("invariant")]
    for mode in modes:
        for n in ns:
            rows = [
                x
                for c in varying
                for x in c["by_n"]
                if x["mode"] == mode and x["n"] == n and x["split_half"]
            ]
            if not rows:
                continue
            fp = sum(r["split_half"]["false_positives"] for r in rows)
            sc = sum(r["split_half"]["scored"] for r in rows)
            means = [
                r["split_half"]["fp_rate_of_scored"]
                for r in rows
                if r["split_half"]["fp_rate_of_scored"] is not None
            ]
            if not sc or not means:
                continue
            pooled = fp / sc
            all_seven = sum(means) / max(1, len(artifact["cells"]))
            if n == 6 and mode == modes[0]:
                print(
                    f"WEIGHTING SENSITIVITY (split-half, mode={mode}, N={n}): "
                    f"replicate-pooled {100 * pooled:.2f}%, scenario-mean over varying "
                    f"{100 * sum(means) / len(means):.2f}%, scenario-mean over all shapes "
                    f"{100 * all_seven:.2f}%"
                )
            artifact.setdefault("weightings", []).append(
                {
                    "mode": mode,
                    "n": n,
                    "replicate_pooled": pooled,
                    "scenario_mean_varying": sum(means) / len(means),
                    "scenario_mean_all_shapes": all_seven,
                }
            )

    if a.out:
        Path(a.out).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(f"\nwritten: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
