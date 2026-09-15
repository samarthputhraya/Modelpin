"""Unit tests for the Anthropic (Claude) adapter and judge -- fully offline, no key, no network.

Two layers, deliberately:

* an injected fake client whose objects mirror the SDK's attribute shapes (``content`` blocks
  with ``.type``, ``tool_use.input`` already a dict, ``usage.input_tokens``), for the adapter's
  own logic; and
* the REAL ``anthropic`` client driven through an ``httpx2.MockTransport``, for everything a
  fake cannot prove: the JSON that actually reaches the wire (``extra_body`` merging, the
  ``tool_result`` shape, thinking blocks round-tripped), the SDK's retry budget, the Vertex URL,
  and real SDK exception classes. anthropic 1.x is built on ``httpx2``, not ``httpx`` -- a
  transport from the ``httpx`` package is rejected by the 1.x client.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from modelpin.judge import AnthropicJudge, build_judge, infer_judge_provider
from modelpin.models import IncompleteReason, Scenario
from modelpin.providers import ProviderError, get_adapter
from modelpin.providers.anthropic import (
    DEFAULT_MAX_TOKENS,
    MAX_TOOL_TURNS,
    REPLAY_MAX_RETRIES,
    AnthropicAdapter,
    _explain_api_error,
    accepts_sampling,
    build_anthropic_client,
)

_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """A developer's real credentials must never leak into (or change) an offline test."""
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)


# --- fakes that mimic the anthropic SDK response shape ---------------------------------


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(name: str, args: dict | None = None, block_id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=args or {})


def _response(content, stop_reason="end_turn", input_tokens=11, output_tokens=7):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


class FakeClient:
    """Canned responses in sequence (clamped to the last, so a looping model stays looping);
    records every request's kwargs, copied, because the adapter reuses its conversation list."""

    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _scenario(**input_kwargs) -> Scenario:
    base: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
    base.update(input_kwargs)
    return Scenario(id="s1", name="demo", input=base)


# --- single turn ---------------------------------------------------------------------


def test_single_turn_text_populates_trace():
    client = FakeClient(_response([_text("Hello "), _text("there")]))
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5", run_idx=2)

    assert trace.final_output == "Hello there"
    assert trace.model_id == "claude-haiku-4-5"
    assert trace.run_idx == 2
    assert trace.tool_calls == []
    assert trace.refused is False
    assert trace.incomplete_reason is None
    assert (trace.tokens_in, trace.tokens_out) == (11, 7)
    assert client.calls == 1
    request = client.requests[0]
    assert request["model"] == "claude-haiku-4-5"
    assert request["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in request and "system" not in request


def test_system_messages_fold_into_the_top_level_system_param():
    """The Messages API has no `system` role in `messages`; sending one is a 400."""
    client = FakeClient(_response([_text("ok")]))
    scenario = _scenario(
        messages=[
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "Answer in English."},
            {"role": "user", "content": "again"},
        ]
    )
    AnthropicAdapter(client=client).run(scenario, "claude-haiku-4-5")

    request = client.requests[0]
    assert request["system"] == "You are terse.\nAnswer in English."
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert all(m["role"] != "system" for m in request["messages"])


def test_max_tokens_is_always_sent_because_the_api_requires_it():
    client = FakeClient(_response([_text("ok")]))
    AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    assert client.requests[0]["max_tokens"] == DEFAULT_MAX_TOKENS

    for key in ("max_tokens", "max_output_tokens"):
        c = FakeClient(_response([_text("ok")]))
        AnthropicAdapter(client=c).run(_scenario(**{key: 64}), "claude-haiku-4-5")
        assert c.requests[0]["max_tokens"] == 64


def test_token_usage_is_summed_across_tool_turns():
    t1 = _response([_tool_use("lookup")], stop_reason="tool_use", input_tokens=100, output_tokens=5)
    t2 = _response([_text("done")], input_tokens=120, output_tokens=9)
    trace = AnthropicAdapter(client=FakeClient([t1, t2])).run(
        _scenario(tools=["lookup"]), "claude-haiku-4-5"
    )
    assert (trace.tokens_in, trace.tokens_out) == (220, 14)


