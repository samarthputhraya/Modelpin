"""A run whose detectors were switched off must not claim it found nothing (MP-138).

`[M] 2026-08-30` reproduced on a REAL third-party app, from a PyPI install, live provider
calls -- `ops/launch/dogfood-kavach.md`:

    $ modelpin check --to openai/gpt-oss-120b     # 12 scenarios x5 runs, 5v5
    OK 12 scenario(s) unchanged                                          exit 0
    last-report.md: "-> No behavioral regressions found; ... looks safe to adopt."

That suite declared no ``tools`` and configured no ``judge_model``, so the tool-trajectory
and tool-argument channels were inert BY CONSTRUCTION (the model was never offered a tool)
and the semantic judge never ran. Replayed against deliberate garbage -- zero detections on
every scam call, every benign caller accused, literal nonsense -- the same suite still
exited 0. The only CI-failing channel left was refusal, which fires when the candidate
starts DECLINING and never when it answers confidently and answers wrong.

This is MP-55's defect one axis across. `_resolve_runs` already refuses to let a run that
cannot conclude pass as a clean bill, and its own comment names the failure exactly -- *"the
user is about to pay for a run that is structurally incapable of reporting a regression, and
'less power' does not say that"*. But it prices ONLY the permutation floor, i.e. RUN COUNT.
The dogfood cleared that floor comfortably (5v5 -> 0.003968, far below ALPHA, so
``underpowered`` was empty) while most of its detectors were off for reasons that have
nothing to do with N.

It is also ADR-0022's rule -- *"a rate quoted without its coverage number is not a result"* --
applied to the number handed to the USER rather than to our own published numbers.

Deliberately NOT changed here: no threshold, no verdict, no exit code, and nothing under
``modelpin/diff/`` (FROZEN, ADR-0030). This governs only what the tool CLAIMS about its own
coverage.
"""

from __future__ import annotations

from modelpin.models import DiffResult, DiffSignals, DiffVerdict
from modelpin.report import ChannelCensus, render_cli, render_pr_comment

_ARMED = ChannelCensus(tools_declared=True, assertions_declared=True, judge_enabled=True)
#: The dogfood's actual shape: assertions only, no tools, no judge.
_DOGFOOD = ChannelCensus(tools_declared=False, assertions_declared=True, judge_enabled=False)


def _unchanged(scenario_id: str = "s1") -> DiffResult:
    return DiffResult(
        scenario_id=scenario_id,
        from_model="m1",
        to_model="m2",
        verdict=DiffVerdict.unchanged,
        confidence=1.0,
        explanation="no behavioral change",
        signals=DiffSignals(),
    )


def _regression(scenario_id: str = "s1") -> DiffResult:
    return DiffResult(
        scenario_id=scenario_id,
        from_model="m1",
        to_model="m2",
        verdict=DiffVerdict.regression,
        confidence=0.99,
        explanation="refusal rate 0% -> 100%",
        signals=DiffSignals(),
    )


# ---------------------------------------------------------------------------------------
# The clearance must not render over a suite that could not have seen a content change.
# ---------------------------------------------------------------------------------------


def test_the_dogfood_suite_is_not_declared_safe_to_adopt():
    """The exact configuration that shipped a false clearance on a real app."""
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert "looks safe to adopt" not in md
    assert "NOT cleared on content" in md


def test_the_cli_says_it_too_and_not_only_the_markdown():
    """MP-138's whole point is the line a user reads in their terminal. `mp report` already
    disclosed a disabled judge; `mp check` did not, and `check` is the first-run path."""
    out = render_cli([_unchanged()], "m1", "m2", 5, (), _DOGFOOD)
    assert "NOT cleared on content" in out


def test_a_fully_armed_suite_still_gets_its_affirmative_clearance():
    """The guard must not fire on every run, or it becomes noise and gets ignored -- the
    failure mode ADR-0029 already names for an over-eager signal."""
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _ARMED)
    assert "looks safe to adopt" in md
    assert "NOT cleared on content" not in md


def test_declaring_tools_alone_restores_the_clearance():
    census = ChannelCensus(tools_declared=True, assertions_declared=False, judge_enabled=False)
    assert census.hard_content_channels == ["tool trajectory"]
    assert "looks safe to adopt" in render_pr_comment(
        [_unchanged()], "m1", "m2", 5, "g", (), census
    )


