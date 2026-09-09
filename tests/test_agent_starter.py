"""The scaffolded agent example — `modelpin init --agent-example` (no network, no key).

`[M] 2026-09-09` first-run audit of 0.3.0. `modelpin init` scaffolded exactly one scenario,
`scenarios/greeting.json`: `kind: single`, no tools. So the tool-call trajectory diff -- the
signal the README calls the moat, and the one thing here that a text diff cannot do -- had
**no runnable example anywhere in the installed wheel**. The eight the README cites live in
`examples/suite/`, which ADR-0011 deliberately keeps out of the wheel, behind a GitHub link.
The audit reconstructed an agent scenario from README prose and it loaded first try, which
proves the format is learnable *given* fluency in OpenAI's function-calling JSON, and proves
nothing about a newcomer who has none.

Two decisions are pinned here, because both are the kind that get quietly reversed:

* **The example is behind a flag, and the flag is advertised by `init` itself.**
  `test_init_scaffolds_no_agent_example_by_default` and
  `test_init_names_the_flag_so_the_example_is_findable_without_leaving_the_package` are
  halves of one contract and neither is safe alone. Default-on would multiply the first
  billed command a stranger runs (a `single` replay is one model call; an agent replay
  drives up to `MAX_TOOL_TURNS`), spending it on a fictional refunds desk -- the shape
  `test_first_run_cost.py` exists to keep out. A flag nobody is told about is defect 1 again
  with an extra step.
* **The scenario is genuinely runnable, not illustrative.**
  `test_the_scaffold_drives_a_real_two_step_tool_trajectory` puts it through the actual
  OpenAI adapter with a stubbed client. An example that only *looks* right is worse than
  none: it is cribbed, it fails in the user's hands, and the failure is blamed on them.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from modelpin.cli import EXIT_SETUP_FAILED, app
from modelpin.models import Scenario
from modelpin.providers.openai import MAX_TOOL_TURNS, OpenAIAdapter
from modelpin.scenarios import load_scenarios
from modelpin.scenarios.starter import (
    AGENT_STARTER_FILENAME,
    AGENT_STARTER_ID,
    AGENT_STARTER_NOTES_FILENAME,
    agent_starter,
    write_agent_starter,
)

runner = CliRunner()


def _init(directory: Path, *flags: str):
    return runner.invoke(app, ["init", str(directory), *flags])


def _flat(output: str) -> str:
    """Rich hard-wraps to the console width; compare on whitespace-normalised text so these
    assert on what was disclosed rather than on terminal geometry."""
    return " ".join(output.split())


def _scaffolded(directory: Path) -> dict:
    return json.loads(
        (directory / "scenarios" / AGENT_STARTER_FILENAME).read_text(encoding="utf-8")
    )


# ------------------------------------------------------------------ the flag decision


def test_init_scaffolds_no_agent_example_by_default(tmp_path):
    """The cost half of the decision, pinned so flipping the default is a deliberate act.

    `modelpin init` writes a config whose `providers:` is `openai`, and the README's next
    line is `modelpin baseline` -- on the user's own key. A `kind: single` replay is exactly
    one completion; an agent replay drives the tool loop up to `MAX_TOOL_TURNS`. Scaffolding
    this by default would roughly double the replays and multiply the calls by up to that
    cap, on a fictional refunds desk that measures nothing about the user's app.
    """
    result = _init(tmp_path)
    assert result.exit_code == 0, result.output
    scenarios = sorted(p.name for p in (tmp_path / "scenarios").glob("*.json"))
    assert scenarios == ["greeting.json"], (
        f"`modelpin init` scaffolded {scenarios}. The agent example costs up to "
        f"{MAX_TOOL_TURNS} model calls per replay and belongs behind --agent-example; if "
        "this default is being changed on purpose, change this test and say why."
    )


def test_init_names_the_flag_so_the_example_is_findable_without_leaving_the_package(tmp_path):
    """The discoverability half. Without this line the flag decision IS the defect.

    A newcomer must not have to open GitHub to find the one feature that distinguishes this
    tool, so the command they just ran has to name the one that produces it.
    """
    out = _flat(_init(tmp_path).output)
    assert "--agent-example" in out, (
        "`modelpin init` does not mention --agent-example, so the only worked example of "
        f"the tool-trajectory diff is unreachable from inside the package.\n{out}"
    )


def test_the_flag_is_offered_to_a_returning_user_too(tmp_path):
    """The pointer prints on the already-initialised branch as well.

    Someone who ran `init` last week and now wants tools gets the "Already initialised" line
    on every subsequent run. If the pointer were only on the freshly-scaffolded branch, they
    would never see it -- and they are the user most likely to want it.
    """
    _init(tmp_path)
    out = _flat(_init(tmp_path).output)
    assert "Already initialised" in out, out
    assert "--agent-example" in out, out


# ------------------------------------------------------------- what the flag writes


def test_the_flag_scaffolds_a_loadable_agent_scenario(tmp_path):
    result = _init(tmp_path, "--agent-example")
    assert result.exit_code == 0, result.output

    scenarios = {s.id: s for s in load_scenarios(tmp_path / "scenarios")}
    assert AGENT_STARTER_ID in scenarios, sorted(scenarios)
    starter = scenarios[AGENT_STARTER_ID]
    assert starter.kind == "agent"
    assert starter.input["tools"], "the scaffolded agent scenario declares no tools"
    assert starter.input["tool_results"], "the scaffolded agent scenario cans no tool results"


def test_the_scaffold_survives_a_round_trip_through_the_model(tmp_path):
    """The written JSON must re-validate as the object it was generated from.

    This is why the scenario is declared as a `Scenario` and serialized at write time,
    exactly as `demo.py` does it: a schema change in `modelpin.models` breaks this test
    rather than silently emitting stale JSON that only fails in a stranger's hands.
    """
    write_agent_starter(tmp_path)
    written = (tmp_path / AGENT_STARTER_FILENAME).read_text(encoding="utf-8")
    assert Scenario(**json.loads(written)) == agent_starter()


def test_the_tools_are_full_function_specs_not_the_bare_name_shorthand(tmp_path):
    """`tools: ["lookup_order"]` is accepted and is what the demo sandbox uses -- but a bare
    name declares no parameters, so nothing it can ever emit has an argument, and the
    argument channel is half of the tool signal. The example worth cribbing is the verbose
    one a real app actually writes."""
    _init(tmp_path, "--agent-example")
    tools = _scaffolded(tmp_path)["input"]["tools"]

    assert [t["function"]["name"] for t in tools] == ["lookup_order", "issue_refund"]
    for tool in tools:
        assert tool["type"] == "function", tool
        fn = tool["function"]
        assert fn["description"], f"{fn['name']} has no description for the model to read"
        properties = fn["parameters"]["properties"]
        assert properties, f"{fn['name']} declares no parameters, so it can emit no arguments"
        assert fn["parameters"]["required"], f"{fn['name']} requires nothing"
        for name in fn["parameters"]["required"]:
            assert name in properties, f"{fn['name']} requires undeclared parameter {name!r}"


def test_every_declared_tool_has_a_canned_result_and_every_result_a_tool(tmp_path):
    """The invariant `examples/calibration/tool/README.md` states for the calibration corpus.

    A declared tool with no canned result silently falls back to a generic `{"status":
    "ok"}` stub, so the second step of the trajectory has nothing to act on; a canned result
    for a tool nobody declared is a key that can never fire. Either way the example teaches
    a shape that does not work.
    """
    _init(tmp_path, "--agent-example")
    payload = _scaffolded(tmp_path)["input"]
    declared = {t["function"]["name"] for t in payload["tools"]}
    canned = set(payload["tool_results"])
    assert declared == canned, f"tools {sorted(declared)} vs tool_results {sorted(canned)}"


def test_the_scaffold_uses_only_input_keys_the_adapters_actually_consume(tmp_path):
    """A scaffold is the highest-leverage place to teach an inert key.

    `tests/test_suite_roles.py` pins the consumed-input allowlist because a key the engine
    does not read is the same defect class as a green tick over an unmeasured run
    (MP-142/147) -- and it is also why the annotation is a sibling `.md` rather than a
    `_note` key in the JSON.
    """
    from tests.test_suite_roles import CONSUMED_INPUT_KEYS

    _init(tmp_path, "--agent-example")
    unread = sorted(set(_scaffolded(tmp_path)["input"]) - CONSUMED_INPUT_KEYS)
    assert not unread, (
        f"the scaffolded agent scenario declares {unread}, which no adapter reads. The "
        "first scenario a user copies must not teach a key that does nothing."
    )


def test_the_assertion_is_one_the_canned_tool_result_supplies(tmp_path):
    """`must_contain` must check compliance, not luck.

    The system prompt requires the order id in the reply and `lookup_order` hands the model
    that exact string, so the assertion measures whether the model followed the instruction.
    An assertion on a word the model merely tends to choose would flap between identical
    runs -- a false positive scaffolded into every user's first suite.
    """
    _init(tmp_path, "--agent-example")
    payload = _scaffolded(tmp_path)
    canned = json.dumps(payload["input"]["tool_results"])
    for needle in payload["assertions"]["must_contain"]:
        assert needle in canned, (
            f"must_contain {needle!r} appears in no canned tool result, so the assertion "
            "depends on the model's choice of words rather than on its compliance"
        )


def test_the_notes_are_written_and_are_inert_to_the_loader(tmp_path):
    """JSON has no comments and a `_note` key is the one thing this schema must not teach,
    so the annotation sits beside the scenario -- where `load_scenarios`, which globs
    `*.json`, cannot mistake it for one."""
    _init(tmp_path, "--agent-example")
    notes = tmp_path / "scenarios" / AGENT_STARTER_NOTES_FILENAME
    assert notes.is_file()
    assert "tool_results" in notes.read_text(encoding="utf-8")
    assert {s.id for s in load_scenarios(tmp_path / "scenarios")} == {
        "greeting",
        AGENT_STARTER_ID,
    }


# --------------------------------------------------- genuinely runnable, not illustrative


def _tool_call(name: str, arguments: str, call_id: str):
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def _message(content=None, tool_calls=None):
    def _dump(exclude_none=False):
        data = {"role": "assistant", "content": content, "tool_calls": tool_calls}
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data

    return SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        refusal=None,
        model_dump=_dump,
    )


def _response(message, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _ScriptedClient:
    """A model that plays the trajectory the scaffold's prompt asks for, one turn at a time.

    Mirrors the SDK response shapes `tests/test_provider_openai.py` uses. It records the
    request kwargs of every turn, which is what lets the assertions below check that the
    CANNED results were actually fed back rather than merely declared.
    """

    def __init__(self, responses):
        self._responses = responses
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]


def test_the_scaffold_drives_a_real_two_step_tool_trajectory():
    """The "genuinely runnable" assertion: through the real adapter, not by inspection.

    Turn 1 calls `lookup_order`, turn 2 calls `issue_refund`, turn 3 answers. What is being
    checked is not that the stub did as it was told -- it is that the adapter fed the
    scenario's CANNED `tool_results` back as `role: tool` messages at each step, so the
    second call had something to act on and the loop terminated without any tool executing.
    That is the whole mechanism the example exists to demonstrate.
    """
    scenario = agent_starter()
    client = _ScriptedClient(
        [
            _response(
                _message(tool_calls=[_tool_call("lookup_order", '{"order_id": "A-1042"}', "c1")]),
                finish_reason="tool_calls",
            ),
            _response(
                _message(
                    tool_calls=[
                        _tool_call(
                            "issue_refund",
                            '{"order_id": "A-1042", "amount": 42.5, "reason": "damaged"}',
                            "c2",
                        )
                    ]
                ),
                finish_reason="tool_calls",
            ),
            _response(_message(content="Refunded $42.50 on order A-1042.")),
        ]
    )

    trace = OpenAIAdapter(client=client).run(scenario, "gpt-4o-mini")

    assert [tc.name for tc in trace.tool_calls] == [
        "lookup_order",
        "issue_refund",
    ], "the scaffolded scenario did not produce the two-step trajectory it advertises"
    assert trace.tool_calls[0].arguments == {"order_id": "A-1042"}
    assert len(client.requests) == 3, "the tool loop did not run three turns"

    # The tools reached the provider in the shape the scenario declared them.
    assert [t["function"]["name"] for t in client.requests[0]["tools"]] == [
        "lookup_order",
        "issue_refund",
    ]

    # ...and the CANNED results came back as tool messages, which is what makes the second
    # step possible without executing anything. `RF-77310` exists only in the scenario file.
    tool_messages = [m for m in client.requests[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 2, tool_messages
    assert "A-1042" in tool_messages[0]["content"], tool_messages[0]
    assert "RF-77310" in tool_messages[1]["content"], tool_messages[1]

    # The final answer satisfies the scenario's own assertion, so a first run is a PASS and
    # not a scaffolded failure the user has to debug before they have learned anything.
    assert all(s in trace.final_output for s in agent_starter().assertions.must_contain)


def test_the_loop_terminates_well_inside_the_turn_cap():
    """A two-step trajectory must not be able to sit against `MAX_TOOL_TURNS`.

    A scaffold that hits the cap records `incomplete_reason=tool_turns` on every run, which
    is a degraded measurement the user did not ask for and would reasonably read as a bug in
    their own scenario.
    """
    client = _ScriptedClient(
        [
            _response(
                _message(tool_calls=[_tool_call("lookup_order", "{}", "c1")]),
                finish_reason="tool_calls",
            ),
            _response(
                _message(tool_calls=[_tool_call("issue_refund", "{}", "c2")]),
                finish_reason="tool_calls",
            ),
            _response(_message(content="Refunded A-1042.")),
        ]
    )
    trace = OpenAIAdapter(client=client).run(agent_starter(), "gpt-4o-mini")
    assert trace.incomplete_reason is None, trace.incomplete_reason
    assert len(client.requests) < MAX_TOOL_TURNS


# ------------------------------------------------------------------ scaffolding contract


def test_the_flag_works_on_an_already_initialised_repo(tmp_path):
    """ "I have used this for a week and now want to try tools" is when it is asked for.

    `init`'s scenario glob is non-empty by then, so gating the example on it would hand this
    user "Already initialised" and nothing else -- the no-exit loop MP-01 was about, one
    flag further out.
    """
    _init(tmp_path)
    result = _init(tmp_path, "--agent-example")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "scenarios" / AGENT_STARTER_FILENAME).is_file(), result.output


def test_the_scaffold_never_overwrites_an_edited_file(tmp_path):
    """Same contract as `write_demo`: re-running must not discard the user's edits."""
    _init(tmp_path, "--agent-example")
    path = tmp_path / "scenarios" / AGENT_STARTER_FILENAME
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["name"] = "my own refunds desk"
    path.write_text(json.dumps(edited, indent=2), encoding="utf-8")

    result = _init(tmp_path, "--agent-example")
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "my own refunds desk"
    assert "already exists" in _flat(result.output), result.output


