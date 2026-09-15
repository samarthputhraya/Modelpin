"""What `modelpin init` writes into modelpin.yaml: a config that runs on the first try.

The scaffold used to be one fixed file -- `gpt-4o-mini` on OpenAI, judged by `gpt-4o-mini` --
which was wrong for most people who ran it: a Gemini or Claude user's first `modelpin baseline`
exited asking for an `OPENAI_API_KEY` they do not have, and everyone else's judge was the very
model it was judging. `init` now fills the three decisions in from what it can actually see:

1. **the model** your code already calls, read off `modelpin scan` of the repo being set up;
2. **the provider** that model belongs to (or, with no model found, the one you hold
   credentials for);
3. **a judge** on that same provider, and never the model under test.

Every choice is written with a comment saying where it came from, so a wrong guess is obvious and
a one-line edit away. Nothing here reads a credential's VALUE -- only whether the variable is set.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from modelpin.config import DEFAULT_RUNS

#: Credential environment variables per provider, in the order `init` prefers providers when the
#: repo names no model. Presence is all that is checked.
PROVIDER_CREDENTIALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openai", ("OPENAI_API_KEY",)),
    ("anthropic", ("ANTHROPIC_API_KEY", "ANTHROPIC_VERTEX_PROJECT_ID")),
    ("google", ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")),
    ("groq", ("GROQ_API_KEY",)),
    ("openrouter", ("OPENROUTER_API_KEY",)),
    ("together", ("TOGETHER_API_KEY",)),
    ("cerebras", ("CEREBRAS_API_KEY",)),
)

#: A sensible current model per provider when the repo names none.
DEFAULT_MODEL: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "google": "gemini-2.5-flash",
    "groq": "openai/gpt-oss-20b",
    "openrouter": "openai/gpt-4o-mini",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "cerebras": "llama-3.3-70b",
}

#: Judge per provider: first choice, and the fallback when the first choice IS the app's model.
JUDGE_MODEL: dict[str, tuple[str, str]] = {
    "openai": ("gpt-4.1-mini", "gpt-4o-mini"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-4-5"),
    "google": ("gemini-2.5-flash", "gemini-2.5-flash-lite"),
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    "openrouter": ("openai/gpt-4.1-mini", "openai/gpt-4o-mini"),
    "together": ("openai/gpt-oss-120b", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "cerebras": ("gpt-oss-120b", "llama-3.3-70b"),
}

#: The judge's host must be written out for these, because their model ids do not name a vendor.
_JUDGE_PROVIDER_REQUIRED = frozenset({"groq", "openrouter", "together", "cerebras"})

#: Environment variable each provider reads, for the comment in the generated file.
CREDENTIAL_HINT: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY (or ANTHROPIC_VERTEX_PROJECT_ID for Claude on Vertex AI)",
    "google": "GEMINI_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together": "TOGETHER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}


@dataclass(frozen=True)
class Setup:
    model: str
    provider: str
    judge_model: str
    #: Why `model` was chosen, for the comment beside it.
    model_source: str
    #: Why `provider` was chosen.
    provider_source: str

    @property
    def judge_provider(self) -> Optional[str]:
        return self.provider if self.provider in _JUDGE_PROVIDER_REQUIRED else None


def provider_for_model(model: str) -> Optional[str]:
    """The provider a model id belongs to, when the id alone says so beyond doubt."""
    tail = model.lower()
    if tail.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if tail.startswith("claude-"):
        return "anthropic"
    if tail.startswith(("gemini-", "models/gemini")):
        return "google"
    return None


def _has_credentials(provider: str, env: Mapping[str, str]) -> bool:
    for name, variables in PROVIDER_CREDENTIALS:
        if name == provider:
            return any((env.get(v) or "").strip() for v in variables)
    return False


def _credentialed_providers(env: Mapping[str, str]) -> list[str]:
    return [name for name, _ in PROVIDER_CREDENTIALS if _has_credentials(name, env)]


def _vertex_claude_id(model: str, env: Mapping[str, str]) -> str:
    """Claude on Vertex AI addresses a model by a dated id (`claude-haiku-4-5@20251001`)."""
    if (env.get("ANTHROPIC_VERTEX_PROJECT_ID") or "").strip() and model == "claude-haiku-4-5":
        return "claude-haiku-4-5@20251001"
    return model


def _same_model(a: str, b: str) -> bool:
    """`claude-haiku-4-5`, `claude-haiku-4-5-20251001` and `claude-haiku-4-5@20251001` are one model."""
    strip = lambda m: re.sub(r"(@\d{8}|-\d{8})$", "", m.lower())  # noqa: E731
    return strip(a) == strip(b)


def infer_setup(hits: list[dict], env: Optional[Mapping[str, str]] = None) -> Setup:
    """Choose model, provider and judge from scan hits and which credentials are set."""
    env = os.environ if env is None else env
    called = Counter(
        h["model"]
        for h in hits
        if h.get("context", "code") == "code" and provider_for_model(h["model"])
    )
    credentialed = _credentialed_providers(env)

    if called:
        # Prefer a model the user can actually run today; among those, the most used.
        ranked = sorted(called.items(), key=lambda kv: (-kv[1], kv[0]))
        runnable = [m for m, _ in ranked if provider_for_model(m) in credentialed]
        model = (runnable or [ranked[0][0]])[0]
        provider = provider_for_model(model) or "openai"
        model_source = f"found by `modelpin scan` ({called[model]} reference(s) in your code)"
        provider_source = "the provider that serves that model"
    elif credentialed:
        provider = credentialed[0]
        model = _vertex_claude_id(DEFAULT_MODEL[provider], env)
        model_source = "a placeholder: no model id was found in your code -- set yours"
        provider_source = f"{CREDENTIAL_HINT[provider].split(' ')[0]} is set in your environment"
    else:
        provider = "openai"
        model = DEFAULT_MODEL[provider]
        model_source = "a placeholder: no model id was found in your code -- set yours"
        provider_source = "a placeholder: no provider credentials were found in your environment"

    first, fallback = JUDGE_MODEL[provider]
    first = _vertex_claude_id(first, env)
    judge = fallback if _same_model(first, model) else first
    return Setup(model, provider, judge, model_source, provider_source)


def render_config(setup: Setup) -> str:
    """The modelpin.yaml text for a setup. Always loads with `config.load_config`."""
    lines = [
        "# modelpin.yaml - written by `modelpin init`. Edit freely; every key is explained below.",
        "#",
        "# The model your app uses today. `modelpin baseline` records how it behaves.",
        f"#   chosen: {setup.model_source}",
        "models:",
        f"  - {setup.model}",
        "",
        "# Where your scenario files live (one JSON file per representative case).",
        "scenarios_dir: scenarios",
        "",
        f"# Who serves the model. Uses YOUR key from {CREDENTIAL_HINT[setup.provider]}.",
        f"#   chosen: {setup.provider_source}",
        "providers:",
        f"  - {setup.provider}",
        "",
        "# How many times each scenario is replayed per model. Models are random, so Modelpin",
        "# compares distributions of runs, never single answers. Below 4 some checks cannot",
        "# fire at all; 5 is the tested default.",
        f"runs: {DEFAULT_RUNS}",
        "",
        "# The LLM that decides whether two answers MEAN the same thing (costs extra calls).",
        "# Pick a model that is NOT one you are comparing: a model judging its own output is not",
        "# an independent reading. Delete this line to compare tool calls, refusals and",
        "# must_contain checks only.",
        f"judge_model: {setup.judge_model}",
    ]
    if setup.judge_provider:
        lines.append(
            f"judge_provider: {setup.judge_provider}   # this id does not name its vendor, so say which host"
        )
    else:
        lines.append(
            "# judge_provider: groq   # only needed when the judge id does not name its vendor"
        )
    return "\n".join(lines) + "\n"

