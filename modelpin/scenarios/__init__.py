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

#: Directory names under a scenarios dir that hold OUTPUTS, not scenarios. Skipped when
#: recursing (MP-199).
#:
#: `[M] 2026-09-06` This is not hypothetical tidiness: switching `glob` to `rglob` immediately
#: pulled `examples/calibration/results/*.json` -- six measurement OUTPUT files -- into the
#: loader and failed three tests, because a run result has no `id`, `name` or `input`. Keeping
#: run output beside the scenarios that produced it is an obvious thing for a user to do too,
#: so the same shape would have met them on their own repo.
#:
#: Enumerated rather than inferred from file shape ON PURPOSE. "Skip any JSON that does not
#: look like a scenario" would reintroduce exactly the silence MP-199 exists to remove: a
#: scenario with a typo'd field would then be skipped without a word instead of raising. A
#: named directory is a decision the user can see; a shape heuristic is one they cannot.
_RESERVED_DIRS = {"results", "__pycache__"}


def _is_scenario_file(f: Path, root: Path) -> bool:
    """Should ``f`` be loaded as a scenario? Reserved filenames, output dirs and dot-dirs out.

    Dot-directories are excluded because ``scenarios_dir`` is user-supplied and pointing it
    at a repo root is a realistic mistake -- ``.git`` and ``.modelpin`` are full of JSON that
    is emphatically not a scenario.
    """
    if f.name in _RESERVED_FILES:
        return False
    return not any(
        part in _RESERVED_DIRS or part.startswith(".") for part in f.relative_to(root).parts[:-1]
    )


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
    # MP-199. `rglob`, matching `load_scenarios`. These two globs must stay identical: an
    # advisory that reads fewer files than the loader would quietly stop advising about the
    # nested ones, which is the same silence one directory deeper.
    for f in sorted(d.rglob("*.json")):
        if not _is_scenario_file(f, d):
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
    """Load every scenario under ``scenarios_dir``, refusing duplicate ids.

    **MP-199 -- ``rglob``, not ``glob``.** `[M] 2026-09-06` the non-recursive glob dropped
    `scenarios/auth/nested.json` without a word, and the run went on to print
    ``OK 1 scenario(s) unchanged``, exit 0, over a nested scenario whose candidate refused
    every run. A false clearance -- the north-star failure -- caused by nothing but putting
    scenarios in folders, which is the first thing anyone does past a handful of them.

    **MP-192 -- a repeated ``id`` is an error, raised before anything is replayed or spent.**
    `[M] 2026-09-06` two files sharing an id silently lost one recording: the store is keyed
    on ``Scenario.id`` (`cli.py`), so the second file's replays were PAID FOR and discarded,
    while the console still said ``5 scenario(s) x5 runs`` and the PR comment published
    ``**REGRESSIONS (3)**`` for two distinct regressions, listing one twice. Copying a
    scenario file and editing only the prompt is an ordinary thing to do.

    The two fixes belong together: recursing creates new ways for ids to collide, so shipping
    ``rglob`` without the duplicate check would widen the defect it is meant to close.
    """
    d = Path(scenarios_dir)
    if not d.exists():
        return []
    out: list[Scenario] = []
    #: id -> the file that claimed it, so the error can name BOTH paths. Without them the
    #: user is told an id is duplicated and left to find where, which is most of the work.
    seen: dict[str, Path] = {}
    for f in sorted(d.rglob("*.json")):
        if not _is_scenario_file(f, d):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            scenario = Scenario(**data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ScenarioError(f"{f} is not valid JSON: {exc}") from exc
        except ValidationError as exc:
            raise ScenarioError(f"{f} is not a valid scenario: {exc}") from exc
        except (TypeError, OSError) as exc:
            raise ScenarioError(f"{f} could not be loaded: {exc}") from exc
        if scenario.id in seen:
            raise ScenarioError(
                f"two scenarios share the id {scenario.id!r}: {seen[scenario.id]} and {f}. "
                "Baselines are keyed on the id, so one of these would be recorded and the "
                "other silently discarded after being replayed -- and every printed count "
                "would still claim both were measured. Give one of them a different `id`."
            )
        seen[scenario.id] = f
        out.append(scenario)
    return out
