"""Unit tests for the Google (Gemini) adapter — fully mocked, no network, no key.

The fake client mimics the google-genai response shape: ``candidates[0].content.parts``
with ``.text`` / ``.function_call`` (``.name`` + ``.args`` dict), ``usage_metadata``,
``candidates[0].finish_reason`` (with a ``.name``), and ``prompt_feedback``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modelpin.models import Scenario
from modelpin.providers import ProviderError, get_adapter
from modelpin.providers.google import GoogleAdapter

# --- fakes mirroring the google-genai response shape ------------------------------


def _fn_part(name, args, *, thought_signature=None, call_id=None):
    return SimpleNamespace(
        text=None,
        function_call=SimpleNamespace(name=name, args=args, id=call_id),
        thought_signature=thought_signature,
    )


def _text_part(text):
    return SimpleNamespace(text=text, function_call=None)


def _response(parts, finish="STOP", prompt_tokens=9, out_tokens=5, block_reason=None):
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        finish_reason=SimpleNamespace(name=finish),
    )
    usage = SimpleNamespace(prompt_token_count=prompt_tokens, candidates_token_count=out_tokens)
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=usage,
        prompt_feedback=SimpleNamespace(block_reason=block_reason),
    )


class FakeClient:
    """Returns canned responses in sequence (clamps to the last); records create kwargs."""

    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.last_kwargs: dict | None = None
        self.calls = 0
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.last_kwargs = kwargs
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _scenario(**input_kwargs) -> Scenario:
    base = {"messages": [{"role": "user", "content": "hi"}]}
    base.update(input_kwargs)
    return Scenario(id="g1", name="demo", input=base)


# --- tests ------------------------------------------------------------------------


def test_basic_text_completion():
    client = FakeClient(_response([_text_part("Hello from Gemini")]))
    trace = GoogleAdapter(client=client).run(_scenario(), "gemini-2.0-flash", run_idx=1)

    assert trace.model_id == "gemini-2.0-flash"
    assert trace.run_idx == 1
    assert trace.final_output == "Hello from Gemini"
    assert trace.refused is False
    assert trace.tokens_in == 9 and trace.tokens_out == 5
    assert trace.tool_calls == []


def test_function_call_args_are_already_a_dict():
    # Gemini gives parsed args (no JSON-string parsing needed, unlike OpenAI).
    turn1 = _response([_fn_part("lookup_order", {"order_id": "A-1042"})], finish="STOP")
    final = _response([_text_part("Your order shipped.")])
    client = FakeClient([turn1, final])
    scenario = _scenario(
        tools=["lookup_order"], tool_results={"lookup_order": {"status": "shipped"}}
    )
    trace = GoogleAdapter(client=client).run(scenario, "gemini-2.0-flash")

    assert [tc.name for tc in trace.tool_calls] == ["lookup_order"]
    assert trace.tool_calls[0].arguments == {"order_id": "A-1042"}
    assert trace.final_output == "Your order shipped."


def test_multi_step_trajectory_emerges():
    t1 = _response([_fn_part("lookup_order", {})])
    t2 = _response([_fn_part("issue_refund", {})])
    final = _response([_text_part("Refund done.")])
    client = FakeClient([t1, t2, final])
    scenario = _scenario(tools=["lookup_order", "issue_refund"])
    trace = GoogleAdapter(client=client).run(scenario, "gemini-2.0-flash")

    assert [tc.name for tc in trace.tool_calls] == ["lookup_order", "issue_refund"]
    assert client.calls == 3
    # the function-response turn was appended to the conversation
    assert any(
        any("function_response" in p for p in m.get("parts", []) if isinstance(p, dict))
        for m in trace.messages
    )


def test_thought_signature_and_call_id_echoed_back_in_tool_loop():
    # Gemini 3.x rejects a fed-back function call whose opaque `thought_signature` was
    # dropped ("Function call is missing a thought_signature in functionCall parts"),
    # which aborted a live gemini-3.1-flash-lite run. The model turn we append on the
    # next loop iteration must carry the signature (and the call id) verbatim.
    sig = b"\x00opaque-thought-sig\xff"
    turn1 = _response(
        [_fn_part("lookup_order", {"order_id": "A-1042"}, thought_signature=sig, call_id="c-1")]
    )
    final = _response([_text_part("Your order shipped.")])
    client = FakeClient([turn1, final])
    scenario = _scenario(
        tools=["lookup_order"], tool_results={"lookup_order": {"status": "shipped"}}
    )
    trace = GoogleAdapter(client=client).run(scenario, "gemini-3.1-flash-lite")

    model_turns = [
        m
        for m in trace.messages
        if m.get("role") == "model"
        and any(isinstance(p, dict) and "function_call" in p for p in m.get("parts", []))
    ]
    assert model_turns, "the model's function-call turn must be appended for the tool loop"
    fc_part = next(p for p in model_turns[0]["parts"] if "function_call" in p)
    assert fc_part["thought_signature"] == sig  # echoed back verbatim (Gemini 3.x requirement)
    assert fc_part["function_call"]["id"] == "c-1"  # call id preserved for correlation


def test_missing_thought_signature_is_omitted_not_none():
    # Earlier (2.5) models don't emit a thought_signature; we must not inject a null key
    # (the SDK would reject `thought_signature: None`), so the key is simply absent.
    turn1 = _response([_fn_part("lookup_order", {})])  # no signature, no id
    final = _response([_text_part("done")])
    client = FakeClient([turn1, final])
    scenario = _scenario(tools=["lookup_order"])
    trace = GoogleAdapter(client=client).run(scenario, "gemini-2.5-flash")

    fc_part = next(
        p
        for m in trace.messages
        if m.get("role") == "model"
        for p in m.get("parts", [])
        if isinstance(p, dict) and "function_call" in p
    )
    assert "thought_signature" not in fc_part
    assert "id" not in fc_part["function_call"]


def test_refusal_via_safety_finish_reason():
    client = FakeClient(_response([_text_part("")], finish="SAFETY"))
    trace = GoogleAdapter(client=client).run(_scenario(), "gemini-2.0-flash")
    assert trace.refused is True


def test_refusal_via_prompt_block():
    client = FakeClient(_response([_text_part("")], block_reason="SAFETY"))
    trace = GoogleAdapter(client=client).run(_scenario(), "gemini-2.0-flash")
    assert trace.refused is True


def test_system_message_becomes_system_instruction():
    client = FakeClient(_response([_text_part("ok")]))
    scenario = _scenario(
        messages=[
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hi"},
        ]
    )
    GoogleAdapter(client=client).run(scenario, "gemini-2.0-flash")
    assert client.last_kwargs["config"]["system_instruction"] == "You are terse."
    # the user turn is sent as Gemini contents, system is NOT a content turn
    assert [c["role"] for c in client.last_kwargs["contents"]] == ["user"]


def test_string_tools_become_function_declarations():
    client = FakeClient(_response([_text_part("ok")]))
    GoogleAdapter(client=client).run(_scenario(tools=["cancel_subscription"]), "gemini-2.0-flash")
    tools = client.last_kwargs["config"]["tools"]
    assert tools[0]["function_declarations"][0]["name"] == "cancel_subscription"


def test_missing_key_raises_provider_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="GEMINI_API_KEY is not set"):
        GoogleAdapter().preflight()


def test_api_error_is_wrapped_key_safe():
    class ClientError(Exception):
        def __init__(self):
            self.code = 400
            self.message = "bad request with key AIzaSyABCDEF1234567890xyz embedded"

    def _boom(**_):
        raise ClientError()

    client = SimpleNamespace(models=SimpleNamespace(generate_content=_boom))
    with pytest.raises(ProviderError) as exc_info:
        GoogleAdapter(client=client).run(_scenario(), "gemini-2.0-flash")
    msg = str(exc_info.value)
    assert "AIza" not in msg  # Google key fragment scrubbed
    assert "Gemini call for model" in msg


def test_empty_candidates_raises():
    resp = _response([_text_part("x")])
    resp.candidates = []
    client = FakeClient(resp)
    with pytest.raises(ProviderError, match="no candidates"):
        GoogleAdapter(client=client).run(_scenario(), "gemini-2.0-flash")


def test_get_adapter_returns_google_adapter():
    assert isinstance(get_adapter("google"), GoogleAdapter)


# --- MP-104: the Vertex backend ------------------------------------------------------
#
# [M] 2026-08-25, measured on a real account. The AI Studio (API-key) path bills a PREPAY
# wallet that is separate from Google Cloud billing: with Cloud credit available, every
# current model returned `429 - "Your prepayment credits are depleted"`, and a freshly
# created key in the credited project was refused identically, because prepay is a property
# of the BILLING ACCOUNT and not of the project. The same project on Vertex answered and
# billed the Cloud credit. So this is not a stylistic second backend - it decides whether a
# user can spend the money they have. These tests are offline (ADR-0006): they assert which
# CLIENT gets built, never that a call succeeds.


class _SpyGenai:
    """Stands in for `google.genai`, recording how Client() was constructed."""

    def __init__(self):
        self.calls = []

    def Client(self, **kwargs):  # noqa: N802 - mirrors the SDK's class name
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


@pytest.fixture
def spy_genai(monkeypatch):
    spy = _SpyGenai()
    monkeypatch.setattr("modelpin.providers.google._import_genai", lambda: spy)
    for var in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(var, raising=False)
    # [M] MUST be mocked here, not per-test: `build_google_client` now resolves ADC eagerly,
    # so an unmocked Vertex test calls the REAL `google.auth.default()`. That passes on a
    # developer machine that happens to be logged in and fails on any CI runner - and it
    # probes the GCE metadata server, which ADR-0006 forbids the suite from doing. Tests that
    # want the failure path override this.
    monkeypatch.setattr("google.auth.default", lambda **_: ("fake-credentials", "fake-project"))
    return spy


def test_the_api_key_path_is_still_the_default(spy_genai, monkeypatch):
    """The default must not move. A user who sets only GEMINI_API_KEY gets AI Studio."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls == [{"api_key": "AIzaTESTKEY", "vertexai": False}], spy_genai.calls


