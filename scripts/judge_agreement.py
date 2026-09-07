"""How much of a published false-positive rate is the JUDGE'S opinion? — offline, no key.

`[M] 2026-09-07` (MP-208) every false-positive and detection number Modelpin had published was
scored by an OpenAI judge, and **51 of the 82 scored trials in the run of record could only have
fired on the semantic channel**. So on the majority of the evidence the bound rests on, the
OpenAI judge *is* the measurement — and a measurement whose instrument has never been compared
to another instrument has no stated reproducibility.

This script closes that by reading two artifacts of the SAME replay scored by two different
judges (`fp_measurement.py --rejudge`) and reporting, per trial, whether they agreed. Because
`--rejudge` re-diffs the stored traces rather than replaying, every channel but the semantic one
is recomputed from byte-identical inputs; a verdict that moves therefore moves *because of the
judge*, and cannot be model noise. That identity is not assumed — it is CHECKED below, and a
mismatch is a refusal, because two different samples would make an agreement rate meaningless.

    python scripts/judge_agreement.py A.jsonl B.jsonl
    python scripts/judge_agreement.py A.jsonl B.jsonl --json agreement.json

The false-positive bound UNDER EACH judge is not computed here: `fp_aggregate.py` already folds
an artifact through the harness's own published helpers, so run it on each file rather than
growing a second, divergent way to state the same rate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import DiffResult  # noqa: E402
from scripts.fp_measurement import (  # noqa: E402
    fp_outcome,
    fp_tally,
    load_artifact,
    upper_bound_95,
)


def _judge_of(header: dict) -> str:
    """`model @ host`. A pre-MP-208 artifact records no host; it could only have been OpenAI,
    because that is precisely the defect MP-208 fixed — but it is labelled as inferred, never
    silently asserted."""
    model = header.get("judge")
    if model is None:
        return "none (--no-judge)"
    host = header.get("judge_provider")
    return f"{model} @ {host}" if host else f"{model} @ openai (unrecorded; inferred)"


def paired_rows(rows_a: list[dict], rows_b: list[dict]) -> tuple[list[tuple], list[str]]:
    """`[(key, row_a, row_b)]` for every trial BOTH artifacts scored, plus the keys only one
    of them reached. A trial missing from either side is excluded from the agreement rate and
    reported separately: it is an absence of evidence, not a disagreement."""
    a = {r["key"]: r for r in rows_a if r.get("result") is not None}
    b = {r["key"]: r for r in rows_b if r.get("result") is not None}
    both = sorted(set(a) & set(b))
    only = sorted(set(a) ^ set(b))
    return [(k, a[k], b[k]) for k in both], only


def replay_is_identical(row_a: dict, row_b: dict) -> bool:
    """The load-bearing precondition. If the two artifacts hold different traces then their
    judges were shown different model behaviour, and any 'disagreement' between them is
    confounded with model nondeterminism — the exact conflation this whole comparison exists
    to avoid."""
    return row_a.get("base_traces") == row_b.get("base_traces") and row_a.get(
        "cand_traces"
    ) == row_b.get("cand_traces")


def compare(pairs: list[tuple]) -> dict:
    """Per-trial agreement between two judges over one replay.

    Three rates, because they answer three different questions:

      verdict      the published answer. Did the two judges' runs end in the same verdict?
      alarm        the north-star one. Did they agree on whether to RAISE AN ALARM at all?
                   Two judges can differ on `changed_minor` vs `regression` and still both be
                   crying wolf, or both be silent; that is a smaller disagreement than one
                   flagging where the other is clean, and it is not worth conflating them.
      semantic     the channel itself: did the mean candidate equivalence score move?
    """
    out: dict = {
        "n": len(pairs),
        "verdict_agree": 0,
        "alarm_agree": 0,
        "semantic_identical": 0,
        "replay_mismatch": [],
        "disagreements": [],
    }
    for key, ra, rb in pairs:
        if not replay_is_identical(ra, rb):
            out["replay_mismatch"].append(key)
        da, db = DiffResult(**ra["result"]), DiffResult(**rb["result"])
        alarm_a, alarm_b = fp_outcome(da) == "flagged", fp_outcome(db) == "flagged"
        sem_a, sem_b = da.signals.semantic_score, db.signals.semantic_score
        if da.verdict == db.verdict:
            out["verdict_agree"] += 1
        if alarm_a == alarm_b:
            out["alarm_agree"] += 1
        if sem_a == sem_b:
            out["semantic_identical"] += 1
        if da.verdict != db.verdict or alarm_a != alarm_b:
            out["disagreements"].append(
                {
                    "key": key,
                    "a_verdict": da.verdict.value,
                    "b_verdict": db.verdict.value,
                    "a_alarm": alarm_a,
                    "b_alarm": alarm_b,
                    "a_semantic": sem_a,
                    "b_semantic": sem_b,
                    "a_confidence": round(da.confidence, 3),
                    "b_confidence": round(db.confidence, 3),
                }
            )
    return out


def bound_under(pairs: list[tuple], side: int) -> dict:
    """The false-positive arm's own tally and bound, over the PAIRED trials only, under one
    judge — folded through `fp_tally`, the same helper the harness publishes with, so this
    page and a transcript can only disagree if the artifact changed.

    Restricting to the paired trials is the point. A bound over each artifact's full set would
    compare two different denominators and call the difference a judge effect.
    """
    outcomes = [
        fp_outcome(DiffResult(**p[side]["result"])) for p in pairs if p[0].startswith("fp:")
    ]
    t = fp_tally(outcomes)
    scored, fps = t["scored"], t["false_positives"]
    return {
        "trials": len(outcomes),
        "scored": scored,
        "could_not_fire": t["no_effect"],
        "unmeasured": t["unmeasured"],
        "false_alarms": fps,
        "upper_bound_scored": round(100.0 * upper_bound_95(fps, scored), 1) if scored else None,
        "upper_bound_reached": round(100.0 * upper_bound_95(fps, len(outcomes)), 1),
    }


def _rate(k: int, n: int) -> str:
    if n == 0:
        return "0/0 — no paired trial"
    return f"{k}/{n} = {100.0 * k / n:.1f}%"


def render(summary: dict) -> list[str]:
    c, n = summary["compare"], summary["compare"]["n"]
    lines = [
        "JUDGE AGREEMENT — one replay, two judges (MP-208)",
        f"  A: {summary['a']['judge']}   [{summary['a']['path']}]",
        f"  B: {summary['b']['judge']}   [{summary['b']['path']}]",
        f"  model under test: {summary['model']} * scenarios: {summary['scenarios_dir']}"
        f" * runs: {summary['runs']} * repeats: {summary['repeats']}",
        "",
        f"  Paired trials (both judges reached a verdict): {n}",
    ]
    if summary["unpaired"]:
        lines.append(
            f"  Scored by only one judge, EXCLUDED from every rate below: "
            f"{len(summary['unpaired'])} ({', '.join(summary['unpaired'][:6])}"
            + (", ..." if len(summary["unpaired"]) > 6 else "")
            + ")"
        )
    if c["replay_mismatch"]:
        lines += [
            "",
            f"  !! {len(c['replay_mismatch'])} paired trial(s) DO NOT share a replay, so their",
            "     judges did not see the same model behaviour and their disagreement is",
            "     confounded. This is not a judge measurement. Keys: "
            + ", ".join(c["replay_mismatch"][:6]),
        ]
    lines += [
        "",
        f"  Verdict agreement:   {_rate(c['verdict_agree'], n)}",
        f"  Alarm agreement:     {_rate(c['alarm_agree'], n)}   (flag / no flag)",
        f"  Identical semantic score: {_rate(c['semantic_identical'], n)}",
    ]
    if n:
        # The bound is on the DISAGREEMENT rate, in the direction that constrains the claim:
        # "the two judges disagree no more often than X". Same one-sided Clopper-Pearson the
        # FP arm publishes (ADR-0022), so a reader meets one interval convention, not two.
        d = n - c["alarm_agree"]
        lines.append(
            f"  95% upper bound on the judge DISAGREEMENT rate (alarm): "
            f"{100.0 * upper_bound_95(d, n):.1f}%  (one-sided Clopper-Pearson, "
            f"{d}/{n})"
        )
    if c["disagreements"]:
        lines += ["", f"  DISAGREEMENTS ({len(c['disagreements'])}):"]
        for d in c["disagreements"]:
            lines.append(
                f"    {d['key']:<34} A {d['a_verdict']:<14} (sem {d['a_semantic']})"
                f"  vs  B {d['b_verdict']:<14} (sem {d['b_semantic']})"
            )
    else:
        lines += ["", "  No disagreement on any paired trial."]
    lines += ["", "  FALSE-POSITIVE ARM, over the paired trials only, under each judge:"]
    for tag, key in (("A", "a"), ("B", "b")):
        b = summary["bound"][key]
        ub = (
            "n/a — nothing scored"
            if b["upper_bound_scored"] is None
            else f"{b['upper_bound_scored']}%"
        )
        lines.append(
            f"    {tag} {summary[key]['judge']:<44} {b['false_alarms']}/{b['scored']} scored "
            f"(ub {ub}); {b['false_alarms']}/{b['trials']} reached "
            f"(ub {b['upper_bound_reached']}%); {b['could_not_fire']} could not fire"
        )
    da, db = summary["bound"]["a"], summary["bound"]["b"]
    if da["scored"] != db["scored"]:
        # The finding this whole comparison can produce that a verdict-agreement rate alone
        # would hide. A judge that calls more outputs equivalent drives the semantic channel to
        # p = 1.00, which ADR-0022 then EXCLUDES - so the choice of judge moves the DENOMINATOR
        # of the published bound, not only its numerator. Same traces, same engine, same
        # constants; a different number of trials in the rate.
        lines += [
            "",
            f"  !! The two judges do not agree on WHAT WAS MEASURED: {da['scored']} scored trial(s)"
            f" under A, {db['scored']} under B,",
            "     over identical traces. A judge that finds more outputs equivalent pushes the",
            "     semantic channel to p = 1.00, which ADR-0022 excludes - so the judge moves the",
            "     bound's DENOMINATOR, not just its numerator.",
        ]
    lines += [
        "",
        "  NB these are the same trials twice, not two samples. Do not pool them.",
    ]
    return lines


def summarise(path_a: str, path_b: str) -> dict:
    header_a, rows_a = load_artifact(path_a)
    header_b, rows_b = load_artifact(path_b)
    if header_a is None or header_b is None:
        raise SystemExit("error: both artifacts need a header line.")
    # NB `repeats` is deliberately absent. A rejudge is routinely a pre-registered PREFIX of the
    # recorded rounds, because the free judge tier that makes it affordable is a daily quota;
    # narrowing is legitimate and pairing is by trial key, so a shorter run is not a mismatch.
    # `runs` is not narrowable and would change what each trial measured, so it stays.
    for field in ("model", "runs", "scenarios_dir", "role"):
        if header_a.get(field) != header_b.get(field):
            raise SystemExit(
                f"error: the two artifacts differ in {field!r} "
                f"({header_a.get(field)!r} vs {header_b.get(field)!r}). An agreement rate over "
                "two different measurements describes neither."
            )
    if header_a.get("judge") == header_b.get("judge") and header_a.get(
        "judge_provider"
    ) == header_b.get("judge_provider"):
        raise SystemExit(
            f"error: both artifacts were scored by the same judge ({_judge_of(header_a)}). "
            "There is no disagreement to measure."
        )
    pairs, unpaired = paired_rows(rows_a, rows_b)
    return {
        "a": {"path": path_a, "judge": _judge_of(header_a)},
        "b": {"path": path_b, "judge": _judge_of(header_b)},
        "model": header_a.get("model"),
        "runs": header_a.get("runs"),
        "repeats": header_a.get("repeats"),
        "scenarios_dir": header_a.get("scenarios_dir"),
        "unpaired": unpaired,
        "compare": compare(pairs),
        "bound": {"a": bound_under(pairs, 1), "b": bound_under(pairs, 2)},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("a", metavar="A.jsonl", help="artifact scored by the first judge")
    ap.add_argument("b", metavar="B.jsonl", help="the same replay, scored by the second")
    ap.add_argument("--json", default=None, metavar="OUT.json", help="also write the summary")
    args = ap.parse_args()
    summary = summarise(args.a, args.b)
    for line in render(summary):
        print(line)
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
