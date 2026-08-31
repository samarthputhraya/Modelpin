"""A run that could not have reported a regression must not claim it found none (MP-55).

`[M] 2026-08-26` reproduced on the SHIPPED demo, offline, before the fix:

    $ mp check ... --runs 2
    warning: only 2 runs/scenario; 5+ gives the statistical diff real power ...
    OK 4 scenario(s) unchanged                                          exit 0
    last-report.md: "-> No behavioral regressions found; `demo-model-v2` looks safe to adopt."

    $ mp check ... --runs 5          # THE SAME FIXTURES
    REGRESSION angry_customer   REGRESSION refund_request   MINOR invoice_parse

Two regressions and a minor, present in the data both times, reported as "safe to adopt"
because an exact permutation test over C(4,2) = 6 relabelings has a hard p-floor of 1/6 =
0.167 and cannot return anything at or below ALPHA = 0.05. The warning said the run had less
POWER; it did not say the run was incapable of a conclusion, and the report said the opposite.

This is the north-star failure from the false-NEGATIVE side, on the first-run path, in the
demo a brand-new user runs -- and it is the same rule ADR-0018 already draws for a run that
measured nothing, applied one level up to a run that measured but could not conclude.

Deliberately NOT changed here: `MIN_RUNS`, the effect-size floors, and the exit code. Those
are sensitivity surfaces (ADR-0016, ADR-0002) and moving one needs its own calibration and
its own FP review review. This governs only what the tool CLAIMS.
"""

from __future__ import annotations

import json
import re
import tempfile
from math import comb
from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import RECOMMENDED_RUNS, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.diff import ALPHA
from modelpin.diff.stats import (
    min_achievable_pvalue_distribution,
    min_achievable_pvalue_mean,
)
from modelpin.models import DiffResult, DiffVerdict, Trace
from modelpin.report import _RECOMMENDED_RUNS as _REPORT_RECOMMENDED_RUNS
from modelpin.report import render_cli, render_pr_comment
from modelpin.storage import load_baseline, nonuniform_run_counts, save_baseline

_DEMO_ROOT = Path(tempfile.mkdtemp(prefix="modelpin-power-demo-"))
write_demo(_DEMO_ROOT)
_DEMO = _DEMO_ROOT / DEMO_DIRNAME
FIXTURES = str(_DEMO / DEMO_FIXTURES)
SCEN = str(_DEMO / "scenarios")
CONFIG = str(_DEMO / "modelpin.yaml")

runner = CliRunner()


# --- the floors themselves ----------------------------------------------------------------


def test_the_floors_are_the_exact_combinatorial_minimum():
    """Not an approximation and not a fitted constant: an exact permutation test over
    `C(nb+nc, nc)` relabelings cannot score below `1/C` one-sided, or `2/C` two-sided."""
    for n in range(2, 9):
        total = comb(2 * n, n)
        assert min_achievable_pvalue_mean(n, n) == 1 / total, n
        assert min_achievable_pvalue_distribution(n, n) == 2 / total, n
    # ...but the 2x relation is DIAGONAL-ONLY. `[M]` With unequal counts no mirror labelling
    # exists at the required group sizes and the two floors coincide. Restating "twice" in
    # general `nb, nc` notation -- which an earlier draft of this file and of stats.py both
    # did -- flips the `> ALPHA` answer at 5v2, a regime a stored baseline reaches routinely.
    assert min_achievable_pvalue_distribution(5, 2) == 1 / comb(7, 2)
    assert min_achievable_pvalue_distribution(5, 2) == min_achievable_pvalue_mean(5, 2)


def test_no_signal_can_fire_at_two_runs_a_side():
    """The MP-55 headline. At N=2 the product cannot report a regression, ever."""
    assert min_achievable_pvalue_mean(2, 2) > ALPHA
    assert min_achievable_pvalue_distribution(2, 2) > ALPHA


def test_at_three_runs_the_tool_signal_is_dead_but_refusal_is_not():
    """The dead zone is MODE-dependent, and this asymmetry is load-bearing: `strict` and
    `unordered` route the tool/argument signals through the two-sided distribution test
    (floor 2/C), while refusal and format drift use the one-sided mean test (floor 1/C)."""
    assert min_achievable_pvalue_mean(3, 3) == ALPHA  # fires, exactly at the boundary
    assert min_achievable_pvalue_distribution(3, 3) > ALPHA  # cannot fire


