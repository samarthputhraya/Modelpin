"""The worked agent scenario written by ``modelpin init --agent-example``.

`[M] 2026-09-09` first-run audit of 0.3.0. ``modelpin init`` scaffolded exactly one
scenario, ``scenarios/greeting.json`` -- ``kind: single``, no tools -- so the tool-call
trajectory diff, the differentiating signal the README calls the moat, had **no runnable
example anywhere in the installed wheel**. The eight worked examples the README points at
live in ``examples/suite/``, which ADR-0011 deliberately does not ship: the wheel is code
only. A newcomer's only route to the main feature was a GitHub link, and the audit's own
reconstruction of an agent scenario from README prose loaded on the first try *only*
because the auditor already knew OpenAI's function-calling JSON by heart.

So this file is generated the way :mod:`modelpin.demo` generates its sandbox, and for the
same two reasons: the wheel ships no data files, and declaring the scenario as a real
:class:`Scenario` object means a schema change in ``modelpin.models`` breaks
``tests/test_agent_starter.py`` instead of quietly emitting JSON that only fails in a
stranger's hands.

**Why a flag and not the default scaffold.** ``modelpin init`` writes a config whose
``providers:`` is ``openai``, and the README's very next line is ``modelpin baseline`` --
on the user's own key. `[M]` A ``single`` scenario costs exactly one completion per replay;
an agent scenario drives the model<->tool loop up to ``MAX_TOOL_TURNS`` completions
(``providers/openai.py``, and the ``>=`` in ``cli._plan_line``'s docstring exists for
precisely this reason). Scaffolding this by default would take the first billed command a
stranger runs from ``DEFAULT_RUNS`` replays and as many calls to twice the replays and up
to seven times the calls, spent entirely on a fictional refund desk that measures nothing
about their app -- the shape ``tests/test_first_run_cost.py`` exists to keep out. The
default first run therefore stays one scenario, and the pointer to this one is printed by
``modelpin init`` itself, so nobody has to leave the package to find it. The flag also
works on an already-initialised repo, because "I have used this for a week and now want to
try tools" is when it is actually wanted.

**Why a sibling ``.md`` rather than comments in the JSON.** JSON has none, and a ``_note``
key is the one thing this schema must not teach: ``tests/test_suite_roles.py`` pins the
consumed-input allowlist (``messages``/``tools``/``tool_results`` plus generation params)
because a key the engine does not read is the same defect class as a green tick over an
unmeasured run (MP-142/147). The annotation goes beside the file, as
``examples/calibration/tool/README.md`` already does for the calibration corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

from modelpin.models import Assertion, Scenario

# Interpolated into the notes below, never typed as a literal: the scaffolded `runs:` was
# three copies of one number that drifted apart, which IS MP-03, and a cost figure a user
# reads before spending is the last place to reintroduce that shape. No SDK is imported at
# module scope in the adapter, so this costs nothing at CLI start-up.
from modelpin.providers.openai import MAX_TOOL_TURNS

#: Scaffold filenames. The ``.md`` is inert to the loader -- `load_scenarios` globs `*.json`
#: -- so it can sit beside the scenario it annotates instead of one directory away.
AGENT_STARTER_ID = "refund_agent"
AGENT_STARTER_FILENAME = f"{AGENT_STARTER_ID}.json"
AGENT_STARTER_NOTES_FILENAME = f"{AGENT_STARTER_ID}.md"


def agent_starter() -> Scenario:
    """The scaffolded two-step agent scenario, as the validated model.

    Everything about it is chosen so a first replay produces a trajectory worth diffing:

    * **Two tools, called in a fixed order, exactly once each.** A trajectory the prompt
      pins is one whose *change* means something. A scenario whose tool use is optional
      moves on its own between identical runs, which is MP-220's false positive -- and the
      example a newcomer copies is the wrong place to teach that shape without the
      ``match`` override that makes it safe.
    * **Full OpenAI function-calling JSON, not the bare-name shorthand.** ``tools:
      ["lookup_order"]`` is accepted (``providers/openai.py::_to_tools`` wraps it), and it
      is what the demo sandbox uses -- but it declares no parameters, so it can never show
      an argument, and the argument channel is half of the tool signal. The verbose form is
      the one a real app writes, so it is the one worth cribbing from.
    * **Canned ``tool_results``.** Keyed by tool name, so the loop terminates without any
      real tool ever executing and every replay sees the same tool output. Without them
      each turn gets a generic ``{"status": "ok"}`` stub and the second step has nothing to
      act on.
    * **An assertion the tool result forces.** The system prompt requires the order id in
      the reply and ``lookup_order`` hands the model that exact string, so ``must_contain``
      is checking the model's compliance rather than its choice of words.

    The closing prompt sentence forbids the model from narrating its own trajectory, the
    convention the calibration corpus uses: without it the tool sequence leaks into the
    final text and the semantic channel starts measuring the structural one.
    """
    return Scenario(
        id=AGENT_STARTER_ID,
        name="Refunds desk - look the order up, then refund it",
        kind="agent",
        input={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the refunds desk for an online shop. Always call "
                        "lookup_order first for the order the customer names, then call "
                        "issue_refund for that same order. Always both, always in that "
                        "order, exactly once each. Then reply to the customer in one short "
                        "sentence quoting the order id and the amount refunded. Do not "
                        "describe which tools you used or what steps you took."
                    ),
                },
                {
                    "role": "user",
                    "content": "Order A-1042 arrived damaged. Please refund it.",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_order",
                        "description": "Look up one order and its current status and total.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "order_id": {
                                    "type": "string",
                                    "description": "The order id, e.g. A-1042.",
                                }
                            },
                            "required": ["order_id"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "issue_refund",
                        "description": "Refund an order in full or in part.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "order_id": {
                                    "type": "string",
                                    "description": "The order id to refund.",
                                },
                                "amount": {
                                    "type": "number",
                                    "description": "How much to refund, in the order's currency.",
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "Why the refund is being issued.",
                                },
                            },
                            "required": ["order_id", "amount"],
                        },
                    },
                },
            ],
            # Keyed by tool NAME, so a tool called twice sees the same result twice. Values
            # are handed back verbatim as the tool message's content (JSON-encoded when they
            # are not already strings) -- see `providers/openai.py::_tool_result_messages`.
            "tool_results": {
                "lookup_order": {
                    "order_id": "A-1042",
                    "status": "delivered",
                    "total": 42.50,
                    "currency": "USD",
                    "refundable": True,
                },
                "issue_refund": {
                    "order_id": "A-1042",
                    "refunded": 42.50,
                    "currency": "USD",
                    "refund_id": "RF-77310",
                },
            },
        },
        assertions=Assertion(must_contain=["A-1042"]),
    )


_NOTES = f"""# `{AGENT_STARTER_FILENAME}` - the agent scenario, annotated

