"""MP-194 — the coverage disclosure contradicted the verdict printed three lines above it.

`[M] 2026-09-06`, reproduced on `2ef82d5`. A scenario that does NOT declare `tools` while its
recorded traces DO carry `tool_calls`:

    REGRESSION refund_gbp: tool-call behavior changed: ['escalate'] -> [] (confidence 0.99)
    -> Pin to m1 until resolved.
    coverage: inert this run -- tool trajectory + arguments (no scenario declares `tools`);
    ...; 1 of 1 scenario(s) called no tool, so no CI-failing channel could see a content
    change in them (refund_gbp)
    EXIT=1

**Three false statements in one line**: the tool-trajectory channel is exactly what fired; the
baseline called `escalate` in 5 of 5 runs; and a CI-failing channel had just seen the change.

Root cause: `_tool_live` requires BOTH `scenario.input["tools"]` and a call in the traces, while
the diff engine requires neither and reads tool calls straight off the traces. The census and the
verdict answered the same question two different ways.

**The conjunction is NOT the bug and is deliberately untouched.** An FP review blocked testing
`tool_active` alone because it would GRANT clearances the previous version withheld, and the
conjunction is what keeps the blind set a superset for every input. Conservatism is right here.
Stating a falsehood in order to be conservative is not. So these scenarios stay uncleared, and
the disclosure stops claiming they called no tool.

`[M]` The trigger is narrow, and I failed to reproduce it twice before finding it: it needs the
scenario to omit `tools` while the traces carry them. Both near-misses are controls below,
because a fix aimed at the wrong configuration would "pass" without touching the defect.
"""

from __future__ import annotations

from modelpin.report import ChannelCensus, _census_note


def _census(**kw) -> ChannelCensus:
    base = dict(
        tools_exercised=False,
        assertions_declared=False,
        judge_enabled=False,
        judge_off_reason="disabled on the offline `fake` provider",
        compared=1,
    )
    base.update(kw)
    return ChannelCensus(**base)  # type: ignore[arg-type]


def test_a_channel_that_fired_is_not_listed_as_inert() -> None:
    """The first false statement: the tool channel produced the run's only regression."""
    census = _census(blind_scenarios=("refund_gbp",), undeclared_tool_calls=("refund_gbp",))
    assert not any(
        "tool trajectory" in entry for entry in census.inert
    ), "The tool channel is listed as inert in a run where it fired: " + str(census.inert)


def test_the_disclosure_does_not_say_a_scenario_called_no_tool_when_it_did() -> None:
    """The second and third: 'called no tool' and 'no CI-failing channel could see a change'."""
    note = _census_note(
        _census(blind_scenarios=("refund_gbp",), undeclared_tool_calls=("refund_gbp",))
    )
    assert note is not None
    assert "called no tool" not in note, note
    assert "no CI-failing channel could see a content change" not in note, note


def test_the_scenario_is_still_named_and_still_not_credited() -> None:
    """Conservatism is preserved: the fix must not turn a withheld clearance into a granted one.

    This is the assertion that would fail if someone "simplified" the fix by dropping these
    scenarios out of `blind_scenarios` -- which is the change the FP review blocked.
    """
    note = _census_note(
        _census(blind_scenarios=("refund_gbp",), undeclared_tool_calls=("refund_gbp",))
    )
    assert note is not None
    assert "refund_gbp" in note, note
    assert "not credited" in note, note


def test_a_suite_with_both_kinds_reports_them_separately() -> None:
    """One sentence cannot be true of both, so they must not be merged into one count."""
    note = _census_note(
        _census(
            compared=2,
            blind_scenarios=("silent_one", "fired_one"),
            undeclared_tool_calls=("fired_one",),
        )
    )
    assert note is not None
    assert "1 of 2 scenario(s) called no tool" in note, note
    assert "silent_one" in note and "fired_one" in note, note
    assert "not credited" in note, note


# --- controls: the two configurations I failed to reproduce the defect in ------------------


def test_the_ordinary_blind_wording_is_unchanged() -> None:
    """A scenario that genuinely called no tool must read exactly as it always did.

    This is the overwhelmingly common case, and changing its wording would be a regression in
    a published disclosure for the sake of a narrow one.
    """
    note = _census_note(_census(blind_scenarios=("greet",)))
    assert note is not None
    assert "1 of 1 scenario(s) called no tool" in note, note
    assert "no CI-failing channel could see a content change" in note, note
    assert "not credited" not in note, note


def test_a_declared_but_uncalled_tool_still_reads_as_inert() -> None:
    """MP-159's case: somebody asked for the channel and no model ever called the tool. That
    channel really is inert and must keep saying so -- its remedy is different."""
    census = _census(blind_scenarios=("s",), declared_unused_tools=("s",))
    assert any("declare `tools` but no run called one" in e for e in census.inert), census.inert


def test_a_suite_with_no_tools_anywhere_still_reads_as_inert() -> None:
    """The other MP-159 branch: nobody asked for the channel."""
    census = _census(blind_scenarios=("s",))
    assert any("no scenario declares `tools`" in e for e in census.inert), census.inert