def test_missing_usage_yields_zero_tokens():
    response = SimpleNamespace(content=[_text("ok")], stop_reason="end_turn", usage=None)
    trace = AnthropicAdapter(client=FakeClient(response)).run(_scenario(), "claude-haiku-4-5")
    assert (trace.tokens_in, trace.tokens_out) == (0, 0)


# --- sampling params ------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-haiku-4-5", True),
        ("claude-haiku-4-5@20251001", True),  # Vertex dated snapshot
        ("claude-sonnet-4-5-20250929", True),
        ("claude-opus-4-20250514", True),  # the bare `-4` model, dated
        ("claude-opus-4-6", True),
        ("claude-sonnet-4-6", True),
        ("claude-3-5-haiku@20241022", True),
        ("claude-opus-4-7", False),  # "released after Claude Opus 4.6"
        ("claude-opus-4-8", False),
        ("claude-opus-5", False),
        ("claude-sonnet-5", False),
        ("claude-fable-5-1", False),
        ("claude-some-future-model", False),  # unknown -> suppressed, never a guaranteed 400
    ],
)
def test_sampling_is_allowed_only_on_models_that_accept_it(model, expected):
    assert accepts_sampling(model) is expected


def test_temperature_travels_in_extra_body_on_a_model_that_accepts_it():
    """anthropic 1.x removed `temperature` from `messages.create`'s signature -- passing it as
    a keyword is a TypeError -- so it must travel in `extra_body`."""
    client = FakeClient(_response([_text("ok")]))
    AnthropicAdapter(client=client).run(_scenario(temperature=0), "claude-haiku-4-5")
    request = client.requests[0]
    assert request["extra_body"] == {"temperature": 0}
    assert "temperature" not in request


def test_temperature_is_dropped_on_a_model_that_would_400_on_it():
    client = FakeClient(_response([_text("ok")]))
    AnthropicAdapter(client=client).run(_scenario(temperature=0.7), "claude-opus-5")
    assert "extra_body" not in client.requests[0]
    assert "temperature" not in client.requests[0]


def test_only_temperature_is_sent_when_a_scenario_sets_both():
    """Haiku 4.5 returns a 400 when both are set."""
    client = FakeClient(_response([_text("ok")]))
    AnthropicAdapter(client=client).run(_scenario(temperature=0.2, top_p=0.9), "claude-haiku-4-5")
    assert client.requests[0]["extra_body"] == {"temperature": 0.2}

    top_p_only = FakeClient(_response([_text("ok")]))
    AnthropicAdapter(client=top_p_only).run(_scenario(top_p=0.9), "claude-haiku-4-5")
    assert top_p_only.requests[0]["extra_body"] == {"top_p": 0.9}


# --- tools ------------------------------------------------------------------------------


def test_every_accepted_tool_shape_becomes_an_anthropic_tool():
    client = FakeClient(_response([_text("ok")]))
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    scenario = _scenario(
        tools=[
            "bare_name",
            {
                "type": "function",
                "function": {"name": "openai_shaped", "description": "d1", "parameters": schema},
            },
            {"name": "bare_function", "description": "d2", "parameters": schema},
            {"name": "native", "input_schema": schema},
        ]
    )
    AnthropicAdapter(client=client).run(scenario, "claude-haiku-4-5")

    assert client.requests[0]["tools"] == [
        {"name": "bare_name", "input_schema": {"type": "object", "properties": {}}},
        {"name": "openai_shaped", "input_schema": schema, "description": "d1"},
        {"name": "bare_function", "input_schema": schema, "description": "d2"},
        {"name": "native", "input_schema": schema},
    ]


