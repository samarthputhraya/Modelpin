"""A scenario with NO baseline must reach the artifact a reviewer reads (MP-160).

`[M] 2026-08-31`, reproduced end to end through the real CLI on canned offline traces.
`check` collects un-baselined scenarios into `skipped`, and that list had exactly one
consumer in the whole package: a console note that runs AFTER `last-report.md` has already
been written. So the scenario appeared in ZERO artifacts -- not the report, not the archived
copy -- while the run exited 0.

    baseline a subset, restore a regressive scenario, check:
        CHECK_EXIT=0
        grep -c angry_customer .modelpin/last-report.md  ->  0

MP-148 gave `rejected` three pieces -- a pre-bucket section, a clearance line, and a place in
the exit condition. `skipped` received none of them.

WHY THIS IS NOT A LITERAL MIRROR OF `rejected`, which is the whole lesson of the row:

  1. `[M]` The clearance line is UNREACHABLE on any run that is not all-clean. Both
     `render_pr_comment` and `render_cli` gate it behind `if regs or minors: ... elif
     unmeasured: ... else: <clearance>`. A clearance-shaped disclosure alone is therefore
     inert on exactly the run where a reviewer most needs it -- a red verdict pronounced
     over a fraction of the suite. The load-bearing piece is the UNCONDITIONAL section that
     renders above the verdict buckets. `[M]` Review BUILT that mirror and ran this module
     against it: **3 fail** -- the red-run test, the archive test (same red run, so the
     clearance is equally unreachable) and the provenance test. Two earlier versions of this
     docstring were wrong here -- one claimed a single test discriminated, the next published
     a denominator that the module outgrew -- so the count of tests is deliberately not
     restated: the three NAMED tests are the part that survives the module growing.

  2. `[M]` The remedy is not mirrorable either, which is why this row deliberately does NOT
     add `skipped` to the build-failing exit. Disclose absolutely; coerce never. The argument
     and its revisit trigger live in ADR-0033 -- `test_a_skipped_scenario_denies_the_
     affirmative_clearance` below pins exit 0 and the withheld clearance TOGETHER, so flipping
     the exit code without superseding that record fails this suite.

What DID change about exit codes is a plain bug, not policy: `[M]` with every scenario
un-baselined the command exited **1**, the code that means REGRESSION, over a run that
compared nothing. ADR-0018 says a run that measured nothing abstains.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path

from typer.testing import CliRunner

import modelpin.cli as cli
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.providers import ProviderError

_ROOT = Path(tempfile.mkdtemp(prefix="modelpin-mp160-"))
write_demo(_ROOT)
_DEMO = _ROOT / DEMO_DIRNAME
FIXTURES = str(_DEMO / DEMO_FIXTURES)
SCEN = _DEMO / "scenarios"
CONFIG = str(_DEMO / "modelpin.yaml")

runner = CliRunner()

#: In the shipped demo: `refund_request` and `angry_customer` are REGRESSIONS,
#: `invoice_parse` is a minor, `greeting` is unchanged. Picking which of them carries a
#: baseline is how each test below chooses its run shape.
_CLEAN = "greeting"
_RED = "refund_request"


def _flat(text: str) -> str:
    """Console output wraps at the terminal width, so a claim can straddle a newline.
    Assertions about MESSAGES must not become assertions about line breaks."""
    return " ".join(text.split())


def _subset_dir(tmp_path: Path, *keep: str, name: str = "") -> Path:
    """A scenarios dir holding only `keep` -- what the user had when they recorded.

    `[M] 2026-09-01` The default name used to be `scen-<joined ids>`, and `check` echoes the
    scenarios directory in its provenance header -- so `assert "angry_customer" in out` was
    satisfied by the PATH, not by any disclosure. Two assertions in this module were passing
    for that reason. The name is now content-independent so a directory can never stand in
    for the thing under test."""
    slug = hashlib.md5("|".join(keep).encode()).hexdigest()[:8] if keep else "none"
    d = tmp_path / (name or f"scen-{slug}")
    d.mkdir(parents=True, exist_ok=True)
    for sid in keep:
        shutil.copy(SCEN / f"{sid}.json", d / f"{sid}.json")
    return d


def _args(store: str, scen_dir: Path) -> list[str]:
    return [
        "--provider",
        "fake",
        "--fixtures",
        FIXTURES,
        "--scenarios-dir",
        str(scen_dir),
        "--config",
        CONFIG,
        "--store-dir",
        store,
        "--runs",
        "5",
    ]


def _baseline_only(tmp_path: Path, *keep: str) -> str:
    """Record a baseline covering ONLY `keep`; every other scenario is later skipped."""
    store = str(tmp_path / ".modelpin")
    r = runner.invoke(
        cli.app, ["baseline", "--model", DEMO_FROM, *_args(store, _subset_dir(tmp_path, *keep))]
    )
    assert r.exit_code == 0, r.output
    return store


def _check(store: str, scen_dir: Path = SCEN):
    return runner.invoke(cli.app, ["check", "--to", DEMO_TO, *_args(store, scen_dir)])


def _report(store: str) -> str:
    return (Path(store) / "last-report.md").read_text(encoding="utf-8")


# --- the discriminating test: a clearance-shaped fix is INERT here ----------------------


def test_a_skipped_scenario_is_named_in_the_report_even_on_a_red_run(tmp_path):
    """THE test. `refund_request` regresses, so the run is already red -- which means the
    headline branch, the clearance line and the exit condition are all no-ops. Only the
    unconditional section can disclose here, and this is the run a reviewer is deciding on.
    """
    store = _baseline_only(tmp_path, _CLEAN, _RED)
    r = _check(store)
    md = _report(store)
    assert r.exit_code == 1, r.output  # a real regression still fails the build
    assert "REGRESSION" in md.upper()
    for skipped_id in ("angry_customer", "invoice_parse"):
        assert skipped_id in md, (
            f"MP-160: {skipped_id!r} had no baseline and was never compared, yet the report "
            f"CI posts does not name it. It appeared 0 times before this row.\n--- report ---\n{md}"
        )
    assert "NO BASELINE (2)" in md

    # `[M]` Review deleted the whole `if skipped:` block from `render_cli` and the suite
    # stayed green; so did changing its count to `len(skipped) - 1`. Half the shipped fix --
    # the surface a local user actually reads -- was asserted by nothing.
    out = _flat(r.output)
    assert "2 scenario(s) had no baseline, were never compared" in out, out
    # Assert the ids appear TWICE each -- once in the console block, once in the trailing
    # note -- so losing either surface fails. A bare `in out` was previously satisfied by the
    # scenarios-directory path that `check` echoes in its own header.
    for sid in ("angry_customer", "invoice_parse"):
        assert out.count(sid) >= 2, f"{sid} appears {out.count(sid)}x, expected >=2: {out}"


def test_the_skipped_ids_survive_into_the_archived_copy(tmp_path):
    """MP-150 archives every run for citation. A disclosure absent from the archive is a
    finding that cannot be cited later, which is the defect MP-150 exists to prevent."""
    store = _baseline_only(tmp_path, _CLEAN, _RED)
    _check(store)
    archived = sorted((Path(store) / "runs").glob("*.md"))
    assert archived, "no archived report written"
    assert "angry_customer" in archived[-1].read_text(encoding="utf-8")


# --- the clearance, on the run shape where it IS reachable ------------------------------


def test_a_skipped_scenario_denies_the_affirmative_clearance(tmp_path):
    store = _baseline_only(tmp_path, _CLEAN)
    r = _check(store)
    md = _report(store)
    assert "looks safe to adopt" not in md, (
        "MP-160: three of four scenarios were never compared and the report still issued an "
        f"affirmative clearance.\n--- report ---\n{md}"
    )
    assert "NOT fully cleared" in md
    # Either honest headline is acceptable; the GREEN one is not. Which of the two renders
    # depends on the compared scenario's own coverage -- here the census also has nothing
    # live, so "could not measure" outranks "partially measured".
    headline = md.splitlines()[0]
    assert "no behavioral change" not in headline, headline
    assert ("partially measured" in headline) or ("could not measure" in headline), headline
    assert r.exit_code == 0  # deliberate: disclose, do not coerce -- see the module docstring


def test_the_provenance_line_states_the_gap_so_shrinkage_is_detectable(tmp_path):
    """`Replayed N scenario(s)` with no denominator let a suite quietly shrink from 4 to 1
    and still read like a suite of 1. The count of what was lost belongs on that line."""
    store = _baseline_only(tmp_path, _CLEAN)
    _check(store)
    md = _report(store)
    provenance = md.splitlines()[1]
    assert "had no baseline" in provenance, provenance
    assert "3" in provenance, provenance


# --- exit codes: the one that was a bug, and the one that is policy ---------------------


def test_every_scenario_unbaselined_abstains_rather_than_claiming_a_regression(tmp_path):
    """`[M]` This exited **1** -- the code that means REGRESSION -- over a run that compared
    nothing at all. `action.yml` cannot tell exit 1 from exit 1, so a stale baseline was
    indistinguishable in CI from a caught regression. ADR-0018: a run that measured nothing
    abstains."""
    store = _baseline_only(tmp_path, _CLEAN)
    r = _check(store, scen_dir=_subset_dir(tmp_path, "angry_customer"))
    assert (
        r.exit_code == cli.EXIT_UNMEASURED
    ), f"expected EXIT_UNMEASURED ({cli.EXIT_UNMEASURED}), got {r.exit_code}\n{r.output}"
    out = _flat(r.output)
    assert "could not measure" in out
    # The DISCLOSURE line, not merely the string somewhere in the stream.
    assert "no baseline: angry_customer" in out, out


def test_the_zero_comparison_message_names_both_causes(tmp_path, monkeypatch):
    """One rejection AND one missing baseline: the old message said "the provider rejected
    all 1 scenario(s)", false about the other one, and the skipped id reached nothing at
    all -- the note that would have named it sits past this early exit."""
    real = cli.replay

    def fake_replay(scenario, model, adapter, runs):
        if scenario.id == _CLEAN:
            raise ProviderError("groq: 400 tool_use_failed")
        return real(scenario, model, adapter, runs=runs)

    store = _baseline_only(tmp_path, _CLEAN)
    monkeypatch.setattr(cli, "replay", fake_replay)
    r = _check(store, scen_dir=_subset_dir(tmp_path, _CLEAN, "angry_customer"))
    assert r.exit_code == cli.EXIT_UNMEASURED, r.output
    out = _flat(r.output)
    assert "rejected 1" in out
    assert "had no recorded baseline" in out
    assert "angry_customer" in out


def test_a_real_regression_still_outranks_the_skipped_disclosure(tmp_path):
    """The disclosure must never downgrade a genuine red: a reviewer needs the failure
    first, and MP-148 established the same ordering for `rejected`."""
    store = _baseline_only(tmp_path, _CLEAN, _RED)
    r = _check(store)
    assert r.exit_code == 1
    assert _report(store).splitlines()[0].startswith("\U0001f6a8")


# --- the crash that lived in the line this row edits ------------------------------------


def test_a_scenario_id_that_looks_like_rich_markup_does_not_abort_the_run(tmp_path):
    """`[M] 2026-08-31` `', '.join(skipped)` was the one scenario-id join MP-148 left
    unescaped, and `Scenario.id` has no pattern validator. An id containing `[/]` raised
    `MarkupError` and aborted the command AFTER the report and archive were written -- CI
    publishing the artifact, then failing on a markup typo."""
    store = _baseline_only(tmp_path, _CLEAN)  # baseline dir must NOT hold the odd id
    scen = _subset_dir(tmp_path, _CLEAN, name="scen-with-markup-id")
    payload = json.loads((SCEN / "angry_customer.json").read_text(encoding="utf-8"))
    payload["id"] = "bravo[/]boom"
    (scen / "bravo_boom.json").write_text(json.dumps(payload), encoding="utf-8")

    r = _check(store, scen_dir=scen)
    assert r.exception is None, f"{type(r.exception).__name__}: {r.exception}"
    assert "bravo[/]boom" in r.output
    assert "bravo[/]boom" in _report(store)


def test_the_cli_disclosure_stays_cp1252_encodable(tmp_path):
    """The module invariant MP-138 shipped a crash by violating: this text reaches a default
    Windows console. Exercised on a run that carries BOTH a skipped scenario and the
    clearance line, since that is the combination the new strings appear in."""
    store = _baseline_only(tmp_path, _CLEAN)
    r = _check(store)
    r.output.encode("cp1252")  # must not raise


def test_the_console_clearance_does_not_leak_markdown_escaping():
    """`[M]` Review mutated the `fmt=` argument away at the `render_cli` call site and the
    suite stayed green. Without it the CLI clearance runs its ids through `_md_inline`, so a
    terminal user sees an escaped pipe. The existing markup test cannot catch it: its
    `bravo[/]boom` id passes through `_md_inline` untouched."""
    from modelpin.report import render_cli

    out = render_cli([], "m1", "m2", 5, skipped=["a|b"])
    assert "`a|b`" in out, out
    assert ("a" + chr(92) + "|b") not in out, out


def test_a_run_that_compared_nothing_still_publishes_a_fresh_artifact(tmp_path):
    """`[M]` The zero-comparison path raised before writing anything, so the PREVIOUS run's
    `last-report.md` stayed on disk as the reviewer-facing verdict -- and `action.yml` posts
    that file whenever it exists, regardless of the exit code. CI published a stale verdict
    under a failing run, and the un-baselined scenario appeared in ZERO artifacts: MP-160's
    own defect, surviving inside the branch MP-160 rewrote."""
    store = _baseline_only(tmp_path, _CLEAN)
    first = _check(store)
    assert first.exit_code == 0, first.output
    assert "greeting" in _report(store)
    before = len(list((Path(store) / "runs").glob("*.md")))

    only_unbaselined = _subset_dir(tmp_path, "angry_customer", name="scen-only-unbaselined")
    second = _check(store, scen_dir=only_unbaselined)
    assert second.exit_code == cli.EXIT_UNMEASURED, second.output
    md = _report(store)
    assert "angry_customer" in md, "stale report left on disk: " + md
    assert "partially measured" not in md, "nothing was compared; 'partially' overstates it"
    assert "could not measure" in md.splitlines()[0], md.splitlines()[0]
    assert len(list((Path(store) / "runs").glob("*.md"))) == before + 1, "no new archive"


def test_mp_report_survives_a_scenario_id_that_looks_like_rich_markup(tmp_path, monkeypatch):
    """The sibling of the `check` crash. `[M]` Review disproved this module's own earlier
    comment: `report()` carried two more unescaped scenario-id sites and `modelpin report`
    aborted with `MarkupError` on the same id."""
    scen = _subset_dir(tmp_path, _CLEAN, name="scen-report-markup")
    payload = json.loads((SCEN / "angry_customer.json").read_text(encoding="utf-8"))
    payload["id"] = "bravo[/]boom"
    (scen / "bravo_boom.json").write_text(json.dumps(payload), encoding="utf-8")

    real = cli.replay

    def fake_replay(scenario, model, adapter, runs):
        if scenario.id == "bravo[/]boom":
            raise ProviderError("groq: 400 tool_use_failed")
        return real(scenario, model, adapter, runs=runs)

    monkeypatch.setattr(cli, "replay", fake_replay)
    r = runner.invoke(
        cli.app,
        [
            "report",
            "--from",
            DEMO_FROM,
            "--to",
            DEMO_TO,
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,
            "--suite-dir",
            str(scen),
            "--config",
            CONFIG,
            "--output-dir",
            str(tmp_path / "out"),
            "--runs",
            "5",
        ],
    )
    assert "MarkupError" not in repr(r.exception), repr(r.exception)


def test_a_regression_verdict_survives_a_scenario_id_that_looks_like_rich_markup(tmp_path):
    """`[M] 2026-09-01` first-run review found a FOURTH unescaped scenario-id site, by
    execution: `render_cli`'s verdict loop interpolated `r.scenario_id` raw while escaping
    `r.explanation` on the same line. A regression or minor on a scenario whose id contains
    `[/]` raised `MarkupError` and killed the command at exit **1** -- the code this row just
    finished defining as "a real regression" -- and it crashes inside the `console.print`
    that runs BEFORE the report is written, so no artifact survives either.

    Two comments in this codebase had already claimed the last such site was closed. This is
    the assertion instead of a third claim."""
    from rich.console import Console

    from modelpin.models import DiffResult, DiffSignals, DiffVerdict
    from modelpin.report import render_cli

    for verdict in (
        DiffVerdict.regression,
        DiffVerdict.changed_minor,
        DiffVerdict.insufficient_evidence,
    ):
        r = DiffResult(
            scenario_id="brack[/]et_reg",
            from_model="a",
            to_model="b",
            verdict=verdict,
            confidence=0.99,
            explanation="tool-call behavior changed",
            signals=DiffSignals(),
        )
        out = render_cli([r], "a", "b", 5)
        buf = io.StringIO()
        Console(file=buf, width=200).print(out)  # must not raise MarkupError
        # Assert on the RENDERED text, not the markup: after escaping, the markup carries
        # rich's marker and only the console output shows what the user actually reads.
        assert "brack[/]et_reg" in buf.getvalue()


def test_no_usable_baseline_abstains_instead_of_claiming_a_regression(tmp_path):
    """`[M] 2026-09-01` claims review. With no baseline FILE the run replays nothing and
    compares nothing, and it exited **1** -- so `action.yml` took its else-branch and
    annotated the PR *"Modelpin detected a behavioral regression migrating to X"* over a run
    that never called a model. A false claim about someone's model, in their PR: the same
    class MP-160 fixed, reached through the setup path rather than the scenario path."""
    r = _check(str(tmp_path / "no-such-store"))
    assert r.exit_code == cli.EXIT_UNMEASURED, r.output
    assert "could not measure" in _flat(r.output)
    assert "baseline" in r.output.lower()


def test_the_headline_arm_that_stops_a_green_tick_is_pinned():
    """`[M] 2026-09-01` Review deleted `or skipped` from the "partially measured" header arm
    -- reverting exactly the MP-160 edit -- and all 741 tests still passed. That line is the
    one `action.yml` posts as the top of the PR comment, and the row's headline promise
    (a skipped scenario removes the affirmative clearance) rode on nothing.

    A direct unit call, not a CLI run: on the `fake` provider the census can never be fully
    live, so a CLI-driven test cannot isolate this arm from the census arm."""
    from modelpin.models import DiffResult, DiffSignals, DiffVerdict
    from modelpin.report import ChannelCensus, render_pr_comment

    fully_live = ChannelCensus(
        tools_exercised=True, assertions_declared=True, judge_enabled=True, compared=1
    )
    clean = [
        DiffResult(
            scenario_id="ok",
            from_model="v1",
            to_model="v2",
            verdict=DiffVerdict.unchanged,
            confidence=1.0,
            explanation="no behavioral change",
            signals=DiffSignals(),
        )
    ]
    green = render_pr_comment(clean, "v1", "v2", 5, "openai", (), fully_live).splitlines()[0]
    assert "no behavioral change" in green, green  # the control: this SHOULD be green

    withheld = render_pr_comment(
        clean, "v1", "v2", 5, "openai", (), fully_live, (), ["never_compared"]
    ).splitlines()[0]
    assert "no behavioral change" not in withheld, withheld
    assert "partially measured" in withheld, withheld


def test_the_zero_comparison_console_escapes_its_scenario_ids(tmp_path):
    """The fifth unescaped-id site: the `no baseline:` line on the abstain path is new code,
    correctly escaped, and was guarded by nothing. Also pins the abstain message's own escape
    against a store path containing brackets."""
    scen = _subset_dir(tmp_path, name="scen-markup-only")
    payload = json.loads((SCEN / "angry_customer.json").read_text(encoding="utf-8"))
    payload["id"] = "bravo[/]boom"
    (scen / "b.json").write_text(json.dumps(payload), encoding="utf-8")

    store = _baseline_only(tmp_path, _CLEAN)
    r = _check(store, scen_dir=scen)
    assert r.exception is None or isinstance(r.exception, SystemExit), repr(r.exception)
    assert r.exit_code == cli.EXIT_UNMEASURED, r.output
    assert "bravo[/]boom" in r.output, r.output


def test_the_zero_comparison_artifact_states_its_own_conclusion(tmp_path):
    """The artifact is posted to a PR on its own. Silence under a "could not measure" header
    reads as an omission; and an affirmative clearance there would be the defect this whole
    row exists to remove."""
    from modelpin.report import render_pr_comment

    md = render_pr_comment([], "v1", "v2", 5, "fake", (), None, (), ["alpha"])
    assert "looks safe to adopt" not in md, md
    assert "UNCHANGED (0)" not in md, md
    assert "Nothing was compared" in md, md
    assert "NOT a clean result and NOT a regression" in md, md


def test_a_missing_baseline_file_also_publishes_a_fresh_artifact(tmp_path):
    """The THIRD zero-comparison exit, and the one the extraction missed.

    `[M] 2026-09-01` `_publish_report` was extracted precisely because a zero-comparison exit
    wrote no artifact -- and the `except (FileNotFoundError, BaselineError)` branch added by
    that same commit raised without publishing. `action.yml` posts `last-report.md` whenever
    it exists with no reference to the exit code, so a previous run's red header was
    republished under a run that never called a model. Sibling of
    `test_a_run_that_compared_nothing_still_publishes_a_fresh_artifact`, which covers only
    the all-skipped path."""
    store = _baseline_only(tmp_path, _CLEAN, _RED)
    first = _check(store)
    assert first.exit_code == 1, first.output  # a red run, so the stale artifact would be red
    assert "REGRESSION" in _report(store).upper()
    before = len(list((Path(store) / "runs").glob("*.md")))

    (Path(store) / f"baseline-{DEMO_FROM}.json").unlink()
    second = _check(store)
    assert second.exit_code == cli.EXIT_UNMEASURED, second.output
    md = _report(store)
    # The conclusion sentence legitimately contains "regression" ("NOT a regression"), so
    # assert on the HEADLINE -- the line action.yml posts at the top of the PR comment.
    assert "behavioral regression" not in md.splitlines()[0], (
        "stale red verdict republished: " + md.splitlines()[0]
    )
    assert "could not measure" in md.splitlines()[0], md.splitlines()[0]
    assert "Nothing was compared" in md, md
    assert len(list((Path(store) / "runs").glob("*.md"))) == before + 1, "no new archive"
