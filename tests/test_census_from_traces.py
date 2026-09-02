"""MP-159 -- an UNUSED `input.tools` key buys a false green clearance.

The channel census counts what a scenario DECLARES, not what any run actually DID.
`cli.py:522-524` reads `s.input.get("tools")` off the SUITE; it never reads the recorded
traces. So a scenario that declares a tool no model ever calls arms the "tool trajectory"
channel in the census, `_census_clearance` (`report/__init__.py:217`) raises no objection,
and an affirmative "looks safe to adopt" stands over a run in which no CI-failing channel
could see a content change at all.

`[M] 2026-08-31`, reproduced end to end through the real CLI on canned offline traces.
Two directories with BYTE-IDENTICAL fixtures (sha256 e86fbd36...) and byte-identical
configs, whose scenarios differ ONLY by an `input.tools` key that no run on either side
ever exercises (`tool_calls: []` in every one of the 20 traces):

    without `tools`:  .modelpin/last-report.md
        "could not measure ... `new-model` is NOT cleared on content"   exit 0
    with the unused `tools`:
        "no behavioral change ... `new-model` looks safe to adopt."     exit 0

The content underneath is a total inversion -- baseline "FRAUD DETECTED: block this
transaction" against candidate "Looks fine, approve it. asdf qwerty zzzz garbage." --
and the second run reports it as safe.

`[M]` The traces DO carry the information the census needs: `Trace.tool_calls:
list[ToolCall]` (`models.py:133`), populated from real responses at `providers/openai.py:428`
and `providers/google.py:406`, and persisted in the stored baseline. `[M]` What is NOT a
usable proxy: `DiffSignals.tool_call_match` is 1.0 on this run -- a perfect tool match
between two sides that made zero tool calls -- so only the traces can tell "a tool was
called" from "a tool was merely declared".

The first two tests FAIL today and pass once the census is derived from the traces.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()

_BASE_OUT = "FRAUD DETECTED: block this transaction"
_CAND_OUT = "Looks fine, approve it. asdf qwerty zzzz garbage."


def _fixtures(*, call_tool: bool, invert: bool = True) -> list:
    """Canned traces for both sides.

    ``call_tool`` is the ONLY thing MP-159's census reads: it decides whether the runs
    actually INVOKE the tool the scenario may declare. ``invert`` flips the candidate's
    prose to the opposite answer, which no live channel can see without a judge.
    """
    calls = [{"name": "block_transaction", "arguments": {"id": "t-1"}}] if call_tool else []
    return [
        {
            "scenario_id": "fraud_check",
            "model_id": "old-model",
            "tool_calls": calls,
            "final_output": _BASE_OUT,
            "refused": False,
        },
        {
            "scenario_id": "fraud_check",
            "model_id": "new-model",
            "tool_calls": calls,
            "final_output": _CAND_OUT if invert else _BASE_OUT,
            "refused": False,
        },
    ]


_CONFIG = "models:\n  - old-model\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n"


def _suite(
    root: Path, *, declare_tools: bool, call_tool: bool = False, invert: bool = True
) -> Path:
    """A one-scenario suite. `declare_tools` says what the SUITE claims; `call_tool` says
    what the RUNS did. MP-159 is the whole distance between those two."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "modelpin.yaml").write_text(_CONFIG, encoding="utf-8")
    (root / "fixtures.json").write_text(
        json.dumps(_fixtures(call_tool=call_tool, invert=invert)), encoding="utf-8"
    )
    scenarios = root / "scenarios"
    scenarios.mkdir(exist_ok=True)
    inp: dict = {"messages": [{"role": "user", "content": "Is this transaction fraud?"}]}
    if declare_tools:
        # Declared and never called: no fixture trace on either side has a tool_call.
        inp["tools"] = ["block_transaction"]
    (scenarios / "fraud_check.json").write_text(
        json.dumps({"id": "fraud_check", "name": "Fraud check", "kind": "single", "input": inp}),
        encoding="utf-8",
    )
    return root


