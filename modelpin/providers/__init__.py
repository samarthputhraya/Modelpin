"""Provider adapters — turn a Scenario into a Trace by calling a model.
See spec sections 4.4 and 9.

RULES:
- Use the END USER's API key from the environment. Never hardcode or ship keys.
- Each adapter returns a Trace.
"""

from __future__ import annotations

from modelpin.providers.base import ProviderAdapter, ProviderError
from modelpin.providers.fake import FakeProvider

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "FakeProvider",
    "get_adapter",
    "UNIMPLEMENTED_PROVIDERS",
    "provider_help",
]

#: Providers this package can NAME but cannot RUN. `[M] 2026-08-27` (MP-128) `anthropic` was
#: advertised without a marker in five places -- `action.yml`, three `--provider` help strings,
#: and this module's own unknown-provider message -- while `providers/anthropic.py:17` is
#: `raise NotImplementedError`. Following the documentation reached a crash. Deliberately NOT
#: removed from the list: a user who types `--provider anthropic` deserves to be told it is
#: coming, not that it is unknown. See MP-20 for the dated trigger to implement it.
UNIMPLEMENTED_PROVIDERS: tuple[str, ...] = ("anthropic",)


def provider_help(include_fake: bool = True) -> str:
    """The `--provider` choice list, with unimplemented adapters marked.

    ONE source for every place this list is shown. Five hand-maintained copies is how the
    marker came to be missing from all of them at once -- the same defect shape as MP-03,
    where three copies of one run count drifted apart.
    """
    from modelpin.providers.openai import OPENAI_COMPATIBLE_PROVIDERS

    live = ["openai", "google", *OPENAI_COMPATIBLE_PROVIDERS]
    if include_fake:
        live.append("fake")
    tail = ", ".join(UNIMPLEMENTED_PROVIDERS)
    return f"{' | '.join(live)}. ({tail}: NOT yet implemented and will fail the run.)"


def get_adapter(provider: str) -> ProviderAdapter:
    provider = provider.lower()
    if provider == "fake":
        return FakeProvider()
    if provider == "openai":
        from modelpin.providers.openai import OpenAIAdapter

        return OpenAIAdapter()
    if provider == "google":
        from modelpin.providers.google import GoogleAdapter

        return GoogleAdapter()
    if provider == "anthropic":
        from modelpin.providers.anthropic import AnthropicAdapter

        return AnthropicAdapter()

    # OpenAI-compatible hosts (Groq/OpenRouter/Together/Cerebras) — a free Llama endpoint
    # can serve as a cross-vendor target through the reused OpenAI adapter (different base_url).
    from modelpin.providers.openai import (
        OPENAI_COMPATIBLE_PROVIDERS,
        build_openai_compatible_adapter,
    )

    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        return build_openai_compatible_adapter(provider)

    # NOT `(try: {provider_help()})`: `provider_help` ends in its own parenthetical, so
    # wrapping it closed with `run.))`. `[M]` first-run review, 2026-08-31.
    raise ValueError(f"Unknown provider: {provider}. Valid: {provider_help()}")
