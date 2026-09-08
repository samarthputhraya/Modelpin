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

from modelpin.models import DiffResult, Trace  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    ADVISORY_CHANNELS,
    FP_OUTCOMES,
    HARD_CHANNELS,
    fp_outcome,
    fp_report,
    load_artifact,
    recall_outcome,
    recall_report,
    recall_summary,
    semantic_pvalue,
    severity_exposure,
    severity_fired,
    structural_channel_pvalues,
    upper_bound_95,
    verify_against_published,
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


def _empty_severity() -> dict[str, int]:
    return {
        "hard_scored": 0,
        "hard_fp": 0,
        "hard_undetermined": 0,
        "advisory_scored": 0,
        "advisory_fp": 0,
        "advisory_undetermined": 0,
    }


def _severity_tally(header: dict, rows: list[dict]) -> dict[str, int]:
    """MP-223. The false-positive rate SPLIT BY SEVERITY, each with its own denominator.

    One pooled rate answered a question nobody asks. `[M] 2026-09-08` on the run of record,
    **30 of the 39 scored trials could only ever have fired on the advisory argument gate**,
    which by ADR-0029 escalates to `changed_minor` and can never fail a build alone
    (`cli.py` gates exit 1 on `regression`). A bound 77% carried by a signal that cannot
    produce a red build does not describe the product's promise -- "if Modelpin says it
    broke, it broke" is a claim about the BUILD-FAILING channels.

    **The classifier is not touched.** `_FLAGGED` still counts `changed_minor` against the
    north-star metric, and it must: if an advisory verdict were reclassified `clean`, any
    future channel could escape the metric permanently by shipping as "advisory", and
    ADR-0029's argument gate is already that shape. The defect was the REPORTING, and this is
    the reporting.

    Denominators follow ADR-0038 D3's per-channel shape: a trial enters a severity's
    denominator when a channel of that severity COULD have fired on it (`p < 1.0`), and its
    numerator when a channel of that severity actually did. The two overlap by design -- a
    trial where both severities were live is counted in both -- so the two denominators do
    not sum to `scored` and are never presented as if they did.
    """
    from modelpin.scenarios import load_scenarios

    scen_dir = header.get("scenarios_path") or header.get("scenarios_dir")
    try:
        scenarios = {s.id: s for s in load_scenarios(scen_dir)} if scen_dir else {}
    except Exception:  # noqa: BLE001 - a missing suite must not silently mis-attribute
        scenarios = {}
    mode = header.get("match") or "strict"

    tally = _empty_severity()
    for rec in rows:
        if rec.get("arm") != "fp" or rec.get("result") is None:
            continue
        result = DiffResult(**rec["result"])
        if fp_outcome(result) in {"unmeasured", "no-effect"}:
            continue  # ADR-0022/ADR-0018 exclusions apply unchanged, before any split
        base = rec.get("base_traces")
        cand = rec.get("cand_traces")
        if base is None or cand is None:
            # No traces means no recomputation is possible. Counted as undetermined on BOTH
            # severities rather than guessed: a guess here re-attributes a published bound.
            tally["hard_undetermined"] += 1
            tally["advisory_undetermined"] += 1
            continue
        base_t = [Trace(**x) for x in base]
        cand_t = [Trace(**x) for x in cand]
        channel_p = structural_channel_pvalues(
            base_t, cand_t, scenarios.get(rec["scenario_id"]), mode
        )
        channel_p["semantic"] = semantic_pvalue(result, channel_p)
        verify_against_published(result, channel_p)
        exposure = severity_exposure(result, channel_p)
        fired = severity_fired(result)
        for severity in ("hard", "advisory"):
            if exposure[severity] is None:
                tally[f"{severity}_undetermined"] += 1
            elif exposure[severity]:
                tally[f"{severity}_scored"] += 1
                tally[f"{severity}_fp"] += int(fired[severity])
    return tally


def _sample_identity(header: dict, path: str) -> str:
    """The SAMPLE an artifact reports on, which is not always the artifact itself.

    A re-scored artifact (`--rejudge`) holds its SOURCE's trials: the same stored replays,
    scored again by a second judge or under a changed engine. It is a second READING of one
    sample, never a second sample (ADR-0037), so it answers to its source's identity.
    """
    return header.get("rejudged_from") or os.path.basename(path)