def test_the_floor_depends_on_BOTH_sides_not_on_the_candidate_alone():
    """`baseline --runs 5` then `check --runs 2` gives 5 vs 2 -- a real, reachable regime
    that a floor computed from `--runs` alone would price wrong."""
    assert min_achievable_pvalue_mean(5, 2) != min_achievable_pvalue_mean(2, 2)
    assert min_achievable_pvalue_mean(5, 2) == 1 / comb(7, 2)


def test_the_floors_are_measured_from_the_real_test_not_restated():
    """The helpers drive the shipped permutation functions on their most extreme input, so a
    change to the test's tie handling or epsilon moves the published floor with it. A
    hand-written formula would silently keep agreeing with a test that had changed."""
    from modelpin.diff.stats import permutation_pvalue_mean

    assert min_achievable_pvalue_mean(4, 4) == permutation_pvalue_mean([0.0] * 4, [1.0] * 4)


# --- what the tool is then allowed to say -------------------------------------------------


def _unchanged(sid):
    return DiffResult(
        scenario_id=sid,
        from_model="a",
        to_model="b",
        verdict=DiffVerdict.unchanged,
        explanation="no statistically significant behavior change",
        confidence=1.0,
    )


def test_an_all_underpowered_run_never_says_safe_to_adopt():
    results = [_unchanged("s1"), _unchanged("s2")]
    md = render_pr_comment(results, "a", "b", 2, None, ["s1", "s2"])
    assert "safe to adopt" not in md, md
    assert "NOT cleared" in md, md
    assert "could not have reported a regression" in md, md


def test_an_all_underpowered_run_does_not_lead_with_a_green_header():
    """A green tick over a run that could not have gone red is the worst header we ship."""
    results = [_unchanged("s1")]
    md = render_pr_comment(results, "a", "b", 2, None, ["s1"])
    assert "no behavioral change" not in md.lower(), md
    assert "could not measure" in md.lower(), md


def test_the_unchanged_bucket_does_not_wear_a_green_tick_over_blind_scenarios():
    """The footer and the bucket label must not contradict each other."""
    md = render_pr_comment([_unchanged("s1")], "a", "b", 2, None, ["s1"])
    assert "**UNCHANGED (1)** ✅" not in md, md
    assert "could not have reported a regression at this run count" in md, md


def test_a_partially_underpowered_run_is_only_partially_cleared():
    results = [_unchanged("s1"), _unchanged("s2"), _unchanged("s3")]
    md = render_pr_comment(results, "a", "b", 3, None, ["s3"])
    assert "safe to adopt" not in md, md
    assert "partially cleared" in md, md
    assert "2 scenario(s) this run could measure" in md, md


def test_a_fully_powered_run_is_unchanged_by_this_feature():
    """Backwards compatibility, stated as a test: with no underpowered scenarios the
    rendering must be byte-identical to what shipped before MP-55."""
    results = [_unchanged("s1"), _unchanged("s2")]
    assert render_pr_comment(results, "a", "b", 5, None, []) == render_pr_comment(
        results, "a", "b", 5, None
    )
    assert "safe to adopt" in render_pr_comment(results, "a", "b", 5, None)
    assert render_cli(results, "a", "b", 5, []) == render_cli(results, "a", "b", 5)


def test_the_cli_summary_never_prints_OK_over_a_blind_scenario():
    cli = render_cli([_unchanged("s1"), _unchanged("s2")], "a", "b", 2, ["s1"])
    assert "NOT a clean result" in cli, cli
    assert "1 scenario(s) unchanged" in cli, cli  # the other one is still genuinely OK


# --- end to end, on the demo a new user actually runs -------------------------------------


