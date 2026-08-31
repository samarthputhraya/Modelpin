"""ADR-0032 / MP-137 -- a violated assertion that exits 0 must SAY it is not failing.

`[M] 2026-08-29`, dogfood on `kavach` with live Groq calls: `modelpin check --to allam-2-7b`
flagged **6 of 12 scenarios at confidence 1.00**, printed `-> Pin to openai/gpt-oss-120b
until resolved.` -- **and exited 0**. `[M]` An independent oracle scoring both models
against ground-truth labels Modelpin never sees confirms all 6 are TRUE positives:
tactic-recall on scam calls 0.967 -> 0.478, benign false-alarm rate 0.000 -> 0.333.
Precision was perfect: 6 flagged, 6 real, 0 false.

The tool measured a real, severe regression and let CI go green, because `fmt_drift` caps at
`changed_minor`. ADR-0032 records why the cap stands for now and what would lift it. This
module pins the INTERIM the ADR requires: reporting a finding and then silently declining to
act on it is the same defect MP-138/MP-140/MP-141 removed elsewhere.

Renderer-only, so ADR-0030 does not block it. The promotion ADR-0032 gates DOES touch
`modelpin/diff/` and stays frozen.

The three findings behind the ADR, each measured, are pinned by the tests below so the
decision cannot be quietly reversed on a wrong premise:

  1. `fmt_drift` is the only verdict signal with no effect-size floor.
  2. The p-gate already requires >=4 absolute violations at every N>=4.
  3. `must_contain` is byte-exact, so `TOTAL:` -> `Total:` fires at p=0.0040 -- maximum
     confidence -- which is why a bigger floor is NOT the fix.
"""

from __future__ import annotations

import pytest

from modelpin.diff import ALPHA
from modelpin.diff.stats import permutation_pvalue_mean
from modelpin.models import Assertion, DiffResult, DiffSignals, DiffVerdict, Scenario, Trace
from modelpin.report import render_cli

_ASSERTION_EXPLANATION = "output format drift: violates the scenario's text assertions"


def _result(sid, verdict, explanation):
    return DiffResult(
        scenario_id=sid,
        from_model="m1",
        to_model="m2",
        verdict=verdict,
        confidence=1.0,
        explanation=explanation,
        signals=DiffSignals(),
    )


# --- the interim disclosure -------------------------------------------------------------


def test_a_violated_assertion_that_exits_zero_says_so():
    out = render_cli(
        [_result("s", DiffVerdict.changed_minor, _ASSERTION_EXPLANATION)], "m1", "m2", 5
    )
    assert "still exits 0" in out
    assert "ADR-0032" in out


def test_the_note_is_silent_when_a_real_regression_already_fails_the_build():
    """Anti-noise: if the run exits 1 anyway, there is nothing being declined."""
    out = render_cli(
        [
            _result("r", DiffVerdict.regression, "refusal rate 0% -> 100%"),
            _result("s", DiffVerdict.changed_minor, _ASSERTION_EXPLANATION),
        ],
        "m1",
        "m2",
        5,
    )
    assert "still exits 0" not in out


def test_the_note_is_silent_for_a_minor_that_is_not_an_assertion():
    """The argument gate is also advisory, but ADR-0029 already governs it and its rationale
    is different -- an uncalibrated floor, not a byte-exact comparison."""
    out = render_cli(
        [
            _result(
                "s",
                DiffVerdict.changed_minor,
                "tool-call arguments changed: amount 49.99 -> 4999.00",
            )
        ],
        "m1",
        "m2",
        5,
    )
    assert "still exits 0" not in out


def test_the_note_is_cp1252_encodable():
    """`[M] 2026-08-30` MP-138 crashed `modelpin check` on a default Windows console."""
    render_cli(
        [_result("s", DiffVerdict.changed_minor, _ASSERTION_EXPLANATION)], "m1", "m2", 5
    ).encode("cp1252")


