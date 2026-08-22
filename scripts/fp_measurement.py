"""Phase-0 DoD harness: measure Modelpin's false-positive rate on a held-out scenario
set, and confirm it still catches genuine regressions.

- False-positive rate: replay a KNOWN-EQUIVALENT pair (the same model vs itself, two
  independent N-run samples) across every suite scenario. Any verdict other than
  `unchanged` is, by definition, a false alarm from model nondeterminism. This is the
  north-star metric ("if Modelpin says it broke, it broke").
- Detection: inject a controlled behavior change into a few scenarios and confirm the
  engine flags it — so a low FP rate isn't just "always unchanged".

BYO-key: reads OPENAI_API_KEY from the environment. Real (cheap) API calls. Run:
    python scripts/fp_measurement.py --model gpt-4o-mini --runs 5
"""

from __future__ import annotations

import argparse
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


# Controlled behavior regressions injected into the candidate (system instruction) to
# prove detection across signals: tool/refusal, format/PII, and classification/meaning.
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
    """ "fp" | "clean" | "unmeasured" - the only classifier either arm may use."""
    if verdict in _FLAGGED:
        return "fp"
    if verdict == DiffVerdict.insufficient_evidence:
        return "unmeasured"
    return "clean"


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
        """Replay base + candidate and diff; returns the DiffResult or None on error."""
        try:
            base = _replay_resilient(base_scn, args.model, adapter, args.runs)
            cand = _replay_resilient(cand_scn, args.model, adapter, args.runs)
            return diff_scenario(sid, args.model, args.model, base, cand, base_scn, judge=judge)
        except ProviderError as exc:
            print(f"  {sid:<22} ERROR  ({str(exc)[:70]})")
            return None

    # --- false-positive rate: same model vs itself ---------------------------------
    false_positives = scored = 0
    print("EQUIVALENT PAIRS (same model vs itself) -- any non-`unchanged` is a false alarm")
    unmeasured = 0
    for s in scenarios:
        r = _verdict(s, s, s.id)
        if r is None:
            continue
        kind = classify(r.verdict)
        if kind == "unmeasured":
            unmeasured += 1
            print(f"  {s.id:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  <-- UNMEASURED")
            continue
        scored += 1
        false_positives += int(kind == "fp")
        flag = "  <-- FALSE POSITIVE" if kind == "fp" else ""
        print(f"  {s.id:<22} {r.verdict.value:<14} conf={r.confidence:.2f}{flag}")
    rate = f"{false_positives/scored:.0%}" if scored else "n/a"
    print(f"\n  False-positive rate: {false_positives}/{scored} = {rate}")
    # Coverage is published ALONGSIDE the rate, never folded into it: a rate computed over a
    # silently shrinking denominator is how a metric flatters itself.
    print(f"  Unmeasured (excluded from the rate): {unmeasured}\n")

    # --- detection: injected regressions -------------------------------------------
    detected = checked = unmeasured_rec = 0
    perturbed = [s for s in scenarios if s.id in PERTURBATIONS]
    print("INJECTED REGRESSIONS (perturbed candidate) -- expect regression/changed_minor")
    for s in perturbed:
        r = _verdict(s, _perturb(s, PERTURBATIONS[s.id]), s.id)
        if r is None:
            continue
        kind = classify(r.verdict)
        if kind == "unmeasured":
            unmeasured_rec += 1
            print(f"  {s.id:<22} {r.verdict.value:<14} UNMEASURED (excluded)")
            continue
        checked += 1
        caught = kind == "fp"
        detected += int(caught)
        print(
            f"  {s.id:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  "
            f"{'detected' if caught else 'MISSED'}  ({r.explanation[:55]})"
        )
    print(f"\n  Detection: {detected}/{checked} injected regressions caught")
    print(f"  Unmeasured (excluded): {unmeasured_rec}")


if __name__ == "__main__":
    try:
        main()
    except ProviderError as exc:
        print(f"\nerror: {exc}\n(network/provider issue — retry when connectivity is stable)")
        raise SystemExit(1)