def test_multi_turn_tool_loop_pairs_each_result_with_its_tool_use_id():
    t1 = _response(
        [
            _text("Let me check."),
            _tool_use("lookup_order", {"order_id": "A-1"}, "toolu_A"),
            _tool_use("check_stock", {"sku": "X"}, "toolu_B"),
        ],
        stop_reason="tool_use",
    )
    t2 = _response([_tool_use("issue_refund", {"order_id": "A-1"}, "toolu_C")], "tool_use")
    final = _response([_text("Refund issued.")])
    client = FakeClient([t1, t2, final])
    scenario = _scenario(
        tools=["lookup_order", "check_stock", "issue_refund"],
        tool_results={"lookup_order": {"status": "shipped"}, "check_stock": "in stock"},
    )
    trace = AnthropicAdapter(client=client).run(scenario, "claude-haiku-4-5")

    assert [(c.name, c.arguments) for c in trace.tool_calls] == [
        ("lookup_order", {"order_id": "A-1"}),
        ("check_stock", {"sku": "X"}),
        ("issue_refund", {"order_id": "A-1"}),
    ]
    assert trace.final_output == "Refund issued."
    assert trace.incomplete_reason is None
    assert client.calls == 3

    second = client.requests[1]["messages"]
    assistant, results = second[-2], second[-1]
    # The assistant turn goes back VERBATIM, text block included.
    assert assistant["role"] == "assistant"
    assert [b["type"] for b in assistant["content"]] == ["text", "tool_use", "tool_use"]
    # ONE user message, one result per call, in call order, keyed by the call's own id.
    assert results["role"] == "user"
    assert results["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_A", "content": '{"status": "shipped"}'},
        {"type": "tool_result", "tool_use_id": "toolu_B", "content": "in stock"},
    ]
    third = client.requests[2]["messages"][-1]["content"]
    assert third == [
        {"type": "tool_result", "tool_use_id": "toolu_C", "content": '{"status": "ok"}'}
    ]

    # The trace records the scenario's own messages, then the replayed turns.
    assert trace.messages[0] == {"role": "user", "content": "hi"}
    assert trace.messages[-1]["role"] == "assistant"


def test_thinking_blocks_are_passed_back_unmodified():
    signature = "EqQBCkYIARgCKkD-opaque"
    thinking = SimpleNamespace(type="thinking", thinking="plan", signature=signature)
    t1 = _response([thinking, _tool_use("lookup", {}, "toolu_1")], "tool_use")
    client = FakeClient([t1, _response([_text("done")])])
    AnthropicAdapter(client=client).run(_scenario(tools=["lookup"]), "claude-opus-5")

    echoed = client.requests[1]["messages"][-2]["content"][0]
    assert echoed == {"type": "thinking", "thinking": "plan", "signature": signature}


def test_tool_loop_is_capped_and_recorded_as_tool_turns():
    looping = _response([_tool_use("lookup")], stop_reason="tool_use")
    client = FakeClient(looping)
    trace = AnthropicAdapter(client=client).run(_scenario(tools=["lookup"]), "claude-haiku-4-5")

    assert client.calls == MAX_TOOL_TURNS == 6
    assert len(trace.tool_calls) == MAX_TOOL_TURNS
    assert trace.incomplete_reason is IncompleteReason.tool_turns


def test_a_turn_truncated_mid_tool_call_is_not_continued():
    truncated = _response([_tool_use("lookup", {"q": "par"})], stop_reason="max_tokens")
    client = FakeClient([truncated, _response([_text("never reached")])])
    trace = AnthropicAdapter(client=client).run(_scenario(tools=["lookup"]), "claude-haiku-4-5")

    assert client.calls == 1
    assert trace.incomplete_reason is IncompleteReason.max_tokens


# --- stop reasons and refusal -----------------------------------------------------------


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("end_turn", None),
        ("stop_sequence", None),
        ("max_tokens", IncompleteReason.max_tokens),
        ("model_context_window_exceeded", IncompleteReason.max_tokens),
        ("refusal", IncompleteReason.content_filter),
        ("pause_turn", IncompleteReason.provider_other),
        ("some_future_reason", IncompleteReason.provider_other),
        (None, None),
    ],
)
def test_stop_reason_maps_to_incomplete_reason(stop_reason, expected):
    client = FakeClient(_response([_text("partial")], stop_reason=stop_reason))
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    assert trace.incomplete_reason == expected


def test_refusal_stop_reason_marks_the_run_refused():
    client = FakeClient(_response([], stop_reason="refusal"))
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-opus-5")
    assert trace.refused is True
    assert trace.incomplete_reason is IncompleteReason.content_filter