def test_the_matched_reason_is_the_one_the_engine_emits():
    """The renderer matches a string `modelpin/diff/` produces. If that wording ever changes
    the note would silently stop firing, so pin them equal here -- reading `diff/`, never
    editing it (ADR-0030)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "modelpin" / "diff" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert _ASSERTION_EXPLANATION in src


# --- the ADR's three findings, pinned ---------------------------------------------------


def test_fmt_drift_is_the_only_signal_with_no_effect_size_floor():
    """`[M]` Finding 1, and the reason the cap is LEGAL rather than arbitrary: ADR-0002 says
    a signal is a regression only when it clears p AND a floor. `fmt_drift` has no floor, so
    capping it at `changed_minor` is what keeps it compliant."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "modelpin" / "diff" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "fmt_drift = fmt_p <= ALPHA and fmt_delta > 0" in src, "the floorless gate moved"
    for floored in ("MIN_TOOL_TVD", "MIN_REFUSAL_DELTA", "MIN_SEMANTIC_DELTA", "MIN_TOOL_ARG_TVD"):
        assert floored in src


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8])
def test_the_p_gate_already_requires_at_least_four_violations(n):
    """`[M]` Finding 2: at every N>=4 the first firing cell is `0 -> 4`. An explicit
    effect-size floor would be inert at the shipped N, which agrees with ADR-0002's own note
    that the floors first bind at N=9-12."""
    firing = [
        (b, c)
        for b in range(n + 1)
        for c in range(n + 1)
        if permutation_pvalue_mean([1.0] * b + [0.0] * (n - b), [1.0] * c + [0.0] * (n - c))
        <= ALPHA
        and (c / n) - (b / n) > 0
    ]
    assert firing, f"fmt_drift can never fire at N={n}"
    assert min(c for _, c in firing) >= 4


@pytest.mark.parametrize("n", [2, 3])
def test_the_gate_is_unreachable_or_total_at_tiny_run_counts(n):
    firing = [
        (b, c)
        for b in range(n + 1)
        for c in range(n + 1)
        if permutation_pvalue_mean([1.0] * b + [0.0] * (n - b), [1.0] * c + [0.0] * (n - c))
        <= ALPHA
        and (c / n) - (b / n) > 0
    ]
    if n == 2:
        assert firing == [], "at 2v2 no assertion violation can reach ALPHA"
    else:
        assert all(c == n for _, c in firing), "at 3v3 only a 100% violation rate fires"


def test_a_capitalisation_change_fires_at_maximum_confidence():
    """`[M]` Finding 3, the decisive one, and why a bigger floor is NOT the fix: the
    comparison is byte-exact (`s in out`), so a purely cosmetic change violates on EVERY run
    -- effect size 1.0, which every floor admits -- and the permutation test's protection is
    INVERTED, because a systematic reformatting looks maximally consistent."""
    from modelpin.diff import diff_scenario

    def traces(text):
        return [
            Trace(
                scenario_id="s",
                model_id="m",
                final_output=text,
                tool_calls=[],
                refused=False,
                latency_ms=10,
                tokens_out=5,
            )
            for _ in range(5)
        ]

    scenario = Scenario(
        id="s",
        name="s",
        kind="single",
        input={"messages": [{"role": "user", "content": "invoice total?"}]},
        assertions=Assertion(must_contain=["TOTAL"]),
    )
    r = diff_scenario(
        "s", "m1", "m2", traces("TOTAL: 42.00"), traces("Total: 42.00"), scenario, "strict"
    )
    assert r.verdict is DiffVerdict.changed_minor, "promoted, this would fail a build"
    assert r.confidence >= 0.99
    assert _ASSERTION_EXPLANATION in r.explanation


def test_the_assertion_channel_is_absent_from_the_scored_calibration_set():
    """`[M]` Finding 4 / ADR-0032 condition 2: 0 of 13 calibration scenarios declare
    assertions, so the false-positive rate of this channel on a CI-failing path is `[A]`.
    When someone adds one, this test fails and points them at the ADR's conditions."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples" / "calibration"
    with_assertions = [
        p.name
        for p in sorted(root.glob("*.json"))
        if isinstance(json.loads(p.read_text(encoding="utf-8")), dict)
        and json.loads(p.read_text(encoding="utf-8")).get("assertions")
    ]
    assert with_assertions == [], (
        "an assertion scenario entered the calibration set -- ADR-0032 condition 2 may now "
        f"be satisfiable; re-read the ADR before promoting: {with_assertions}"
    )
