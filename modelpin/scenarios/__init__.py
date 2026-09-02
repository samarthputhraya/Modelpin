"""Scenarios — load a repo's representative cases. See spec section 4.3.

A scenario is a JSON file: {id, name, kind, input:{messages,tools?}, assertions?}.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from modelpin.models import Assertion, Scenario


class ScenarioError(Exception):
    """A scenario file is unreadable, not valid JSON, or fails validation."""


#: Reserved filenames in a scenarios/suite directory that are NOT scenarios (e.g. the public
#: report suite's manifest, or the examples tree's fit/score role declaration). Skipped so they
#: don't fail validation as malformed scenarios.
_RESERVED_FILES = {"manifest.json", "roles.json"}


def unrecognised_assertion_keys(scenarios_dir: str | Path) -> dict[str, list[str]]:
    """Assertion keys in the FILES that this version's `Assertion` model does not have.

    MP-147 deleted `expected_tool_calls` and `output_schema`, which were consulted by
    nothing. Deleting a pydantic field does not make a file carrying it an error -- the model
    ignores extra keys -- so on its own the deletion would move the "silently does nothing"
    defect out of our model and INTO the user's scenario file, where it is harder to see.
    Reading the raw keys is what keeps the silence removed.

    Returns `{key: [scenario ids]}`. Never raises: a file that cannot be read is a problem
    for `load_scenarios` to report, not for an advisory.
    """
    known = set(Assertion.model_fields)
    found: dict[str, list[str]] = {}
    d = Path(scenarios_dir)
    if not d.exists():
        return found
    for f in sorted(d.glob("*.json")):
        if f.name in _RESERVED_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            assertions = data.get("assertions")
            if not isinstance(assertions, dict):
                continue
            sid = str(data.get("id") or f.stem)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, AttributeError):
            continue
        for key in assertions:
            if key not in known:
                found.setdefault(key, []).append(sid)
    return found


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