def _demo_check(tmp_path, n, *, baseline_runs=None):
    store = str(tmp_path / f"store{n}")
    common = [
        "--provider",
        "fake",
        "--fixtures",
        FIXTURES,
        "--scenarios-dir",
        SCEN,
        "--config",
        CONFIG,
        "--store-dir",
        store,
    ]
    base = runner.invoke(
        app, ["baseline", "--model", DEMO_FROM, *common, "--runs", str(baseline_runs or n)]
    )
    assert base.exit_code == 0, base.output
    chk = runner.invoke(
        app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *common, "--runs", str(n)]
    )
    report = (Path(store) / "last-report.md").read_text(encoding="utf-8")
    return chk, report


def test_the_shipped_demo_at_two_runs_does_not_clear_the_candidate(tmp_path):
    """The reproduction, pinned. Before MP-55 this printed `OK 4 scenario(s) unchanged` and
    wrote "looks safe to adopt"."""
    chk, report = _demo_check(tmp_path, 2)
    assert "safe to adopt" not in report, report
    assert "NOT cleared" in report, report
    assert "OK 4 scenario(s) unchanged" not in chk.output, chk.output


def test_the_same_fixtures_at_five_runs_find_what_two_runs_could_not(tmp_path):
    """Without this the guard above is unfalsifiable -- "not cleared" would be trivially
    correct if there were nothing to find. There is: two regressions and a minor."""
    chk, report = _demo_check(tmp_path, 5)
    assert chk.exit_code == 1, chk.output
    assert report.count("❌") == 2, report
    assert "safe to adopt" not in report


def test_the_spend_disclosure_says_the_run_cannot_conclude_before_it_spends(tmp_path):
    """ADR-0019 discloses the run SIZE before spending. A size that cannot reach ALPHA is
    the same disclosure one level up, and "less power" does not say it."""
    chk, _ = _demo_check(tmp_path, 2)
    out = " ".join(chk.output.split())
    assert "cannot report a regression" in out, chk.output
    assert "0.167" in out, chk.output


def test_three_runs_warns_that_the_tool_signal_specifically_is_dead(tmp_path):
    """N=3 is the trap: refusal and format drift still fire, so the run looks alive while
    the signal the product is NAMED for cannot reach ALPHA in the default match mode."""
    chk, _ = _demo_check(tmp_path, 3)
    out = " ".join(chk.output.split())
    assert "tool-call and argument signals cannot reach" in out, chk.output
    assert "0.100" in out, chk.output


def test_an_underpowered_run_still_exits_zero(tmp_path):
    """FP-neutral, deliberately. Turning this into a non-zero exit is a sensitivity change
    that would fail builds on a configuration users are free to choose, and it belongs to
    MP-59's exit-code redesign -- not to a change about what the tool CLAIMS."""
    chk, _ = _demo_check(tmp_path, 2)
    assert chk.exit_code == 0, chk.output


def test_an_unequal_baseline_and_candidate_are_judged_on_the_pooled_floor(tmp_path):
    """`baseline --runs 5` then `check --runs 2` is 5v2, whose floor is 1/C(7,2) = 0.0476 --
    BELOW alpha. A blindness test keyed on `--runs` alone would wrongly declare this run
    incapable and refuse to clear a candidate it genuinely measured."""
    assert min_achievable_pvalue_mean(5, 2) < ALPHA
    chk, report = _demo_check(tmp_path, 2, baseline_runs=5)
    # `assert "NOT cleared" not in report` was VACUOUS here and is the exact hazard this file
    # exists to catch: `[M]` the 5v2 demo yields 2 regressions + 1 minor, so the renderer takes
    # the `if regs or minors:` branch and `_underpowered_clearance` is never called at all.
    # Assert the affirmative property on a scenario that really is measurable instead.
    assert "**UNCHANGED (1)** ✅" in report, report
    out = " ".join(chk.output.split())
    # This test is about the RUN-COUNT axis, so assert that axis and nothing else: the
    # `??` marker is the one that means "reported unchanged at a run count that could not
    # reach significance", and 5v2 clears the pooled floor, so it must not appear.
    assert "??" not in out, out
    assert "1 scenario(s) unchanged" in out, chk.output
    # `[M] 2026-08-31` MP-141: the marker is `OK?`, not `OK`, and that is CORRECT and not a
    # run-count statement. The surviving unchanged scenario is `greeting`, which declares no
    # `tools` and runs with the judge off, so its only CI-failing channel is refusal -- a
    # green tick over it is the false comfort the per-scenario census exists to remove.
    # Asserting `OK ` here again would re-pin the defect this row fixed.
    assert "OK? 1 scenario(s) unchanged" in out, chk.output