def test_refusal_detected_via_phrase_heuristic():
    client = FakeClient(_response([_text("I can't help with that request.")]))
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    assert trace.refused is True
    assert trace.incomplete_reason is None


def test_ordinary_answer_is_not_a_refusal():
    client = FakeClient(_response([_text("Your order shipped yesterday.")]))
    assert AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5").refused is False


# --- registration ------------------------------------------------------------------------


def test_get_adapter_returns_the_anthropic_adapter():
    adapter = get_adapter("anthropic")
    assert isinstance(adapter, AnthropicAdapter)
    assert adapter.name == "anthropic"
    assert get_adapter("Anthropic").name == "anthropic"


# --- credentials -------------------------------------------------------------------------


def test_missing_credentials_name_both_options_and_build_nothing():
    with pytest.raises(ProviderError) as exc:
        AnthropicAdapter().preflight()
    message = str(exc.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "ANTHROPIC_VERTEX_PROJECT_ID" in message
    assert "gcloud auth application-default login" in message


def test_blank_credentials_are_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "  ")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        build_anthropic_client()


def test_api_key_path_builds_a_first_party_client_with_the_raised_retry_budget(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    client, backend = build_anthropic_client()
    assert type(client) is anthropic.Anthropic
    assert client.api_key == "sk-ant-test-not-a-real-key"
    assert client.max_retries == REPLAY_MAX_RETRIES > 2
    assert backend.label == "Anthropic"


class _FakeCredentials:
    token = "ya29.fake-token-for-tests"
    expired = False


def test_vertex_is_selected_by_its_project_env_and_resolves_adc_eagerly(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    pytest.importorskip("google.auth")
    calls: list[dict] = []
    creds = _FakeCredentials()
    monkeypatch.setattr("google.auth.default", lambda **kw: calls.append(kw) or (creds, None))
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-proj")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")

    client, backend = build_anthropic_client()

    assert type(client) is anthropic.AnthropicVertex
    assert client.project_id == "my-proj"
    assert client.region == "global"  # the default when CLOUD_ML_REGION is unset
    assert client.credentials is creds  # ADC resolved once, in preflight, and handed over
    assert client.max_retries == REPLAY_MAX_RETRIES
    assert calls == [{"scopes": ["https://www.googleapis.com/auth/cloud-platform"]}]
    assert backend.label == "Claude on Vertex AI"


def test_vertex_region_comes_from_cloud_ml_region(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("google.auth")
    monkeypatch.setattr("google.auth.default", lambda **_: (_FakeCredentials(), None))
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-proj")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    client, _ = build_anthropic_client()
    assert client.region == "us-east5"


def test_vertex_without_adc_fails_in_preflight_with_the_fix(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("google.auth")

    def _no_adc(**_):
        raise RuntimeError("Your default credentials were not found.")

    monkeypatch.setattr("google.auth.default", _no_adc)
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-proj")
    with pytest.raises(ProviderError) as exc:
        AnthropicAdapter().preflight()
    message = str(exc.value)
    assert "my-proj" in message
    assert "gcloud auth application-default login" in message
    assert "NOT an API key" in message


# --- key-safe errors -----------------------------------------------------------------------


def test_rejected_key_message_names_the_variable_and_drops_the_text():
    class AuthenticationError(Exception):
        pass

    class Boom(FakeClient):
        def _create(self, **kwargs):
            raise AuthenticationError("401 invalid x-api-key sk-ant-api03-SECRETfragment")

    boom = Boom([])
    boom.messages = SimpleNamespace(create=boom._create)
    with pytest.raises(ProviderError) as exc:
        AnthropicAdapter(client=boom).run(_scenario(), "claude-haiku-4-5")
    message = str(exc.value)
    assert "SECRETfragment" not in message and "sk-ant" not in message
    assert "AuthenticationError" in message
    assert "ANTHROPIC_API_KEY" in message


def test_other_errors_keep_their_text_but_not_a_key():
    class BadRequestError(Exception):
        pass

    message = _explain_api_error(
        BadRequestError("400 temperature unsupported; key sk-ant-api03-abcdefSECRET"),
        "claude-opus-5",
    )
    assert "temperature unsupported" in message
    assert "abcdefSECRET" not in message
    assert "[redacted]" in message


def test_exhausted_rate_limit_and_overload_say_retries_were_spent():
    for name in ("RateLimitError", "OverloadedError"):
        err = type(name, (Exception,), {})("429 Quota exceeded")
        message = _explain_api_error(err, "claude-haiku-4-5@20251001", "Claude on Vertex AI")
        assert str(REPLAY_MAX_RETRIES) in message
        assert "Claude on Vertex AI" in message


# --- the real SDK, through a mock transport ---------------------------------------------


def _ok_body(content: list[dict], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }


def _real_client(monkeypatch, handler):
    """A REAL first-party client from `build_anthropic_client`, with only its transport swapped
    AFTER construction -- so the retry budget under test is the one the builder chose."""
    pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    client, _ = build_anthropic_client()
    client._client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return client, httpx2


def test_the_wire_request_carries_the_shapes_the_api_documents(monkeypatch):
    """Everything the fake client cannot prove, proven on the JSON the SDK actually sends."""
    bodies: list[dict] = []
    responses = [
        _ok_body(
            [
                {"type": "thinking", "thinking": "plan", "signature": "sig-opaque"},
                {"type": "tool_use", "id": "toolu_9", "name": "lookup", "input": {"q": 1}},
            ],
            stop_reason="tool_use",
        ),
        _ok_body([{"type": "text", "text": "done"}]),
    ]

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=responses[len(bodies) - 1])

    client, httpx2 = _real_client(monkeypatch, handler)
    scenario = _scenario(
        messages=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
        tools=["lookup"],
        tool_results={"lookup": {"found": True}},
        temperature=0,
    )
    trace = AnthropicAdapter(client=client).run(scenario, "claude-haiku-4-5")

    assert trace.final_output == "done"
    assert [c.name for c in trace.tool_calls] == ["lookup"]
    assert trace.tool_calls[0].arguments == {"q": 1}
    first, second = bodies
    assert first["system"] == "Be brief."
    assert first["temperature"] == 0  # extra_body merged into the top-level JSON
    assert first["max_tokens"] == DEFAULT_MAX_TOKENS
    assert first["tools"] == [
        {"name": "lookup", "input_schema": {"type": "object", "properties": {}}}
    ]
    assistant, results = second["messages"][-2:]
    assert assistant["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig-opaque",
    }
    assert assistant["content"][1]["id"] == "toolu_9"
    assert results == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_9", "content": '{"found": true}'}
        ],
    }
    json.dumps(trace.model_dump(mode="json"))  # the trace persists to a baseline as JSON


