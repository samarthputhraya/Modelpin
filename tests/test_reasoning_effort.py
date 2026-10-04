"""MP-153: tools reach gpt-5.6 / gpt-6-sol / gpt-6-luna on Chat Completions, and the effort is said.

Pinned here:
- **The gate.** The models whose Chat Completions tool calls require `reasoning_effort: none`
  get it when a scenario sends tools and nobody chose an effort. Every other model's request is
  byte-identical to before (no `reasoning_effort` key), and that includes the o-series
  BASELINE side of these migrations and gpt-5.1..5.5, which carry no such rule.
- **The setting.** A user's effort reaches reasoning models only. A scenario's own effort
  overrides the run's. A chosen non-`none` effort with tools on a gated model is refused
  before any call is spent.
- **The disclosure.** Each side's effort is read off the recorded traces and printed in both
  reports.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from modelpin.cli import _with_reasoning_effort, app
from modelpin.config import ConfigError, load_config
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, write_demo
from modelpin.models import Scenario, Trace
from modelpin.providers import ProviderError
from modelpin.providers.fake import FakeProvider
from modelpin.providers.openai import (
    OpenAIAdapter,
    _build_request,
    _tools_need_reasoning_off,
    reasoning_effort_for,
)
from modelpin.report import _md_code, render_pr_comment
from modelpin.report.migration import MigrationFacts, render_migration_report
from modelpin.report.reasoning import reasoning_disclosure


def _response(content: str = "ok"):
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=None,
        refusal=None,
        model_dump=lambda exclude_none=False: {"role": "assistant", "content": content},
    )
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage
    )


class Client:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return _response()


def _scenario(**extra) -> Scenario:
    return Scenario(
        id="s1", name="s1", input={"messages": [{"role": "user", "content": "hi"}], **extra}
    )


TOOLS = {"tools": ["lookup_order"]}
GATED = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6", "gpt-6-sol", "gpt-6-luna"]
UNTOUCHED = [
    "gpt-4-turbo",
    "gpt-4-0613",
    "gpt-4o-2024-05-13",
    "gpt-3.5-turbo-0125",
    "o1-2024-12-17",
    "o3-mini-2025-01-31",
    "o4-mini-2025-04-16",
    "gpt-5",
    "gpt-5.1",
    "gpt-5.4-nano",
    "gpt-5.5",
    "gpt-6-astra",
    "gpt-6.1-sol",
    "openai/gpt-oss-120b",
]


@pytest.mark.parametrize("model", GATED + ["openai/gpt-5.6-sol", "gpt-5.6-sol-2026-11-01"])
def test_tools_to_a_gated_model_go_with_reasoning_off_and_the_trace_says_so(model) -> None:
    client = Client()
    trace = OpenAIAdapter(client=client).run(_scenario(**TOOLS), model)
    assert client.requests[0]["reasoning_effort"] == "none"
    assert trace.reasoning_effort == "none"


@pytest.mark.parametrize("model", GATED)
def test_a_gated_model_without_tools_is_sent_no_effort(model) -> None:
    client = Client()
    trace = OpenAIAdapter(client=client).run(_scenario(), model)
    assert "reasoning_effort" not in client.requests[0]
    assert trace.reasoning_effort is None


@pytest.mark.parametrize("model", UNTOUCHED)
@pytest.mark.parametrize("extra", [{}, TOOLS])
def test_every_other_model_gets_no_reasoning_effort_key_at_all(model, extra) -> None:
    """Verdict neutrality: these requests are what they were before MP-153."""
    client = Client()
    OpenAIAdapter(client=client).run(_scenario(**extra), model)
    assert "reasoning_effort" not in client.requests[0]
    assert _tools_need_reasoning_off(model) is False


@pytest.mark.parametrize("model", ["gpt-4-turbo", "gpt-4o-2024-05-13", "openai/gpt-oss-120b"])
def test_a_chosen_effort_never_reaches_a_model_that_is_not_a_reasoning_model(model) -> None:
    client = Client()
    trace = OpenAIAdapter(client=client, reasoning_effort="low").run(_scenario(**TOOLS), model)
    assert "reasoning_effort" not in client.requests[0]
    assert trace.reasoning_effort is None


@pytest.mark.parametrize(
    "model, extra, effort",
    [
        ("o3-mini-2025-01-31", TOOLS, "high"),
        ("gpt-5.5", TOOLS, "low"),  # no tools rule on 5.5: the chosen effort goes through
        ("gpt-5.6-sol", {}, "high"),  # no tools: any effort is the user's call
        ("gpt-5.6-sol", TOOLS, "none"),
    ],
)
def test_a_chosen_effort_reaches_reasoning_models(model, extra, effort) -> None:
    client = Client()
    trace = OpenAIAdapter(client=client, reasoning_effort=effort).run(_scenario(**extra), model)
    assert client.requests[0]["reasoning_effort"] == effort
    assert trace.reasoning_effort == effort


def test_a_scenario_effort_overrides_the_run_setting() -> None:
    client = Client()
    adapter = OpenAIAdapter(client=client, reasoning_effort="low")
    trace = adapter.run(_scenario(reasoning_effort="high"), "gpt-5.6-sol")
    assert client.requests[0]["reasoning_effort"] == "high" and trace.reasoning_effort == "high"


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-luna"])
def test_a_chosen_effort_that_chat_completions_refuses_with_tools_spends_nothing(model) -> None:
    client = Client()
    with pytest.raises(ProviderError, match="only with reasoning_effort 'none'.*Responses API"):
        OpenAIAdapter(client=client, reasoning_effort="medium").run(_scenario(**TOOLS), model)
    assert client.requests == []


def test_gpt_6_is_a_reasoning_model_so_temperature_is_not_sent() -> None:
    req = _build_request("gpt-6-sol", [], None, {"temperature": 0, "max_tokens": 64})
    assert "temperature" not in req and req["max_completion_tokens"] == 64


def test_the_decision_function_is_what_the_request_carries() -> None:
    for model in GATED + UNTOUCHED:
        for tools in (True, False):
            effort = reasoning_effort_for(model, has_tools=tools)
            req = _build_request(model, [], [{"type": "function"}] if tools else None, {}, effort)
            assert req.get("reasoning_effort") == effort


def test_config_accepts_the_named_efforts_and_refuses_others(tmp_path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text("reasoning_effort: low\n", encoding="utf-8")
    assert load_config(good).reasoning_effort == "low"
    bad = tmp_path / "bad.yaml"
    bad.write_text("reasoning_effort: turbo\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_the_run_setting_reaches_the_openai_adapter_only() -> None:
    adapter = _with_reasoning_effort(OpenAIAdapter(client=Client()), "low")
    assert isinstance(adapter, OpenAIAdapter) and adapter.reasoning_effort == "low"
    fake = FakeProvider()
    assert _with_reasoning_effort(fake, "low") is fake


def test_the_cli_refuses_an_unknown_effort_and_says_where_a_known_one_goes(
    tmp_path, monkeypatch
) -> None:
    write_demo(tmp_path)
    monkeypatch.chdir(tmp_path / DEMO_DIRNAME)
    base = ["baseline", "--fixtures", DEMO_FIXTURES]
    r = CliRunner().invoke(app, [*base, "--reasoning-effort", "turbo"])
    assert r.exit_code == 4 and "must be one of" in r.output
    r = CliRunner().invoke(app, [*base, "--reasoning-effort", "low"])
    assert r.exit_code == 0, r.output
    assert "sent only to OpenAI" in r.output and "does not send it" in r.output


def test_an_old_baseline_without_the_field_still_loads() -> None:
    t = Trace.model_validate({"scenario_id": "s", "model_id": "m", "final_output": "x"})
    assert t.reasoning_effort is None


def _traces(model: str, efforts: dict[str, str | None]) -> dict[str, list[Trace]]:
    return {
        sid: [Trace(scenario_id=sid, model_id=model, reasoning_effort=e) for _ in range(5)]
        for sid, e in efforts.items()
    }


def _gpt4_to_sol(requested=None):
    efforts = {f"t{i}": "none" for i in range(3)} | {f"p{i}": None for i in range(5)}
    return reasoning_disclosure(
        [
            ("gpt-4-turbo", _traces("gpt-4-turbo", dict.fromkeys(efforts)), False),
            ("gpt-5.6-sol", _traces("gpt-5.6-sol", efforts), True),
        ],
        requested=requested,
        fmt=_md_code,
    )


def test_the_disclosure_states_each_side_per_scenario_and_why_none_was_sent() -> None:
    d = _gpt4_to_sol()
    assert d is not None
    assert "`gpt-4-turbo` not sent (not a reasoning model)" in d.note
    assert "`gpt-5.6-sol` `none` on 3 scenarios, not sent (the model's default) on 5" in d.note
    assert "accepts tools from" in d.note and "Set `reasoning_effort`" in d.note
    assert d.reasoning_off_for_tools == ("gpt-5.6-sol",)


def test_a_chosen_none_is_not_explained_as_forced() -> None:
    d = _gpt4_to_sol(requested="none")
    assert d is not None and d.reasoning_off_for_tools == () and "accepts tools" not in d.note


def test_a_check_with_no_reasoning_model_states_nothing() -> None:
    gem = {"a": None, "b": None}
    d = reasoning_disclosure(
        [
            ("gemini-2.5-flash-lite", _traces("x", gem), False),
            ("gemini-3.1-flash-lite", _traces("y", gem), False),
        ],
        requested=None,
        fmt=_md_code,
    )
    assert d is None


def test_both_reports_carry_the_disclosure() -> None:
    d = _gpt4_to_sol()
    assert d is not None
    comment = render_pr_comment([], "gpt-4-turbo", "gpt-5.6-sol", 5, reasoning_note=d.note)
    assert f"<sub>{d.note}</sub>" in comment
    facts = MigrationFacts(
        date_iso="2026-10-04 12:00 UTC",
        from_model="gpt-4-turbo",
        to_model="gpt-5.6-sol",
        provider="openai",
        runs=5,
        match_mode="strict",
        modelpin_version="0.5.1",
        judge_model=None,
        baseline_runs=40,
        candidate_runs=40,
        judge_calls=None,
        tokens_in=1,
        tokens_out=1,
        reasoning_note=d.note,
        reasoning_off_for_tools=d.reasoning_off_for_tools,
    )
    md = render_migration_report([], facts, skipped=["a"])
    assert "| Reasoning effort | Reasoning effort sent:" in md
    assert "`gpt-5.6-sol` ran with reasoning switched off on the scenarios that use tools" in md
