"""MP-207: the per-channel exposure report, and the two ways it could lie quietly.

Why this exists. `[M] 2026-09-07` the published 3.6% false-positive bound was carried entirely
by the semantic and argument channels: across the run of record's **710** same-model-null trials,
**zero** had `tool_call_match < 1.0` and **zero** had `format_valid == False`, while the 46
deliberately-perturbed recall trials produced 10 and 7. `MIN_TOOL_TVD` therefore had a
false-positive exposure of exactly zero trials, and `0/0` on the signal a migration tool exists
for is not a low rate — it is no measurement at all.

`scripts/channel_exposure.py` is what turns that from an assumption into a number, so the two
ways it could silently mislead are pinned here:

1. **The falsy zero.** `tool_call_match` of exactly `0.0` — completely disjoint trajectories, the
   single most important value in this measurement — is falsy in Python. `signals.get(...) or 1.0`
   reports it as a perfect match. That bug was written, and caught, during this row's own
   scenario review, where it turned 10 exposed recall trials into 0.
2. **Attribution by prose.** A false alarm is attributed to a channel by the reason string
   `diff/__init__.py` wrote. Prose that something parses is an interface; reworded, every false
   alarm would silently become "unattributed" and both per-channel bounds would read 0/n.

All offline: no providers, no network, no key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import DiffResult, DiffSignals, DiffVerdict  # noqa: E402
from scripts import channel_exposure as ce  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DIFF_SRC = (REPO / "modelpin" / "diff" / "__init__.py").read_text(encoding="utf-8")
RUN_DIR = REPO / "reports" / "channel-exposure" / "2026-09-07"


def _result(verdict=DiffVerdict.unchanged, explanation="", **signals) -> DiffResult:
    return DiffResult(
        scenario_id="s",
        from_model="m",
        to_model="m",
        verdict=verdict,
        explanation=explanation,
        signals=DiffSignals(**signals),
    )


# --- 1. the falsy zero ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, True),  # completely disjoint trajectories - the value most easily lost
        (0.2, True),
        (0.8, True),
        (1.0, False),
        (None, False),  # not measured is not exposure
    ],
)
def test_tool_exposure_counts_a_zero_match_as_exposed(value, expected):
    assert ce.tool_exposed(_result(tool_call_match=value)) is expected


def test_the_falsy_zero_would_change_the_published_number():
    """Not a hypothetical. This asserts the exact mutation - `or 1.0` - produces a different
    answer on the value it is most likely to be applied to, so the guard above has teeth."""
    res = _result(tool_call_match=0.0)
    naive = (res.signals.tool_call_match or 1.0) < 1.0
    assert ce.tool_exposed(res) and not naive


# --- 2. attribution by prose ---------------------------------------------------------------


def test_every_channel_marker_still_appears_in_the_engine():
    """The parse contract. If `diff/__init__.py` rewords a reason, this fails HERE rather than
    quietly reattributing every false alarm on that channel to nothing."""
    for channel, marker in ce.CHANNEL_REASONS.items():
        assert marker in DIFF_SRC, (
            f"channel {channel!r} is attributed by the literal {marker!r}, which no longer "
            "appears in modelpin/diff/__init__.py. Update CHANNEL_REASONS deliberately; do not "
            "delete this assertion."
        )


def test_a_flagged_verdict_is_attributed_to_the_channel_that_named_it():
    tool = _result(
        DiffVerdict.regression,
        "tool-call behavior changed: ['a', 'b'] -> ['a']",
        tool_call_match=0.2,
    )
    sem = _result(
        DiffVerdict.regression,
        "semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%)",
        tool_call_match=1.0,
    )
    assert ce.channels_fired(tool) == ["tool"]
    assert ce.channels_fired(sem) == ["semantic"]
    assert ce.channels_fired(_result()) == []


# --- 3. the pooling refusal ADR-0038 D2 turns into code ------------------------------------


def test_it_refuses_to_read_the_run_of_record():
    """`examples/fp-suite-v2` is exposure-maximising by construction, so its rate is a
    near-worst-case CONDITIONAL figure. Averaged with a corpus built to be representative it
    describes neither, and the cheapest way to do that by accident is one glob."""
    with pytest.raises(SystemExit, match="run of record"):
        ce.summarise(
            [str(REPO / "reports" / "fp-runs" / "2026-09-07" / "s2b-fp-suite-gpt-4o-mini.jsonl")]
        )


# --- 4. the committed run --------------------------------------------------------------------


@pytest.fixture(scope="module")
def summary() -> dict:
    paths = sorted(str(p) for p in RUN_DIR.glob("v2*.jsonl"))
    if not paths:
        pytest.skip("reports/channel-exposure/2026-09-07 is not present")
    return ce.summarise(paths)


def test_the_negative_control_held(summary):
    """`[M]` ADR-0038 D1d: the anchor has one mandatory tool call and one fully specified output
    line, so it CANNOT vary by design. If it moves, the variance is the harness's or the
    adapter's and no other number in the run is readable."""
    a = summary["anchor"]
    assert a["trials"] > 0
    assert a["tool_exposed"] == 0, "the negative control moved on the tool channel"
    assert a["assertion_exposed"] == 0, "the negative control moved on the assertion channel"
    assert a["flagged"] == 0


