"""MP-156: an unknown `--provider` is reported before anything else can hide it.

`[M] 2026-11-27` In an empty directory, `modelpin check --to gpt-4o --provider mistral` and
`modelpin baseline --provider mistral` reported "no scenarios found ... Run `modelpin init`"
and exit 4; the provider name was only checked when the adapter was built, after scenarios,
the baseline model and the baseline file. A user following that advice scaffolded and
recorded before learning the name had never been valid.

Exit codes are part of what this repository promises not to change, so these tests pin them
next to the message: 4 wherever the old order exited 4, and `check`'s abstain (3) for a
missing baseline unchanged.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from modelpin.cli import _unknown_provider, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.providers import LIVE_PROVIDERS, get_adapter
from modelpin.providers.openai import OPENAI_COMPATIBLE_PROVIDERS

runner = CliRunner()
_UNKNOWN = "Unknown provider: mistral"


def _out(result) -> str:
    return " ".join(result.output.split())


# --- the name check agrees with the adapter factory ----------------------------------------


@pytest.mark.parametrize(
    "name", ["fake", *LIVE_PROVIDERS, *OPENAI_COMPATIBLE_PROVIDERS, "OpenAI", "FAKE"]
)
def test_every_name_the_factory_serves_passes_the_check(name: str) -> None:
    assert _unknown_provider(name) is None
    try:
        get_adapter(name)
    except ValueError as exc:  # the factory's own unknown-provider error
        pytest.fail(f"get_adapter({name!r}) rejected a name the early check accepts: {exc}")
    except Exception:  # a missing key or SDK is the adapter's business, not the name's
        pass


def test_an_unknown_name_gets_the_factorys_own_message() -> None:
    with pytest.raises(ValueError) as exc:
        get_adapter("mistral")
    assert _unknown_provider("mistral") == str(exc.value)


# --- the commands, in an empty directory -----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["baseline", "--model", "gpt-4o-mini", "--provider", "mistral"],
        ["check", "--to", "gpt-4o", "--from", "gpt-4o-mini", "--provider", "mistral"],
    ],
    ids=["baseline", "check"],
)
def test_an_empty_directory_does_not_hide_an_unknown_provider(tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, args)
    assert _UNKNOWN in _out(result), result.output
    assert result.exit_code == 4, result.output


def test_report_checks_the_name_before_the_suite(tmp_path):
    empty_suite = tmp_path / "suite"
    empty_suite.mkdir()
    result = runner.invoke(
        app,
        ["report", "--from", "a", "--to", "b", "--suite-dir", str(empty_suite)]
        + ["--provider", "mistral", "--output-dir", str(tmp_path / "out")],
    )
    assert _UNKNOWN in _out(result), result.output
    assert "no scenarios found" not in _out(result), result.output
    assert result.exit_code == 4, result.output


def test_check_in_an_empty_directory_reports_both_problems(tmp_path, monkeypatch):
    """`check` prints the name first and then stops where it always did, so the missing
    scenarios are still named and the exit code is the one the old order produced."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["check", "--to", "gpt-4o", "--from", "gpt-4o-mini", "--provider", "mistral"]
    )
    out = _out(result)
    assert out.index(_UNKNOWN) < out.index("no scenarios found"), result.output
    assert result.exit_code == 4, result.output


# --- the commands, with scenarios: exit codes unchanged ---------------------------------------


@pytest.fixture
def demo(tmp_path, monkeypatch):
    write_demo(tmp_path)
    monkeypatch.chdir(tmp_path / DEMO_DIRNAME)
    return tmp_path / DEMO_DIRNAME


def test_baseline_with_scenarios_still_exits_4_and_says_it_once(demo):
    result = runner.invoke(app, ["baseline", "--model", DEMO_FROM, "--provider", "mistral"])
    assert _out(result).count(_UNKNOWN) == 1, result.output
    assert result.exit_code == 4, result.output


def test_check_over_a_recorded_baseline_still_exits_4_and_says_it_once(demo):
    recorded = runner.invoke(
        app,
        ["baseline", "--model", DEMO_FROM, "--provider", "fake", "--fixtures", DEMO_FIXTURES],
    )
    assert recorded.exit_code == 0, recorded.output
    result = runner.invoke(
        app, ["check", "--to", DEMO_TO, "--from", DEMO_FROM, "--provider", "mistral"]
    )
    assert _out(result).count(_UNKNOWN) == 1, result.output
    assert result.exit_code == 4, result.output


def test_check_without_a_baseline_still_abstains_with_3(demo):
    """The one path where the old order did not exit 4: no baseline file abstains (exit 3,
    ADR-0018) and publishes an artifact. That is unchanged; the name is now printed too."""
    result = runner.invoke(
        app, ["check", "--to", "gpt-4o", "--from", "never-recorded", "--provider", "mistral"]
    )
    assert _UNKNOWN in _out(result), result.output
    assert "could not measure" in _out(result), result.output
    assert result.exit_code == 3, result.output


def test_a_name_from_modelpin_yaml_says_where_it_came_from(demo):
    """A user who never typed `--provider` needs to know where the bad name lives."""
    cfg = demo / "modelpin.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("- fake", "- opnai", 1), encoding="utf-8"
    )
    assert "opnai" in cfg.read_text(encoding="utf-8")
    result = runner.invoke(app, ["baseline", "--model", DEMO_FROM])
    out = _out(result)
    assert "Unknown provider: opnai (from `providers:` in modelpin.yaml)" in out, result.output
    assert result.exit_code == 4, result.output
    flagged = runner.invoke(app, ["baseline", "--model", DEMO_FROM, "--provider", "opnai"])
    assert "from `providers:`" not in _out(flagged), flagged.output
