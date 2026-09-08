"""MP-220 - the tool channel manufactures a `regression` on a same-model null.

`[M] 2026-09-07` The run of record is `reports/channel-exposure/2026-09-07/`, produced by
`scripts/channel_exposure.py` over `scripts/fp_measurement.py`'s FP arm. That arm is a NULL
BY CONSTRUCTION, in two independent places:

  * `plan_trials` (`scripts/fp_measurement.py:806`) appends
    `(trial_key(scn, scn, sid), sid, scn, scn)` - the SAME `Scenario` OBJECT as baseline and
    as candidate, identity not equality, so the two sides cannot differ in a single prompt
    byte; and
  * `_verdict_live` (`scripts/fp_measurement.py:1464`) calls
    `diff_scenario(sid, args.model, args.model, ...)` - the SAME model id on both sides.

Any verdict other than `unchanged` on an `arm == "fp"` row is therefore a false positive by
construction. It is not a judgement call and there is no reading of the data that rescues it.

Three rows of 480 came back `regression`; this is the one the TOOL channel produced. (The other
two, `plaintext_answer_bold_optional#2` on v2b and `sql_answer_fence_unspecified#6` on v2d, are
semantic-channel alarms on scenarios that make no tool calls at all -- `tool_call_match 1.0` on
both -- and belong to ADR-0040's territory, not this row's. `[M] 2026-09-08, FP review`: the
pooled v2a-d FP rate is 3/101; the TOOL-channel rate is 1/26.)

    artifact   v2a-fp-suite-v2-gpt-4o-mini.jsonl
    key        fp:optional_notify_after_status_update#6
    baseline   4 of 5 runs ['update_order_status', 'notify_customer']
               1 of 5 runs ['update_order_status']
    candidate  5 of 5 runs ['update_order_status']
    verdict    regression @ confidence 0.952, tool_call_match 0.20 -> exit 1

The scenario's own system prompt says of the second tool: "notify_customer emails the
customer; use it when it would be useful." The call is OPTIONAL, the model exercised the
discretion it was handed, and `gpt-4o-mini` sent the courtesy email on 4 of 5 samples in one
window and on 0 of 5 in the next. `[M]` `tool_tvd` = 0.8 clears `MIN_TOOL_TVD` (0.5) and the
permutation test returns p = 0.047619047619047616 <= `ALPHA` (0.05), so `tool_regressed` in
`modelpin/diff/__init__.py` fires and the verdict block promotes it to a hard, CI-failing
`regression`. That is the north-star promise - "if Modelpin says it broke, it broke" -
inverted, on the commonest agent shape there is, and it is the exit code the GitHub Action
fails a pull request on.

OFFLINE (ADR-0006): the traces come off the committed artifact, `judge=None`, no API key, no
network, no spend. The judge is not needed to see this: the stored run DID have one
(`semantic_score` 0.6) and the semantic channel did not fire - `channels` on the stored
`flagged` row is `["tool"]` alone - so dropping it leaves the tool channel's arithmetic and
the published verdict bit-identical.

STATUS. MP-220 is OPEN and **BLOCKED**, not merely unfixed. `[M] 2026-09-08, the FP review`
rejected both candidate engine rules on measured evidence, and the reasons bind anyone who
picks this up:

  * **0 of 37** tool-exposed FP-arm trials have disjoint trajectory key sets, so a
    disjointness precondition makes this gate structurally incapable of a hard alarm on 100%
    of the trials where it has ever met a null. ADR-0038 D3's per-channel bound becomes `0/0`.
  * Its detection probability is `q**N` — **strictly decreasing in N** (0.590 at N=5 -> 0.349
    at N=10 for q=0.9). That inverts ADR-0016 and `stats.py`'s *"N is a correctness input, not
    a cost dial"*: buying more runs would make the engine LESS likely to see a real regression.
  * Demoting to `changed_minor` does not close this row either — `fp_measurement.py:127` counts
    `changed_minor` as a false alarm, so 1/26 is unmoved. It buys the exit code only (MP-223).
  * **Choosing any rule because it takes this corpus's alarms from 1 to 0 is fitting on a
    `score` set** (ADR-0025 / ADR-0038). That is the block that does not negotiate; the rule
    must be chosen on a labelled set that does not exist yet (MP-224).

So this file pins the defect and is expected to stay `xfail` for some time. The customer-facing
half is MP-227 (a per-scenario `match` mode, outside `modelpin/diff/`), which does not change
what this test measures: `examples/fp-suite-v2` is `score` under ADR-0025 and stays `strict`.

The repro below is `xfail(strict=True)`, which is this repo's
convention for a conceded defect: the same shape as MP-165 in
`tests/test_census_from_traces.py:291`, and as the three markers MP-05 carried for eleven
days that `tests/test_baseline_scenario_collision.py:27` records. When the fix lands this
test XPASSes, the suite goes red, and whoever landed it is told to delete the marker.

`[!]` Re-score, do not spot-fix. The bound this row moves is 1 false alarm in 26 SCORED
tool-exposed trials (one-sided 95% upper bound 17.0%); the other 25 are listed in MP-220's
report and any change to the tool channel must be re-scored against all of them, plus the
40/40 `anchor_mandatory_lookup_fixed_format` negative control, before it is believed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelpin.diff import diff_scenario
from modelpin.models import DiffVerdict, Trace
from modelpin.scenarios import load_scenarios

ROOT = Path(__file__).resolve().parent.parent

#: Surface v2a of the run of record: gpt-4o-mini vs itself, runs 5, repeats 10, git_sha 202274b.
ARTIFACT = (
    ROOT / "reports" / "channel-exposure" / "2026-09-07" / "v2a-fp-suite-v2-gpt-4o-mini.jsonl"
)

#: The one tool-channel false alarm among the 26 scored tool-exposed trials.
TRIAL_KEY = "fp:optional_notify_after_status_update#6"

SCENARIO_ID = "optional_notify_after_status_update"

SCENARIOS_DIR = ROOT / "examples" / "fp-suite-v2"

#: `match` off the artifact header - the mode the run of record was actually scored under.
#: Read from the header rather than defaulted, because `--match subset` is one of the
#: candidate fixes and a test that silently defaults would score the wrong engine.
MATCH_MODE = "strict"

#: What the model actually did, per run, on each side. Transcribed from the artifact and
#: re-asserted below, so that a re-recorded or truncated artifact fails LOUDLY instead of
#: quietly changing what this file reproduces.
BASELINE_TRAJECTORIES = [
    ["update_order_status", "notify_customer"],
    ["update_order_status", "notify_customer"],
    ["update_order_status", "notify_customer"],
    ["update_order_status"],
    ["update_order_status", "notify_customer"],
]
CANDIDATE_TRAJECTORIES = [["update_order_status"] for _ in range(5)]


def _stored_trial() -> dict:
    """The one stored trial, or a hard failure naming exactly what is missing.

    Never returns a default and never returns `None`. A reproduction that silently reads a
    missing artifact asserts nothing, and under `xfail(strict=True)` it would then report as
    a healthy `xfailed` forever - a conceded bug that had stopped being reproduced at all.
    """
    if not ARTIFACT.is_file():
        raise AssertionError(f"the MP-220 run of record is missing: {ARTIFACT}")
    for line in ARTIFACT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "trial" and record.get("key") == TRIAL_KEY:
            return record
    raise AssertionError(f"{TRIAL_KEY} is not in {ARTIFACT.name}")


def _traces(rows: list[dict]) -> list[Trace]:
    return [Trace.model_validate(row) for row in rows]


def _trajectories(traces: list[Trace]) -> list[list[str]]:
    return [[call.name for call in trace.tool_calls] for trace in traces]


def test_the_flagged_trial_is_a_genuine_same_model_null() -> None:
    """The premise, pinned separately so the repro below cannot pass vacuously.

    This test is deliberately NOT xfail: it asserts what is true today and must stay true,
    namely that the trial MP-220 rests on really is a null - same model, same scenario, both
    sides - and that the stored verdict really is `regression`. If the artifact is ever
    re-recorded, this goes red and names the drift rather than letting the xfail below
    quietly reproduce nothing.
    """
    record = _stored_trial()
    assert record["arm"] == "fp", (
        "MP-220 rests on the FP arm, whose two sides are the same Scenario object. "
        f"This row is arm={record['arm']!r}."
    )
    assert record["error"] is None

    result = record["result"]
    assert result["from_model"] == result["to_model"] == "gpt-4o-mini", (
        "a false positive needs a null: from_model and to_model must be the same model. "
        f"Got {result['from_model']!r} -> {result['to_model']!r}."
    )

    base = _traces(record["base_traces"])
    cand = _traces(record["cand_traces"])
    assert {t.model_id for t in base} == {"gpt-4o-mini"}
    assert {t.model_id for t in cand} == {"gpt-4o-mini"}
    assert {t.scenario_id for t in base} == {t.scenario_id for t in cand} == {SCENARIO_ID}
    assert not any(t.refused for t in base + cand), "no run refused; this is not that channel"

    assert _trajectories(base) == BASELINE_TRAJECTORIES
    assert _trajectories(cand) == CANDIDATE_TRAJECTORIES

    assert result["verdict"] == "regression"
    assert round(result["confidence"], 3) == 0.952
    assert result["signals"]["tool_call_match"] == 0.2


@pytest.mark.xfail(
    strict=True,
    reason="MP-220: an OPTIONAL tool call the model makes on 4 of 5 baseline samples and 0 of "
    "5 candidate samples clears MIN_TOOL_TVD (0.8 >= 0.5) at p = 0.0476 <= ALPHA, so the tool "
    "channel publishes a hard `regression` @ 0.952 and exit 1 over a SAME-MODEL, SAME-PROMPT "
    "null. Options are priced in the MP-220 backlog row; the fix must be re-scored against all "
    "26 scored tool-exposed trials AND the 40/40 anchor control. Delete this marker when "
    "MP-220 lands.",
)
def test_an_optional_tool_call_must_not_manufacture_a_regression_on_a_null() -> None:
    """The reproduction: the stored trajectories, through the real engine, offline.

    Reconstructed from the per-run traces rather than by re-deriving a number by hand, so it
    exercises the same `modelpin/diff/` path that produced the published verdict: the
    equivalence-mode branch keys each run with `canonical_sequence`, `tool_tvd` comes from
    `total_variation_distance` and `tool_p` from `permutation_pvalue_distribution`.
    """
    record = _stored_trial()
    base = _traces(record["base_traces"])
    cand = _traces(record["cand_traces"])
    scenario = {s.id: s for s in load_scenarios(SCENARIOS_DIR)}[SCENARIO_ID]

    result = diff_scenario(
        record["sid"],
        "gpt-4o-mini",
        "gpt-4o-mini",
        base,
        cand,
        scenario,
        mode=MATCH_MODE,
        judge=None,
    )

    assert result.verdict is not DiffVerdict.regression, (
        "MP-220: same model, same prompt, both sides - so this is a FALSE POSITIVE on the "
        "north-star metric. The engine reports "
        f"{result.verdict.value!r} @ confidence {result.confidence} "
        f"(tool_call_match {result.signals.tool_call_match}): {result.explanation}. "
        "The only difference between the sides is that the model chose to send the OPTIONAL "
        "courtesy email on 4 of 5 baseline samples and on 0 of 5 candidate samples. "
        "`regression` is the CI-failing verdict; a null must never reach it."
    )
