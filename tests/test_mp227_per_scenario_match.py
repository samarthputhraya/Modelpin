"""MP-227 - a scenario whose tool call is genuinely optional can say so.

`--match` was a single global flag and `Scenario` had no `match` field, so a suite could
only be compared one way. That is the customer-facing half of MP-220: `[M] 2026-09-07`,
`reports/channel-exposure/2026-09-07/v2a-fp-suite-v2-gpt-4o-mini.jsonl`, the scenario
`optional_notify_after_status_update` tells the model of its second tool *"use it when it
would be useful"*, `gpt-4o-mini` sent the courtesy email on 4 of 5 baseline samples and 0 of 5
candidate samples, and the engine published `regression` @ 0.952 -- exit 1, the code
`action.yml` fails a pull request on -- over a SAME-MODEL, SAME-PROMPT null.

Two facts bound what this row was allowed to do, and both are asserted below rather than
described:

  * `[M]` Under `--match subset` that trial does not fire at all, today, with no engine
    change; and
  * `[M]` making `subset` the GLOBAL default exposes only 3 of the 10 detection rows, so the
    relation has to come from the scenario that holds it, not from a new default.

**Nothing here touches `modelpin/diff/`.** The engine is frozen (ADR-0030 D1) and MP-220 is
BLOCKED on a labelled calibration set that does not exist yet (MP-224); no threshold moves,
and nothing is chosen because it takes a scored corpus's alarm count to zero (ADR-0025).
`examples/fp-suite-v2` is role `score` and is NOT edited by this row -- the declaring scenario
below is built in memory with `model_copy`, so the published 1/26 (one-sided 95% upper bound
17.0%) stands exactly as it did, and `tests/test_mp220_tool_channel_false_positive.py` stays
`xfail(strict=True)`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import VALID_MATCH_MODES, _effective_match, _match_override_note, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.diff import diff_scenario
from modelpin.diff.structural import MatchMode
from modelpin.models import MATCH_MODES, DiffVerdict, MatchModeName, Scenario, Trace
from modelpin.report.suite import compute_suite_hash, scenario_fingerprint
from modelpin.scenarios import ScenarioError, load_scenarios

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

#: The MP-220 artifact and trial, named identically to the reproduction file so the two
#: cannot drift onto different evidence.
ARTIFACT = (
    ROOT / "reports" / "channel-exposure" / "2026-09-07" / "v2a-fp-suite-v2-gpt-4o-mini.jsonl"
)
TRIAL_KEY = "fp:optional_notify_after_status_update#6"
SCENARIO_ID = "optional_notify_after_status_update"
SCENARIOS_DIR = ROOT / "examples" / "fp-suite-v2"


def _base_scenario() -> Scenario:
    scenarios = {s.id: s for s in load_scenarios(SCENARIOS_DIR)}
    assert SCENARIO_ID in scenarios, f"{SCENARIO_ID} is not in {SCENARIOS_DIR}"
    return scenarios[SCENARIO_ID]


def _stored_traces() -> tuple[list[Trace], list[Trace]]:
    """The two recorded sides of the MP-220 trial, or a hard failure naming what is missing.

    Never defaults and never returns empty lists: a test that silently reads a missing
    artifact asserts nothing while reporting green.
    """
    assert ARTIFACT.is_file(), f"the MP-220 run of record is missing: {ARTIFACT}"
    for line in ARTIFACT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "trial" and record.get("key") == TRIAL_KEY:
            base = [Trace.model_validate(r) for r in record["base_traces"]]
            cand = [Trace.model_validate(r) for r in record["cand_traces"]]
            assert base and cand
            return base, cand
    raise AssertionError(f"{TRIAL_KEY} is not in {ARTIFACT.name}")


# --------------------------------------------------------------------------------------
# The acceptance criterion: the MP-220 traces, under a declaring scenario, offline.
# --------------------------------------------------------------------------------------


def test_the_mp220_false_positive_does_not_fire_when_the_scenario_declares_subset() -> None:
    """The row's own acceptance, on the stored traces, through the real engine.

    OFFLINE (ADR-0006): the traces come off the committed artifact, `judge=None`, no key, no
    network, no spend. The stored run DID have a judge and the semantic channel did not fire
    (`channels` on the flagged row is `["tool"]` alone), so dropping it leaves the tool
    channel's arithmetic bit-identical.
    """
    base, cand = _stored_traces()
    scenario = _base_scenario().model_copy(update={"match": "subset"})

    strict = diff_scenario("s", "gpt-4o-mini", "gpt-4o-mini", base, cand, scenario, "strict")
    assert strict.verdict is DiffVerdict.regression, (
        "the premise: under the global `strict` this same-model null is a hard, CI-failing "
        f"regression. It came back {strict.verdict.value!r}, so this test is no longer "
        "reproducing MP-220 and the artifact or the engine has moved."
    )

    declared = diff_scenario(
        "s",
        "gpt-4o-mini",
        "gpt-4o-mini",
        base,
        cand,
        scenario,
        _effective_match(scenario, "strict"),
    )
    assert declared.verdict is not DiffVerdict.regression, (
        "a scenario that tells the model to use a tool `when it would be useful` and then "
        "declares `match: subset` must not get a red build for the model taking it at its "
        f"word. Verdict was {declared.verdict.value!r} @ {declared.confidence:.3f}."
    )


def test_a_scenario_that_declares_nothing_still_gets_the_global_flag() -> None:
    """The other half of the acceptance: the default path is untouched.

    A per-scenario override that quietly changed undeclared scenarios would be a far worse
    defect than the one it fixes -- it would move the published bound (`examples/fp-suite-v2`
    is role `score`) without anyone choosing to.
    """
    base, cand = _stored_traces()
    scenario = _base_scenario()
    assert scenario.match is None, "the scored suite must not declare a mode; ADR-0025"

    for mode in VALID_MATCH_MODES:
        assert _effective_match(scenario, mode) == mode

    result = diff_scenario(
        "s",
        "gpt-4o-mini",
        "gpt-4o-mini",
        base,
        cand,
        scenario,
        _effective_match(scenario, "strict"),
    )
    assert result.verdict is DiffVerdict.regression
    assert round(result.confidence, 3) == 0.952
    assert result.signals.tool_call_match == 0.2


def test_the_declaration_overrides_the_global_flag_in_both_directions() -> None:
    """`_effective_match` is the whole rule, so pin it directly rather than only through a diff."""
    declaring = _base_scenario().model_copy(update={"match": "subset"})
    for mode in VALID_MATCH_MODES:
        assert _effective_match(declaring, mode) == "subset", (
            "a declared mode wins over every global flag, including a global `subset` "
            "(where the two agree) and a global `superset` (where they contradict)."
        )


# --------------------------------------------------------------------------------------
# The landmine: a comparison directive must not invalidate a paid-for baseline.
# --------------------------------------------------------------------------------------


def test_declaring_match_does_not_change_a_scenario_fingerprint() -> None:
    """ADR-0039 gates every comparison on the fingerprint, so this is a spend question.

    `scenario_fingerprint` answers *does this recorded baseline describe the scenario it is
    about to be compared against?* `match` cannot change a byte sent to a provider, so a
    baseline recorded before a declaration still describes it exactly. Were it included, the
    user who adds `"match": "subset"` to stop a false red build would be told every scenario
    in their store is stale and asked to pay to re-record traces that were never wrong --
    i.e. the fix for MP-220 would bill them for using it.
    """
    plain = _base_scenario()
    for mode in VALID_MATCH_MODES:
        declared = plain.model_copy(update={"match": mode})
        assert scenario_fingerprint(declared) == scenario_fingerprint(plain), (
            f"declaring `match: {mode}` moved the fingerprint, so every existing baseline for "
            "this scenario would be reported stale and skipped."
        )
        assert compute_suite_hash([declared]) == compute_suite_hash([plain])


def test_the_optional_field_did_not_move_any_existing_fingerprint() -> None:
    """The upgrade path: a `None`-valued field must not restate every hash in the world.

    `compute_suite_hash` dumps the validated model, and pydantic serialises an unset
    `Optional` as an explicit `null` -- so a field added without the exclusion would have put
    `"match": null` into EVERY scenario's canonical JSON and marked EVERY baseline on disk
    stale on upgrade. `mp check` would then abstain for every user who did nothing at all.
    This pins the published hash of the public report suite, which is a value strangers cite.
    """
    suite = load_scenarios(ROOT / "examples" / "report-suite")
    assert suite, "the public report suite must load"
    assert all(s.match is None for s in suite)
    # The literal published for `modelpin-public-v2` v3.0.0 -- it is written into
    # `examples/report-suite/manifest.json`'s own description, cited in every Report this
    # repo has produced, and pinned independently at `tests/test_cli.py:315`. Hard-coded
    # rather than recomputed: a test that recomputes the value it is checking would pass
    # through exactly the change it exists to catch.
    assert compute_suite_hash(suite) == "sha256:5cba1dc8b691"


def test_the_baseline_survives_a_declaration_added_after_it_was_recorded(tmp_path) -> None:
    """The actual user story, end to end and offline: hit a red build, declare, re-run.

    Deliberately does NOT re-baseline between the two `check`s. That is the whole point --
    the second run reuses traces recorded before the declaration existed, which only works
    because the fingerprint excludes `match`.
    """
    write_demo(tmp_path)
    demo = tmp_path / DEMO_DIRNAME
    store = str(tmp_path / ".modelpin")
    common = [
        "--provider", "fake",
        "--fixtures", str(demo / DEMO_FIXTURES),
        "--scenarios-dir", str(demo / "scenarios"),
        "--config", str(demo / "modelpin.yaml"),
        "--store-dir", store,
        "--runs", "5",
    ]  # fmt: skip

    assert runner.invoke(app, ["baseline", "--model", DEMO_FROM, *common]).exit_code == 0

    before = runner.invoke(app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *common])
    assert before.exit_code == 1, before.output
    assert "REGRESSION refund_request" in before.output, before.output

    # `demo-model-v2` calls `lookup_order` twice where v1 called it once, so the candidate
    # trajectory is a SUPERSET of the baseline's -- the relation the demo scenario would
    # declare if the extra lookup were acceptable to its author.
    path = demo / "scenarios" / "refund_request.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["match"] = "superset"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    after = runner.invoke(app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *common])
    assert "no baseline" not in after.output.lower(), (
        "the declaration invalidated the recorded baseline: " + after.output
    )
    assert "REGRESSION refund_request" not in after.output, after.output
    # The declaration is scoped to the one scenario. `angry_customer` regresses on the
    # refusal channel and must be entirely unaffected, exit code included.
    assert "REGRESSION angry_customer" in after.output, after.output
    assert after.exit_code == 1, after.output


# --------------------------------------------------------------------------------------
# Disclosure: a run that used two modes must not publish one.
# --------------------------------------------------------------------------------------


def test_the_run_header_names_every_scenario_that_overrode_the_flag() -> None:
    plain = _base_scenario()
    declaring = plain.model_copy(update={"id": "opt_subset", "match": "subset"})
    other = plain.model_copy(update={"id": "opt_superset", "match": "superset"})

    assert _match_override_note([plain], "strict") == ""
    note = _match_override_note([plain, declaring, other], "strict")
    assert "opt_subset" in note and "subset" in note
    assert "opt_superset" in note and "superset" in note
    assert plain.id not in note, "a scenario using the global flag is not an override"


def test_a_declaration_that_agrees_with_the_global_flag_is_not_reported_as_an_override() -> None:
    declaring = _base_scenario().model_copy(update={"match": "subset"})
    assert _match_override_note([declaring], "subset") == ""
    assert _effective_match(declaring, "subset") == "subset"


def test_the_published_report_names_the_overrides_in_its_settings_table() -> None:
    """The Report is an ADR-0009 surface: "under these settings, we observed...".

    Publishing `strict` over a run where a scenario used `subset` is a false statement about
    the settings, and it is not recoverable from the suite hash either -- `compute_suite_hash`
    excludes `match` on purpose (see the fingerprint test above). The settings table is
    therefore the only place the reader can learn it, so it must say so.
    """
    from modelpin.report import ReportMeta, render_report_md

    def _meta(**over):
        base = dict(
            suite_id="s", suite_version="1", suite_hash="sha256:abc", suite_path="p",
            candidate_model="m2", reference_model="m1", provider="fake", runs=5,
            judge_model="disabled", match_mode="strict", modelpin_version="0.3.0",
            diff_thresholds={"alpha": 0.05, "min_tool_tvd": 0.5, "min_refusal_delta": 0.3,
                             "min_semantic_delta": 0.25, "min_tool_arg_tvd": 0.5},
            date_iso="2026-09-08", reproduce_cmd="mp report",
        )  # fmt: skip
        base.update(over)
        return ReportMeta(**base)

    plain = render_report_md([], _meta())
    assert "| Tool-call match mode | `strict` |" in plain, plain

    with_override = render_report_md(
        [], _meta(match_overrides={"optional_notify_after_status_update": "subset"})
    )
    assert "`optional_notify_after_status_update` (`subset`)" in with_override
    assert "except where a scenario declares its own" in with_override


# --------------------------------------------------------------------------------------
# The boundary: a bad value fails at load, before anything is replayed or spent.
# --------------------------------------------------------------------------------------


def test_an_unknown_match_in_a_scenario_file_is_refused_and_names_the_file(tmp_path) -> None:
    """Fail fast at the CLI boundary, the same contract `--match` already has.

    The failure must reach the user BEFORE a provider call: a scenario dict is loaded once,
    up front, and every replay downstream of it is billable.
    """
    (tmp_path / "bad.json").write_text(
        json.dumps(
            {
                "id": "bad",
                "name": "bad",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "match": "loose",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError) as exc:
        load_scenarios(tmp_path)
    message = str(exc.value)
    assert "bad.json" in message
    assert "match" in message
    for mode in VALID_MATCH_MODES:
        assert mode in message, f"the error must name the accepted modes; {mode!r} is missing"


@pytest.mark.parametrize("mode", VALID_MATCH_MODES)
def test_every_accepted_cli_mode_is_also_accepted_in_a_scenario_file(mode, tmp_path) -> None:
    (tmp_path / "s.json").write_text(
        json.dumps(
            {
                "id": "s",
                "name": "s",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "match": mode,
            }
        ),
        encoding="utf-8",
    )
    (loaded,) = load_scenarios(tmp_path)
    assert loaded.match == mode


def test_the_three_match_mode_declarations_agree() -> None:
    """One list of four names now has three homes, and the engine's copy cannot be imported.

    `diff/structural.py::MatchMode` is the engine's, and `models.py` cannot import it --
    `structural.py` imports `Trace` from `models`, so the arrow points one way only. That
    mirror is the drift MP-03 (three copies of `DEFAULT_RUNS`) and MP-204 (two copies of the
    version) each cost a session to unpick, so it is pinned rather than trusted. Adding a mode
    to the engine and not to `Scenario.match` would make it undeclarable; adding it to
    `Scenario.match` and not the engine would let a scenario file name a mode the diff cannot
    execute.
    """
    engine = get_args(MatchMode)
    assert get_args(MatchModeName) == engine
    assert tuple(MATCH_MODES) == engine
    assert tuple(VALID_MATCH_MODES) == engine
    assert (
        cli.VALID_MATCH_MODES is MATCH_MODES
    ), "`cli.VALID_MATCH_MODES` must alias the single declaration, not re-list it."
