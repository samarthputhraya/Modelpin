"""MP-139 -- a 429 whose own body says "try again in 52.5ms" must not cost the whole run.

`[M] 2026-08-30`, dogfood on the Groq free tier: `modelpin check --to openai/gpt-oss-120b`
died on `RateLimitError 429 ... tokens per minute (TPM): Limit 8000, Used 6480, Requested
1527. Please try again in 52.5ms`. Twelve scenarios, 60 replays, ZERO reported. The free
tier is how most strangers arrive -- the wedge is the solo-dev long tail -- and TPM limits
bite hardest there.

`[M] 2026-08-31` THE ROW'S PREMISE IS WRONG, and correcting it is what makes this fix a
one-line budget rather than a retry loop. The row says "No backoff/retry exists on the
provider path." Retry DOES exist: it is the OpenAI SDK's, it is on by default at
`max_retries=2`, and it honours the server's own `retry-after` / `retry-after-ms`.

That was not visible to the earlier reproduction because it stubbed
`client.chat.completions.create`, which sits ABOVE the SDK's retry -- a stub there replaces
the retrying layer wholesale and can prove nothing about it. These tests drive an
`httpx.MockTransport` instead, which is the only layer that exercises it, and
`test_a_stub_above_the_sdk_cannot_see_the_retry` pins that distinction so the next person
does not re-write the test that proved nothing.

So the free-tier run did not die for want of a retry. It died because a budget of 2, spread
over ~1.5s of backoff, is tuned for a paid tier whose token window is not already exhausted.
`REPLAY_MAX_RETRIES` raises the SDK's own budget, which composes with nothing and inherits
`retry-after` handling for free -- unlike a loop around `_complete`, which would multiply
with the SDK's (2 x ours) and again by `MAX_TOOL_TURNS = 6` on an agent scenario.

Fully offline (ADR-0006): every response is synthesised by a mock transport.
"""

from __future__ import annotations

import httpx
import pytest

from modelpin.providers.openai import (
    MAX_TOOL_TURNS,
    REPLAY_MAX_RETRIES,
    _API_ERROR_HINTS,
    _explain_api_error,
    build_openai_client,
)

_RATE_LIMIT_BODY = {
    "error": {
        "message": (
            "Rate limit reached for model `openai/gpt-oss-120b` in organization `x` on tokens "
            "per minute (TPM): Limit 8000, Used 6480, Requested 1527. Please try again in 52.5ms."
        ),
        "type": "tokens",
        "code": "rate_limit_exceeded",
    }
}

_OK_BODY = {
    "id": "x",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
}


def _client(monkeypatch, handler, **kw):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    client = build_openai_client(**kw)
    # Swap in the mock transport AFTER construction so the real `max_retries` wiring under
    # test is the one `build_openai_client` chose, not one this test passed to httpx.
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _flaky(n_failures, *, retry_after_ms="1"):
    """429 for the first `n_failures` attempts, then 200. Counts HTTP attempts."""
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] <= n_failures:
            return httpx.Response(
                429, headers={"retry-after-ms": retry_after_ms}, json=_RATE_LIMIT_BODY
            )
        return httpx.Response(200, json=_OK_BODY)

    return handler, state


def _call(client):
    return client.chat.completions.create(
        model="openai/gpt-oss-120b", messages=[{"role": "user", "content": "hi"}]
    )


# --- the incident: a transient 429 must not end the run --------------------------------


def test_the_dogfood_rate_limit_is_survived(monkeypatch):
    """`[M]` The exact incident: a 429 whose body says "try again in 52.5ms". Under the
    SDK's default budget of 2 this is already survivable; the point of the test is that the
    call SUCCEEDS rather than raising, and that it does so by retrying."""
    handler, state = _flaky(2)
    r = _call(_client(monkeypatch, handler))
    assert r.choices[0].message.content == "ok"
    assert state["n"] == 3, "1 attempt + 2 retries"


