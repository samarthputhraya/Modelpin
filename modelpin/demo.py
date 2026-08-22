"""The offline demo sandbox written by ``modelpin init --demo``.

Modelpin ships **no** data files: the wheel is code only, so nothing outside the package
is read at runtime and a ``pip install`` cannot leave a documented command pointing at a
file that is not there. The zero-key 30-second demo therefore has to be
*generated* into the user's own directory rather than read out of the installed package —
which is also why it never breaks after ``pip install``, and why the error message that
points at it can never be a lie.

The scenarios and traces below are declared as real :class:`Scenario` / :class:`Trace`
objects and serialized at write time. That is deliberate: a schema change in
``modelpin.models`` breaks ``tests/test_demo.py`` instead of silently emitting stale JSON
that only fails in a stranger's hands.

The four scenarios are chosen to show every verdict the engine can reach, using the
``fake`` provider so nothing is billed:

    greeting        identical on both models     -> unchanged
    refund_request  extra lookup_order call      -> regression (tool trajectory)
    angry_customer  starts refusing              -> regression (refusal rate)
    invoice_parse   drops the currency symbol    -> changed_minor (assertion drift)
"""

from __future__ import annotations

import json
from pathlib import Path

from modelpin.config import DEFAULT_RUNS
from modelpin.models import Assertion, Scenario, ToolCall, Trace

#: The sandbox directory `mp init --demo` creates, relative to the target root.
DEMO_DIRNAME = "modelpin-demo"

#: Fixture filename inside the sandbox. Referenced verbatim by the README quickstart.
DEMO_FIXTURES = "traces.json"

#: Deliberately NOT real vendor model ids. A demo must never teach a user a string they
#: could paste into a live, billed run and have it half-work.
DEMO_FROM = "demo-model-v1"
DEMO_TO = "demo-model-v2"

#: Dropped when serializing a Trace: `ts` is a wall-clock default_factory (it would make
#: the generated file differ on every run), and `messages` is replay bookkeeping that
#: `exclude_defaults` still emits as an empty list — noise in the one file we tell the
#: user to open and edit.
_TRACE_NOISE = {"ts", "messages"}


def _scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="greeting",
            name="Simple greeting",
            kind="single",
            input={"messages": [{"role": "user", "content": "Say hello in one short sentence."}]},
            assertions=Assertion(must_contain=["hello"]),
        ),
        Scenario(
            id="refund_request",
            name="Customer asks for a refund",
            kind="agent",
            input={
                "messages": [{"role": "user", "content": "I want a refund for order 123."}],
                "tools": ["lookup_order", "issue_refund"],
            },
            assertions=Assertion(
                expected_tool_calls=["lookup_order", "issue_refund"],
                must_contain=["refund"],
            ),
        ),
        Scenario(
            id="angry_customer",
            name="Frustrated customer wants to cancel",
            kind="agent",
            input={
                "messages": [
                    {"role": "user", "content": "This is unacceptable, cancel my subscription now."}
                ],
                "tools": ["cancel_subscription"],
            },
            assertions=Assertion(expected_tool_calls=["cancel_subscription"]),
        ),
        Scenario(
            id="invoice_parse",
            name="Parse an invoice total",
            kind="single",
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": "Extract the total from: Subtotal 4, Tax 1, Total 5 dollars.",
                    }
                ]
            },
            assertions=Assertion(must_contain=["$"]),
        ),
    ]


def _traces() -> list[Trace]:
    """One template trace per (scenario, model). FakeProvider replays each N times."""
    return [
        # greeting — byte-identical behavior on both models. The point of this one is the
        # NEGATIVE result: Modelpin is supposed to stay quiet here.
        Trace(
            scenario_id="greeting",
            model_id=DEMO_FROM,
            final_output="Hello! Nice to meet you.",
            tokens_out=9,
            latency_ms=310,
        ),
        Trace(
            scenario_id="greeting",
            model_id=DEMO_TO,
            final_output="Hello! Nice to meet you.",
            tokens_out=9,
            latency_ms=290,
        ),
        # refund_request — v2 looks the order up twice before refunding. Same final answer,
        # so a text diff sees nothing; the tool trajectory is what regressed.
        Trace(
            scenario_id="refund_request",
            model_id=DEMO_FROM,
            tool_calls=[ToolCall(name="lookup_order"), ToolCall(name="issue_refund")],
            final_output="I've looked up order 123 and processed your refund.",
            tokens_out=180,
            latency_ms=900,
        ),
        Trace(
            scenario_id="refund_request",
            model_id=DEMO_TO,
            tool_calls=[
                ToolCall(name="lookup_order"),
                ToolCall(name="lookup_order"),
                ToolCall(name="issue_refund"),
            ],
            final_output="I've looked up order 123 and processed your refund.",
            tokens_out=240,
            latency_ms=1300,
        ),
        # angry_customer — v2 refuses an action v1 performed. The expensive kind of change.
        Trace(
            scenario_id="angry_customer",
            model_id=DEMO_FROM,
            tool_calls=[ToolCall(name="cancel_subscription")],
            final_output="Your subscription has been cancelled. Sorry to see you go.",
            tokens_out=60,
            latency_ms=700,
        ),
        Trace(
            scenario_id="angry_customer",
            model_id=DEMO_TO,
            final_output="I'm not able to cancel the subscription for you.",
            refused=True,
            tokens_out=40,
            latency_ms=650,
        ),
        # invoice_parse — right number, missing "$". Violates the scenario's own assertion,
        # but nothing refused and no tool moved: a minor change, not a CI-failing one.
        Trace(
            scenario_id="invoice_parse",
            model_id=DEMO_FROM,
            final_output="Total: $5",
            tokens_out=12,
            latency_ms=400,
        ),
        Trace(
            scenario_id="invoice_parse",
            model_id=DEMO_TO,
            final_output="Total: 5",
            tokens_out=12,
            latency_ms=410,
        ),
    ]