def _refuse_double_counting(loaded: list[tuple[str, dict, list]]) -> None:
    """Refuse any SET that reads one sample twice. MP-208, generalised by MP-216.

    The rule this enforces is about the SET, not about any single artifact. Two readings of
    one sample pooled together count every trial twice and inflate exactly the denominator
    the north-star bound rests on, so both of these must fail loudly:

      * a source artifact and a re-score OF it (the original MP-208 shape); and
      * two different re-scores of the SAME source - a second judge and a changed engine,
        say - which the old flag-based check happened to catch only as collateral.

    What it must NOT refuse, and what a blanket `replay_reused` check did refuse, is a set of
    re-scores of DISTINCT sources. `[M] 2026-09-07` those are five separate surfaces and 710
    distinct trials; refusing them made MP-206's re-scored run of record unaggregatable, so
    the block `docs/fp-measurement.md` publishes could not be regenerated by any command and
    the page kept a bound the engine no longer produced.
    """
    by_sample: dict[str, list[str]] = defaultdict(list)
    for path, header, _ in loaded:
        by_sample[_sample_identity(header, path)].append(os.path.basename(path))
    for sample, artifacts in sorted(by_sample.items()):
        if len(artifacts) > 1:
            names = sorted(artifacts)
            listed = " and ".join(names) if len(names) == 2 else ", ".join(names)
            raise SystemExit(
                f"error: {listed} {'both' if len(names) == 2 else 'all'} report on the SAME "
                f"sample ({sample}) — they are one set of trials read more than once, not "
                "separate samples, and pooling them would double-count every trial. Aggregate "
                "each reading separately, and compare two judges with "
                "scripts/judge_agreement.py."
            )


def _provenance(loaded: list[tuple[str, dict, list]]) -> list[dict]:
    """Every artifact in the set that is a re-score, and what it is a re-score OF.

    Carried into `render` so a regenerated table can never be read as fresh evidence. A
    re-score REPLACES its source's numbers; it never adds to them.
    """
    out = []
    for path, header, _ in loaded:
        if not header.get("replay_reused"):
            continue
        judge, was = header.get("judge"), header.get("rejudged_from_judge")
        out.append(
            {
                "artifact": os.path.basename(path),
                "source": header.get("rejudged_from"),
                "kind": "a second judge" if judge != was else "a changed engine",
                "judge": judge,
                "source_judge": was,
                "git_sha": header.get("git_sha"),
                "source_git_sha": header.get("rejudged_from_git_sha"),
            }
        )
    return out


