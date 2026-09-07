"""Pool several `fp_measurement.py --out` artifacts into the tables `docs/fp-measurement.md`
publishes - offline, no key, from the committed JSONL alone.

One artifact is one SURFACE: a scenario set, a candidate model, a judge, a run count and a
repeat count. This script never re-derives a verdict; it reads the `DiffResult` each trial
recorded and folds it through the SAME pure functions the harness prints with (`fp_report`,
`fp_summary`, `recall_report`, `recall_summary`), so a number here and a number in a
transcript can only disagree if the artifact changed.

Two false-positive rates are printed for every surface, and the difference is the point:

  conditional     false alarms / SCORED trials - trials in which some channel could have
                  fired (ADR-0022). This is the engine-centric number and the stricter one.
  unconditional   false alarms / every trial that reached a verdict (scored + could-not-fire).
                  This is what a user sees: of the same-model checks that were run, how many
                  raised an alarm. Abstentions (ADR-0018) are outside both denominators and
                  are printed beside them; a provider error is neither.

Both carry the one-sided 95% Clopper-Pearson upper bound from `upper_bound_95`.

    python scripts/fp_aggregate.py reports/fp-runs/2026-09-07/*.jsonl
    python scripts/fp_aggregate.py ... --json summary.json     # machine-readable, for tests
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import DiffResult  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    FP_OUTCOMES,
    fp_outcome,
    fp_report,
    load_artifact,
    recall_outcome,
    recall_report,
    recall_summary,
    upper_bound_95,
)


def _rows_for(rows: list[dict], arm: str):
    """`(sid, DiffResult | None, base_rep, cand_rep)` rows, the shape both arms consume."""
    out = []
    for rec in sorted(rows, key=lambda r: r["sid"]):
        if rec["arm"] != arm:
            continue
        res = None if rec.get("result") is None else DiffResult(**rec["result"])
        out.append((rec["sid"], res, rec.get("base_rep"), rec.get("cand_rep")))
    return out


def _temperatures(header: dict) -> str:
    """The declared temperature(s) of the scenarios a surface ran, read from disk."""
    try:
        from modelpin.scenarios import load_scenarios

        path = header.get("scenarios_path") or header.get("scenarios_dir")
        wanted = set(header.get("scenarios") or [])
        temps = sorted(
            {
                str(s.input.get("temperature", "default"))
                for s in load_scenarios(path)
                if s.id in wanted
            }
        )
        return "/".join(temps) if temps else "?"
    except Exception:  # noqa: BLE001 - a missing directory must not hide the numbers
        return "?"


def _rate(fp: int, n: int) -> str:
    if n == 0:
        return "n/a (0 trials)"
    return f"{fp}/{n} = {fp / n:.1%}, 95% ub {upper_bound_95(fp, n):.1%}"


def summarise(paths: list[str]) -> dict:
    """Everything the tables need, as data. Pure over the artifacts on disk."""
    surfaces = []
    pooled_fp_rows = []
    pooled_recall_rows = []
    per_scenario: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"attempted": 0, "scored": 0, "fp": 0, "no_effect": 0, "unmeasured": 0, "errors": 0}
    )
    flagged = []
    for path in paths:
        header, rows = load_artifact(path)
        if header is None:
            raise SystemExit(f"error: {path} has no header line; is it an fp_measurement artifact?")
        fp_rows = _rows_for(rows, "fp")
        rc_rows = _rows_for(rows, "recall")
        t, _ = fp_report(fp_rows)
        rt, _ = recall_report(rc_rows)
        name = os.path.basename(path)
        label = f"{header['scenarios_dir']}" + (
            f" ({header['role']})" if header.get("role") else ""
        )
        for sid, res, brep, crep in fp_rows:
            cell = per_scenario[(label, sid.split("#", 1)[0])]
            cell["attempted"] += 1
            if res is None:
                cell["errors"] += 1
                continue
            outcome = fp_outcome(res)
            scored, numerator, bucket, _ = FP_OUTCOMES[outcome]
            cell["scored"] += int(scored)
            cell["fp"] += int(numerator)
            if bucket:
                cell[bucket] += 1
            if numerator:
                flagged.append(
                    {
                        "artifact": name,
                        "surface": label,
                        "model": header["model"],
                        "sid": sid,
                        "verdict": res.verdict.value,
                        "confidence": res.confidence,
                        "explanation": res.explanation,
                        "base_rep": brep,
                        "cand_rep": crep,
                        "signals": res.signals.model_dump(mode="json"),
                    }
                )
        reached = t["scored"] + t["no_effect"]
        surfaces.append(
            {
                "artifact": name,
                "surface": label,
                "model": header["model"],
                "provider": header["provider"],
                "judge": header.get("judge"),
                "runs": header["runs"],
                "repeats": header["repeats"],
                "temperature": _temperatures(header),
                "git_sha": header.get("git_sha"),
                "started_utc": header.get("started_utc"),
                "attempted": len(fp_rows),
                "reached_verdict": reached,
                "scored": t["scored"],
                "false_positives": t["false_positives"],
                "no_effect": t["no_effect"],
                "unmeasured": t["unmeasured"],
                "errors": t["errors"],
                "conditional_ub": (
                    upper_bound_95(t["false_positives"], t["scored"]) if t["scored"] else None
                ),
                "unconditional_ub": (
                    upper_bound_95(t["false_positives"], reached) if reached else None
                ),
                "recall": rt,
                "recall_outcomes": [recall_outcome(r) for _, r, _, _ in rc_rows if r is not None],
                "recall_rows": [
                    {
                        "sid": sid,
                        "model": header["model"],
                        "outcome": "error" if r is None else recall_outcome(r),
                        "verdict": None if r is None else r.verdict.value,
                        "confidence": None if r is None else r.confidence,
                        "explanation": None if r is None else r.explanation,
                    }
                    for sid, r, _, _ in rc_rows
                ],
                "tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
                "tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
                "judge_calls": sum(r.get("judge_calls") or 0 for r in rows),
            }
        )
        pooled_fp_rows += [(f"{sid}@{header['model']}", r, b, c) for sid, r, b, c in fp_rows]
        pooled_recall_rows += [(f"{sid}@{header['model']}", r, b, c) for sid, r, b, c in rc_rows]
    pt, _ = fp_report(pooled_fp_rows)
    prt, pooled_recall_lines = recall_report(pooled_recall_rows)
    reached = pt["scored"] + pt["no_effect"]
    return {
        "surfaces": surfaces,
        "pooled": {
            "attempted": len(pooled_fp_rows),
            "reached_verdict": reached,
            "scored": pt["scored"],
            "false_positives": pt["false_positives"],
            "no_effect": pt["no_effect"],
            "unmeasured": pt["unmeasured"],
            "errors": pt["errors"],
            "conditional_ub": (
                upper_bound_95(pt["false_positives"], pt["scored"]) if pt["scored"] else None
            ),
            "unconditional_ub": upper_bound_95(pt["false_positives"], reached) if reached else None,
            "recall": prt,
            "recall_lines": pooled_recall_lines,
            "recall_summary": recall_summary(prt),
        },
        "per_scenario": [
            {"surface": k[0], "scenario": k[1], **v}
            for k, v in sorted(per_scenario.items(), key=lambda kv: (-kv[1]["scored"], kv[0]))
        ],
        "flagged": flagged,
    }


def render(summary: dict) -> list[str]:
    out = ["## Surfaces", ""]
    out.append(
        "| surface | candidate (judge) | temp | runs x repeats | attempted | reached verdict | "
        "SCORED | could not fire | abstained | errors | false alarms | conditional (of scored) | "
        "unconditional (of reached) |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = summary["surfaces"] + [
        {**summary["pooled"], "surface": "**POOLED**", "model": "", "judge": ""}
    ]
    for s in rows:
        cand = s.get("model", "")
        judge = s.get("judge")
        who = f"`{cand}` ({judge or 'no judge'})" if cand else ""
        rr = f"{s['runs']} x {s['repeats']}" if "runs" in s else ""
        out.append(
            f"| {s['surface']} | {who} | {s.get('temperature', '')} | {rr} | {s['attempted']} | "
            f"{s['reached_verdict']} | **{s['scored']}** | {s['no_effect']} | {s['unmeasured']} | "
            f"{s['errors']} | **{s['false_positives']}** | "
            f"{_rate(s['false_positives'], s['scored'])} | "
            f"{_rate(s['false_positives'], s['reached_verdict'])} |"
        )
    out += ["", "## Per scenario (FP arm)", ""]
    out.append(
        "| surface | scenario | attempted | scored | false alarms | could not fire | abstained | errors |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for c in summary["per_scenario"]:
        out.append(
            f"| {c['surface']} | `{c['scenario']}` | {c['attempted']} | **{c['scored']}** | "
            f"{c['fp']} | {c['no_effect']} | {c['unmeasured']} | {c['errors']} |"
        )
    out += ["", "## Flagged trials (every one, never excluded)", ""]
    if not summary["flagged"]:
        out.append("(none)")
    for f in summary["flagged"]:
        out.append(
            f"- `{f['sid']}` on `{f['model']}` ({f['surface']}, {f['artifact']}): **{f['verdict']}** "
            f"conf {f['confidence']:.2f} - {f['explanation']} - repertoire base {f['base_rep']} / "
            f"cand {f['cand_rep']}"
        )
    out += ["", "## Detection (pooled across surfaces)", ""]
    for s in summary["surfaces"]:
        for r in s["recall_rows"]:
            out.append(
                f"| `{r['sid']}` on `{r['model']}` | {r['verdict']} (conf {r['confidence']}) | "
                f"{'**detected**' if r['outcome'] == 'detected' else '**MISSED**' if r['outcome'] == 'missed' else r['outcome']} "
                f"- {r['explanation']} |"
            )
    out.append("")
    out.append("```")
    out += [ln for ln in summary["pooled"]["recall_summary"] if ln.strip()]
    out.append("```")
    out += ["", "## Cost", ""]
    tin = sum(s["tokens_in"] for s in summary["surfaces"])
    tout = sum(s["tokens_out"] for s in summary["surfaces"])
    jc = sum(s["judge_calls"] for s in summary["surfaces"])
    out.append(
        f"Replay tokens in/out {tin:,}/{tout:,} across {len(summary['surfaces'])} artifact(s); "
        f"{jc} judge calls implied by the traces (judge tokens not metered)."
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifacts", nargs="+")
    ap.add_argument("--json", default=None, help="also write the summary as JSON here")
    args = ap.parse_args()
    summary = summarise(args.artifacts)
    for line in render(summary):
        print(line)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1, ensure_ascii=False, default=str)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