def test_the_raised_budget_survives_more_failures_than_the_sdk_default(monkeypatch):
    """The actual fix. `[M]` At the SDK default of 2 a third consecutive 429 ends the run;
    the free tier's token window routinely takes longer than ~1.5s of backoff to clear."""
    handler, state = _flaky(4)
    r = _call(_client(monkeypatch, handler))
    assert r.choices[0].message.content == "ok"
    assert state["n"] == 5

    handler2, state2 = _flaky(4)
    default_client = _client(monkeypatch, handler2, max_retries=2)
    with pytest.raises(Exception) as exc:
        _call(default_client)
    assert type(exc.value).__name__ == "RateLimitError"
    assert state2["n"] == 3, "the SDK default gives up after 3 attempts"


def test_the_budget_is_finite_and_the_error_survives_it(monkeypatch):
    """Capped, not infinite: a quota that is genuinely exhausted must still surface as a
    clean error rather than hanging the run forever."""
    handler, state = _flaky(99)
    with pytest.raises(Exception) as exc:
        _call(_client(monkeypatch, handler))
    assert type(exc.value).__name__ == "RateLimitError"
    assert state["n"] == REPLAY_MAX_RETRIES + 1


def test_the_client_is_built_with_the_replay_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert build_openai_client().max_retries == REPLAY_MAX_RETRIES
    assert REPLAY_MAX_RETRIES > 2, "the whole point is that the SDK default was too low"


# --- the methodological trap the earlier reproduction fell into ------------------------


def test_a_stub_above_the_sdk_cannot_see_the_retry(monkeypatch):
    """`[M]` An earlier reproduction of this row stubbed `client.chat.completions.create`
    and concluded no retry existed. A stub THERE replaces the retrying layer wholesale: it
    is called exactly once however large the budget is. Pinned so the next person writes
    the transport-level test instead of the one that proves nothing."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    client = build_openai_client()
    calls = {"n": 0}

    def stub(**kwargs):
        calls["n"] += 1
        raise RuntimeError("429-ish")

    monkeypatch.setattr(client.chat.completions, "create", stub)
    with pytest.raises(RuntimeError):
        _call(client)
    assert calls["n"] == 1, "no retry is observable above the SDK, whatever max_retries says"


# --- what the user is told when the budget IS exhausted --------------------------------


def test_the_exhausted_rate_limit_message_says_retries_were_already_spent():
    """Without this a user re-runs straight into the same wall, believing nothing was tried.
    It must also name the levers they actually have."""

    class RateLimitError(Exception):
        pass

    msg = _explain_api_error(RateLimitError("429 ... try again in 52.5ms"), "m", "Groq")
    assert str(REPLAY_MAX_RETRIES) in msg
    assert "automatic retries" in msg
    assert "--runs" in msg


def test_the_rate_limit_hint_tracks_the_constant():
    """The number in the message and the number in the client are one constant, so they
    cannot drift -- three copies of one number drifting apart IS MP-03."""
    assert str(REPLAY_MAX_RETRIES) in _API_ERROR_HINTS["RateLimitError"]


def test_a_rate_limit_message_still_carries_its_body_and_no_key():
    """`RateLimitError` is NOT in `_KEY_BEARING_ERRORS`, so its text is kept (scrubbed) --
    the server's own "try again in 52.5ms" is the most useful thing in it."""

    class RateLimitError(Exception):
        pass

    msg = _explain_api_error(
        RateLimitError("429 tokens per minute (TPM): Limit 8000 ... try again in 52.5ms"), "m"
    )
    assert "52.5ms" in msg
    assert "sk-" not in msg


# --- why this is a budget and not a loop -----------------------------------------------


def test_the_retry_is_not_multiplied_by_the_tool_loop():
    """`[M]` `_complete` is called inside `for _turn in range(MAX_TOOL_TURNS)`, so a retry
    loop installed around it would cost `MAX_TOOL_TURNS x budget` attempts on an agent
    scenario. Raising the SDK's own budget is per-HTTP-request and composes with nothing.
    This test documents the reasoning; it fails loudly if someone adds a competing loop."""
    import ast
    import inspect
    import textwrap

    from modelpin.providers.openai import OpenAIAdapter

    tree = ast.parse(textwrap.dedent(inspect.getsource(OpenAIAdapter._complete)))
    loops = [n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While, ast.AsyncFor))]
    assert loops == [], "no retry loop belongs here; the SDK already retries per HTTP request"
    assert MAX_TOOL_TURNS == 6
