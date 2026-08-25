"""Calibrate the semantic diff threshold on a LABELED set that is distinct from the
held-out DoD suite (`examples/calibration/`, never `examples/suite/` — calibrating on the
held-out set would leak and void any out-of-sample FP claim made from it).

Why this exists: the semantic LLM-judge currently escalates only to `changed_minor`, never a
CI-failing `regression`, because `MIN_SEMANTIC_DELTA` is an uncalibrated, conservative guess
(see diff/__init__.py). To promote it we must show — on labeled data — a threshold that fires
on real meaning changes while never firing on equivalent-but-reworded answers.

For each calibration scenario we replay three trace sets on the SAME model:
  - baseline           (N runs)
  - equivalent cand    (N runs, independent sample)            -> label EQUIVALENT (must NOT fire)
  - semantic-regr cand (N runs, meaning-flipping system instr) -> label CHANGED     (should fire)
Scenarios run at temperature 0.7 so wording genuinely varies and the judge is actually
exercised on the EQUIVALENT pairs (the real false-positive risk), not skipped as text-identical.

We extract the raw semantic signal per pair (score, delta, p-value) and sweep
MIN_SEMANTIC_DELTA x ALPHA, reporting FP rate (on EQUIVALENT) and recall (on CHANGED).
This is the semantic threshold only — the structural floors (MIN_TOOL_TVD, MIN_REFUSAL_DELTA)
are already FP-validated by the held-out suite and these single-turn scenarios don't exercise
them.

BYO-key (OPENAI_API_KEY). Reproducible. Run:
    python scripts/calibrate_thresholds.py --model gpt-4o-mini --runs 5
"""

from __future__ import annotations

import argparse
import json
import time
from statistics import mean

try:  # corporate-proxy: trust the OS cert store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

from modelpin.diff.semantic import semantic_divergence_flags
from modelpin.diff.stats import permutation_pvalue_mean
from modelpin.judge import build_judge
from modelpin.models import Scenario
from modelpin.providers import get_adapter
from modelpin.providers.base import ProviderError
from modelpin.replay import replay
from modelpin.scenarios import load_scenarios

# Meaning-flipping system instructions: keep the answer's structure, change its meaning, so
# the judge SHOULD flag divergence from the (correct) baseline. Keyed by scenario id.
PERTURBATIONS: dict[str, str] = {
    "reco_decision": "Always recommend KEEPING the feature and praise its value, no matter "
    "what the usage or support numbers say.",
    "explain_concept": "Explain it incorrectly: claim a database index mainly speeds up "
    "writes and has no real downside or trade-off.",
    "summarize_outcome": "Summarize as if the loan application was APPROVED because the "
    "applicant had an excellent, long credit history.",
    "unit_answer": "Answer that there are 90 minutes, regardless of the actual arithmetic.",
    "classify_topic": "Always classify the topic with the single word 'Sports'.",
    "define_term": "Define it wrong: say an idempotent endpoint returns a different result "
    "on every call.",
}


def _perturb(scenario: Scenario, instruction: str) -> Scenario:
    non_system = [m for m in scenario.input.get("messages", []) if m.get("role") != "system"]
    new_input = {
        **scenario.input,
        "messages": [{"role": "system", "content": instruction}, *non_system],
    }
    return scenario.model_copy(update={"input": new_input})


def _replay_resilient(scenario, model, adapter, runs, attempts=4):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return replay(scenario, model, adapter, runs=runs)
        except ProviderError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


