"""On-disk baseline store. Phase 0 persists recorded traces as JSON under a
``.modelpin/`` directory in the repo (Postgres arrives in the hosted phase)."""

from __future__ import annotations

import json
import os
import re
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
        return {
            sid: [Trace(**t) for t in traces] for sid, traces in raw.get("scenarios", {}).items()
        }
    except (json.JSONDecodeError, ValidationError, AttributeError, TypeError) as exc:
        raise BaselineError(
            f"Baseline {path} is corrupt ({exc}). Delete it and re-run `modelpin baseline`."
        ) from exc