def test_the_flag_warns_that_an_agent_replay_costs_more(tmp_path):
    """The user is told the bill changed, on the run that changed it.

    ADR-0019's principle -- state the size of a run before it happens -- applies to a
    scaffold that silently adds the most expensive scenario shape there is.
    """
    out = _flat(_init(tmp_path, "--agent-example").output)
    assert str(MAX_TOOL_TURNS) in out, out
    assert "model calls" in out, out


def test_demo_and_agent_example_together_are_refused_not_silently_ignored(tmp_path):
    """MP-198's principle: if we cannot use what you wrote, say so.

    `--demo` returns before the scaffold path runs, so honouring one of two flags the user
    typed would discard the other without a word -- and the two really are different things:
    a free offline sandbox versus a scenario replayed on the user's own key.
    """
    result = _init(tmp_path, "--demo", "--agent-example")
    assert result.exit_code == EXIT_SETUP_FAILED, result.output
    out = _flat(result.output)
    assert "--demo and --agent-example" in out, out
    assert not (tmp_path / "modelpin-demo").exists(), "the demo was written anyway"


def test_the_scaffold_is_generated_by_code_and_reads_no_data_file():
    """ADR-0011: the wheel is code only, so the scaffold cannot come out of a shipped file.

    `test_packaging_contents.py` enforces the archive; this enforces the mechanism, in the
    place the mistake would actually be made. `write_agent_starter` is given a directory
    that exists and nothing else -- if it ever grew a `read_text` of a packaged asset, it
    would still pass there and fail here only if the asset were missing. Asserting the
    module holds the content is the check that cannot be satisfied by a stale wheel.
    """
    import modelpin.scenarios.starter as starter

    source = Path(starter.__file__).read_text(encoding="utf-8")
    assert "lookup_order" in source and "issue_refund" in source
    assert "read_text" not in source, (
        "the scaffold reads a file. Nothing outside the package may be read at runtime "
        "(ADR-0011); the scenario is declared as a `Scenario` object and serialized here."
    )


@pytest.mark.parametrize("flags", [(), ("--agent-example",)])
def test_what_init_writes_still_loads(tmp_path, flags):
    """Whatever combination was asked for, the result is a directory `baseline` can read."""
    assert _init(tmp_path, *flags).exit_code == 0
    scenarios = load_scenarios(tmp_path / "scenarios")
    assert scenarios, "init left a scenarios/ directory that loads nothing"
    for scenario in scenarios:
        assert scenario.input["messages"], f"{scenario.id} has no messages"