def test_the_written_report_is_valid_json_free_markdown_at_every_run_count(tmp_path):
    """Cheap total check that neither branch emits a broken document."""
    for n in (2, 3, 5):
        _, report = _demo_check(tmp_path, n)
        assert report.startswith(("🚨", "❔", "⚠️", "✅")), (n, report[:80])
        assert json.dumps(report)  # no lone surrogates / control chars


# --- the three claims the first draft of this fix got wrong --------------------------------
#
# `[M] 2026-08-26`, all three reproduced by the review gates on the first version of MP-55.
# They are the same defect class the branch exists to eliminate -- a confident statement that
# the code does not keep -- committed while fixing it.


def _tool_only_sandbox(tmp_path):
    """The demo trimmed to `refund_request`, whose ONLY difference between the two models is
    the tool-call trajectory. Its output text is identical, so no one-sided signal can fire
    and the two-sided distribution floor alone decides whether this run can conclude."""
    scen = tmp_path / "scen"
    scen.mkdir()
    src = Path(SCEN) / "refund_request.json"
    (scen / "refund_request.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(scen)


def _check(tmp_path, scen, n, *, baseline_runs=None, mode="strict", tag=""):
    store = str(tmp_path / f"s{tag}{n}{mode}")
    common = [
        "--provider",
        "fake",
        "--fixtures",
        FIXTURES,
        "--scenarios-dir",
        scen,
        "--config",
        CONFIG,
        "--store-dir",
        store,
    ]
    base = runner.invoke(
        app, ["baseline", "--model", DEMO_FROM, *common, "--runs", str(baseline_runs or n)]
    )
    assert base.exit_code == 0, base.output
    chk = runner.invoke(
        app,
        ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *common, "--runs", str(n), "--match", mode],
    )
    return chk, (Path(store) / "last-report.md").read_text(encoding="utf-8")


def test_the_spend_warning_is_priced_on_the_pooled_floor_not_the_candidate_alone(tmp_path):
    """`[M]` Priced on `(n, n)` this printed "This run cannot report a regression, whatever
    the models do" over `baseline --runs 5` + `check --runs 2` -- and the same invocation then
    reported two regressions and exited 1. The real 5v2 floor is 0.047619, BELOW alpha."""
    assert min_achievable_pvalue_mean(5, 2) < ALPHA < min_achievable_pvalue_mean(2, 2)
    chk, _ = _demo_check(tmp_path, 2, baseline_runs=5)
    out = " ".join(chk.output.split())
    assert "cannot report a regression" not in out, out
    assert chk.exit_code == 1, out  # and it demonstrably CAN: it reported them


def test_the_warning_never_contradicts_a_verdict_in_its_own_output(tmp_path):
    """`[M]` Under `--match subset --runs 3` the old text said the tool-call signal "cannot
    reach p <= 0.05" and the very next lines reported a `subset` tool-call regression. The
    directional modes route the tool and argument signals through the ONE-sided statistic,
    whose floor at N=3 is exactly ALPHA."""
    for mode in ("subset", "superset"):
        chk, _ = _check(tmp_path, SCEN, 3, mode=mode, tag="c")
        out = " ".join(chk.output.split())
        if "REGRESSION" in out:
            assert "cannot reach" not in out, out
            assert "cannot report a regression" not in out, out


