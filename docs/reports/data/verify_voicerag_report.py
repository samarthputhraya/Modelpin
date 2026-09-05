"""Recompute every headline number in docs/reports/modelpin-voicerag-abstention-1.md.

No API key, no network. Reads only the committed artifacts beside this file.
Run it from the repo root:  python docs/reports/data/verify_voicerag_report.py
"""

import collections
import json
import pathlib
import re

DATA = pathlib.Path(__file__).resolve().parent
ABSTAIN = "INSUFFICIENT_CONTEXT"


def traces(path):
    """Every recorded run in a Modelpin baseline artifact."""

    def walk(o):
        if isinstance(o, dict):
            if "final_output" in o and "scenario_id" in o:
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    return list(walk(json.loads(path.read_text(encoding="utf-8"))))


def stamp_days(path):
    """The distinct calendar days any `ts` in the artifact falls on."""
    days = collections.Counter()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "ts" and isinstance(v, str):
                    days[v[:10]] += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return days


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# --------------------------------------------------------------------------
section("1. Run date and size")
for name in (
    "voicerag_traces_openai_gpt-oss-20b.json",
    "voicerag_traces_openai_gpt-oss-120b.json",
    "aegis_traces_openai_gpt-oss-120b.json",
    "aegis_traces_openai_gpt-oss-20b.json",
):
    p = DATA / name
    ts = traces(p)
    days = stamp_days(p)
    print(f"{name:44s} {len(ts):3d} traces  days={dict(days)}")

# --------------------------------------------------------------------------
section("2. VoiceRAG: abstention register, per model")
print(f"{'scenario':30s} {'20b abst':>9s} {'20b ref':>8s} {'120b abst':>10s} {'120b ref':>9s}")
vr = {
    "20b": traces(DATA / "voicerag_traces_openai_gpt-oss-20b.json"),
    "120b": traces(DATA / "voicerag_traces_openai_gpt-oss-120b.json"),
}
by = {k: collections.defaultdict(list) for k in vr}
for k, ts in vr.items():
    for t in ts:
        by[k][t["scenario_id"]].append(t)

tot = {k: {"abst": 0, "ref": 0, "n": 0} for k in vr}
for sid in sorted(by["20b"]):
    cells = []
    for k in ("20b", "120b"):
        rows = by[k][sid]
        a = sum(1 for t in rows if ABSTAIN in (t["final_output"] or ""))
        r = sum(1 for t in rows if t.get("refused"))
        cells += [f"{a}/{len(rows)}", f"{r}/{len(rows)}"]
        tot[k]["abst"] += a
        tot[k]["ref"] += r
        tot[k]["n"] += len(rows)
    print(f"{sid:30s} {cells[0]:>9s} {cells[1]:>8s} {cells[2]:>10s} {cells[3]:>9s}")
for k in ("20b", "120b"):
    t = tot[k]
    print(f"  TOTAL {k:5s}: abstained {t['abst']}/{t['n']}   refused(detector) {t['ref']}/{t['n']}")

# --------------------------------------------------------------------------
section("3. The regression: abstain_unsafe_question, verbatim")
for k in ("20b", "120b"):
    outs = collections.Counter((t["final_output"] or "") for t in by[k]["abstain_unsafe_question"])
    for text, n in outs.items():
        flags = {t.get("refused") for t in by[k]["abstain_unsafe_question"]}
        print(f"  gpt-oss-{k:4s} x{n}  refused={flags}  {text!r}")

# --------------------------------------------------------------------------
section("4. The blind spot: genuine abstentions the refusal detector did not see")
blind = [t for t in vr["20b"] if ABSTAIN in (t["final_output"] or "") and not t.get("refused")]
print(f"  gpt-oss-20b: {len(blind)} traces emitted {ABSTAIN} and recorded refused=False")
print("  REFUSAL_MARKERS are first-person English; a sentinel token matches none of them.")

# --------------------------------------------------------------------------
section("5. Output-shape facts an assertion author needs")
for k in ("20b", "120b"):
    ts = vr[k]
    nl = sum(1 for t in ts if "\n" in (t["final_output"] or ""))
    nnbsp = sum((t["final_output"] or "").count(" ") for t in ts)
    forms = collections.Counter()
    for t in ts:
        for m in re.findall(r"\[[0-9][^\]]*\]", t["final_output"] or ""):
            forms[m] += 1
    print(
        f"  gpt-oss-{k:4s}: newline in {nl}/{len(ts)}  U+202F count {nnbsp}  citations {dict(forms)}"
    )

# --------------------------------------------------------------------------
section("6. aegis: tool trajectories and the two-sided decline")
ag = {
    "120b": traces(DATA / "aegis_traces_openai_gpt-oss-120b.json"),
    "20b": traces(DATA / "aegis_traces_openai_gpt-oss-20b.json"),
}
agby = {k: collections.defaultdict(list) for k in ag}
for k, ts in ag.items():
    for t in ts:
        agby[k][t["scenario_id"]].append(t)
for sid in sorted(agby["120b"]):
    line = f"  {sid:22s}"
    for k in ("120b", "20b"):
        rows = agby[k][sid]
        trajs = collections.Counter(
            tuple(tc["name"] for tc in t.get("tool_calls", [])) for t in rows
        )
        modal, n = trajs.most_common(1)[0]
        ref = sum(1 for t in rows if t.get("refused"))
        line += f" | {k}: {list(modal)} x{n}/{len(rows)} ref={ref}"
    print(line)

# --------------------------------------------------------------------------
section("7. The correction (MP-187): the STAGED 2026-08-31 aegis reference")
staged = pathlib.Path("ops/launch/aegis-suite/baseline-openai_gpt-oss-120b.json")
if staged.exists():
    ts = traces(staged)
    ref = sum(1 for t in ts if t.get("refused"))
    print(f"  {staged}: {ref} of {len(ts)} traces carry refused=true")
    print("  The write-up dated the same day reported 0 of 30. The artifact refutes it.")
else:
    print(f"  {staged} is not in this checkout (ops/ is private); claim not checkable here.")
