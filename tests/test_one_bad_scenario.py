"""One scenario the provider rejects must not delete the other eleven (MP-148).

`[M] 2026-08-31`, MP-144's live aegis run (`ops/launch/dogfood-aegis.md`). Three distinct hard
400s each killed all six scenarios and produced NOTHING:

  1. `allam-2-7b`          -- "'tool calling' is not supported with this model"
  2. `openai/gpt-oss-20b`  -- "Tool choice is none, but model called a tool"
  3. `openai/gpt-oss-120b` -- a HALLUCINATED tool name (`verify_vendor` for
     `verify_vendor_bank`), which Groq rejects as `tool_use_failed`

Case 3 is the worst of the three: the identical scenario succeeded on retry, and a model
inventing a tool name is *itself* the behaviour change Modelpin exists to catch. Killing the
run over it throws away five good measurements to report one bad one.

`report` already survives this (`except ProviderError -> skipped`). `check` -- the command CI
runs -- did not: `_guard_replay` turned any `ProviderError` into `_fail`, exit 1, no verdicts,
no report. This is deliberately NOT MP-139: a 400 must still not be RETRIED. It must be
SKIPPED, DISCLOSED, and must cost the run its clean-clearance.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import modelpin.cli as cli
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.providers import ProviderError

_ROOT = Path(tempfile.mkdtemp(prefix="modelpin-badscenario-"))
write_demo(_ROOT)
_DEMO = _ROOT / DEMO_DIRNAME
FIXTURES = str(_DEMO / DEMO_FIXTURES)
SCEN = str(_DEMO / "scenarios")
CONFIG = str(_DEMO / "modelpin.yaml")

runner = CliRunner()

#: The verbatim Groq message from case 3, because the disclosure must survive being shown to
#: a user -- including its punctuation and its embedded tool name.
GROQ_400 = (
    "openai/gpt-oss-120b: 400 tool_use_failed - Failed to call a function. "
    "Please adjust your prompt. (called `verify_vendor`, which is not a declared tool)"
)


def _common(store: str) -> list[str]:
    return [
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
        "--runs",
        "5",
    ]


def _baseline(tmp_path, model: str = DEMO_FROM) -> str:
    store = str(tmp_path / ".modelpin")
    r = runner.invoke(cli.app, ["baseline", "--model", model, *_common(store)])
    assert r.exit_code == 0, r.output
    return store


def _break_scenarios(monkeypatch, *ids: str, message: str = GROQ_400) -> None:
    """Make `replay` raise a hard ProviderError for exactly these scenario ids.

    Patched at the CLI's call site rather than inside an adapter: the defect under test is
    the CLI's error BOUNDARY (one `try` around the whole loop instead of one per scenario),
    so the test must exercise that boundary and not a provider's internals.
    """
    real = cli.replay
    broken = set(ids)

    def fake_replay(scenario, model, adapter, runs):
        if scenario.id in broken:
            raise ProviderError(message)
        return real(scenario, model, adapter, runs=runs)

    monkeypatch.setattr(cli, "replay", fake_replay)


def test_a_rejected_scenario_does_not_delete_the_measured_ones(tmp_path, monkeypatch):
    """The core of MP-148: three good scenarios must survive one bad one."""
    store = _baseline(tmp_path)
    _break_scenarios(monkeypatch, "greeting")
    r = runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    # The demo's other scenarios hold real regressions, so a REGRESSION still outranks the
    # incomplete coverage: CI must go red for the reason it actually found.
    assert r.exit_code == 1, r.output
    assert "refund_request" in r.output
    assert "angry_customer" in r.output


def test_the_rejected_scenario_is_named_with_its_provider_message(tmp_path, monkeypatch):
    """Skipping silently would be its own defect: an absent scenario reads as a passing one."""
    store = _baseline(tmp_path)
    _break_scenarios(monkeypatch, "greeting")
    r = runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    assert "greeting" in r.output
    assert "tool_use_failed" in r.output


def test_a_run_with_a_rejected_scenario_is_never_a_clean_pass(tmp_path, monkeypatch):
    """No regression + one scenario never replayed = EXIT_UNMEASURED, not 0.

    Same-model check, so every scenario that DID run compares as unchanged. Exit 0 here
    would be a green tick over a suite one scenario short -- the MP-116 defect with a new
    cause.
    """
    store = _baseline(tmp_path)
    clean = runner.invoke(
        cli.app, ["check", "--to", DEMO_FROM, "--from", DEMO_FROM, *_common(store)]
    )
    assert clean.exit_code == 0, clean.output  # the control: this suite passes cleanly

    _break_scenarios(monkeypatch, "greeting")
    r = runner.invoke(cli.app, ["check", "--to", DEMO_FROM, "--from", DEMO_FROM, *_common(store)])
    assert r.exit_code == cli.EXIT_UNMEASURED, r.output
    assert "greeting" in r.output


def test_every_scenario_rejected_reports_the_provider_not_a_missing_baseline(tmp_path, monkeypatch):
    """`[M]` The pre-fix path told the user to "Record a baseline first" -- advice for a
    condition that was not the one they hit. A baseline exists; the provider refused."""
    store = _baseline(tmp_path)
    _break_scenarios(monkeypatch, "greeting", "refund_request", "angry_customer", "invoice_parse")
    r = runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    assert r.exit_code == cli.EXIT_UNMEASURED, r.output
    assert "tool_use_failed" in r.output
    assert "Record a baseline first" not in r.output


def test_the_written_report_discloses_the_rejected_scenario(tmp_path, monkeypatch):
    """The PR comment is the artifact a reviewer actually reads. A scenario missing from it
    without a word is indistinguishable from one that passed."""
    store = _baseline(tmp_path)
    _break_scenarios(monkeypatch, "greeting")
    runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    report = (Path(store) / "last-report.md").read_text(encoding="utf-8")
    assert "greeting" in report
    assert "tool_use_failed" in report


def test_an_unimplemented_adapter_is_still_a_hard_failure(tmp_path, monkeypatch):
    """LOAD-BEARING boundary. `NotImplementedError` is a CONFIG error -- the whole run is
    misconfigured, so every scenario would fail identically. Swallowing it per scenario
    would turn `--provider anthropic` into a silent no-op run (MP-128's crash, hidden)."""
    store = _baseline(tmp_path)
    real = cli.replay

    def fake_replay(scenario, model, adapter, runs):
        if scenario.id == "greeting":
            raise NotImplementedError
        return real(scenario, model, adapter, runs=runs)

    monkeypatch.setattr(cli, "replay", fake_replay)
    r = runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    assert r.exit_code == 1
    assert "isn't implemented yet" in r.output


@pytest.mark.parametrize("broken", ["greeting", "invoice_parse"])
def test_coverage_numbers_exclude_the_rejected_scenario(tmp_path, monkeypatch, broken):
    """MP-138's mistake, which review caught once already: reading a coverage disclosure off
    the LOADED suite describes a run that did not happen. The census and the underpowered
    list must both be computed on the scenarios that actually produced a comparison."""
    store = _baseline(tmp_path)
    _break_scenarios(monkeypatch, broken)
    r = runner.invoke(cli.app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, *_common(store)])
    # `greeting` and `invoice_parse` are the two scenarios that declare no `tools`, so the
    # census note names them by id. Whichever one was rejected must NOT be listed as a
    # scenario this run measured-but-could-not-see; it was not measured at all.
    census_line = [ln for ln in r.output.splitlines() if "called no tool" in ln]
    assert census_line, r.output  # the disclosure must still be PRESENT, not just silent
    assert not any(broken in ln for ln in census_line), r.output
