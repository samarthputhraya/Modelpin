"""On-disk baseline store. Phase 0 persists recorded traces as JSON under a
``.modelpin/`` directory in the repo (Postgres arrives in the hosted phase)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from modelpin.models import Trace

STORE_DIRNAME = ".modelpin"


class BaselineError(Exception):
    """A baseline file exists but is corrupt or cannot be parsed."""


def _safe(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model_id)


def baseline_path(model_id: str, store_dir: str | Path = STORE_DIRNAME) -> Path:
    return Path(store_dir) / f"baseline-{_safe(model_id)}.json"


def save_baseline(
    traces_by_scenario: dict[str, list[Trace]],
    model_id: str,
    store_dir: str | Path = STORE_DIRNAME,
) -> Path:
    """Persist N recorded traces per scenario for a model. Returns the file path.

    Writes atomically (temp file + ``os.replace``) so an interrupted run never leaves
    a half-written baseline that would later fail to parse.
    """
    path = baseline_path(model_id, store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_id": model_id,
        "scenarios": {
            sid: [t.model_dump(mode="json") for t in traces]
            for sid, traces in traces_by_scenario.items()
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


#: Text a *fabricating* provider wrote instead of replaying a model. The pre-ADR-0015
#: `FakeProvider` returned this for any key it did not hold, and `baseline` persisted it.
#: ADR-0015 closed the generator and says in terms: "It does nothing for baselines already
#: on disk - see MP-43". This is that guard, at the only boundary that can still see them.
FABRICATED_TRACE_MARKERS: tuple[str, ...] = ("(fake) no canned trace",)


def _trace_texts(trace: Trace) -> list[str]:
    """Every free-text field of a trace: the prompt the user wrote AND the model's output.

    Prompts are included deliberately -- a key pasted into a scenario is the likelier leak,
    because the user typed it themselves.
    """
    texts = [trace.final_output or ""]
    for message in trace.messages or []:
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
    for call in trace.tool_calls or []:
        for value in (call.arguments or {}).values():
            if isinstance(value, str):
                texts.append(value)
    return texts


def fabricated_scenarios(baseline: dict[str, list[Trace]]) -> dict[str, int]:
    """Scenario -> count of traces that were INVENTED rather than replayed.

    `[M] 2026-09-06` (MP-189) Reproduced: 5 such traces against a genuine, well-behaved
    candidate yield `REGRESSION ... refusal rate 0% -> 100% (confidence 1.00)`. That is the
    north-star promise inverted at maximum confidence, so this is a hard error, not a warning.
    Keyed on the sentinel and never on the word "fake" -- a scenario about fake news must
    still load.
    """
    out: dict[str, int] = {}
    for sid, traces in baseline.items():
        n = sum(
            1 for t in traces if any(m in (t.final_output or "") for m in FABRICATED_TRACE_MARKERS)
        )
        if n:
            out[sid] = n
    return out


def degenerate_scenarios(baseline: dict[str, list[Trace]]) -> dict[str, int]:
    """Scenario -> run count, for sides where EVERY run is unusable for comparison.

    Degeneracy is `diff/structural.py`'s own definition -- no tool call, no refusal, no
    text -- imported rather than restated so the two can never drift. Only an ALL-degenerate
    scenario qualifies: a side with one good run still carries signal, and warning about it
    would be the crying-wolf shape the north-star metric exists to prevent.
    """
    from modelpin.diff.structural import is_degenerate  # local: avoids an import cycle

    out: dict[str, int] = {}
    for sid, traces in baseline.items():
        if traces and all(is_degenerate(t) for t in traces):
            out[sid] = len(traces)
    return out


def secret_bearing_scenarios(baseline: dict[str, list[Trace]]) -> dict[str, int]:
    """Scenario -> count of traces holding a key-shaped token, in prompt OR output.

    Deliberately reports rather than rewrites. Silently mutating recorded evidence would
    make the artifact disagree with the run that produced it, and this project treats
    recorded evidence as load-bearing. The caller warns; the user decides.
    """
    from modelpin.providers._common import contains_secret

    out: dict[str, int] = {}
    for sid, traces in baseline.items():
        n = sum(1 for t in traces if any(contains_secret(x) for x in _trace_texts(t)))
        if n:
            out[sid] = n
    return out


def load_baseline(model_id: str, store_dir: str | Path = STORE_DIRNAME) -> dict[str, list[Trace]]:
    """Load recorded traces per scenario for a model.

    Raises ``FileNotFoundError`` (with guidance) if no baseline has been recorded yet,
    or ``BaselineError`` if the file exists but is corrupt.
    """
    path = baseline_path(model_id, store_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No baseline for {model_id!r} at {path}. Run `modelpin baseline` first."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        loaded = {
            sid: [Trace(**t) for t in traces] for sid, traces in raw.get("scenarios", {}).items()
        }
    except (json.JSONDecodeError, ValidationError, AttributeError, TypeError) as exc:
        raise BaselineError(
            f"Baseline {path} is corrupt ({exc}). Delete it and re-run `modelpin baseline`."
        ) from exc

    fabricated = fabricated_scenarios(loaded)
    if fabricated:
        named = ", ".join(f"{sid} ({n} run(s))" for sid, n in sorted(fabricated.items()))
        raise BaselineError(
            f"Baseline {path} holds FABRICATED traces that were never replayed against any "
            f"model: {named}. They were written by an older `--provider fake` run with no "
            f"matching fixture. Comparing against them reports a confident regression for a "
            f"candidate that did nothing wrong, so this run is refused rather than answered. "
            f"Delete the file and re-record it with `modelpin baseline` against a real "
            f"provider (or with `--provider fake --fixtures <file>` that covers every scenario)."
        )
    return loaded


def nonuniform_run_counts(
    baseline: dict[str, list[Trace]], only: Iterable[str] | None = None
) -> dict[str, int]:
    """Scenario -> recorded-run-count, but ONLY when the baseline is not uniform; ``{}`` when
    every scenario holds the same number of runs (the normal case).

    ``only`` restricts the comparison to the scenarios a run will actually replay, and
    zero-run entries are dropped in every case. Both narrowings exist because the caller is a
    PRE-SPEND power warning, and one that fires when nothing in the run is affected is the
    crying-wolf shape the north-star metric exists to prevent. `[M]` Unscoped, it fired on a
    baseline entry whose scenario file had been deleted, and on an entry holding 0 recorded
    runs -- which `check` skips -- in both cases while every scenario in the run was measured
    at full power.

    A heterogeneous baseline is not corrupt and is not mishandled: every comparison is scored
    against its own scenario's recorded runs (MP-72), which is why this reports rather than
    raises. What it fixes is that nothing SAID so. `[M]` MP-116: with 2 of 4 scenarios holding
    2 recorded runs and the check at ``--runs 4``, two scenarios were structurally blind and
    two were measured at full power, and the run was silently partial -- `save_baseline` and
    `load_baseline` applied zero uniformity validation in either direction.

    `[M]` No Modelpin command can produce one today: `save_baseline` is called from exactly
    one site and REPLACES the whole ``scenarios`` dict, and `replay()` always returns exactly
    ``runs`` traces, so even a partial replay failure cannot. It arrives by hand-editing, by
    a merge, or from an externally generated file -- all of which are ordinary things to do
    to a JSON file the demo README itself invites editing.
    """
    keep = None if only is None else set(only)
    counts = {
        sid: len(traces)
        for sid, traces in baseline.items()
        if traces and (keep is None or sid in keep)
    }
    return {} if len(set(counts.values())) <= 1 else counts
