"""A transient Gemini 429 must not cost the whole run.

`[M] 2026-09-15`, live on Vertex with a real project: `modelpin baseline` over 2 scenarios x 5
runs died on the first call with `rate limit or quota exceeded [ClientError 429]` (exit 4), and
`modelpin check` died the same way inside the JUDGE, after every candidate replay had already
been billed. Re-running a minute later succeeded, so nothing was wrong but a shared-quota blip.

Root cause, read off the installed SDK (`google-genai` 2.19.0, `_api_client.py::retry_args`):
with no `http_options.retry_options` the SDK retries NOTHING (`stop_after_attempt(1)`). The
OpenAI adapter has always inherited its SDK's retry; the Google path had none.

These tests drive the REAL SDK client through an `httpx.MockTransport`, which is the only
layer that exercises the SDK's retry loop -- a stub on `models.generate_content` sits above it
and could never observe a retry (the same trap `test_provider_retry.py` pins for OpenAI).
Fully offline (ADR-0006).
"""

from __future__ import annotations

import httpx
import pytest

from modelpin.providers import google as google_provider
from modelpin.providers.google import RETRY_OPTIONS, build_google_client

_OK_BODY = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "hello"}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
}
_QUOTA_BODY = {
    "error": {"code": 429, "message": "Resource exhausted.", "status": "RESOURCE_EXHAUSTED"}
}


@pytest.fixture
def no_backoff(monkeypatch):
    """Keep the real retry POLICY (attempt count, retryable codes) but make the waits instant."""
    fast = dict(RETRY_OPTIONS, initial_delay=0.001, max_delay=0.001, jitter=0.001)
    monkeypatch.setattr(google_provider, "_HTTP_OPTIONS", {"retry_options": fast})
    return fast


def _client_with_transport(monkeypatch, handler):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST-not-a-real-key")
    for var in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(var, raising=False)
    client = build_google_client()
    # Swap only the transport, AFTER construction, so the retry wiring under test is exactly
    # the one `build_google_client` chose.
    client._api_client._httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _flaky(n_failures):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] <= n_failures:
            return httpx.Response(429, json=_QUOTA_BODY)
        return httpx.Response(200, json=_OK_BODY)

    return handler, state


def _call(client):
    return client.models.generate_content(model="gemini-2.5-flash-lite", contents="hi")


def test_a_transient_quota_429_is_retried_until_it_clears(monkeypatch, no_backoff):
    handler, state = _flaky(3)
    response = _call(_client_with_transport(monkeypatch, handler))
    assert response.candidates[0].content.parts[0].text == "hello"
    assert state["n"] == 4, "3 failures + 1 success"


def test_the_retry_budget_is_finite(monkeypatch, no_backoff):
    """An exhausted quota must still surface as an error rather than hang the run forever."""
    handler, state = _flaky(99)
    with pytest.raises(Exception) as exc:
        _call(_client_with_transport(monkeypatch, handler))
    assert getattr(exc.value, "code", None) == 429
    assert state["n"] == RETRY_OPTIONS["attempts"]


def test_a_bad_request_is_not_retried(monkeypatch, no_backoff):
    """A 400 is the caller's error; retrying it only multiplies the bill for the same refusal."""
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx.Response(
            400, json={"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}
        )

    with pytest.raises(Exception):
        _call(_client_with_transport(monkeypatch, handler))
    assert state["n"] == 1


def test_without_retry_options_the_sdk_would_not_retry_at_all(monkeypatch, no_backoff):
    """Pins the root cause, so a later 'simplification' that drops `http_options` fails here."""
    monkeypatch.setattr(google_provider, "_HTTP_OPTIONS", None)
    handler, state = _flaky(1)
    with pytest.raises(Exception):
        _call(_client_with_transport(monkeypatch, handler))
    assert state["n"] == 1