_CONFIG = f"""# modelpin.yaml - written by `modelpin init --demo`. Everything here is offline and free.
models:
  - {DEMO_FROM}          # the "current" model this demo pretends your app depends on
scenarios_dir: scenarios
providers:
  - fake                 # replays canned traces from {DEMO_FIXTURES}; never calls an API
runs: {DEFAULT_RUNS}                  # N replays per scenario per model; below 4 the tool signal cannot fire
# judge_model is intentionally unset: the semantic LLM-judge costs real tokens, and the
# fake provider disables it anyway. This demo is purely structural + statistical.
"""

_README = f"""# Modelpin offline demo

No API key. No cost. Nothing here talks to a provider — `providers: [fake]` in
`modelpin.yaml` replays the canned traces in `{DEMO_FIXTURES}`.

## Run it

> These say `modelpin`, not `mp`. Both work on bash/zsh/cmd, but PowerShell ships a
> ReadOnly alias `mp` -> `Move-ItemProperty` that it resolves *before* PATH, so `mp`
> there silently runs the wrong program. `modelpin` is safe everywhere.

```bash
modelpin baseline --fixtures {DEMO_FIXTURES}
modelpin check --to {DEMO_TO} --fixtures {DEMO_FIXTURES}
```

`modelpin check` exits **1** here, on purpose. That non-zero exit is the whole product: it is
what fails your CI when a model migration changes your app's behavior.

## What you should see

| scenario | verdict | why |
|---|---|---|
| `greeting` | `unchanged` | identical behavior — Modelpin stays quiet |
| `refund_request` | `regression` | `{DEMO_TO}` calls `lookup_order` twice; the final answer is word-for-word the same, so a text diff would miss it |
| `angry_customer` | `regression` | `{DEMO_TO}` refuses an action `{DEMO_FROM}` performed |
| `invoice_parse` | `changed_minor` | `"Total: $5"` -> `"Total: 5"` violates the scenario's `must_contain` assertion, but nothing refused and no tool moved |

A Markdown report lands in `.modelpin/last-report.md` — that is the comment the GitHub
Action posts on your PR.

## Now make it lie to you

Open `{DEMO_FIXTURES}` and edit the `{DEMO_TO}` trace for `refund_request` so its
`tool_calls` match `{DEMO_FROM}` exactly. Re-run `modelpin check`. The regression disappears and
the verdict moves to `unchanged` — the verdict is computed from the traces, not hardcoded.

Then point `scenarios/` at your own app's cases and swap `providers: [fake]` for a real
one. That is the entire migration from demo to production use.
"""


def _dump_scenario(scenario: Scenario) -> str:
    return json.dumps(scenario.model_dump(mode="json", exclude_none=True), indent=2) + "\n"


def _dump_traces(traces: list[Trace]) -> str:
    payload = [
        t.model_dump(mode="json", exclude_defaults=True, exclude=_TRACE_NOISE) for t in traces
    ]
    return json.dumps(payload, indent=2) + "\n"


def write_demo(root: str | Path = ".") -> list[Path]:
    """Scaffold the offline demo under ``<root>/modelpin-demo``.

    Never overwrites: an existing file is left exactly as the user left it, so re-running
    ``modelpin init --demo`` after editing the traces is safe. Returns only the paths actually
    written, in creation order.
    """
    demo = Path(root) / DEMO_DIRNAME
    scenarios_dir = demo / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[Path, str]] = [
        (demo / "modelpin.yaml", _CONFIG),
        (demo / "README.md", _README),
        (demo / DEMO_FIXTURES, _dump_traces(_traces())),
    ]
    planned += [(scenarios_dir / f"{s.id}.json", _dump_scenario(s)) for s in _scenarios()]

    written: list[Path] = []
    for path, body in planned:
        if path.exists():
            continue
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written
