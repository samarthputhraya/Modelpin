"""Tests for the cross-provider secret-scrubber + refusal helper."""

import pytest

from modelpin.providers._common import looks_like_refusal, scrub_secrets


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-ABCDEF1234567890",  # OpenAI
        "gsk_ABCDEF1234567890abcdef",  # Groq (OpenAI-compatible host we support)
        "AIzaSyABCDEF1234567890xyz",  # Google API key
        "ya29.A0ABCDEF1234567890",  # Google OAuth token
        "AQ.Ab8RN6ABCDEF1234567890",  # Google (AQ.-prefixed key, as in the wild)
        "Bearer sometokenvalue123",  # raw auth header
    ],
)
def test_scrub_redacts_key_shaped_tokens(secret):
    text = f"request failed near {secret} in the body"
    scrubbed = scrub_secrets(text)
    assert "[redacted]" in scrubbed
    # no recognizable fragment of the secret survives
    assert secret.split(".")[-1][:8] not in scrubbed
    assert "AQ." not in scrubbed or secret.startswith("Bearer")


def test_scrub_leaves_benign_text_alone():
    assert (
        scrub_secrets("the model returned a 503 try again") == "the model returned a 503 try again"
    )


def test_looks_like_refusal():
    assert looks_like_refusal("I'm not able to help with that.")
    assert looks_like_refusal("I cannot share that information.")
    assert not looks_like_refusal("Here is the answer you asked for.")
    assert not looks_like_refusal("As an AI, here is the requested code.")  # not a refusal