def test_a_tool_only_scenario_at_three_runs_is_not_declared_safe_to_adopt(tmp_path):
    """The narrower trap, and a MORE confident false clearance than the N=2 one this file
    opens with. `[M]` At 3v3 `strict` the first version of this fix rendered
    `✅ no behavioral change` / "looks safe to adopt" over `refund_request`, while N=4 on
    identical fixtures reports the regression. The blind predicate consulted only the
    one-sided floor; the signal that was dead is gated by the two-sided one."""
    assert min_achievable_pvalue_distribution(3, 3) > ALPHA
    scen = _tool_only_sandbox(tmp_path)

    chk3, report3 = _check(tmp_path, scen, 3, tag="t")
    assert "safe to adopt" not in report3, report3
    assert "NOT cleared" in report3, report3
    assert "OK 1 scenario(s) unchanged" not in " ".join(chk3.output.split()), chk3.output

    # Falsifier: at N=4 the very same fixtures must find the regression, or "not cleared"
    # above would be trivially correct rather than a real rescue.
    chk4, report4 = _check(tmp_path, scen, 4, tag="t")
    assert chk4.exit_code == 1, chk4.output
    assert "tool-call behavior changed" in report4, report4


def test_the_directional_modes_are_not_declared_blind_at_three_runs(tmp_path):
    """The mirror of the test above: `subset`/`superset` reach ALPHA at N=3, so the same
    scenario must NOT be suppressed there. A blind predicate that ignored `mode` in the other
    direction would withhold a real clearance."""
    scen = _tool_only_sandbox(tmp_path)
    for mode in ("subset", "superset"):
        _, report = _check(tmp_path, scen, 3, mode=mode, tag="d")
        assert "could not have reported a regression" not in report, (mode, report)


# --- MP-116 / MP-117: what a PARTIALLY blind run is allowed to say ------------------------
#
# `[M] 2026-08-27`, reproduced before either fix. MP-55 taught the renderer to stop clearing
# a run that could not have gone red, but only when EVERY scenario was blind: the header
# branch read `len(underpowered) >= len(results)`, so partial coverage fell through to the
# green tick while the bucket label and the footer four lines below both called the model
# "only partially cleared" -- the document contradicting its own first line, which is the
# line `action.yml` posts at the top of the PR comment.
#
# The SHAPE matters and cost one dead hypothesis: "3 results, 2 unchanged, 1 blind" does NOT
# reproduce it. Any non-`unchanged` verdict pre-empts the header before the blindness branch
# is reached, so the minimal case is ALL results unchanged with `underpowered` a proper
# non-empty subset. That pre-emption is pinned below, because a fix that broke it would
# trade a false green for a false grey.


def _trace(sid: str, i: int) -> Trace:
    return Trace(scenario_id=sid, model_id="m", run_idx=i, final_output="ok")


def _heterogeneous_check(tmp_path):
    """The state MP-116 needs, built the only way it can be: by editing the file on disk.
    `[M]` No Modelpin command produces one -- `save_baseline` replaces the whole `scenarios`
    dict and `replay()` always returns exactly `runs` traces -- so this hand-edits a recorded
    baseline the way a merge or an externally generated file would.

    Same model on both sides, so every verdict is `unchanged` and nothing pre-empts the
    header. 2 of 4 scenarios are truncated to 2 recorded runs and the check runs at 4.
    """
    store = str(tmp_path / "hetero")
    common = [
        "--provider",
        "fake",
        "--fixtures",
        FIXTURES,
        "--scenarios-dir",
        SCEN,
        "--config",
        CONFIG,
        "--store-dir",
        store,
    ]
    base = runner.invoke(app, ["baseline", "--model", DEMO_FROM, *common, "--runs", "4"])
    assert base.exit_code == 0, base.output
    recorded = load_baseline(DEMO_FROM, store)
    for sid in ("angry_customer", "invoice_parse"):
        recorded[sid] = recorded[sid][:2]
    save_baseline(recorded, DEMO_FROM, store)
    chk = runner.invoke(
        app, ["check", "--to", DEMO_FROM, "--from", DEMO_FROM, *common, "--runs", "4"]
    )
    report = (Path(store) / "last-report.md").read_text(encoding="utf-8")
    return chk, report


def test_a_partially_blind_run_does_not_lead_with_the_green_header():
    """MP-116, the reproduction: line 1 cleared the model while the footer said it was only
    partially cleared."""
    results = [_unchanged("s1"), _unchanged("s2"), _unchanged("s3")]
    md = render_pr_comment(results, "a", "b", 3, None, ["s3"])
    first = md.splitlines()[0]
    assert "no behavioral change" not in first.lower(), first
    assert "partially measured" in first.lower(), first
    # ... and the honest lines it used to contradict are still there.
    assert "partially cleared" in md, md
    assert "safe to adopt" not in md, md


