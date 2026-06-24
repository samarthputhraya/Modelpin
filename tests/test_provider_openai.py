"""Unit tests for the OpenAI adapter — fully mocked, no network, no API key.

The adapter takes an injected client, so we hand it a fake whose response objects
mirror the real `openai` SDK shapes (attribute access, `arguments` as a JSON string,
`usage.prompt_tokens`/`completion_tokens`). This keeps CI free and deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modelpin.models import Scenario
from modelpin.providers import ProviderError, get_adapter
from modelpin.providers.openai import OpenAIAdapter

# --- fakes that mimic the openai SDK response shape -------------------------------


def _fn_tool_call(name: str, arguments: str, call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def _message(content=None, tool_calls=None, refusal=None):
    def _dump(exclude_none=False):
        data = {"role": "assistant", "content": content, "tool_calls": tool_calls}
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data

    return SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        refusal=refusal,
        model_dump=_dump,
    )


def _response(message, finish_reason="stop", prompt_tokens=11, completion_tokens=7):
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    """Returns canned responses in sequence (clamps to the last so the tool loop always
    terminates), and records the kwargs + count of create() calls."""

    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.last_kwargs: dict | None = None
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _scenario(**input_kwargs) -> Scenario:
    base = {"messages": [{"role": "user", "content": "hi"}]}
    base.update(input_kwargs)
    return Scenario(id="s1", name="demo", input=base)


# --- tests ------------------------------------------------------------------------


def test_basic_completion_populates_trace():
    client = FakeClient(_response(_message(content="Hello there")))
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o-mini", run_idx=2)

    assert trace.scenario_id == "s1"
    assert trace.model_id == "gpt-4o-mini"
    assert trace.run_idx == 2
    assert trace.final_output == "Hello there"
    assert trace.refused is False
    assert trace.tokens_in == 11
    assert trace.tokens_out == 7
    assert trace.latency_ms >= 0.0
    assert trace.tool_calls == []


def test_tool_calls_parsed_with_json_arguments():
    msg = _message(
        content=None,
        tool_calls=[
            _fn_tool_call("lookup_order", '{"order_id": 123}'),
            _fn_tool_call("issue_refund", '{"amount": 5.0}'),
        ],
    )
    # turn 1 emits both calls; turn 2 has no calls -> the loop ends with the final answer
    client = FakeClient([_response(msg, finish_reason="tool_calls"), _response(_message("done"))])
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(tools=["lookup_order", "issue_refund"]), "gpt-4o")

    assert [tc.name for tc in trace.tool_calls] == ["lookup_order", "issue_refund"]
    assert trace.tool_calls[0].arguments == {"order_id": 123}
    assert trace.tool_calls[1].arguments == {"amount": 5.0}
    assert trace.final_output == "done"


def test_multi_step_agent_trajectory_emerges_across_turns():
    """The replay-depth fix: a tool call on turn 1, a SECOND tool call on turn 2 (after
    its result is fed back), then a final answer. The full [lookup_order, issue_refund]
    trajectory must be captured — not truncated at the first tool call."""
    turn1 = _response(_message(None, [_fn_tool_call("lookup_order", "{}")]), "tool_calls")
    turn2 = _response(_message(None, [_fn_tool_call("issue_refund", "{}")]), "tool_calls")
    final = _response(_message("Refund issued."))
    client = FakeClient([turn1, turn2, final])
    scenario = _scenario(
        tools=["lookup_order", "issue_refund"],
        tool_results={"lookup_order": {"order_id": 123, "status": "shipped"}},
    )
    trace = OpenAIAdapter(client=client).run(scenario, "gpt-4o")

    assert [tc.name for tc in trace.tool_calls] == ["lookup_order", "issue_refund"]
    assert trace.final_output == "Refund issued."
    assert client.calls == 3  # two tool turns + the final answer
    # the fed-back tool result is in the conversation the trace records
    assert any(m.get("role") == "tool" for m in trace.messages)


def test_tool_result_messages_echo_the_matching_tool_call_id():
    """OpenAI returns a 400 if a role:tool message's tool_call_id doesn't match the id of
    the assistant tool_call that triggered it. With two calls in one turn carrying DISTINCT
    ids, each fed-back tool message must carry its own matching id (a regression that
    collapsed every id to a constant would still pass the looser 'a tool message exists'
    check, so assert the ids explicitly)."""
    turn1 = _message(
        None,
        [
            _fn_tool_call("lookup_order", "{}", call_id="call_abc"),
            _fn_tool_call("check_stock", "{}", call_id="call_def"),
        ],
    )
    client = FakeClient([_response(turn1, "tool_calls"), _response(_message("done"))])
    scenario = _scenario(tools=["lookup_order", "check_stock"])
    trace = OpenAIAdapter(client=client).run(scenario, "gpt-4o")

    tool_msgs = [m for m in trace.messages if m.get("role") == "tool"]
    ids = {m["tool_call_id"] for m in tool_msgs}
    assert ids == {"call_abc", "call_def"}  # each call's own id, not a shared constant


def test_tool_loop_is_capped_at_max_turns():
    """A model that calls tools forever must not loop forever."""
    from modelpin.providers.openai import MAX_TOOL_TURNS

    looping = _response(_message(None, [_fn_tool_call("spin", "{}")]), "tool_calls")
    client = FakeClient([looping])  # always returns a tool call
    trace = OpenAIAdapter(client=client).run(_scenario(tools=["spin"]), "gpt-4o")

    assert client.calls == MAX_TOOL_TURNS
    assert len(trace.tool_calls) == MAX_TOOL_TURNS


def test_string_tools_are_wrapped_into_function_specs():
    client = FakeClient(_response(_message(content="ok")))
    adapter = OpenAIAdapter(client=client)

    adapter.run(_scenario(tools=["cancel_subscription"]), "gpt-4o")

    tools = client.last_kwargs["tools"]
    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "cancel_subscription",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_refusal_detected_via_content_filter():
    client = FakeClient(_response(_message(content=""), finish_reason="content_filter"))
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o")

    assert trace.refused is True


def test_refusal_detected_via_structured_refusal_field():
    client = FakeClient(_response(_message(content=None, refusal="I won't do that.")))
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o")

    assert trace.refused is True


def test_refusal_detected_via_phrase_heuristic():
    client = FakeClient(_response(_message(content="I'm not able to cancel that for you.")))
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o")

    assert trace.refused is True


def test_benign_ai_self_reference_is_not_a_refusal():
    # North-star: low false-positive rate. A normal answer that mentions AI identity
    # must NOT be flagged as a refusal.
    client = FakeClient(_response(_message(content="As an AI, here is the code you asked for.")))
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o")

    assert trace.refused is False


def test_empty_choices_raises_clear_error():
    response = _response(_message(content="ok"))
    response.choices = []
    client = FakeClient(response)
    adapter = OpenAIAdapter(client=client)

    with pytest.raises(ProviderError, match="no choices"):
        adapter.run(_scenario(), "gpt-4o")


def test_tool_call_missing_function_is_skipped():
    good = _fn_tool_call("issue_refund", "{}")
    broken = SimpleNamespace(id="call_x", type="function")  # no .function attribute
    msg = _message(content=None, tool_calls=[broken, good])
    client = FakeClient([_response(msg, finish_reason="tool_calls"), _response(_message("done"))])
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(tools=["issue_refund"]), "gpt-4o")

    assert [tc.name for tc in trace.tool_calls] == ["issue_refund"]


def test_reasoning_model_drops_temperature_and_renames_max_tokens():
    client = FakeClient(_response(_message(content="ok")))
    adapter = OpenAIAdapter(client=client)

    adapter.run(_scenario(temperature=0.2, max_tokens=256), "o3-mini")

    kwargs = client.last_kwargs
    assert "temperature" not in kwargs  # o-series rejects non-default temperature
    assert "max_tokens" not in kwargs
    assert kwargs["max_completion_tokens"] == 256


def test_gpt_model_passes_temperature_and_max_tokens():
    client = FakeClient(_response(_message(content="ok")))
    adapter = OpenAIAdapter(client=client)

    adapter.run(_scenario(temperature=0.2, max_tokens=256), "gpt-4o")

    kwargs = client.last_kwargs
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 256
    assert "max_completion_tokens" not in kwargs


def test_malformed_tool_arguments_degrade_gracefully():
    msg = _message(content=None, tool_calls=[_fn_tool_call("do_thing", "{not valid json")])
    client = FakeClient([_response(msg, finish_reason="tool_calls"), _response(_message("done"))])
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(tools=["do_thing"]), "gpt-4o")

    assert trace.tool_calls[0].name == "do_thing"
    assert trace.tool_calls[0].arguments == {}


def test_missing_usage_yields_zero_tokens():
    response = _response(_message(content="ok"))
    response.usage = None
    client = FakeClient(response)
    adapter = OpenAIAdapter(client=client)

    trace = adapter.run(_scenario(), "gpt-4o")

    assert trace.tokens_in == 0
    assert trace.tokens_out == 0


def test_missing_api_key_raises_provider_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter()  # no injected client -> must build a real one

    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set"):
        adapter.run(_scenario(), "gpt-4o")


def test_blank_api_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")  # whitespace only
    adapter = OpenAIAdapter()

    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set"):
        adapter.preflight()


def test_preflight_passes_with_injected_client():
    adapter = OpenAIAdapter(client=FakeClient(_response(_message(content="ok"))))
    adapter.preflight()  # must not raise


def test_api_error_is_wrapped_in_provider_error():
    class Boom:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("network down"))
            )
        )

    adapter = OpenAIAdapter(client=Boom())
    with pytest.raises(ProviderError, match="OpenAI call for model 'gpt-4o' failed"):
        adapter.run(_scenario(), "gpt-4o")


# --- OpenAI-compatible hosts (Groq/OpenRouter/...) reuse this adapter via base_url ------- #


def test_get_adapter_groq_is_openai_compatible():
    # A free Llama host (Groq) is reached through the SAME adapter, pointed at its base_url
    # with its own BYO-key env — no bespoke provider code.
    adapter = get_adapter("groq")
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._base_url == "https://api.groq.com/openai/v1"
    assert adapter._api_key_env == "GROQ_API_KEY"
    assert adapter._label == "Groq"


def test_unknown_provider_error_lists_compatible_hosts():
    with pytest.raises(ValueError, match="groq"):
        get_adapter("definitely-not-a-provider")


def test_compatible_provider_missing_key_names_its_own_env(monkeypatch):
    # The "key not set" message must name GROQ_API_KEY, not OPENAI_API_KEY.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GROQ_API_KEY is not set"):
        get_adapter("groq").preflight()


def test_compatible_provider_api_error_uses_its_label():
    # An error from a Groq-configured adapter must say "Groq", not "OpenAI".
    def _boom(**_):
        raise RuntimeError("upstream 500")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_boom)))
    adapter = OpenAIAdapter(client=client, label="Groq")
    with pytest.raises(ProviderError, match="Groq call for model 'llama-3.3-70b' failed"):
        adapter.run(_scenario(), "llama-3.3-70b")


def test_build_client_passes_base_url(monkeypatch):
    # build_openai_client must forward base_url + the BYO-key to the SDK client.
    import sys
    import types

    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")

    from modelpin.providers.openai import build_openai_client

    build_openai_client("GROQ_API_KEY", "https://api.groq.com/openai/v1")
    assert captured["api_key"] == "gsk_test_key"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_auth_error_message_does_not_echo_the_raw_exception():
    # An AuthenticationError's text can embed a redacted key fragment; we must not echo it.
    class AuthenticationError(Exception):
        pass

    def _raise(**_):
        raise AuthenticationError("Incorrect API key provided: sk-abc...secret")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_raise)))
    adapter = OpenAIAdapter(client=client)
    with pytest.raises(ProviderError) as exc_info:
        adapter.run(_scenario(), "gpt-4o")
    msg = str(exc_info.value)
    assert "sk-abc" not in msg
    assert "AuthenticationError" in msg  # the class name is safe to surface


def test_generic_api_error_message_is_secret_scrubbed():
    # The generic (non-allowlisted) branch DOES interpolate the error text, so it must
    # scrub key-shaped tokens — otherwise a key fragment in an error body reaches the
    # terminal via cli._fail. This guards the BYO-key north-star guardrail.
    class BadRequestError(Exception):
        pass

    def _raise(**_):
        raise BadRequestError("Invalid value near key sk-proj-ABCDEF1234567890 in body")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_raise)))
    adapter = OpenAIAdapter(client=client)
    with pytest.raises(ProviderError) as exc_info:
        adapter.run(_scenario(), "gpt-4o")
    msg = str(exc_info.value)
    assert "sk-" not in msg
    assert "ABCDEF1234567890" not in msg
    assert "BadRequestError" in msg  # class name + redaction marker still informative


def test_model_id_is_scrubbed_in_error_message():
    # A user who fat-fingers their API key into --to/--from must not have it echoed back:
    # the model_id is scrubbed before it lands in the (otherwise key-safe) error message.
    class BadRequestError(Exception):
        pass

    def _raise(**_):
        raise BadRequestError("model does not exist")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_raise)))
    adapter = OpenAIAdapter(client=client)
    with pytest.raises(ProviderError) as exc_info:
        adapter.run(_scenario(), "sk-proj-LEAKED1234567890")
    msg = str(exc_info.value)
    assert "LEAKED1234567890" not in msg
    assert "[redacted]" in msg


def test_non_list_tools_raises_provider_error():
    client = FakeClient(_response(_message(content="ok")))
    adapter = OpenAIAdapter(client=client)
    bad = Scenario(id="s1", name="demo", input={"messages": [{"role": "user", "content": "hi"}]})
    # bypass model validation by mutating after construction to simulate a bad shape
    object.__setattr__(bad, "input", {"messages": [], "tools": "lookup_order"})

    with pytest.raises(ProviderError, match="tools' must be a list"):
        adapter.run(bad, "gpt-4o")


def test_get_adapter_returns_openai_adapter():
    assert isinstance(get_adapter("openai"), OpenAIAdapter)
