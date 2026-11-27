"""MP-177: a missing SDK is reported before a missing key.

`[M] 2026-11-27` With neither the SDK nor a key (the state right after a plain
`pip install modelpin`), `modelpin baseline --provider openai` said `OPENAI_API_KEY is not
set`, exit 4. Only with a key set did it say the SDK was missing. The Anthropic API-key path
did the same; Google already checked its SDK first. Checking an import costs nothing, while
getting a key means creating an account and making a billing decision, so the free check goes
first and a user fixes both in the right order.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.demo import DEMO_DIRNAME, DEMO_FROM, write_demo
from modelpin.providers import ProviderError
from modelpin.providers.anthropic import build_anthropic_client
from modelpin.providers.google import build_google_client
from modelpin.providers.openai import build_openai_client

_KEY_VARS = (
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
)


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _no_sdk(monkeypatch: pytest.MonkeyPatch, *modules: str) -> None:
    """`sys.modules[name] = None` makes `import name` raise ImportError."""
    for name in modules:
        monkeypatch.setitem(sys.modules, name, None)


def test_openai_names_the_sdk_when_both_are_missing(no_keys) -> None:
    _no_sdk(no_keys, "openai")
    with pytest.raises(ProviderError, match="The OpenAI SDK is not installed"):
        build_openai_client()


def test_an_openai_compatible_host_names_the_sdk_when_both_are_missing(no_keys) -> None:
    _no_sdk(no_keys, "openai")
    with pytest.raises(ProviderError, match="The OpenAI SDK is not installed"):
        build_openai_client("GROQ_API_KEY", base_url="https://api.groq.com/openai/v1")


def test_anthropic_names_the_sdk_when_both_are_missing(no_keys) -> None:
    _no_sdk(no_keys, "anthropic")
    with pytest.raises(ProviderError, match="The Anthropic SDK is not installed"):
        build_anthropic_client()


def test_google_already_named_the_sdk_first(no_keys) -> None:
    """Pinned so the three adapters cannot drift apart again."""
    _no_sdk(no_keys, "google")  # `from google import genai` then raises ImportError
    with pytest.raises(ProviderError, match="The Google GenAI SDK is not installed"):
        build_google_client()


@pytest.mark.parametrize("build", [build_openai_client, build_anthropic_client])
def test_with_the_sdk_present_a_missing_key_is_still_reported(no_keys, build) -> None:
    pytest.importorskip("openai")
    pytest.importorskip("anthropic")
    with pytest.raises(ProviderError, match="_API_KEY is not set"):
        build()


def test_baseline_tells_a_new_user_to_install_the_sdk_first(no_keys, tmp_path) -> None:
    """The first-run path end to end: no key, no SDK, a real scenario directory."""
    write_demo(tmp_path)
    no_keys.chdir(tmp_path / DEMO_DIRNAME)
    _no_sdk(no_keys, "openai")
    result = CliRunner().invoke(
        app, ["baseline", "--model", DEMO_FROM, "--provider", "openai", "--runs", "2"]
    )
    out = " ".join(result.output.split())
    assert "The OpenAI SDK is not installed" in out, result.output
    assert "OPENAI_API_KEY is not set" not in out, result.output
    assert result.exit_code == 4, result.output