def test_a_real_verdict_still_outranks_the_partial_header():
    """The pre-emption the fix must not break: a regression, an abstention or a minor each
    says more than "partially measured", so each keeps the header even with blind scenarios
    in the same run."""
    for verdict, expected in (
        (DiffVerdict.regression, "behavioral regression"),
        (DiffVerdict.insufficient_evidence, "could not measure"),
        (DiffVerdict.changed_minor, "minor changes"),
    ):
        results = [
            DiffResult(
                scenario_id="s1",
                from_model="a",
                to_model="b",
                verdict=verdict,
                explanation="x",
                confidence=0.9,
            ),
            _unchanged("s2"),
        ]
        first = render_pr_comment(results, "a", "b", 3, None, ["s2"]).splitlines()[0]
        assert expected in first.lower(), (verdict, first)


def test_the_blind_scenarios_are_named_by_both_renderers():
    """MP-117. Both surfaces printed a COUNT and nothing else, so a reviewer told "1 of 3
    could not have reported a regression" had no way to learn WHICH -- and the ids appear in
    no other bucket, because every other bucket is empty whenever this path is live."""
    results = [
        _unchanged("greeting"),
        _unchanged("refund_request"),
        _unchanged("invoice_parse"),
    ]
    md = render_pr_comment(results, "a", "b", 3, None, ["invoice_parse"])
    cli = render_cli(results, "a", "b", 3, ["invoice_parse"])
    assert "invoice_parse" in md, md
    assert "invoice_parse" in cli, cli
    # Naming the blind one must not smear the clean ones with the same doubt.
    assert "greeting" not in md, md
    assert "greeting" not in cli, cli


def test_the_persisted_clearance_carries_the_concrete_run_count():
    """MP-117's second half. The CLI's pre-spend warning says `Use --runs 5`; the persisted
    report -- the artifact the Action posts, and the only thing a PR reviewer sees -- said
    merely "re-run with more runs per side" in the all-blind branch and gave NO remedy at all
    in the partial branch. Both now name the number, and both name `baseline` as well as
    `check`: the floor depends on BOTH sides, so a remedy naming only `check` leaves a short
    baseline blind."""
    every = render_pr_comment([_unchanged("s1")], "a", "b", 2, None, ["s1"])
    part = render_pr_comment([_unchanged("s1"), _unchanged("s2")], "a", "b", 2, None, ["s1"])
    for md in (every, part):
        assert f"--runs {RECOMMENDED_RUNS}" in md, md
        assert "modelpin baseline" in md and "modelpin check" in md, md


def test_the_report_and_the_cli_advertise_the_same_run_count():
    """The two surfaces bind the number independently (`cli.RECOMMENDED_RUNS`,
    `report._RECOMMENDED_RUNS`). Pin them equal, or MP-117's fix decays back into the very
    mismatch it removed the moment either constant moves."""
    assert RECOMMENDED_RUNS == _REPORT_RECOMMENDED_RUNS


# --- MP-116's input state: the heterogeneous baseline nothing validated -------------------


def test_nonuniform_run_counts_is_silent_on_a_uniform_baseline():
    uniform = {
        "s1": [_trace("s1", i) for i in range(3)],
        "s2": [_trace("s2", i) for i in range(3)],
    }
    assert nonuniform_run_counts(uniform) == {}
    assert nonuniform_run_counts({}) == {}
    assert nonuniform_run_counts({"only": [_trace("only", 0)]}) == {}


def test_nonuniform_run_counts_reports_every_scenario_when_they_disagree():
    mixed = {"s1": [_trace("s1", i) for i in range(3)], "s2": [_trace("s2", 0)]}
    assert nonuniform_run_counts(mixed) == {"s1": 3, "s2": 1}


