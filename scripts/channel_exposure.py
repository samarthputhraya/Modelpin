"""Per-CHANNEL false-positive exposure and bounds — offline, no key (MP-207, ADR-0038).

`fp_aggregate.py` answers "how often did the engine cry wolf?". This answers the question that
one cannot: **on which channel, and over how many trials where that channel could have cried at
all?**

`[M] 2026-09-07`, over `reports/fp-runs/2026-09-07/*.jsonl`, the run of record's FP arm:

| arm | trials | `tool_call_match < 1.0` | `format_valid == False` |
|---|---|---|---|
| fp (same model vs itself) | **710** | **0** | **0** |
| recall (perturbed) | 46 | 10 | 7 |

So the published 3.6% bound was carried entirely by the semantic and argument channels, and
`MIN_TOOL_TVD` had a false-positive exposure of exactly zero trials. A rate of 0/0 on the signal
a migration tool exists for is not a low rate; it is no measurement. This script exists so that
"which channel was actually exposed" is a number in an artifact rather than an assumption.

    python scripts/channel_exposure.py reports/channel-exposure/<date>/v2*.jsonl
    python scripts/channel_exposure.py ... --json summary.json

**It refuses artifacts under `reports/fp-runs/`** and never pools with them: `examples/fp-suite-v2`
is deliberately exposure-maximising on two channels, so its rate is a near-worst-case CONDITIONAL
figure — "given a corpus where the tool trajectory and assertions are live" — and averaging that
with a corpus built to be representative describes neither (ADR-0038 D2).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.diff.structural import assertion_violation_flags  # noqa: E402
from modelpin.models import DiffResult, Trace  # noqa: E402
from modelpin.scenarios import load_scenarios  # noqa: E402
from scripts.fp_measurement import fp_outcome, load_artifact, upper_bound_95  # noqa: E402

#: How a flagged verdict is attributed to the channel that raised it. These are the literal
#: reason strings `diff/__init__.py` appends, and `tests/test_channel_exposure.py` asserts each
#: one still appears in that file -- so rewording an explanation fails a test instead of
#: silently reattributing every false alarm to "other". Same guard shape as the [ARM:] markers
#: in `fp_measurement.py`, and for the same reason: prose that something parses is an interface.
CHANNEL_REASONS: dict[str, str] = {
    "tool": "tool-call behavior changed",
    "tool_relation": "tool-call trajectory now violates",
    "refusal": "refusal rate",
    "argument": "tool-call arguments changed",
    "assertion": "output format drift",
    "semantic": "semantic drift",
}

#: The negative control (ADR-0038 D1d). It must never move on either target channel; if it does,
#: the run measured noise and nothing else in it is readable.
ANCHOR = "anchor_mandatory_lookup_fixed_format"


def channels_fired(result: DiffResult) -> list[str]:
    """Which channels the engine named in its own explanation. Empty for `unchanged`."""
    return [k for k, marker in CHANNEL_REASONS.items() if marker in result.explanation]


def tool_exposed(result: DiffResult) -> bool:
    """Did the tool-call TRAJECTORY differ at all between the two sides?

    `[M]` Written as an explicit `is not None` test because `tool_call_match` of exactly **0.0**
    -- a completely disjoint trajectory, the single most important value here -- is falsy, so
    `signals.get("tool_call_match") or 1.0` silently reports it as a perfect match. That bug was
    written and caught during this row's own scenario review, where it turned 10 exposed recall
    trials into 0.
    """
    t = result.signals.tool_call_match
    return t is not None and t < 1.0


def assertion_exposed(rec: dict, scenario) -> bool | None:
    """Did the per-run assertion VIOLATION RATE differ between the two sides?

    `None` when the scenario declares no assertions, which is not the same as a rate that did
    not move: one is out of scope, the other is evidence. Recomputed from the stored traces
    rather than read off `format_valid`, because that signal is a single summary flag and the
    channel's statistic is the per-run rate.
    """
    a = scenario.assertions
    if a is None or not (a.must_contain or a.must_not_contain):
        return None
    if rec.get("base_traces") is None or rec.get("cand_traces") is None:
        return None
    base = [Trace(**t) for t in rec["base_traces"]]
    cand = [Trace(**t) for t in rec["cand_traces"]]
    bf = assertion_violation_flags(base, a.must_contain, a.must_not_contain)
    cf = assertion_violation_flags(cand, a.must_contain, a.must_not_contain)
    return sum(bf) != sum(cf)


def summarise(paths: list[str], scenarios_dir: str | None = None) -> dict:
    for p in paths:
        if "fp-runs" in Path(p).as_posix():
            raise SystemExit(
                f"error: {os.path.basename(p)} lives under reports/fp-runs/, the run of record. "
                "This script measures a deliberately exposure-maximising corpus and its rate "
                "must never be pooled with that one (ADR-0038 D2)."
            )
    header, _ = load_artifact(paths[0])
    if header is None:
        raise SystemExit(f"error: {paths[0]} has no header line.")
    scn = {
        s.id: s
        for s in load_scenarios(
            scenarios_dir or header.get("scenarios_path") or f"examples/{header['scenarios_dir']}"
        )
    }

    # `exposed` is "the channel moved at all"; `scored` additionally requires that the trial
    # COULD have fired at some ALPHA (ADR-0022). The bound is published over `scored`, never
    # over `exposed`: a denominator padded with trials that could not have produced a false
    # positive flatters the rate, which is the exact failure ADR-0022 exists to prevent.
    # `[M] 2026-09-07` on this run the two differ materially - 37 exposed, 26 scored - so the
    # distinction is load-bearing, not pedantic.
    tool = {"exposed": 0, "scored": 0, "flagged": 0}
    assertion = {"exposed": 0, "scored": 0, "flagged": 0, "in_scope": 0}
    overall = {"trials": 0, "scored": 0, "flagged": 0}
    per_scenario: dict[str, dict[str, int]] = defaultdict(
        lambda: {"trials": 0, "tool_exposed": 0, "assertion_exposed": 0, "flagged": 0}
    )
    anchor = {"trials": 0, "tool_exposed": 0, "assertion_exposed": 0, "flagged": 0}
    flagged_rows = []
    surfaces = []

    for path in paths:
        head, rows = load_artifact(path)
        if head is None:
            raise SystemExit(f"error: {path} has no header line.")
        surfaces.append(
            {
                "artifact": os.path.basename(path),
                "model": head["model"],
                "judge": head.get("judge"),
                "judge_provider": head.get("judge_provider"),
                "runs": head["runs"],
                "repeats": head["repeats"],
                "git_sha": head.get("git_sha"),
            }
        )
        for rec in rows:
            if rec.get("arm") != "fp" or rec.get("result") is None:
                continue
            res = DiffResult(**rec["result"])
            sid = rec["scenario_id"]
            fired = channels_fired(res)
            is_fp = fp_outcome(res) == "fp"
            overall["trials"] += 1
            if fp_outcome(res) in ("fp", "clean"):
                overall["scored"] += 1
            overall["flagged"] += int(is_fp)
            cell = anchor if sid == ANCHOR else per_scenario[sid]
            cell["trials"] += 1
            cell["flagged"] += int(is_fp)

            could_fire = fp_outcome(res) in ("fp", "clean")
            if tool_exposed(res):
                cell["tool_exposed"] += 1
                if sid != ANCHOR:
                    tool["exposed"] += 1
                    tool["scored"] += int(could_fire)
                    if is_fp and ("tool" in fired or "tool_relation" in fired):
                        tool["flagged"] += 1
            ax = assertion_exposed(rec, scn[sid])
            if ax is not None:
                if sid != ANCHOR:
                    assertion["in_scope"] += 1
                if ax:
                    cell["assertion_exposed"] += 1
                    if sid != ANCHOR:
                        assertion["exposed"] += 1
                        assertion["scored"] += int(could_fire)
                        if is_fp and "assertion" in fired:
                            assertion["flagged"] += 1
            if is_fp:
                flagged_rows.append(
                    {
                        "artifact": os.path.basename(path),
                        "sid": rec["sid"],
                        "verdict": res.verdict.value,
                        "confidence": round(res.confidence, 3),
                        "channels": fired,
                        "tool_call_match": res.signals.tool_call_match,
                        "explanation": res.explanation,
                    }
                )

    return {
        "surfaces": surfaces,
        "overall": overall,
        "tool": tool,
        "assertion": assertion,
        "anchor": anchor,
        "per_scenario": dict(per_scenario),
        "flagged": flagged_rows,
    }


def _bound(k: int, n: int) -> str:
    if n == 0:
        return "n/a — ZERO exposure, so there is no denominator and no bound"
    return f"{k}/{n} = {100.0 * k / n:.1f}%, one-sided 95% upper bound {100.0 * upper_bound_95(k, n):.1f}%"


def render(s: dict) -> list[str]:
    o, t, a, anc = s["overall"], s["tool"], s["assertion"], s["anchor"]
    lines = [
        "PER-CHANNEL FALSE-POSITIVE EXPOSURE — examples/fp-suite-v2 (MP-207, ADR-0038)",
        "  A same-model null on a corpus built so the TOOL TRAJECTORY and ASSERTION channels",
        "  can move. Not comparable with, and never pooled into, the fp-suite bound.",
        "",
    ]
    for f in s["surfaces"]:
        lines.append(
            f"  surface: {f['artifact']}  model {f['model']} vs itself, judge {f['judge']} "
            f"@ {f['judge_provider']}, runs {f['runs']} x repeats {f['repeats']}, git {f['git_sha']}"
        )
    lines += [
        "",
        (
            f"  FP arm, all channels: {o['flagged']}/{o['scored']} scored "
            f"({o['trials']} reached a verdict); "
            f"ub {100.0 * upper_bound_95(o['flagged'], o['scored']):.1f}%"
            if o["scored"]
            else "  FP arm: nothing scored"
        ),
        "",
        "  TOOL-CALL TRAJECTORY",
        f"    exposed (tool_call_match < 1.0)      : {t['exposed']} trial(s)",
        f"    ...of which SCORED (could have fired): {t['scored']}",
        f"    false alarms, over the SCORED ones   : {_bound(t['flagged'], t['scored'])}",
        "",
        "  FORMAT / ASSERTION",
        f"    in scope (scenario declares assertions): {a['in_scope']} trial(s)",
        f"    exposed (violation RATE differs)       : {a['exposed']} trial(s)",
        f"    ...of which SCORED                     : {a['scored']}",
        f"    false alarms, over the SCORED ones     : {_bound(a['flagged'], a['scored'])}",
    ]
    ok = anc["tool_exposed"] == 0 and anc["assertion_exposed"] == 0 and anc["flagged"] == 0
    lines += [
        "",
        f"  NEGATIVE CONTROL ({ANCHOR}): {anc['trials']} trial(s), "
        f"tool-exposed {anc['tool_exposed']}, assertion-exposed {anc['assertion_exposed']}, "
        f"flagged {anc['flagged']}  -> {'HELD' if ok else '*** MOVED — this run measured noise'}",
    ]
    if not ok:
        lines.append(
            "    The control was built so it CANNOT vary: one mandatory tool call, one fully "
            "specified output line. Its movement means the variance is the harness's or the "
            "adapter's, and no other number here is readable until that is explained."
        )
    if s["flagged"]:
        lines += ["", "  FALSE ALARMS:"]
        for f in s["flagged"]:
            lines.append(
                f"    {f['sid']:<38} {f['verdict']} @ {f['confidence']} "
                f"[{', '.join(f['channels']) or 'unattributed'}]"
            )
            lines.append(f"      {f['explanation'][:150]}")
    lines += ["", "  EXPOSURE BY SCENARIO (tool / assertion / trials):"]
    for sid, c in sorted(s["per_scenario"].items(), key=lambda kv: -kv[1]["tool_exposed"]):
        lines.append(
            f"    {sid:<40} {c['tool_exposed']:>3} / {c['assertion_exposed']:>3} / {c['trials']:>3}"
        )
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("artifacts", nargs="+", metavar="ARTIFACT.jsonl")
    ap.add_argument("--scenarios-dir", default=None)
    ap.add_argument("--json", default=None, metavar="OUT.json")
    args = ap.parse_args()
    s = summarise(args.artifacts, args.scenarios_dir)
    for line in render(s):
        print(line)
    if args.json:
        Path(args.json).write_text(
            json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
