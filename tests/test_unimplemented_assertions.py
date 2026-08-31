"""MP-142 -- `expected_tool_calls` and `output_schema` are declared, set, and read by nothing.

`[M] 2026-08-31` References anywhere under `modelpin/` that READ either field: zero. The
only non-declaration references SET them. They are write-only fields.

`[M]` Proven by DIFFERENTIAL rather than by grep, because grep cannot show that the field
has no effect -- five trace configurations, verdict/confidence/explanation byte-identical
with the field set and with `assertions=None`:

    baseline SATISFIES exp, candidate VIOLATES (drops issue_refund)  regression @0.992  ==
    baseline VIOLATES, candidate SATISFIES                           regression @0.992  ==
    both SATISFY (no change)                                          unchanged @1.0    ==
    both VIOLATE identically                                          unchanged @1.0    ==
    candidate calls a tool the expectation forbids entirely          regression @0.992  ==

The first and last cases matter: an earlier reproduction used only "both violate
identically", which yields `unchanged` whether or not the field is implemented -- Modelpin
measures change RELATIVE to baseline -- so it could not discriminate the defect it was
named for. A verdict that is identical even when the candidate violates an expectation the
baseline satisfied is what "consulted by nothing" actually means.

WHY THE FIELDS ARE NOT DELETED HERE. `[M]` `compute_suite_hash` hashes the VALIDATED
pydantic model, so removing them changes the content hash of both shipped suites:
`examples/report-suite` (role `public`, ADR-0009) `sha256:ffd99774f681` ->
`sha256:eed334061b5e`, and `examples/suite` (role `score`, the held-out FP set) ->
`sha256:5482ccd734fd`. The first is cited in a published report's own reproduce block, in
`GOLDEN_SUITE_HASH`, and by the frozen `drift-suite` fixture behind the Drift Map. That is
a public suite-version bump, not the one-hour edit the row assumed, so it is filed
separately. What this row removes is the word SILENTLY.

Implementing the fields instead would touch `modelpin/diff/`, FROZEN under ADR-0030.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.diff import diff_scenario
from modelpin.models import Assertion, Scenario, ToolCall, Trace
from modelpin.report.suite import compute_suite_hash
from modelpin.scenarios import load_scenarios

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]

_EXPECTED = ["lookup_order", "issue_refund"]


def _scn(assertions):
    return Scenario(
        id="s",
        name="s",
        kind="single",
        input={
            "messages": [{"role": "user", "content": "refund my order"}],
            "tools": [{"type": "function", "function": {"name": "lookup_order", "parameters": {}}}],
        },
        assertions=assertions,
    )


def _traces(names):
    return [
        Trace(
            scenario_id="s",
            model_id="m",
            final_output="ok",
            tool_calls=[ToolCall(name=n, arguments={}) for n in names],
            refused=False,
            latency_ms=10,
            tokens_out=5,
        )
        for _ in range(5)
    ]


# --- the defect: the field has no effect, including when it is violated ----------------


@pytest.mark.parametrize(
    "base_calls,cand_calls,label",
    [
        (_EXPECTED, ["lookup_order"], "baseline satisfies, candidate violates"),
        (["lookup_order"], _EXPECTED, "baseline violates, candidate satisfies"),
        (_EXPECTED, _EXPECTED, "both satisfy"),
        ([], [], "both violate identically"),
        (_EXPECTED, ["delete_account"], "candidate calls a tool never expected"),
    ],
)
def test_expected_tool_calls_changes_no_verdict(base_calls, cand_calls, label):
    b, c = _traces(base_calls), _traces(cand_calls)
    with_field = diff_scenario(
        "s", "m1", "m2", b, c, _scn(Assertion(expected_tool_calls=_EXPECTED)), "strict"
    )
    without = diff_scenario("s", "m1", "m2", b, c, _scn(None), "strict")
    assert with_field.verdict == without.verdict, label
    assert with_field.confidence == without.confidence, label
    assert with_field.explanation == without.explanation, label


def test_output_schema_changes_no_verdict():
    b, c = _traces(_EXPECTED), _traces(["lookup_order"])
    schema = {"type": "object", "required": ["refund_id"]}
    with_field = diff_scenario(
        "s", "m1", "m2", b, c, _scn(Assertion(output_schema=schema)), "strict"
    )
    without = diff_scenario("s", "m1", "m2", b, c, _scn(None), "strict")
    assert (with_field.verdict, with_field.explanation) == (without.verdict, without.explanation)


# --- what this row actually changes: the silence ---------------------------------------


def test_the_cli_says_the_field_is_not_checked(tmp_path):
    """The defect MP-142 names is the word SILENTLY. A user who writes
    `expected_tool_calls` and sees a clean run has no way to learn it was never compared."""
    suite = tmp_path / "scenarios"
    suite.mkdir()
    (suite / "s.json").write_text(
        '{"id":"s","name":"s","kind":"single",'
        '"input":{"messages":[{"role":"user","content":"hi"}]},'
        '"assertions":{"expected_tool_calls":["lookup_order"]}}',
        encoding="utf-8",
    )
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app,
        [
            "check",
            "--to",
            "m2",
            "--from",
            "m1",
            "--provider",
            "fake",
            "--scenarios-dir",
            str(suite),
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--store-dir",
            str(tmp_path / "empty"),
        ],
    )
    out = " ".join(r.output.split())
    assert "records but never checks" in out, r.output
    assert "expected_tool_calls" in out, r.output
    assert "must_contain" in out, r.output  # the remedy names the field that IS compared


def test_a_suite_using_only_live_assertions_gets_no_note(tmp_path):
    """Anti-noise: the disclosure must not fire on a suite that declares nothing dead."""
    suite = tmp_path / "scenarios"
    suite.mkdir()
    (suite / "s.json").write_text(
        '{"id":"s","name":"s","kind":"single",'
        '"input":{"messages":[{"role":"user","content":"hi"}]},'
        '"assertions":{"must_contain":["hi"]}}',
        encoding="utf-8",
    )
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        app,
        [
            "check",
            "--to",
            "m2",
            "--from",
            "m1",
            "--provider",
            "fake",
            "--scenarios-dir",
            str(suite),
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--store-dir",
            str(tmp_path / "empty"),
        ],
    )
    assert "records but never checks" not in r.output


def test_the_shipped_demo_declares_no_assertion_the_engine_cannot_check():
    """`[M]` `angry_customer` shipped an `Assertion` whose ONLY field was
    `expected_tool_calls` -- an assertion that asserts nothing, in the suite a brand-new
    user runs first, which MP-141's census then counted as armed coverage."""
    from modelpin.demo import write_demo

    root = Path(tempfile.mkdtemp(prefix="modelpin-mp142-"))
    write_demo(root)
    for s in load_scenarios(str(root / "modelpin-demo" / "scenarios")):
        a = s.assertions
        if a is None:
            continue
        assert a.expected_tool_calls is None, s.id
        assert a.output_schema is None, s.id
        assert a.must_contain or a.must_not_contain, f"{s.id} asserts nothing"


# --- the guard: this row must NOT move a published content hash ------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("examples/report-suite", "sha256:ffd99774f681"),
        ("examples/suite", "sha256:44cbde8e3b74"),
    ],
)
def test_the_published_suite_hashes_are_untouched_by_this_row(path, expected):
    """`[M]` Deleting the dead fields would move these to `sha256:eed334061b5e` and
    `sha256:5482ccd734fd`. The first is cited in a published report's reproduce block, in
    `GOLDEN_SUITE_HASH`, and by the frozen drift-suite fixture. Whoever eventually deletes
    the fields must bump the suite version deliberately and update this test in the same
    commit -- which is the point: it cannot happen as a side effect."""
    assert compute_suite_hash(load_scenarios(str(REPO / path))) == expected