def test_the_tool_channel_finally_has_a_denominator(summary):
    """The whole point of the row. Before this corpus the tool channel's false-positive rate
    was 0/0 over 710 same-model trials."""
    assert summary["tool"]["exposed"] >= 20, (
        "fewer than 20 tool-exposed trials: the corpus, not the engine, is the blocker, and "
        "that is a stated limit rather than a bound (ADR-0038 falsifier)."
    )


def test_the_assertion_channels_status_is_reported_not_assumed(summary):
    """`[M]` This channel came back with ZERO exposure even on the corpus built for it. That is
    a real, publishable finding — but it must never be rendered as a rate, because 0/0 is
    exactly the thing MP-207 was opened to stop being published."""
    a = summary["assertion"]
    assert a["in_scope"] > 0, "no scenario declared assertions at all"
    if a["exposed"] == 0:
        text = "\n".join(ce.render(summary))
        assert "ZERO exposure" in text and "no bound" in text


def test_the_bound_is_over_scored_trials_not_merely_exposed_ones(summary):
    """`[M] 2026-09-07` 37 trials moved the tool trajectory but only 26 could have fired at any
    ALPHA. Publishing 1/37 rather than 1/26 pads the denominator with trials incapable of
    producing a false positive and flatters the rate — the exact failure ADR-0022 exists to
    prevent, and the reason `scored` is tracked separately from `exposed`."""
    t = summary["tool"]
    assert t["scored"] <= t["exposed"]
    assert t["scored"] >= 20, "the acceptance bar is scored trials, not exposed ones"
    text = "\n".join(ce.render(summary))
    assert f"false alarms, over the SCORED ones   : {t['flagged']}/{t['scored']}" in text
    assert f"{t['exposed']} trial(s)" in text, "the exposure count is still published beside it"


# --- 5. the doc, tied to the artifacts ------------------------------------------------------
#
# `[M] 2026-09-07` this section exists because its absence was demonstrated, not imagined: the
# tool bound was corrected from 1/37 (ub 12.2%) to 1/26 (ub 17.0%) in the table, the prose 24
# lines below kept saying 12.2%, and the whole suite stayed green. Every OTHER measurement block
# on that page is pinned to its artifacts; this one was not, and a claims review found the stale
# number rather than a test.

DOC = REPO / "docs" / "fp-measurement.md"


@pytest.fixture(scope="module")
def section() -> str:
    doc = DOC.read_text(encoding="utf-8")
    start = doc.index("## Channel exposure")
    return doc[start : doc.index("\n## ", start + 1)]


def test_the_published_channel_table_is_the_artifacts_table(summary, section):
    t, a, o = summary["tool"], summary["assertion"], summary["overall"]
    ub_tool = f"{100.0 * ce.upper_bound_95(t['flagged'], t['scored']):.1f}%"
    assert f"**{t['exposed']}** (`tool_call_match < 1.0`) | **{t['scored']}**" in section
    assert f"**{ub_tool}** ({t['flagged']}/{t['scored']} = " in section
    assert f"**{a['exposed']}**, of {a['in_scope']} in scope" in section
    assert f"{o['scored']}, of {o['trials']} reached | {o['flagged']}" in section


def test_no_superseded_bound_survives_anywhere_in_the_section(summary, section):
    """The exact failure this file was extended for. A bound that has been corrected must not
    still appear as prose further down the page."""
    live = (
        f"{100.0 * ce.upper_bound_95(summary['tool']['flagged'], summary['tool']['scored']):.1f}%"
    )
    stale = (
        f"{100.0 * ce.upper_bound_95(summary['tool']['flagged'], summary['tool']['exposed']):.1f}%"
    )
    assert live in section
    assert stale not in section, (
        f"the section still quotes {stale}, the bound over EXPOSED trials. The published bound "
        f"is {live}, over SCORED trials (ADR-0022)."
    )


def test_the_section_states_the_concentration_the_artifacts_show(summary, section):
    """`[M]` 24 of the 26 scored trials come from one scenario. A bound whose denominator is one
    shape must say so where the bound is stated, not only in a backlog row."""
    per = summary["per_scenario"]
    top = max(per.items(), key=lambda kv: kv[1]["tool_exposed"])
    assert f"**{top[1]['tool_exposed']} come from one scenario** (`{top[0]}`)" in section
    assert "one dominant shape" in section


def test_the_section_says_the_run_was_extended_rather_than_pre_registered(section):
    """ADR-0038 registered 240 trials on two surfaces; 480 on four were run. Calling that
    pre-registered would claim a stronger design than was executed."""
    assert "sequentially extended, not as" in section and "pre-registered" in section
    assert "240 trials on two surfaces" in section and "480 on" in section


def test_the_section_discloses_the_pilot_and_that_both_judges_are_openai(section):
    assert "pilot" in section.lower() and "gpt-oss-20b" in section
    assert "both judges are OpenAI models" in section


def test_the_section_carries_a_runnable_offline_reproduce_command(section):
    assert "python scripts/channel_exposure.py reports/channel-exposure/2026-09-07/v2*.jsonl" in (
        section
    )
    assert "ALPHA 0.05" in section and "MIN_TOOL_TVD 0.5" in section
