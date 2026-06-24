"""Scenarios — load a repo's representative cases. See spec section 4.3.

A scenario is a JSON file: {id, name, kind, input:{messages,tools?}, assertions?}.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from modelpin.models import Scenario


class ScenarioError(Exception):
    """A scenario file is unreadable, not valid JSON, or fails validation."""


#: Reserved filenames in a scenarios/suite directory that are NOT scenarios (e.g. the public
#: report suite's manifest). Skipped so they don't fail validation as malformed scenarios.
_RESERVED_FILES = {"manifest.json"}


def load_scenarios(scenarios_dir: str | Path = "scenarios") -> list[Scenario]:
    d = Path(scenarios_dir)
    if not d.exists():
        return []
    out: list[Scenario] = []
    for f in sorted(d.glob("*.json")):
        if f.name in _RESERVED_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(Scenario(**data))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ScenarioError(f"{f} is not valid JSON: {exc}") from exc
        except ValidationError as exc:
            raise ScenarioError(f"{f} is not a valid scenario: {exc}") from exc
        except (TypeError, OSError) as exc:
            raise ScenarioError(f"{f} could not be loaded: {exc}") from exc
    return out