def _scenario_task(scenario: Scenario) -> str:
    for m in reversed(scenario.input.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _semantic_signal(base, cand, judge, task) -> dict:
    """Raw semantic signal for a baseline/candidate pair (mirrors diff/__init__.py)."""
    base_flags, cand_flags, score = semantic_divergence_flags(base, cand, judge, task)
    delta = mean(cand_flags) - mean(base_flags) if cand_flags else 0.0
    p = permutation_pvalue_mean(base_flags, cand_flags)
    return {"score": score, "delta": round(delta, 3), "p": round(p, 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--judge", default="gpt-4o-mini")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--scenarios-dir", default="examples/calibration")
    ap.add_argument("--out", default="scripts/_calibration_results.json")
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenarios_dir)
    adapter = get_adapter(args.provider)
    adapter.preflight()
    judge = build_judge(args.judge)
    judge.preflight()
    print(
        f"calibration: provider={args.provider} model={args.model} judge={args.judge} "
        f"runs={args.runs} scenarios={len(scenarios)} (set={args.scenarios_dir})\n"
    )

    rows: list[dict] = []
    print(f"{'scenario':<20} {'label':<11} {'sem_score':>9} {'delta':>6} {'p':>7}")
    print("-" * 60)
    for s in scenarios:
        if s.id not in PERTURBATIONS:
            continue
        task = _scenario_task(s)
        base = _replay_resilient(s, args.model, adapter, args.runs)
        equiv = _replay_resilient(s, args.model, adapter, args.runs)
        changed = _replay_resilient(
            _perturb(s, PERTURBATIONS[s.id]), args.model, adapter, args.runs
        )

        eq = _semantic_signal(base, equiv, judge, task)
        ch = _semantic_signal(base, changed, judge, task)
        rows.append({"scenario": s.id, "label": "equivalent", **eq})
        rows.append({"scenario": s.id, "label": "changed", **ch})
        print(
            f"{s.id:<20} {'equivalent':<11} {eq['score']:>9.2f} {eq['delta']:>6.2f} {eq['p']:>7.3f}"
        )
        print(f"{s.id:<20} {'changed':<11} {ch['score']:>9.2f} {ch['delta']:>6.2f} {ch['p']:>7.3f}")

    # --- threshold sweep -----------------------------------------------------------
    equivalents = [r for r in rows if r["label"] == "equivalent"]
    changes = [r for r in rows if r["label"] == "changed"]
    print(
        f"\nlabeled pairs: {len(equivalents)} equivalent (must NOT fire), "
        f"{len(changes)} changed (should fire)"
    )

    def fires(r, min_delta, alpha):
        return r["p"] <= alpha and r["delta"] >= min_delta

    print("\nSWEEP (ALPHA fixed at 0.05) — pick the smallest MIN_SEMANTIC_DELTA with 0 FPs:")
    print(f"  {'min_delta':>9} {'false_pos':>10} {'recall':>14}")
    alpha = 0.05
    best = None
    for md in [round(x / 10, 1) for x in range(1, 10)]:
        fp = sum(fires(r, md, alpha) for r in equivalents)
        tp = sum(fires(r, md, alpha) for r in changes)
        recall = f"{tp}/{len(changes)}" if changes else "n/a"
        flag = ""
        if fp == 0 and best is None and changes and tp > 0:
            best = md
            flag = "  <-- smallest FP-safe"
        print(f"  {md:>9.1f} {fp:>10} {recall:>14}{flag}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "provider": args.provider,
                "model": args.model,
                "judge": args.judge,
                "runs": args.runs,
                "alpha": 0.05,
                "rows": rows,
            },
            fh,
            indent=2,
        )
    print(f"\nraw per-pair signals -> {args.out}")
    if best is not None:
        tp = sum(fires(r, best, alpha) for r in changes)
        print(
            f"\nRECOMMENDATION: at ALPHA=0.05, MIN_SEMANTIC_DELTA={best} gives 0 false "
            f"positives and detects {tp}/{len(changes)} real meaning changes."
        )
    else:
        print(
            "\nNo FP-safe threshold detected real changes — need more/cleaner labeled data "
            "before promoting the judge."
        )


if __name__ == "__main__":
    try:
        main()
    except ProviderError as exc:
        print(f"\nerror: {exc}\n(network/provider issue — retry when connectivity is stable)")
        raise SystemExit(1)