def test_storage_round_trips_a_heterogeneous_baseline_without_complaint(tmp_path):
    """`[M]` The state MP-116 needs is reachable because NOTHING rejects it: `save_baseline`
    and `load_baseline` apply zero run-count uniformity validation in either direction. This
    pins that as a fact rather than a bug -- an uneven baseline is scored correctly per
    scenario (MP-72), so the fix is to SAY so, not to refuse a file the engine can use.
    """
    store = tmp_path / "store"
    uneven = {
        "s1": [_trace("s1", i) for i in range(4)],
        "s2": [_trace("s2", i) for i in range(2)],
    }
    save_baseline(uneven, "m", store)
    back = load_baseline("m", store)
    assert {k: len(v) for k, v in back.items()} == {"s1": 4, "s2": 2}
    assert nonuniform_run_counts(back) == {"s1": 4, "s2": 2}


def test_a_heterogeneous_baseline_is_disclosed_before_the_run_spends(tmp_path):
    """The user is about to pay for a run whose coverage is not what its run count suggests.
    Printed before the ADR-0019 pre-spend line, which prices the whole run off
    `min(baseline_sizes)` and so describes only the weakest scenario."""
    chk, _ = _heterogeneous_check(tmp_path)
    out = " ".join(chk.output.split())
    assert "different numbers of recorded runs per scenario" in out, chk.output
    assert "angry_customer=2" in out and "greeting=4" in out, chk.output


def test_a_heterogeneous_baseline_reaches_the_partial_header_end_to_end(tmp_path):
    """MP-116 through the real CLI, on the real renderer, with the blind list produced by
    `cli._blind` rather than hand-passed. `[M]` At 2 vs 4 runs the floor is 0.0667 > ALPHA
    (blind); at 4 vs 4 it is 0.0143 (measured), so the split is genuine, not constructed.
    """
    assert min_achievable_pvalue_mean(2, 4) > ALPHA
    assert min_achievable_pvalue_mean(4, 4) < ALPHA
    chk, report = _heterogeneous_check(tmp_path)
    assert chk.exit_code == 0, chk.output
    first = report.splitlines()[0]
    assert "no behavioral change" not in first.lower(), report
    assert "partially measured" in first.lower(), report
    # MP-117 on the artifact that is actually posted: the blind ones are named there.
    assert "angry_customer" in report and "invoice_parse" in report, report
    assert f"--runs {RECOMMENDED_RUNS}" in report, report


def test_the_uniformity_warning_ignores_scenarios_the_run_will_not_replay():
    """`[M]` claims-auditor, on the first draft of this fix: unscoped, the warning fired on a
    baseline entry whose scenario file had been DELETED, and on an entry holding 0 recorded
    runs -- which `check` skips -- in both cases while every scenario in the run was measured
    at full power. A pre-spend power warning that fires when nothing in the run is affected
    is the crying-wolf shape the north-star metric exists to prevent."""
    base = {
        "greeting": [_trace("greeting", i) for i in range(4)],
        "refund_request": [_trace("refund_request", i) for i in range(4)],
        "deleted_from_disk": [_trace("deleted_from_disk", i) for i in range(2)],
    }
    assert nonuniform_run_counts(base, ["greeting", "refund_request"]) == {}
    # ... and it still fires when a scenario the run WILL replay is short.
    assert nonuniform_run_counts(base, ["greeting", "deleted_from_disk"]) == {
        "greeting": 4,
        "deleted_from_disk": 2,
    }


def test_the_uniformity_warning_ignores_zero_run_entries():
    """`check` skips a scenario with no recorded baseline and says so separately, so counting
    its 0 as a disagreeing run count would report a second time on the same fact."""
    base = {
        "greeting": [_trace("greeting", i) for i in range(4)],
        "never_recorded": [],
    }
    assert nonuniform_run_counts(base) == {}
    assert nonuniform_run_counts(base, ["greeting", "never_recorded"]) == {}


def test_the_partial_clearance_prose_is_still_free_of_comparative_language():
    """ADR-0009 / spec section 9. `[M]` claims-auditor: the `_BANNED` regex was applied at
    three sites, none of which passed a non-empty `underpowered`, so every string this branch
    adds to the report was unguarded prose. A Report is posted on someone else's repository;
    "worse", "downgrade" and their kin must be unreachable on EVERY branch, not most."""
    banned = re.compile(
        r"(?i)\b(better|worse|best|beats|wins|loses|superior|inferior|upgrade|downgrade)\b"
    )
    results = [_unchanged("s1"), _unchanged("s2")]
    for blind in (["s1"], ["s1", "s2"]):
        md = render_pr_comment(results, "a", "b", 2, None, blind)
        assert not banned.search(md), md
        assert not banned.search(render_cli(results, "a", "b", 2, blind))