def test_enabling_the_judge_alone_restores_the_clearance():
    census = ChannelCensus(tools_declared=False, assertions_declared=False, judge_enabled=True)
    assert census.hard_content_channels == ["semantic judge"]
    assert "looks safe to adopt" in render_pr_comment(
        [_unchanged()], "m1", "m2", 5, "g", (), census
    )


# ---------------------------------------------------------------------------------------
# The refusal trap. This is the mistake the fix was one edit away from shipping.
# ---------------------------------------------------------------------------------------


def test_refusal_does_not_count_as_a_content_channel():
    """Refusal IS always live -- it is read off the model's own text and needs no scenario
    declaration -- and a candidate that started refusing WOULD have failed the build. It is
    therefore tempting to count it as coverage. It must not be counted: it fires only when
    the candidate DECLINES, so a model that answers confidently and answers wrong never
    touches it. Counting it here would restore the exact false comfort this census removes.

    `[M]` The dogfood proved the gap empirically: 0 refusals in 60/60 baseline runs, while a
    model that halved the app's scam recall was reported as 6 advisory minors and exit 0.
    """
    assert _DOGFOOD.hard_content_channels == []


# ---------------------------------------------------------------------------------------
# Coverage is disclosed on every verdict, not only the clean ones.
# ---------------------------------------------------------------------------------------


def test_coverage_is_disclosed_even_on_a_red_run():
    """A red run whose detectors were half off is misread just as badly as a green one: the
    reader concludes the tool looked everywhere and found only this."""
    md = render_pr_comment([_regression()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert "coverage:" in md
    assert "no scenario declares `tools`" in md


def test_a_fully_armed_run_carries_no_coverage_footnote():
    assert "coverage:" not in render_pr_comment([_unchanged()], "m1", "m2", 5, "g", (), _ARMED)
    assert "coverage:" not in render_cli([_unchanged()], "m1", "m2", 5, (), _ARMED)


def test_the_inert_list_names_every_channel_and_why():
    census = ChannelCensus(tools_declared=False, assertions_declared=False, judge_enabled=False)
    joined = " ".join(census.inert)
    assert "tool trajectory + arguments" in joined
    assert "semantic judge" in joined
    assert "text assertions" in joined
    # Each entry carries its REASON, so the reader can act on it without reading our source.
    assert joined.count("(") == 3


# ---------------------------------------------------------------------------------------
# Back-compatibility: the census is additive.
# ---------------------------------------------------------------------------------------


def test_omitting_the_census_changes_nothing():
    """Every existing caller passes no census. The 493-test suite is the real assertion here;
    this pins the contract so a later refactor cannot make the parameter mandatory."""
    assert render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", ()) == render_pr_comment(
        [_unchanged()], "m1", "m2", 5, "groq", (), None
    )
    assert render_cli([_unchanged()], "m1", "m2", 5, ()) == render_cli(
        [_unchanged()], "m1", "m2", 5, (), None
    )


def test_an_underpowered_run_keeps_its_own_warning_when_both_apply():
    """Two independent ways to be unable to fail. The run-count one is the more specific
    diagnosis (it names a fix: more runs), so it must not be masked by the census."""
    md = render_pr_comment([_unchanged("s1")], "m1", "m2", 2, "groq", ["s1"], _DOGFOOD)
    assert "could not have reported a regression at all" in md
    assert "looks safe to adopt" not in md


# ---------------------------------------------------------------------------------------
# No green tick may sit above a line that says the run could not conclude (MP-116's rule).
# ---------------------------------------------------------------------------------------


def test_the_header_does_not_lead_green_over_an_inert_run():
    """MP-116 fixed exactly this contradiction for a partly blind run -- a green header above
    a footer that called the model only partially cleared. Shipping it again for inert
    channels would be the same defect with a new cause. The existing branch's own comment
    states the rule: 'a green check over a run that could not have gone red is the worst
    header we ship.'"""
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert md.splitlines()[0].startswith("❔")
    assert "no behavioral change" not in md.splitlines()[0]


def test_the_unchanged_bucket_does_not_wear_a_green_tick_over_inert_channels():
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert "**UNCHANGED (1)** ✅" not in md
    assert "on the channels that were live" in md


def test_an_armed_run_keeps_its_green_header_and_tick():
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _ARMED)
    assert md.splitlines()[0].startswith("✅")
    assert "**UNCHANGED (1)** ✅" in md