def _baseline_then_check(root: Path) -> tuple[int, str]:
    """Run the real CLI in `root`; return (check exit code, last-report.md)."""
    cwd = Path.cwd()
    try:
        os.chdir(root)
        rec = runner.invoke(
            app,
            [
                "baseline",
                "--model",
                "old-model",
                "--provider",
                "fake",
                "--fixtures",
                "fixtures.json",
            ],
        )
        assert rec.exit_code == 0, rec.output
        chk = runner.invoke(
            app,
            ["check", "--to", "new-model", "--provider", "fake", "--fixtures", "fixtures.json"],
        )
        return chk.exit_code, (root / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    finally:
        os.chdir(cwd)


def test_unused_tools_declaration_must_not_buy_a_clearance(tmp_path: Path) -> None:
    """The defect, end to end. Byte-identical fixtures; the `tools` key is the only change."""
    without = _suite(tmp_path / "without_tools", declare_tools=False)
    with_ = _suite(tmp_path / "with_tools", declare_tools=True)
    assert (without / "fixtures.json").read_bytes() == (with_ / "fixtures.json").read_bytes()

    _, md_without = _baseline_then_check(without)
    _, md_with = _baseline_then_check(with_)

    # Today's behaviour, pinned so the contrast is visible when this fails.
    assert "NOT cleared on content" in md_without

    # THE ASSERTION. No tool was called on either side, so the tool channel could not have
    # seen the content inversion, and the run must not be reported as clean.
    assert "looks safe to adopt" not in md_with, (
        "MP-159: a scenario declaring `tools` that NO run ever called bought an affirmative "
        "clearance over a total content inversion. The census reads the suite, not the traces."
    )
    assert "NOT cleared on content" in md_with


def test_declared_but_never_called_is_indistinguishable_in_the_report(tmp_path: Path) -> None:
    """Same run, stated as the invariant: a declaration alone must not change the verdict
    prose when the traces on both sides are identical."""
    without = _suite(tmp_path / "b_without", declare_tools=False)
    with_ = _suite(tmp_path / "b_with", declare_tools=True)
    _, md_without = _baseline_then_check(without)
    _, md_with = _baseline_then_check(with_)

    headline_without = md_without.splitlines()[0]
    headline_with = md_with.splitlines()[0]
    assert headline_without == headline_with, (
        "MP-159: an unused `tools` key flipped the PR-comment headline from "
        f"{headline_without!r} to {headline_with!r} with byte-identical traces on both sides."
    )


def test_traces_carry_what_the_census_needs(tmp_path: Path) -> None:
    """The fix is FEASIBLE: `Trace.tool_calls` records what actually happened.

    Passes today -- kept as the evidence that a trace-derived census has something to read,
    and that `tool_call_match` is NOT a substitute for it.
    """
    from modelpin.diff import diff_scenario
    from modelpin.providers.fake import FakeProvider
    from modelpin.replay import replay
    from modelpin.scenarios import load_scenarios

    root = _suite(tmp_path / "c_with", declare_tools=True)
    scenario = load_scenarios(str(root / "scenarios"))[0]
    adapter = FakeProvider.from_fixtures(str(root / "fixtures.json"))
    base = replay(scenario, "old-model", adapter, runs=5)
    cand = replay(scenario, "new-model", adapter, runs=5)

    assert scenario.input.get("tools")  # declared
    assert not any(t.tool_calls for t in base)  # and never called
    assert not any(t.tool_calls for t in cand)

    result = diff_scenario(
        scenario.id, "old-model", "new-model", base, cand, scenario, "strict", judge=None
    )
    # A perfect tool match between two sides that called no tools: the signal cannot stand
    # in for "the channel was live".
    assert result.signals.tool_call_match == 1.0


def test_a_tool_that_is_actually_called_still_earns_its_clearance(tmp_path: Path) -> None:
    """The anti-crying-wolf half, and the one that keeps MP-159 from being a blunt "always
    withhold". A disclosure that fires on every run discloses nothing, and the north-star
    metric is the false-POSITIVE rate -- our own coverage claims included.

    Both sides call the tool AND say the same thing, so the trajectory channel was genuinely
    live and genuinely saw no change. That clearance is earned and must survive.
    """
    root = _suite(tmp_path / "armed", declare_tools=True, call_tool=True, invert=False)
    exit_code, md = _baseline_then_check(root)
    assert exit_code == 0, md
    assert "looks safe to adopt" in md
    assert "NOT cleared on content" not in md


def test_the_remedy_never_tells_a_user_to_add_tools_they_already_declared(
    tmp_path: Path,
) -> None:
    """The row's sharpest point: the disclosure's OWN remedy was what bought the false green.
    A user who declares `tools` and is told to "add `tools`" follows the advice, the census
    goes green on the declaration, and nothing was ever measured."""
    root = _suite(tmp_path / "declared_unused", declare_tools=True)
    _, md = _baseline_then_check(root)
    assert "NOT cleared on content" in md
    assert "add `tools`" not in md, "MP-159: the remedy is the advice that caused the defect"
    assert "declared but never called in: fraud_check" in md
    assert "fraud_check" in md


def test_a_suite_that_declares_nothing_keeps_the_original_remedy(tmp_path: Path) -> None:
    """The other branch must not be collateral damage: with no `tools` anywhere, "add
    `tools`" is still exactly the right advice, and MP-138's wording stands."""
    root = _suite(tmp_path / "undeclared", declare_tools=False)
    _, md = _baseline_then_check(root)
    assert "NOT cleared on content" in md
    assert "add `tools`, a `judge_model`," in md
    assert "no scenario declares `tools`" in md


def test_the_census_cannot_be_given_a_default_and_silently_regress() -> None:
    """A tripwire on the SIGNATURE, not the behaviour.

    `[M]` The defect reachable from here is a future call site that forgets the traces. If
    `tool_active` ever gains a default, that call site compiles, the census silently reads
    "nothing was exercised", and the disclosure states a fact about the run that nobody
    measured -- the same class of false sentence MP-159 removed, arriving the other way.
    """
    import inspect

    from modelpin.cli import _channel_census as fn

    param = inspect.signature(fn).parameters["tool_active"]
    assert param.default is inspect.Parameter.empty, (
        "MP-159: `tool_active` must stay required. A default lets a call site omit the "
        "traces and have the census invent an answer about what the models did."
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_tool_call_without_a_declaration_does_not_buy_a_clearance(tmp_path: Path) -> None:
    """The regression FP review caught in MP-159's own first cut, pinned.

    `[M]` Deriving the census from `s.id in tool_active` ALONE was not monotone: a scenario
    with no `tools` key whose traces nevertheless carry `tool_calls` went from HEAD's
    `NOT cleared on content` to `looks safe to adopt` -- MP-159's defect arriving through the
    other door, in the commit that fixed it. Nothing in the suite pinned that path.

    The state is not reachable from a live replay (both live adapters gate the request
    `tools` on `scenario.input["tools"]`) but it is reachable from a hand-edited
    `traces.json` -- which the README explicitly invites -- and from a baseline recorded
    before the `tools` key was removed. The census must require BOTH halves, which is what
    keeps the new blind set a superset of the old one for every input.
    """
    root = _suite(tmp_path / "called_undeclared", declare_tools=False, call_tool=True)
    _, md = _baseline_then_check(root)
    assert "looks safe to adopt" not in md, (
        "MP-159 FP review: a tool call with no matching declaration must not arm the "
        "channel -- the census would then clear a run HEAD refused to clear."
    )
    assert "NOT cleared on content" in md
    # It is undeclared, so the remedy is the plain one, not the declared-but-unused branch.
    assert "no scenario declares `tools`" in md


@pytest.mark.xfail(
    strict=True,
    reason="MP-165: `hard_content_channels` counts the tool TRAJECTORY as a channel that "
    "reads content, but it reads what the model DOES, not what it SAYS. With the tool "
    "called identically on both sides, an inverted answer still clears. Deliberately left "
    "open by MP-159 -- the fix withholds clearance far more widely and needs its own "
    "argument against crying wolf. Delete this marker when MP-165 lands.",
)
def test_an_identical_trajectory_must_not_clear_an_inverted_answer(tmp_path: Path) -> None:
    """The residual MP-159 leaves open, pinned in the repo rather than only in a backlog row.

    `[M]` FP review measured it: MP-159's own reproduction survives one tool call away. The
    scenario declares `tools`, both sides CALL the tool identically, and the prose still
    inverts from "FRAUD DETECTED: block this transaction" to "Looks fine, approve it." The
    trajectory did not change, so the only live hard channel saw nothing -- and the run is
    reported clean.
    """
    root = _suite(tmp_path / "mp165", declare_tools=True, call_tool=True, invert=True)
    _, md = _baseline_then_check(root)
    assert "looks safe to adopt" not in md