# --- what the new prose does under adversarial input --------------------------------------
#
# `[M] 2026-08-27` first-run-auditor, on the first draft of this branch. Both findings are
# in code this branch ADDED, not pre-existing, and neither had any test.


_HOSTILE_ID = "evil[/][bold red]pwned|pipe"


def test_a_scenario_id_that_looks_like_rich_markup_does_not_crash_mp_check(tmp_path):
    """`[M]` The new uneven-baseline warning interpolated raw scenario ids into a rich-markup
    string, so an id carrying a lone `[/]` crashed `mp check` with an unhandled MarkupError,
    a full traceback and exit 1 -- instead of printing the warning it exists to print.
    Scenario ids are author-controlled text and reach every console string through this path.
    """
    root = tmp_path / "hostile"
    write_demo(root)
    d = root / DEMO_DIRNAME
    (d / "scenarios" / "hostile.json").write_text(
        json.dumps(
            {
                "id": _HOSTILE_ID,
                "name": "Hostile id",
                "kind": "single",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
            }
        ),
        encoding="utf-8",
    )
    fixtures = d / DEMO_FIXTURES
    canned = json.loads(fixtures.read_text(encoding="utf-8"))
    canned.append(
        {
            "scenario_id": _HOSTILE_ID,
            "model_id": DEMO_FROM,
            "final_output": "hello",
            "tokens_out": 3,
            "latency_ms": 100.0,
        }
    )
    fixtures.write_text(json.dumps(canned), encoding="utf-8")

    store = str(d / ".modelpin")
    common = [
        "--provider",
        "fake",
        "--fixtures",
        str(fixtures),
        "--scenarios-dir",
        str(d / "scenarios"),
        "--config",
        str(d / "modelpin.yaml"),
        "--store-dir",
        store,
    ]
    base = runner.invoke(app, ["baseline", "--model", DEMO_FROM, *common, "--runs", "4"])
    assert base.exit_code == 0, base.output
    recorded = load_baseline(DEMO_FROM, store)
    recorded[_HOSTILE_ID] = recorded[_HOSTILE_ID][:2]
    save_baseline(recorded, DEMO_FROM, store)

    chk = runner.invoke(
        app, ["check", "--to", DEMO_FROM, "--from", DEMO_FROM, *common, "--runs", "4"]
    )
    assert chk.exit_code == 0, chk.output
    assert "MarkupError" not in chk.output and "Traceback" not in chk.output, chk.output
    out = " ".join(chk.output.split())
    assert "different numbers of recorded runs per scenario" in out, chk.output
    # Printed literally, with no styling applied and nothing swallowed.
    assert f"{_HOSTILE_ID}=2" in out, chk.output


def test_the_blind_scenario_list_truncates_instead_of_walling_the_line(tmp_path):
    """`[M]` At 30 blind scenarios both surfaces rendered a ~1,200-character comma wall.
    Naming them is the point of MP-117; naming all of them at any suite size is not, and the
    full list is in the sidecar JSON either way."""
    ids = [f"scenario_{i:02d}" for i in range(30)]
    results = [_unchanged(sid) for sid in ids]
    md = render_pr_comment(results, "a", "b", 2, None, ids)
    cli = render_cli(results, "a", "b", 2, ids)
    for text in (md, cli):
        assert "scenario_00" in text and "scenario_07" in text, text
        assert "scenario_08" not in text, text
        assert "and 22 more" in text, text
    # A suite at the cap names every one and says nothing about a remainder. Matched as a
    # pattern, not the bare word "more" -- the remedy line legitimately says "or more".
    exact = render_pr_comment(results[:8], "a", "b", 2, None, ids[:8])
    assert "scenario_07" in exact, exact
    assert not re.search(r"and \d+ more", exact), exact