def test_a_real_regression_still_leads_with_the_alarm_not_the_census():
    """The census must never downgrade a genuine red. A regression outranks every coverage
    caveat -- the user needs the failure first."""
    md = render_pr_comment([_regression()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert md.splitlines()[0].startswith("\U0001f6a8")


# ---------------------------------------------------------------------------------------
# The CLI surface must stay cp1252-encodable. This is the test whose absence let the first
# cut of MP-138 ship a crash.
# ---------------------------------------------------------------------------------------

_ALL_CENSUSES = [
    ChannelCensus(tools_declared=t, assertions_declared=a, judge_enabled=j)
    for t in (True, False)
    for a in (True, False)
    for j in (True, False)
]


def test_the_cli_output_is_cp1252_encodable_for_every_census():
    """`[M] 2026-08-30` The first cut of MP-138 put U+2192 into `_census_clearance` and routed
    it to the console. On a default Windows console that raised `UnicodeEncodeError` -- and it
    fired ONLY when `hard_content_channels` was empty, i.e. only in the exact state the feature
    exists to serve. It aborted before `last-report.md` was written and exited 1, which the
    GitHub Action cannot tell from a real regression.

    The module states this invariant at the top of the file and nothing enforced it: a grep for
    `cp1252` across `tests/` returned nothing. 508 tests were green over the crash because none
    of them ever ENCODED the string.
    """
    for census in _ALL_CENSUSES:
        for results in ([_unchanged()], [_regression()], [_unchanged(), _regression("s2")]):
            out = render_cli(results, "m1", "m2", 5, (), census)
            out.encode("cp1252")  # must not raise
    # and with the run-count warning also present
    render_cli([_unchanged("s1")], "m1", "m2", 2, ["s1"], _DOGFOOD).encode("cp1252")


def test_the_markdown_keeps_its_arrow():
    """The fix must not over-correct: the PR comment is written with encoding='utf-8' and its
    sibling lines all use the arrow, so stripping it there would be a cosmetic regression."""
    md = render_pr_comment([_unchanged()], "m1", "m2", 5, "groq", (), _DOGFOOD)
    assert "→ This run had NO CI-failing channel" in md


def test_the_cli_bucket_loses_its_green_marker_when_inert():
    """The Markdown bucket was guarded in the first cut and the CLI one was not -- so the
    surface a first-run user actually reads still showed a green OK above a NOT-cleared line."""
    out = render_cli([_unchanged()], "m1", "m2", 5, (), _DOGFOOD)
    assert "[green]OK[/]" not in out
    assert "[yellow]OK?[/]" in out
    assert "[green]OK[/]" in render_cli([_unchanged()], "m1", "m2", 5, (), _ARMED)


def test_both_clearances_are_printed_when_both_apply():
    """Too few runs and too few armed channels are INDEPENDENT diagnoses. An earlier cut joined
    them with `or` and printed only the first, hiding half the reason a clearance was withheld."""
    md = render_pr_comment([_unchanged("s1")], "m1", "m2", 2, "groq", ["s1"], _DOGFOOD)
    assert "could not have reported a regression at all" in md  # the run-count diagnosis
    assert "NOT cleared on content" in md  # the channel diagnosis
    assert "looks safe to adopt" not in md


def test_the_judge_reason_is_carried_not_inferred():
    """`ChannelCensus` cannot tell "no judge_model in the config" from "disabled because the
    provider is the offline fake". Inferring the first would print a false statement inside the
    disclosure that exists to be honest."""
    offline = ChannelCensus(
        tools_declared=False,
        assertions_declared=True,
        judge_enabled=False,
        judge_off_reason="disabled on the offline `fake` provider",
    )
    joined = " ".join(offline.inert)
    assert "disabled on the offline `fake` provider" in joined
    assert "no `judge_model` configured" not in joined
    # the default is preserved for the ordinary case
    assert "no `judge_model` configured" in " ".join(_DOGFOOD.inert)