def summarise(paths: list[str]) -> dict:
    """Everything the tables need, as data. Pure over the artifacts on disk."""
    surfaces = []
    pooled_fp_rows = []
    pooled_recall_rows = []
    per_scenario: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"attempted": 0, "scored": 0, "fp": 0, "no_effect": 0, "unmeasured": 0, "errors": 0}
    )
    flagged = []
    pooled_severity = _empty_severity()
    loaded: list[tuple[str, dict, list]] = []
    for path in paths:
        header, rows = load_artifact(path)
        if header is None:
            raise SystemExit(f"error: {path} has no header line; is it an fp_measurement artifact?")
        loaded.append((path, header, rows))
    _refuse_double_counting(loaded)
    provenance = _provenance(loaded)
    for path, header, rows in loaded:
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
        sev = _severity_tally(header, rows)
        for k, v in sev.items():
            pooled_severity[k] += v
        surfaces.append(
            {
                "severity": sev,
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
            "severity": pooled_severity,
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
        "provenance": provenance,
    }


def _render_severity(summary: dict) -> list[str]:
    """The severity split, FIRST, above the pooled table (MP-223).

    Placed above `### Surfaces` deliberately. The pooled rate is the number a skimmer quotes,
    and `[M]` on the run of record it is 77% carried by a gate that cannot fail a build -- so
    the split has to be read BEFORE the number it qualifies, not in a footnote under it.
    """
    out = ["### False-positive rate by severity (MP-223)", ""]
    out += [
        "A `changed_minor` counts against the north-star metric exactly like a `regression`",
        "and must keep doing so -- otherwise a channel could escape the metric permanently by",
        "shipping as advisory. But the two have incompatible consequences: only `regression`",
        "exits 1 and fails a build. Each severity therefore gets its OWN denominator -- the",
        "trials in which a channel of that severity could have fired at all (ADR-0022's",
        "predicate, applied one severity at a time; ADR-0038 D3's shape).",
        "",
        f"Hard (CI-failing) channels: {', '.join(f'`{c}`' for c in HARD_CHANNELS)}. "
        f"Advisory: {', '.join(f'`{c}`' for c in ADVISORY_CHANNELS)}.",
        "**The two denominators overlap and do not sum to SCORED** -- a trial on which both",
        "severities were live is in both.",
        "",
        "| surface | HARD (fails your build) | advisory (annotates only) | undetermined |",
        "|---|---|---|---|",
    ]
    rows = summary["surfaces"] + [{**summary["pooled"], "surface": "**POOLED**"}]
    for s in rows:
        sev = s.get("severity") or _empty_severity()
        und = sev["hard_undetermined"] + sev["advisory_undetermined"]
        out.append(
            f"| {s['surface']} | **{_rate(sev['hard_fp'], sev['hard_scored'])}** | "
            f"{_rate(sev['advisory_fp'], sev['advisory_scored'])} | {und} |"
        )
    out.append("")
    return out


def render(summary: dict) -> list[str]:
    out: list[str] = []
    if summary.get("provenance"):
        # Printed FIRST and unconditionally, because the tables below are indistinguishable
        # from a fresh run's and a reader who mistakes one for the other has silently doubled
        # the evidence behind the bound. ADR-0037.
        out += [
            "> **These surfaces are RE-SCORES of stored replays, not new samples.** Each one",
            "> REPLACES its source's numbers; it never adds to them.",
            "",
        ]
        for p in summary["provenance"]:
            out.append(
                f"> - `{p['artifact']}` re-scores `{p['source']}` under {p['kind']} "
                f"(judge `{p['source_judge']}` -> `{p['judge']}`, "
                f"engine `{p['source_git_sha']}` -> `{p['git_sha']}`)"
            )
        out.append("")
    out += _render_severity(summary)
    out += ["### Surfaces", ""]
    out.append(
        "| surface | artifact | candidate (judge) | temp | runs x repeats | attempted | "
        "reached verdict | SCORED | could not fire | abstained | errors | false alarms | "
        "conditional (of scored) | unconditional (of reached) |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = summary["surfaces"] + [
        {**summary["pooled"], "surface": "**POOLED**", "model": "", "judge": ""}
    ]
    for s in rows:
        cand = s.get("model", "")
        judge = s.get("judge")
        who = f"`{cand}` ({judge or 'no judge'})" if cand else ""
        rr = f"{s['runs']} x {s['repeats']}" if "runs" in s else ""
        out.append(
            f"| {s['surface']} | {s.get('artifact', '')} | {who} | {s.get('temperature', '')} | "
            f"{rr} | {s['attempted']} | "
            f"{s['reached_verdict']} | **{s['scored']}** | {s['no_effect']} | {s['unmeasured']} | "
            f"{s['errors']} | **{s['false_positives']}** | "
            f"{_rate(s['false_positives'], s['scored'])} | "
            f"{_rate(s['false_positives'], s['reached_verdict'])} |"
        )
    out += ["", "### Per scenario (FP arm)", ""]
    out.append(
        "| surface | scenario | attempted | scored | false alarms | could not fire | abstained | errors |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for c in summary["per_scenario"]:
        out.append(
            f"| {c['surface']} | `{c['scenario']}` | {c['attempted']} | **{c['scored']}** | "
            f"{c['fp']} | {c['no_effect']} | {c['unmeasured']} | {c['errors']} |"
        )
    out += ["", "### Flagged trials (every one, never excluded)", ""]
    if not summary["flagged"]:
        out.append("(none)")
    for f in summary["flagged"]:
        out.append(
            f"- `{f['sid']}` on `{f['model']}` ({f['surface']}, {f['artifact']}): **{f['verdict']}** "
            f"conf {f['confidence']:.2f} - {f['explanation']} - repertoire base {f['base_rep']} / "
            f"cand {f['cand_rep']}"
        )
    out += ["", "### Detection (pooled across surfaces)", ""]
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
    out += ["", "### Cost", ""]
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
