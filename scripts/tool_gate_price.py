"""Price the tool-trajectory gate's candidate rules on a LABELLED set — offline, no key.

MP-224. `[M] 2026-09-08` The tool gate cannot be changed at all until a labelled set exists,
and this is the scorer that set is for. Two blocks stand in the way of choosing a rule any
other way, and both are measurements rather than opinions:

  * **ADR-0025 / ADR-0038.** `examples/fp-suite-v2/` is role `score` forever. Choosing a rule
    because it takes that corpus's alarm count from 1 to 0 is fitting on a scored set, which
    is ADR-0025's exact prohibition with one word changed from "threshold" to "structural
    rule".
  * **`[M]` ADR-0002's own revisit bar is ~26 pairs short.** What exists is **4 distinct**
    tool-channel detection scenarios replayed 20 times, so `n_eff = 4` and the one-sided 95%
    upper bound on a detection-loss rate is **52.7%**, not the 25.9% that "0 in 10" suggests.
    This script therefore reports `n_eff` beside every rate and refuses to print a bound over
    replicates alone.

WHAT IT PRICES

Three rules, as POST-HOC preconditions on the tool channel's hard alarm. Nothing here edits
`modelpin/diff/` — the engine is frozen (ADR-0030 D1), and a rule is scored by suppressing the
alarm the real engine already produced, never by running a second engine:

  status_quo   the shipped gate: `tool_p <= ALPHA and tool_tvd >= MIN_TOOL_TVD`.
  novelty      additionally require `set(cand_keys) - set(base_keys) != {}` — the candidate
               must do something the baseline never did. `[M]` The surviving candidate from
               the 2026-09-08 FP review: it keeps every detection that review could see and
               leaves 7 of 39 FP trials reachable. It must be CHOSEN here, not there.
  disjoint     additionally require the two key sets to be disjoint. `[M]` REJECTED on the old
               corpus for two reasons this script re-tests on a real one: 0 of 37 tool-exposed
               FP trials had disjoint key sets, so the gate became structurally incapable of a
               hard alarm on 100% of the trials where it had ever met a null (ADR-0038 D3's
               bound becomes 0/0); and its detection probability is `q**N`, strictly
               DECREASING in N, which inverts ADR-0016's "N is a correctness input, not a cost
               dial".

HOW A RULE IS APPLIED, EXACTLY

A precondition can only ever SUPPRESS the tool channel's alarm; it can never create one. So
for each trial: read which channels the engine itself named (`channels_that_fired`, off the
published explanation). If `tool` is not among them the rule changes nothing. If it is, test
the precondition on the stored traces; when it fails, drop `tool` and recompute the verdict
from the channels that remain — any hard channel left is still a `regression`, any advisory
one is still a `changed_minor`, none left is `unchanged`. That needs no p-values and no second
copy of the verdict block.

`[M] 2026-09-08` This is why the set has to keep the SEMANTIC channel quiet: on the corpus that
exists, all 10 scored tool detections also name `semantic drift`, and `semantic_diverged`
appends to `hard_pvalues` independently of the match mode — so suppressing the tool channel
changes nothing about their verdict and they cannot price a tool rule at all. A rule that looks
free on that corpus is not being measured; it is being missed. This script reports
`masked_by_another_channel` per rule so that failure is visible instead of flattering.

    python scripts/tool_gate_price.py --check examples/calibration/tool      # no artifact yet
    python scripts/tool_gate_price.py reports/tool-calibration/<date>/*.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.diff.structural import canonical_sequence, tool_call_sequence  # noqa: E402
from modelpin.models import DiffResult, Trace  # noqa: E402
from modelpin.scenarios import load_scenarios  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    ADVISORY_CHANNELS,
    HARD_CHANNELS,
    channels_that_fired,
    load_artifact,
    upper_bound_95,
)

#: The label vocabulary. `equivalent` is a same-model null on which the gate must NOT fire;
#: `changed` is a real migration shape on which it SHOULD. Anything else in `labels.json` is
#: refused rather than silently bucketed - an unlabelled pair in a labelled set is the whole
#: defect this row exists to fix.
LABELS = ("equivalent", "changed")


def _keys(traces: list[Trace], mode: str) -> list[tuple]:
    return [canonical_sequence(tool_call_sequence(t), mode) for t in traces]


def novelty_holds(base: list[Trace], cand: list[Trace], mode: str) -> bool:
    """Did the candidate do something the baseline never did?"""
    return bool(set(_keys(cand, mode)) - set(_keys(base, mode)))


def disjoint_holds(base: list[Trace], cand: list[Trace], mode: str) -> bool:
    """Do the two sides share no trajectory at all?"""
    return not (set(_keys(base, mode)) & set(_keys(cand, mode)))


RULES = {
    "status_quo": lambda base, cand, mode: True,
    "novelty": novelty_holds,
    "disjoint": disjoint_holds,
}


def verdict_under(result: DiffResult, base: list[Trace], cand: list[Trace], mode: str, rule: str):
    """`(verdict, tool_alarm_survived, masked)` for one trial under one rule.

    `masked` is True when the tool channel's alarm was suppressed and the verdict did NOT move
    because another channel was carrying it. That is the condition under which this corpus
    cannot price the rule, and it is reported rather than absorbed.
    """
    fired = set(channels_that_fired(result))
    tool_fired = bool(fired & {"tool", "tool_relation"})
    if not tool_fired:
        return result.verdict.value, False, False
    if RULES[rule](base, cand, mode):
        return result.verdict.value, True, False
    remaining = fired - {"tool", "tool_relation"}
    if remaining & set(HARD_CHANNELS):
        return "regression", False, True
    if remaining & set(ADVISORY_CHANNELS):
        return "changed_minor", False, result.verdict.value == "changed_minor"
    return "unchanged", False, False


def _load_labels(scenarios_dir: Path) -> dict:
    path = scenarios_dir / "labels.json"
    if not path.is_file():
        raise SystemExit(
            f"error: {path} is missing. A labelled set without labels is the corpus MP-224 "
            "exists to replace; this script will not guess a ground truth."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    bad = {k: v.get("label") for k, v in data.items() if v.get("label") not in LABELS}
    if bad:
        raise SystemExit(f"error: labels outside {LABELS}: {bad}")
    return data


def check_set(scenarios_dir: str) -> int:
    """Validate the labelled set OFFLINE, before anything is replayed or spent.

    Deliberately available with no artifact: every check here is one that would otherwise be
    discovered after the money was gone.
    """
    d = Path(scenarios_dir)
    labels = _load_labels(d)
    scenarios = load_scenarios(d)
    ids = {s.id for s in scenarios}
    problems = []
    if missing := ids - set(labels):
        problems.append(f"{len(missing)} scenario(s) carry no label: {sorted(missing)}")
    if extra := set(labels) - ids:
        problems.append(f"{len(extra)} label(s) name no scenario: {sorted(extra)}")
    for s in scenarios:
        if s.assertions:
            problems.append(f"{s.id}: declares assertions; this set is one seam only (the tool")
        if not s.input.get("tools"):
            problems.append(f"{s.id}: declares no tools, so it cannot exercise the tool channel")
        for tool in s.input.get("tools") or []:
            fn = (tool or {}).get("function") or {}
            if not fn.get("parameters", {}).get("properties"):
                problems.append(
                    f"{s.id}: tool {fn.get('name')!r} has no parameters block. `[M]` A bare or "
                    "empty tool normalises to an empty schema, which is why three scenarios in "
                    "examples/suite/ cannot exercise this channel at all."
                )
    for sid, meta in labels.items():
        if meta["label"] == "changed" and not meta.get("perturbation"):
            problems.append(f"{sid}: labelled `changed` with no perturbation to apply")
        if meta["label"] == "equivalent" and meta.get("perturbation"):
            problems.append(f"{sid}: labelled `equivalent` but carries a perturbation")
        if not meta.get("why"):
            problems.append(f"{sid}: no `why`; a label without a reason is not a label")

    counts = {lab: sum(1 for m in labels.values() if m["label"] == lab) for lab in LABELS}
    print(f"{scenarios_dir}: {len(scenarios)} scenarios, labels {counts}")
    if sum(counts.values()) < 30:
        problems.append(
            f"ADR-0002's bar is >= 30 labelled pairs; this set has {sum(counts.values())}"
        )
    for p in problems:
        print(f"  PROBLEM  {p}")
    print("  OK" if not problems else f"  {len(problems)} problem(s)")
    return 1 if problems else 0


def summarise(paths: list[str]) -> dict:
    labels: dict = {}
    per_rule: dict[str, dict] = {
        r: {
            "fp": 0,
            "fp_exposed": 0,
            "detected": 0,
            "changed_pairs": 0,
            "masked": 0,
            "fp_scenarios": set(),
            "detect_scenarios": set(),
        }
        for r in RULES
    }
    trials = 0
    for path in paths:
        header, rows = load_artifact(path)
        if header is None:
            raise SystemExit(f"error: {path} has no header line")
        d = Path(header.get("scenarios_path") or header.get("scenarios_dir"))
        labels.update(_load_labels(d))
        mode = header.get("match") or "strict"
        for rec in rows:
            if rec.get("result") is None or rec.get("base_traces") is None:
                continue
            meta = labels.get(rec["scenario_id"])
            if meta is None:
                raise SystemExit(f"error: {rec['scenario_id']} is in the artifact but not labelled")
            arm_label = "changed" if rec["arm"] == "recall" else "equivalent"
            if arm_label != meta["label"] and rec["arm"] == "recall":
                continue
            result = DiffResult(**rec["result"])
            base = [Trace(**t) for t in rec["base_traces"]]
            cand = [Trace(**t) for t in rec["cand_traces"]]
            trials += 1
            for rule in RULES:
                v, survived, masked = verdict_under(result, base, cand, mode, rule)
                cell = per_rule[rule]
                if arm_label == "equivalent":
                    # Exposure: the tool channel COULD have raised a hard alarm under this rule.
                    if RULES[rule](base, cand, mode):
                        cell["fp_exposed"] += 1
                    if v == "regression":
                        cell["fp"] += 1
                        cell["fp_scenarios"].add(rec["scenario_id"])
                else:
                    cell["changed_pairs"] += 1
                    if v in ("regression", "changed_minor"):
                        cell["detected"] += 1
                        cell["detect_scenarios"].add(rec["scenario_id"])
                    cell["masked"] += int(masked)
    return {"trials": trials, "rules": per_rule, "labels": labels}


def render(summary: dict) -> list[str]:
    out = [
        "### Tool-gate rules, priced on the labelled set (MP-224)",
        "",
        "`n_eff` is DISTINCT scenarios, not trials. `[M]` The corpus this set replaces read "
        "`0 in 10` while being 4 distinct shapes replayed 20 times, so its true bound on a "
        "detection-loss rate was 52.7%, not 25.9%. Every bound below is over `n_eff`.",
        "",
        "| rule | FP (hard alarms on the null) | detections kept | n_eff FP / detect | "
        "masked by another channel |",
        "|---|---|---|---|---|",
    ]
    for rule, c in summary["rules"].items():
        n_fp, k_fp = c["fp_exposed"], c["fp"]
        fp_cell = (
            f"{k_fp}/{n_fp} = {k_fp / n_fp:.1%}, ub {upper_bound_95(k_fp, n_fp):.1%}"
            if n_fp
            else "**0/0 - NOT A LOW RATE, NO MEASUREMENT**"
        )
        det = f"{c['detected']}/{c['changed_pairs']}" if c["changed_pairs"] else "n/a"
        out.append(
            f"| `{rule}` | {fp_cell} | {det} | "
            f"{len(c['fp_scenarios'])} / {len(c['detect_scenarios'])} | {c['masked']} |"
        )
    out += [
        "",
        "**Read `masked by another channel` before reading anything else.** A trial whose tool "
        "alarm this rule suppressed while the verdict stayed put was carried by a different "
        "channel, so it prices nothing. A rule that looks free because every detection was "
        "masked has not been measured.",
        "",
        "**A `0/0` FP cell is a refusal, not a result** (ADR-0038 D3): it means the rule made "
        "the gate structurally incapable of a hard alarm on every trial where it met the null.",
    ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifacts", nargs="*")
    ap.add_argument("--check", default=None, help="validate a labelled set offline and exit")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    if args.check:
        raise SystemExit(check_set(args.check))
    if not args.artifacts:
        ap.error("pass artifacts, or --check <dir> to validate the set before spending")
    summary = summarise(args.artifacts)
    for line in render(summary):
        print(line)
    if args.json:
        payload = {
            "trials": summary["trials"],
            "rules": {
                r: {k: (sorted(v) if isinstance(v, set) else v) for k, v in c.items()}
                for r, c in summary["rules"].items()
            },
        }
        Path(args.json).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
