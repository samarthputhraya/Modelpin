"""MP-136 -- the REJECTED-key error did not name the variable to fix; the UNSET one did.

`[M] 2026-08-29`, dogfood on a real app from a PyPI install with a revoked Groq key:

    error: Groq call for model 'llama-3.3-70b-versatile' failed: your API key was
    rejected (invalid or revoked) [AuthenticationError].

Correct, clean, no traceback, exit 1 -- and it never says WHICH variable to fix. The
sibling unset-key path does: *"GROQ_API_KEY is not set. Modelpin uses YOUR own API key
(cost + provider ToS) -- export it and retry."* A stranger whose key is stale got the less
actionable of the two messages and had to guess.

`[M] 2026-08-31` THE FIX IS NOT "RESTORE THE MESSAGE", AND THAT CORRECTION IS THE POINT.
The rejected branch drops `str(exc)` ON PURPOSE: `_KEY_BEARING_ERRORS` is a default-deny
guard on the secret surface, documented at `openai.py`'s explainer -- auth and permission
errors may embed a redacted key fragment, so their text is dropped entirely rather than
scrubbed and hoped over. What is added here is the variable NAME, which the user set
themselves and which is not a secret. The exception text is still dropped.

`[M]` And the defect spans THREE call sites, not the one the row names: the replay adapter
(`providers/openai.py`), the semantic judge (`judge.py`, which spends the same key), and
the Gemini path (`providers/google.py`, whose 401/403 branch named no variable either).
"""

from __future__ import annotations

import pytest

from modelpin.providers.base import ProviderError
from modelpin.providers.google import _explain_api_error as explain_gemini
from modelpin.providers.openai import _explain_api_error as explain_openai
from modelpin.providers.openai import build_openai_client

#: A realistic leaked-key shape. The guard must keep this out of every message.
_LEAKY = "Incorrect API key provided: gsk_liveSECRET1234567890abcdef. You can find your key at..."


class AuthenticationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class RateLimitError(Exception):
    pass


class _GeminiClientError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


# --- the defect ------------------------------------------------------------------------


@pytest.mark.parametrize("exc_cls", [AuthenticationError, PermissionDeniedError])
def test_the_rejected_key_error_names_the_variable_to_fix(exc_cls):
    msg = explain_openai(exc_cls(_LEAKY), "llama-3.3-70b-versatile", "Groq", "GROQ_API_KEY")
    assert "GROQ_API_KEY" in msg
    assert "rejected" in msg or "lacks access" in msg


def test_the_gemini_rejected_key_error_names_its_variable_too():
    """Not in the row: the 401/403 branch named no variable either."""
    msg = explain_gemini(_GeminiClientError("bad key AIzaSyLIVE123", 401), "gemini-2.0-flash")
    assert "GEMINI_API_KEY" in msg


def test_the_judge_names_the_variable_on_a_rejected_key():
    """The judge spends the user's key too, so it must not be the one surface that still
    makes them guess."""
    import inspect

    from modelpin.judge import OpenAIJudge

    src = inspect.getsource(OpenAIJudge.equivalent)
    assert "api_key_env=self._api_key_env" in src


# --- the guard the fix must not weaken -------------------------------------------------


@pytest.mark.parametrize("exc_cls", [AuthenticationError, PermissionDeniedError])
def test_the_key_itself_is_still_dropped_entirely(exc_cls):
    """`_KEY_BEARING_ERRORS` default-denies on the secret surface. Naming the VARIABLE must
    not become an excuse to echo its VALUE."""
    msg = explain_openai(exc_cls(_LEAKY), "m", "Groq", "GROQ_API_KEY")
    assert "gsk_" not in msg
    assert "SECRET" not in msg
    assert "Incorrect API key provided" not in msg


def test_the_gemini_key_is_still_dropped_on_the_rejected_branch():
    msg = explain_gemini(_GeminiClientError("bad key AIzaSyLIVE123", 403), "gemini-2.0-flash")
    assert "AIzaSy" not in msg


def test_a_non_key_bearing_error_still_keeps_its_body():
    """`RateLimitError` is not key-bearing, so its text survives (scrubbed) -- the server's
    own "try again in 52.5ms" is the most useful thing in it (MP-139)."""
    msg = explain_openai(RateLimitError("429 ... try again in 52.5ms"), "m", "Groq", "GROQ_API_KEY")
    assert "52.5ms" in msg


def test_omitting_the_variable_changes_nothing_else():
    """Back-compat: the parameter is optional, and without it the message is what it was."""
    msg = explain_openai(AuthenticationError(_LEAKY), "m", "Groq")
    assert "Modelpin read your key from" not in msg
    assert "gsk_" not in msg


# --- the two messages must stay a matched pair -----------------------------------------


def test_the_unset_and_rejected_messages_name_the_same_variable(monkeypatch):
    """The whole row is that these two disagreed about how much help to give. `[M]` The
    unset path already named it; now both do, from the same value."""
    monkeypatch.delenv("MODELPIN_TEST_KEY", raising=False)
    with pytest.raises(ProviderError) as unset:
        build_openai_client("MODELPIN_TEST_KEY")
    rejected = explain_openai(AuthenticationError(_LEAKY), "m", "Groq", "MODELPIN_TEST_KEY")
    assert "MODELPIN_TEST_KEY" in str(unset.value)
    assert "MODELPIN_TEST_KEY" in rejected


def test_the_message_is_cp1252_encodable():
    """`[M] 2026-08-30` MP-138 crashed `modelpin check` on a default Windows console with a
    U+2192 in a message. Every string that can reach the terminal gets this test now."""
    explain_openai(AuthenticationError(_LEAKY), "m", "Groq", "GROQ_API_KEY").encode("cp1252")
    explain_gemini(_GeminiClientError("x", 401), "gemini-2.0-flash").encode("cp1252")
