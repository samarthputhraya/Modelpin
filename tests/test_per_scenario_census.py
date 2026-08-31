"""MP-141 -- the channel census is a suite-wide `any()` while blindness is PER-scenario.

MP-138 asked "did ANY scenario in this suite declare `tools`?" and MP-140 published that
answer on the ADR-0009 surface. But `tools` is declared on a SCENARIO, not on a suite, so
one armed scenario spoke for every blind neighbour.

`[M] 2026-08-31`, reproduced end to end through the real CLI on canned offline traces. Two
suites whose two content-blind scenarios are BYTE-IDENTICAL; suite A adds one unrelated
third scenario that declares `tools` and is itself unchanged:

    B (2 blind scenarios):  OK? 2 scenario(s) unchanged
                            "-> ... `demo-model-v2` is NOT cleared on content."
                            last-report.md: "could not measure"
    A (same 2 + 1 armed):   OK  3 scenario(s) unchanged
                            last-report.md: "no behavioral change ... looks safe to adopt."

`[M]` It fires on all three SHIPPED suites -- `examples/suite` 5 of 8 content-blind,
`examples/report-suite` 11 of 14, and the `init --demo` suite a brand-new user runs first
2 of 4 -- so the published Report for the public suite carried an unqualified green
headline over 11 scenarios that had no CI-failing content channel at all.

Two co-located defects in the same row, both fixed here:
  * `render_cli` never called `_underpowered_clearance` (`[M]` grep: only `render_pr_comment`
    did), so a terminal user whose run was blind on RUN COUNT was handed the CHANNEL remedy.
  * `assertions_declared` counted an `Assertion` object the engine never reads.

Deliberately NOT here: nothing under `modelpin/diff/` (FROZEN, ADR-0030). Whether
`expected_tool_calls` should be implemented or deleted is MP-142; this row only stops the
census from COUNTING it as coverage.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from modelpin.cli import _channel_census
from modelpin.models import Assertion, DiffResult, DiffSignals, DiffVerdict, Scenario
from modelpin.report import ChannelCensus, render_cli, render_pr_comment

_TOOLS = [{"type": "function", "function": {"name": "lookup_order", "parameters": {}}}]


def _scn(sid, *, tools=False, assertions=None):
    inp = {"messages": [{"role": "user", "content": "hi"}]}
    if tools:
        inp["tools"] = _TOOLS
    return Scenario(id=sid, name=sid, kind="single", input=inp, assertions=assertions)


def _unchanged(sid):
    return DiffResult(
        scenario_id=sid,
        from_model="m1",
        to_model="m2",
        verdict=DiffVerdict.unchanged,
        confidence=1.0,
        explanation="no behavioral change",
        signals=DiffSignals(),
    )


# --- the defect itself -----------------------------------------------------------------


def test_one_armed_scenario_does_not_clear_its_blind_neighbours():
    """The sharp case, reduced from the CLI reproduction: the ONLY difference between these
    two censuses is a third scenario that declares `tools` and is itself unchanged."""
    blind_only = _channel_census([_scn("blind_a"), _scn("blind_b")], None, "fake")
    plus_armed = _channel_census(
        [_scn("blind_a"), _scn("blind_b"), _scn("armed", tools=True)], None, "fake"
    )
    assert blind_only.blind_scenarios == ("blind_a", "blind_b")
    # Before MP-141 this was `()` -- the `any()` said "tools declared" and every scenario
    # inherited the clearance.
    assert plus_armed.blind_scenarios == ("blind_a", "blind_b")
    assert plus_armed.compared == 3


def test_the_pr_comment_withholds_a_full_clearance_when_only_some_scenarios_are_armed():
    census = _channel_census(
        [_scn("blind_a"), _scn("blind_b"), _scn("armed", tools=True)], None, "fake"
    )
    md = render_pr_comment(
        [_unchanged("blind_a"), _unchanged("blind_b"), _unchanged("armed")],
        "m1",
        "m2",
        5,
        census=census,
    )
    assert "looks safe to adopt" not in md
    assert "only partially cleared" in md
    assert "blind_a" in md and "blind_b" in md


def test_a_fully_armed_suite_still_earns_its_clearance():
    """The anti-crying-wolf half: every scenario declares `tools`, so nothing is withheld."""
    census = _channel_census([_scn("a", tools=True), _scn("b", tools=True)], None, "fake")
    assert census.blind_scenarios == ()
    md = render_pr_comment([_unchanged("a"), _unchanged("b")], "m1", "m2", 5, census=census)
    assert "looks safe to adopt" in md
    assert "partially cleared" not in md


def test_an_enabled_judge_arms_every_scenario_including_those_without_tools():
    """The judge is suite-wide and reads MEANING on every scenario, so with it on nothing is
    content-blind -- the per-scenario rule must not over-fire into a false alarm."""
    census = _channel_census([_scn("a"), _scn("b")], object(), "openai")
    assert census.blind_scenarios == ()
    assert census.judge_enabled is True


def test_the_cli_does_not_print_a_green_tick_over_a_blind_scenario():
    census = _channel_census([_scn("blind"), _scn("armed", tools=True)], None, "fake")
    out = render_cli([_unchanged("blind"), _unchanged("armed")], "m1", "m2", 5, census=census)
    assert "[green]OK[/]" not in out
    assert "[yellow]OK?[/]" in out


# --- co-located defect: render_cli never carried the run-count clearance ----------------


def test_the_terminal_gets_the_run_count_remedy_not_the_channel_one():
    """`[M]` `_underpowered_clearance` was called only from `render_pr_comment`, so a run
    blind purely on RUN COUNT was told on the terminal to `add tools, a judge_model` --
    advice that fixes nothing, confidently given."""
    armed = _channel_census([_scn("a", tools=True), _scn("b", tools=True)], None, "fake")
    out = render_cli(
        [_unchanged("a"), _unchanged("b")], "m1", "m2", 2, underpowered=["a", "b"], census=armed
    )
    assert "could not have reported a regression at all" in out
    assert "add `tools`" not in out  # the CHANNEL remedy must not appear: channels are armed


def test_both_remedies_appear_on_the_terminal_when_both_axes_are_blind():
    blind = _channel_census([_scn("a"), _scn("b")], None, "fake")
    out = render_cli(
        [_unchanged("a"), _unchanged("b")], "m1", "m2", 2, underpowered=["a", "b"], census=blind
    )
    assert "could not have reported a regression at all" in out  # run count
    assert "NO CI-failing channel able to see a change" in out  # channels


def test_the_terminal_clearances_stay_cp1252_encodable():
    """`[M] 2026-08-30` MP-138 shipped U+2192 into `render_cli` and crashed `modelpin check`
    on a default Windows console. `_underpowered_clearance` had only ever reached UTF-8
    Markdown, so wiring it into the terminal re-opened that exact hole."""
    blind = _channel_census([_scn("a"), _scn("b")], None, "fake")
    for up in ([], ["a"], ["a", "b"]):
        out = render_cli(
            [_unchanged("a"), _unchanged("b")], "m1", "m2", 2, underpowered=up, census=blind
        )
        out.encode("cp1252")  # must not raise


# --- co-located defect: assertions_declared counted a channel the engine never reads ----


def test_an_assertion_the_engine_never_reads_is_not_counted_as_coverage():
    """`[M]` `demo.py` gives `angry_customer` an `Assertion` whose only field is
    `expected_tool_calls`, which `modelpin/diff/` consults nowhere (MP-142). The engine reads
    `must_contain` / `must_not_contain` only."""
    dead = _channel_census(
        [_scn("s", assertions=Assertion(expected_tool_calls=["cancel_subscription"]))],
        None,
        "fake",
    )
    assert dead.assertions_declared is False
    live = _channel_census([_scn("s", assertions=Assertion(must_contain=["TOTAL"]))], None, "fake")
    assert live.assertions_declared is True


def test_the_shipped_demo_suite_is_reported_per_scenario():
    """`[M]` The suite `modelpin init --demo` writes: 4 scenarios, 2 declare `tools`."""
    from modelpin.demo import write_demo
    from modelpin.scenarios import load_scenarios

    root = Path(tempfile.mkdtemp(prefix="modelpin-mp141-"))
    write_demo(root)
    scenarios = load_scenarios(str(root / "modelpin-demo" / "scenarios"))
    census = _channel_census(scenarios, None, "fake")
    assert census.compared == 4
    assert len(census.blind_scenarios) == 2, census.blind_scenarios
    assert census.tools_declared is True  # the suite-wide reading is still True...
    assert census.blind_scenarios  # ...and it no longer speaks for the blind half


# --- back-compat: a census with no per-scenario data keeps MP-138's behaviour -----------


def test_a_census_without_per_scenario_data_still_reads_suite_wide():
    """MP-138's constructor shape must keep working: `blind_scenarios` defaults to empty and
    the suite-wide reading stands, so the disclosure never gets LESS honest than it was."""
    legacy = ChannelCensus(tools_declared=False, assertions_declared=True, judge_enabled=False)
    md = render_pr_comment([_unchanged("s")], "m1", "m2", 5, census=legacy)
    assert "NOT cleared on content" in md
    assert "looks safe to adopt" not in md
