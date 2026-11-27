"""MP-248: no scenario in a SCORED set declares a per-scenario `match` mode.

`[M] 2026-09-12` FP review: on the CLI path, which honours a scenario's `match`, a
`match: subset` makes the tool channel read `unchanged @ 1.000`. A trial that reads that way is
what `scripts/fp_measurement.py` classifies as *could not have fired*, and ADR-0022 drops it
from the denominator. The published false-positive rate would improve with no engine change --
the `0/0` shape ADR-0041 D2 exists to prevent, reached from the corpus side. (`[M] 2026-11-27`
the measurement script itself scores in `strict` mode today and does not read `match`; this
guard keeps the scored corpus safe for every path that does.)

`tests/test_mp227_per_scenario_match.py` guarded `fp-suite-v2` and `report-suite` by name; the
other scored sets were open. This derives the list from `examples/roles.json`, which every new
set must join (`tests/test_suite_roles.py`), so the next scored set is covered the day it lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelpin.scenarios import load_scenarios

REPO = Path(__file__).resolve().parents[1]
ROLES = REPO / "examples" / "roles.json"


def _scored_sets() -> list[dict]:
    sets = json.loads(ROLES.read_text(encoding="utf-8"))["sets"]
    return [s for s in sets if s["role"] == "score"]


def test_there_are_scored_sets_to_check() -> None:
    assert len(_scored_sets()) >= 5, "roles.json lists fewer scored sets than it did; check it"


@pytest.mark.parametrize(
    "entry", _scored_sets(), ids=lambda e: f"{e['path']}:{len(e['scenarios'])}"
)
def test_no_scored_scenario_declares_a_match_mode(entry: dict) -> None:
    loaded = {s.id: s for s in load_scenarios(REPO / "examples" / entry["path"])}
    listed = set(entry["scenarios"])
    missing = listed - loaded.keys()
    assert not missing, f"roles.json lists scenarios {sorted(missing)} that do not load"
    declared = sorted(sid for sid in listed if loaded[sid].match is not None)
    assert not declared, (
        f"scored set {entry['path']!r} declares a per-scenario `match` on {declared}. On a "
        "scored scenario that moves trials out of the false-positive denominator without any "
        "engine change (MP-248, ADR-0022). Move the scenario to a fit set, or remove `match`."
    )