Written by `modelpin init --agent-example`. This file is not a scenario; Modelpin loads
`*.json` only, so it is safe to keep, edit or delete. **The `.json` beside it is real** -
`modelpin baseline` replays it like any other scenario.

## What each key is for

| key | what it does |
|---|---|
| `"kind": "agent"` | declares a multi-turn run. Modelpin keeps feeding tool results back until the model answers without calling a tool, or hits its {MAX_TOOL_TURNS}-turn cap. |
| `input.messages` | the conversation as it starts. Exactly what you would send yourself. |
| `input.tools` | the tools offered, in OpenAI function-calling JSON. Bare names also work (`"tools": ["lookup_order"]`), but a bare name declares no parameters, so the argument half of the tool signal has nothing to compare. |
| `input.tool_results` | canned results **keyed by tool name**. No tool is ever executed: the recorded value is handed straight back, so every replay sees the same tool output and only the *model* varies. A tool with no entry here gets a generic `{{"status": "ok"}}`. |
| `assertions.must_contain` | plain substring checks on the final answer. The system prompt demands the order id and `lookup_order` supplies it, so this checks compliance, not phrasing. |

## What it is meant to show

The prompt pins a two-call trajectory - `lookup_order` then `issue_refund`, once each, in
that order. That is the point: a trajectory a prompt pins is one whose **change** means
something. When a candidate model starts calling `lookup_order` twice, or skips it, or
refunds without checking, the final sentence to the customer can stay word-for-word
identical while the behavior behind it has moved. A text diff sees nothing there. The
tool-trajectory channel is what does.

## Before you run it

This scenario costs more than `greeting.json`. A `kind: single` replay is one model call;
an agent replay drives up to **{MAX_TOOL_TURNS}** model calls as the tool loop turns, so at
the scaffolded `runs:` this one scenario can be several times the bill of the other.
`modelpin baseline` prints the size of the run before it spends anything - read that line.

It is also a **fictional** refunds desk. It exists to be read, not to measure your app.
Once the shape is clear, replace it with your own tools and prompts, or delete it.

## If your app's tool use is genuinely optional

A prompt that says "call this when it would be useful" is *stating* that the trajectory may
vary, and comparing it strictly makes the model's own discretion look like a regression. Say
so in the scenario rather than loosening the whole run - add `"match": "superset"` (or
`"subset"`, `"unordered"`) at the top level of the JSON. It overrides the run's `--match`
for this scenario alone, and it does **not** invalidate a baseline you already paid to
record: it changes how traces are compared, never what is sent to the model.
"""


def _dump_scenario(scenario: Scenario) -> str:
    """Serialize exactly as `demo.py` does, so the two scaffolds cannot drift in shape."""
    return json.dumps(scenario.model_dump(mode="json", exclude_none=True), indent=2) + "\n"


def write_agent_starter(scenarios_dir: str | Path) -> list[Path]:
    """Write the agent scenario and its annotation into ``scenarios_dir``.

    Never overwrites, for the reason `write_demo` gives: re-running the command after
    editing the scenario must not silently discard the edit. Returns only the paths
    actually written, in creation order, so the caller can say nothing when there was
    nothing to say.
    """
    directory = Path(scenarios_dir)
    directory.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[Path, str]] = [
        (directory / AGENT_STARTER_FILENAME, _dump_scenario(agent_starter())),
        (directory / AGENT_STARTER_NOTES_FILENAME, _NOTES),
    ]

    written: list[Path] = []
    for path, body in planned:
        if path.exists():
            continue
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written