def _flaky(n_failures: int, status: int = 429):
    state = {"n": 0}

    def handler(request):
        import httpx2

        state["n"] += 1
        if state["n"] <= n_failures:
            return httpx2.Response(
                status,
                headers={"retry-after-ms": "1"},
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
            )
        return httpx2.Response(200, json=_ok_body([{"type": "text", "text": "ok"}]))

    return handler, state


def test_a_transient_429_is_retried_by_the_sdk_and_the_run_succeeds(monkeypatch):
    handler, state = _flaky(2)
    client, _ = _real_client(monkeypatch, handler)
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    assert trace.final_output == "ok"
    assert state["n"] == 3, "1 attempt + 2 retries"


def test_an_overloaded_529_is_retried_too(monkeypatch):
    handler, state = _flaky(3, status=529)
    client, _ = _real_client(monkeypatch, handler)
    assert AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5").final_output == "ok"
    assert state["n"] == 4


def test_the_retry_budget_is_finite_and_ends_in_a_friendly_error(monkeypatch):
    handler, state = _flaky(99)
    client, _ = _real_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    assert state["n"] == REPLAY_MAX_RETRIES + 1
    assert "RateLimitError" in str(exc.value)


def test_a_real_401_never_echoes_the_key(monkeypatch):
    def handler(request):
        import httpx2

        return httpx2.Response(
            401,
            json={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key sk-ant-api03-LEAKEDfragment",
                },
            },
        )

    client, _ = _real_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5")
    message = str(exc.value)
    assert "LEAKEDfragment" not in message
    assert "[AuthenticationError]" in message


