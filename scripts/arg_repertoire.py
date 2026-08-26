"""Measure the observed argument *repertoire*: how many distinct tool-call payloads a model
emits for a scenario across N runs.

`examples/calibration/README-arguments.md` ends by asking for exactly this number —

    Publish the observed repertoire, not just the verdict. A run that reports "0 false
    positives" over pools that turned out to be unimodal has reproduced exactly the
    non-evidence this subset was written to remove.

— as a standalone PRE-FLIGHT probe. `scripts/fp_measurement.py::repertoire()` already reports
this per scenario per *side*, but only inside a paired run that also prices a false-positive
rate and excludes trials that could not have fired (ADR-0022). This script answers the question
upstream of that one, before a key is spent: **how many payloads does this model actually
produce here?**

`[M] 2026-08-25 (MP-105)` What it found, on the same seven files at `temperature: 0.7`:

    gemini-2.5-flash   3 of 7 vary   (MP-105's own run; per 5-run SIDE, 8-9 trials)
    gpt-4o-mini        1 of 7 vary   (n=16, pooled)
    gpt-4.1-mini       2 of 7 vary   (n=16, pooled); 3 of 7 once arg_optional_fields is
                                     run to n=24

MP-105 concluded from its 7 same-scenario scored trials that the other six shapes are
schema-constrained and therefore deterministic regardless of temperature. `[M]` **Its own
transcript refutes that** — `arg_numeric_rounding` returned two distinct payloads on 5 of 8
trials and `arg_optional_fields` on 3 of 8. They were excluded because the base and candidate
POOLS OVERLAPPED (a 1-payload side inside a 2-payload side gives p = 1.00 on every channel),
which is the gate's false-positive defence working, not a property of the model.

What a second candidate adds is RATE, not kind: `arg_numeric_rounding` emits 1 payload in 16
runs on `gpt-4o-mini` and 7 on `gpt-4.1-mini` — the same vendor. `[A]` WHICH axes can move
still looks scenario-driven: four shapes held at one payload on every model tested.

Reports two counts per scenario, and the gap between them is the point:

    raw        payloads as emitted, dict order preserved
    canonical  keys sorted, matching how an argument key would be canonicalised. `[M]` The
               engine component that does this, `diff/argkey.py`, is on the unmerged MP-04
               branch -- nothing under `modelpin/diff/` on `main` reads `.arguments` at all.

BYO-key (ADR-0008); never run from an agent seat (ADR-0006).

    python scripts/arg_repertoire.py --provider openai --model gpt-4.1-mini --runs 16 \\
        --scenarios-dir examples/calibration --glob 'arg_*.json'
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import Scenario  # noqa: E402
from modelpin.providers.base import ProviderAdapter  # noqa: E402


def _git_sha() -> str:
    """The tree this run was produced from, or `"unknown"` off a checkout."""
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


def canonical(obj: Any) -> Any:
    """Key-sorted deep copy — the shape an argument key would be compared under.

    `[M]` `modelpin/diff/argkey.py` is on the unmerged MP-04 branch; nothing under
    `modelpin/diff/` on `main` reads `.arguments`, so this mirrors that branch, not `main`.
    """
    if isinstance(obj, dict):
        return {k: canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [canonical(x) for x in obj]
    return obj


def build_adapter(provider: str) -> ProviderAdapter:
    from modelpin.providers.openai import (
        OPENAI_COMPATIBLE_PROVIDERS,
        OpenAIAdapter,
        build_openai_compatible_adapter,
    )

    if provider == "openai":
        return OpenAIAdapter()
    if provider == "google":
        from modelpin.providers.google import GoogleAdapter

        return GoogleAdapter()
    if provider not in OPENAI_COMPATIBLE_PROVIDERS:
        # [M] `providers/anthropic.py` is a NotImplementedError stub, and an unknown name here
        # used to surface as a bare KeyError from the registry lookup.
        known = ", ".join(["openai", "google", *sorted(OPENAI_COMPATIBLE_PROVIDERS)])
        raise SystemExit(f"error: unknown --provider {provider!r}. Known: {known}.")
    return build_openai_compatible_adapter(provider)


def repertoire(
    scenario: Scenario, model_id: str, adapter: ProviderAdapter, runs: int, pace: float
) -> dict[str, Any]:
    raw: list[str] = []
    canon: list[str] = []
    errors: list[str] = []
    no_tool = 0

    for i in range(runs):
        try:
            trace = adapter.run(scenario, model_id, run_idx=i)
        except Exception as exc:  # noqa: BLE001 - a provider error is data, not a crash
            # [M] MP-105: the first version of this probe reported "0 of 7 vary" on a host
            # whose key was revoked -- every call raised, every list stayed empty, and an
            # empty set has one distinct element. An error column is not optional here.
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
            time.sleep(pace)
            continue
        if not trace.tool_calls:
            no_tool += 1
        payload = [{"name": c.name, "arguments": c.arguments} for c in trace.tool_calls]
        raw.append(json.dumps(payload, ensure_ascii=False))
        canon.append(json.dumps(canonical(payload), ensure_ascii=False, sort_keys=True))
        time.sleep(pace)

    tallies = Counter(raw).most_common()
    order = [payload for payload, _ in tallies]
    modal = tallies[0][1] if tallies else 0
    return {
        "scored_runs": len(raw),
        "errors": errors,
        "no_tool_call": no_tool,
        "distinct_raw": len(set(raw)),
        "distinct_canonical": len(set(canon)),
        "modal_share": f"{modal}/{len(raw)}" if raw else "0/0",
        "varies": len(set(raw)) > 1,
        "varies_beyond_key_order": len(set(canon)) > 1,
        # [M] claims-auditor 2026-08-25: `sorted(set(raw))` discarded frequencies, so the
        # 7-payload rounding distribution was unrecoverable from the artifact and the
        # disjointness risk it implies could not be audited. Counts, not a set.
        "payload_counts": dict(Counter(raw).most_common()),
        # [M] 2026-08-25: counts alone also discard ORDER, which makes exchangeability
        # untestable -- no way to check for drift or lag-1 correlation across a run burst,
        # and `scripts/arg_gate_price.py` has to ASSUME i.i.d. draws because of it. An index
        # per run into the key order of `payload_counts` restores that at negligible size.
        "payload_sequence": [order.index(r) for r in raw],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--provider",
        required=True,
        help="openai, google, or an OpenAI-compatible host (groq, openrouter, together, cerebras)",
    )
    ap.add_argument("--model", required=True)
    ap.add_argument("--runs", type=int, default=12)
    ap.add_argument("--scenarios-dir", default="examples/calibration")
    ap.add_argument("--glob", default="arg_*.json")
    ap.add_argument("--pace", type=float, default=0.8, help="seconds between replays")
    ap.add_argument("--out", default=None, help="write the full report here as JSON")
    a = ap.parse_args(argv)

    if a.runs < 2:
        raise SystemExit("error: --runs must be >= 2; a repertoire needs at least two samples.")

    paths = sorted(Path(a.scenarios_dir).glob(a.glob))
    if not paths:
        raise SystemExit(f"error: no scenarios matched {a.glob!r} in {a.scenarios_dir}.")

    adapter = build_adapter(a.provider)
    print(f"provider={a.provider} model={a.model} runs={a.runs} scenarios={len(paths)}")
    print(f"budget: {len(paths) * a.runs} replays (an agent scenario is >1 API call per replay)")
    print()

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelpin_version": version("modelpin"),
        "git_sha": _git_sha(),
        "provider": a.provider,
        "model": a.model,
        "runs": a.runs,
        "pace_seconds": a.pace,
        "scenarios_dir": a.scenarios_dir,
        "glob": a.glob,
        # Declared per scenario file, not by this script -- recorded so a reader does not have
        # to open seven JSONs to learn the sampling temperature the numbers depend on.
        "scenario_temperatures": {},
        "scenarios": {},
    }
    for path in paths:
        sc = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        row = repertoire(sc, a.model, adapter, a.runs, a.pace)
        report["scenarios"][sc.id] = row
        report["scenario_temperatures"][sc.id] = sc.input.get("temperature")
        gap = "" if row["distinct_canonical"] == row["distinct_raw"] else "  <- key-order only"
        print(
            f"  {sc.id:<24} ok={row['scored_runs']}/{a.runs} err={len(row['errors'])} "
            f"notool={row['no_tool_call']}  distinct_raw={row['distinct_raw']} "
            f"distinct_canon={row['distinct_canonical']}  "
            f"{'VARIES' if row['varies'] else 'identical'}{gap}"
        )
        if row["errors"]:
            print(f"      first error: {row['errors'][0]}")

    varying = [k for k, v in report["scenarios"].items() if v["varies"]]
    dead = [k for k, v in report["scenarios"].items() if not v["scored_runs"]]
    print()
    print(f"VARY ({len(varying)}/{len(paths)}): {varying or 'none'}")
    if dead:
        print(
            f"NO SUCCESSFUL RUN ({len(dead)}): {dead} -- these measured NOTHING, not 'no variance'."
        )
    if not varying:
        print(
            f"Every scenario returned ONE payload. That is an abstention (ADR-0018), not "
            f"quietness: {a.runs} identical runs bound the per-run divergence rate at "
            f"upper_bound_95(0, {a.runs}), not at zero. Raise --runs, or pick a candidate "
            "measured to vary (MP-105)."
        )

    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"written: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
