"""Tests for scenario loading + error handling (no network)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import EXIT_SETUP_FAILED, app
from modelpin.scenarios import ScenarioError, load_scenarios

REPO = Path(__file__).resolve().parents[1]


def test_missing_directory_returns_empty(tmp_path):
    assert load_scenarios(tmp_path / "nope") == []


def test_loads_valid_scenarios(tmp_path):
    (tmp_path / "a.json").write_text(
        '{"id": "a", "name": "A", "input": {"messages": [{"role": "user", "content": "hi"}]}}'
    )
    scenarios = load_scenarios(tmp_path)
    assert [s.id for s in scenarios] == ["a"]


def test_malformed_json_raises_scenario_error_naming_the_file(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{ not valid json")
    with pytest.raises(ScenarioError, match="broken.json"):
        load_scenarios(tmp_path)


def test_invalid_scenario_raises_scenario_error(tmp_path):
    # valid JSON, but the scenario fails model validation (messages not a list)
    (tmp_path / "x.json").write_text('{"id": "x", "name": "X", "input": {"messages": "nope"}}')
    with pytest.raises(ScenarioError, match="not a valid scenario"):
        load_scenarios(tmp_path)


# ------------------------------------------------------------------------------------
# The validation-error message. `[M] 2026-09-09` first-run audit of 0.3.0: a scenario file
# that was valid JSON with no `messages` key printed, verbatim --
#
#     error: scenarios\nomessages.json is not a valid scenario: 1 validation error
#     for Scenario
#       Value error, scenario 'nomessages': input.messages must be a list of message
#     dicts [type=value_error, input_value={'id': 'nomessages',
#     'nam...'must_contain': ['hi']}}, input_type=dict]
#         For further information visit
#         https://errors.pydantic.dev/2.13/v/value_error
#
# -- while the malformed-JSON path one test above printed one clean line. The sentence this
# project wrote was in there, wrapped in a dump of the user's own file and a link to a third
# party's documentation. `test_invalid_scenario_raises_scenario_error` above passed
# throughout, because "not a valid scenario" was never the part that was wrong.
# ------------------------------------------------------------------------------------

#: Every fragment of `str(ValidationError)` that is pydantic talking to whoever wrote the
#: model rather than to whoever wrote the file. Enumerated rather than "assert the message is
#: short": each of these appeared in the shipped 0.3.0 output, and naming them means the
#: failure says WHICH internal leaked.
_PYDANTIC_INTERNALS = (
    "validation error for Scenario",
    "[type=",
    "input_value=",
    "input_type=",
    "errors.pydantic.dev",
    "For further information visit",
    "Value error, ",
)


def _nomessages(directory: Path) -> Path:
    """The audit's file: valid JSON, every other field right, no `input.messages`."""
    path = directory / "nomessages.json"
    path.write_text(
        json.dumps(
            {
                "id": "nomessages",
                "name": "No messages key",
                "kind": "single",
                "input": {"prompt": "say hi"},
                "assertions": {"must_contain": ["hi"]},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("internal", _PYDANTIC_INTERNALS)
def test_a_validation_error_leaks_no_pydantic_internals(tmp_path, internal):
    """None of the debugging apparatus reaches the user. Parametrised so the failure names
    the specific leak instead of dumping the whole message and leaving it to be read."""
    _nomessages(tmp_path)
    with pytest.raises(ScenarioError) as exc:
        load_scenarios(tmp_path)
    assert internal not in str(exc.value), (
        f"{internal!r} reached a user-facing error. The malformed-JSON path is one clean "
        f"line; this path must read the same way.\nFull message:\n{exc.value}"
    )


def test_a_validation_error_keeps_the_sentence_that_says_what_is_wrong(tmp_path):
    """Stripping the noise must not strip the signal.

    The load-bearing clause is written in `models.Scenario._check_input_shape` and names
    both the scenario and the field. A message that lost it would be tidier and useless,
    which is the failure mode a "no internals" assertion alone would happily accept.
    """
    _nomessages(tmp_path)
    with pytest.raises(ScenarioError) as exc:
        load_scenarios(tmp_path)
    message = str(exc.value)
    assert "nomessages.json is not a valid scenario:" in message, message
    assert "scenario 'nomessages': input.messages must be a list of message dicts" in message, (
        "the sentence that says what is actually wrong did not survive the cleanup.\n"
        f"Full message:\n{message}"
    )


def test_every_validation_error_is_reported_and_names_its_field(tmp_path):
    """An empty `{}` fails three ways at once, and pydantic collects them in one pass.

    Reporting only the first would cost three edits and three runs for one broken file. The
    field name is the whole content of `Field required` -- without it the user is told
    something is missing and left to work out what, which is most of the work.
    """
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ScenarioError) as exc:
        load_scenarios(tmp_path)
    message = str(exc.value)
    for field in ("id", "name", "input"):
        assert f"{field}: Field required" in message, (
            f"{field!r} is required and missing, but the error does not name it as a field.\n"
            f"Full message:\n{message}"
        )


def test_a_bad_match_value_reads_the_same_as_the_flag_does(tmp_path):
    """MP-227's `_friendly_match_error` writes a sentence; the wrapper used to bury it.

    This is the second validator on the model and it goes through the same path, so it is
    the control that shows the cleanup is not special-cased to one message.
    """
    (tmp_path / "m.json").write_text(
        json.dumps(
            {
                "id": "m",
                "name": "M",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "match": "loose",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError) as exc:
        load_scenarios(tmp_path)
    message = str(exc.value)
    assert "`match` must be one of strict, unordered, subset, superset" in message, message
    assert "errors.pydantic.dev" not in message, message


def test_the_cli_prints_the_clean_message_and_still_exits_4(tmp_path):
    """End to end, through the command a user actually runs. ADR-0035 fixes the code at 4:
    an unreadable scenario is a SETUP failure, not a regression, and no cleanup of the text
    is allowed to move it into the range CI reads as "the model broke something"."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    _nomessages(scenarios)
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - demo-model-v1\nscenarios_dir: scenarios\nproviders:\n  - fake\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "baseline",
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--scenarios-dir",
            str(scenarios),
            "--provider",
            "fake",
            "--fixtures",
            str(tmp_path / "traces.json"),
        ],
    )

    assert result.exit_code == EXIT_SETUP_FAILED, result.output
    # Rich hard-wraps to the console width, so compare on whitespace-normalised text --
    # otherwise this asserts on terminal geometry rather than on what was disclosed.
    out = " ".join(result.output.split())
    assert "input.messages must be a list of message dicts" in out, out
    assert "errors.pydantic.dev" not in out, out
    assert "input_value=" not in out, out


def test_bundled_evaluation_suite_loads_and_validates():
    """The shipped examples/suite must be 8 valid scenarios with non-empty messages —
    guards the JSON + schema offline so a typo can't ship broken."""
    scenarios = load_scenarios(REPO / "examples" / "suite")
    assert len(scenarios) == 8
    ids = {s.id for s in scenarios}
    assert {"refund_request", "decline_pii", "summarize_ticket"} <= ids
    for s in scenarios:
        assert s.input.get("messages"), f"{s.id} has no messages"
    # the agent scenarios carry canned tool_results for deterministic multi-step replay
    agents = [s for s in scenarios if s.kind == "agent"]
    assert agents and all(s.input.get("tool_results") for s in agents)