def test_the_vertex_client_posts_to_the_project_scoped_rawpredict_url(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("google.auth")
    httpx2 = pytest.importorskip("httpx2")
    monkeypatch.setattr("google.auth.default", lambda **_: (_FakeCredentials(), None))
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-proj")
    seen: list = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=_ok_body([{"type": "text", "text": "hi"}]))

    client, _ = build_anthropic_client()
    client._client = httpx2.Client(transport=httpx2.MockTransport(handler))
    trace = AnthropicAdapter(client=client).run(_scenario(), "claude-haiku-4-5@20251001")

    assert trace.final_output == "hi"
    (request,) = seen
    assert request.url.path == (
        "/v1/projects/my-proj/locations/global/publishers/anthropic/models/"
        "claude-haiku-4-5@20251001:rawPredict"
    )
    assert request.headers["authorization"] == "Bearer ya29.fake-token-for-tests"
    body = json.loads(request.content)
    assert body["anthropic_version"] == "vertex-2023-10-16"
    assert "model" not in body


# --- judge ---------------------------------------------------------------------------------


class FakeJudgeClient:
    def __init__(self, text: str = '{"equivalent": false, "reason": "different answer"}'):
        self.text = text
        self.request: dict = {}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.request = kwargs
        return _response([_text(self.text)])


@pytest.mark.parametrize(
    "model", ["claude-haiku-4-5", "claude-opus-5", "claude-haiku-4-5@20251001"]
)
def test_claude_ids_route_the_judge_to_anthropic(model):
    assert infer_judge_provider(model) == "anthropic"
    assert isinstance(build_judge(model, client=FakeJudgeClient()), AnthropicJudge)


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"equivalent": true, "reason": "same"}', True),
        ('{"equivalent": false, "reason": "different"}', False),
        ("I am not sure what you mean.", True),  # unparseable -> FP-safe equivalent
    ],
)
def test_the_anthropic_judge_verdicts(text, expected):
    client = FakeJudgeClient(text)
    assert build_judge("claude-haiku-4-5", client=client).equivalent("a", "b") is expected


def test_the_anthropic_judge_asks_the_shared_question_deterministically():
    from modelpin.judge import _SYSTEM, _judge_prompt

    client = FakeJudgeClient()
    build_judge("claude-haiku-4-5", client=client).equivalent("ref", "cand", task="t")
    assert client.request["system"] == _SYSTEM
    assert client.request["messages"] == [
        {"role": "user", "content": _judge_prompt("ref", "cand", "t")}
    ]
    assert client.request["extra_body"] == {"temperature": 0}
    assert "temperature" not in client.request  # not a 1.x keyword
    assert client.request["max_tokens"] > 0

    newer = FakeJudgeClient()
    build_judge("claude-opus-5", client=newer).equivalent("a", "b")
    assert "extra_body" not in newer.request  # temperature 0 would 400 there


def test_an_anthropic_judge_that_says_nothing_is_read_as_equivalent():
    class Empty(FakeJudgeClient):
        def _create(self, **kwargs):
            return _response([], stop_reason="refusal")

    empty = Empty()
    empty.messages = SimpleNamespace(create=empty._create)
    assert build_judge("claude-haiku-4-5", client=empty).equivalent("a", "b") is True


def test_an_anthropic_judge_error_is_key_safe():
    class AuthenticationError(Exception):
        pass

    class Boom(FakeJudgeClient):
        def _create(self, **kwargs):
            raise AuthenticationError("401 sk-ant-api03-JUDGEsecret")

    boom = Boom()
    boom.messages = SimpleNamespace(create=boom._create)
    with pytest.raises(ProviderError) as exc:
        build_judge("claude-haiku-4-5", client=boom).equivalent("a", "b")
    assert "JUDGEsecret" not in str(exc.value)
    assert "Anthropic" in str(exc.value)


def test_the_anthropic_judge_preflight_needs_credentials():
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        build_judge("claude-haiku-4-5").preflight()