def test_vertex_is_selected_by_the_sdks_own_env_var(spy_genai, monkeypatch):
    """[M] The SDK's documented contract, not a Modelpin-specific name, so a user already
    running google-genai elsewhere needs no extra setup."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-123")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls == [
        {"vertexai": True, "project": "proj-123", "location": "global"}
    ], spy_genai.calls


def test_vertex_never_receives_an_api_key(spy_genai, monkeypatch):
    """[M] Vertex REJECTS API keys outright ("API keys are not supported by this API.
    Expected OAuth2 access token"), so passing one through would turn a working ADC setup
    into a hard failure for any user who has both variables set."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-123")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSHOULD-NOT-BE-USED")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert "api_key" not in spy_genai.calls[0], spy_genai.calls
    assert spy_genai.calls[0]["vertexai"] is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_vertex_switch_accepts_the_usual_truthy_spellings(spy_genai, monkeypatch, value):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", value)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls[0].get("vertexai") is True, value


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  "])
def test_a_falsey_switch_leaves_the_api_key_path_alone(spy_genai, monkeypatch, value):
    """Guards the direction that would silently break existing users: a stray or disabled
    switch must NOT divert them to a backend they have no credentials for."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", value)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls == [{"api_key": "AIzaTESTKEY", "vertexai": False}], (
        value,
        spy_genai.calls,
    )


def test_vertex_without_a_project_names_the_missing_variable(spy_genai, monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    from modelpin.providers.google import build_google_client

    with pytest.raises(ProviderError, match="GOOGLE_CLOUD_PROJECT is empty"):
        build_google_client()
    assert spy_genai.calls == [], "no client may be built without a billing project"


def test_the_location_is_overridable_for_data_residency(spy_genai, monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls[0]["location"] == "asia-south1"


def test_the_no_key_error_points_at_the_backend_that_bills_cloud_credit(spy_genai):
    """A user staring at `GEMINI_API_KEY is not set` has no way to discover that the money
    they already hold is spendable through the other backend."""
    from modelpin.providers.google import build_google_client

    with pytest.raises(ProviderError) as exc_info:
        build_google_client()
    msg = str(exc_info.value)
    assert "GEMINI_API_KEY is not set" in msg
    assert "GOOGLE_GENAI_USE_VERTEXAI" in msg and "GOOGLE_CLOUD_PROJECT" in msg, msg


def test_a_vertex_client_failure_is_wrapped_and_names_ADC(monkeypatch):
    """The failure mode a user WILL hit: Vertex selected, but `gcloud auth
    application-default login` never run. The message must not read as a missing API key.

    [M] provider-SDK review 2026-08-25: the FIRST version of this test was a guard that
    asserted nothing real. It raised from `Client()`, and the SDK does not fail there - it
    only calls `load_auth` when `project` is ABSENT, and this code path requires `project`,
    so ADC is deferred to the first REQUEST. `preflight()` therefore passed with no
    credentials at all and the run died mid-replay on a raw `DefaultCredentialsError`, after
    the user had already been quoted a cost. `build_google_client` now resolves ADC eagerly;
    this test raises from THAT call, which is where the real failure lives.
    """

    class _Boom:
        def Client(self, **_):  # noqa: N802
            raise AssertionError("must fail on credentials before a client is ever built")

    def _no_adc(**_):
        raise RuntimeError("could not find default credentials for project sekret-proj")

    monkeypatch.setattr("modelpin.providers.google._import_genai", lambda: _Boom())
    monkeypatch.setattr("google.auth.default", _no_adc)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "sekret-proj")
    from modelpin.providers.google import build_google_client

    with pytest.raises(ProviderError) as exc_info:
        build_google_client()
    msg = str(exc_info.value)
    assert "application-default login" in msg, msg
    assert "NOT an API key" in msg, msg
    assert "aiplatform.googleapis.com" in msg, msg


def test_credentials_are_resolved_before_a_client_exists(monkeypatch):
    """[M] The non-negotiable: preflight must fail BEFORE any token is spent. Ordering is the
    assertion - a client built first would defer the credential check to the first paid call."""
    order = []

    class _Spy:
        def Client(self, **kwargs):  # noqa: N802
            order.append("client")
            return SimpleNamespace(**kwargs)

    monkeypatch.setattr("modelpin.providers.google._import_genai", lambda: _Spy())
    monkeypatch.setattr("google.auth.default", lambda **_: order.append("adc") or ("cred", "p"))
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert order == ["adc", "client"], order


def test_the_default_location_serves_current_models_not_just_legacy_ones(spy_genai, monkeypatch):
    """[M] provider-SDK review 2026-08-25, free `count_tokens` against a real project:

        us-central1  gemini-2.5-flash  SERVED     global  gemini-2.5-flash  SERVED
        us-central1  gemini-3.5-flash  404        global  gemini-3.5-flash  SERVED

    Every `gemini-3.x` id 404s regionally. The first version of this backend defaulted to
    `us-central1` with a comment claiming it "carries the broadest model coverage" - exactly
    inverted. On a tool whose PURPOSE is testing new models, a default that cannot reach any
    current-generation model is the worst available failure. `global` is also the SDK's own
    default, so this stops overriding the SDK with something worse.
    """
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setattr("google.auth.default", lambda **_: ("cred", "p"))
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls[0]["location"] == "global", spy_genai.calls
    assert (
        spy_genai.calls[0]["location"] != "us-central1"
    ), "a regional default cannot serve any Gemini 3.x model"


def test_the_enterprise_env_var_selects_vertex_too(spy_genai, monkeypatch):
    """[M] `GOOGLE_GENAI_USE_ENTERPRISE` is the SDK's CURRENT name and it WINS over
    `GOOGLE_GENAI_USE_VERTEXAI` (google-genai 2.19.0, `_api_client.py:650-676`), which the
    installed docstring calls "legacy". Reading only the legacy name meant Modelpin took its
    API-key branch while the SDK built a Vertex client underneath it."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setattr("google.auth.default", lambda **_: ("cred", "p"))
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert spy_genai.calls[0].get("vertexai") is True, spy_genai.calls


def test_the_api_key_path_pins_the_backend_against_the_sdks_own_env_reading(spy_genai, monkeypatch):
    """[M] THE silent-divergence guard. With `GOOGLE_GENAI_USE_ENTERPRISE=true` and an unpinned
    `vertexai=`, the real SDK builds `vertexai=True`, `base_url=https://aiplatform.googleapis.com/`
    under what Modelpin believes is its AI Studio branch - posting an AI Studio key to Vertex.
    Verified directly against the installed SDK, then pinned here."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY")
    from modelpin.providers.google import build_google_client

    build_google_client()
    assert (
        spy_genai.calls[0].get("vertexai") is False
    ), f"the API-key path left the backend for the SDK to re-decide: {spy_genai.calls}"
