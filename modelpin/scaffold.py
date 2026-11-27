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

import importlib.util
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
#:
#: MP-310. `[M] 2026-11-24` OpenAI's models endpoint (`GET /v1/models/{id}`, `shutdown_date`)
#: returns 2027-01-14 for `gpt-4o-mini` and `gpt-4.1-mini` and their dated snapshots, and
#: Anthropic's deprecations page retires `claude-sonnet-4-5` on 2026-11-30. All three were
#: defaults here, so a config `init` wrote this year would name a model that no longer answers.
#: The OpenAI placeholder is the replacement OpenAI names for `gpt-4o-mini`. No default may be
#: inside its notice window: `tests/test_scaffold.py` checks every one against the registry.
DEFAULT_MODEL: dict[str, str] = {
    "openai": "gpt-6-luna",
    "anthropic": "claude-haiku-4-5",
    "google": "gemini-3.1-flash-lite",
    "groq": "openai/gpt-oss-20b",
    "openrouter": "openai/gpt-6-luna",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "cerebras": "llama-3.3-70b",
}

#: Judge per provider: first choice, and the fallback when the first choice IS the app's model.
#:
#: MP-310. The OpenAI judges are deliberately NOT `gpt-6-luna`: it is the replacement OpenAI
#: names for everything shutting down on 2027-01-14, so it is the candidate of the migrations
#: users run next, and a judge must not be a side. `[M] 2026-11-24` live, through this
#: package's own judge, `gpt-5.6-terra`, `gpt-5.6-luna` and `claude-sonnet-5-5` each called an
#: equivalent pair equivalent and a changed pair not equivalent; OpenAI lists no shutdown date
#: for either gpt-5.6 model.
JUDGE_MODEL: dict[str, tuple[str, str]] = {
    "openai": ("gpt-5.6-terra", "gpt-5.6-luna"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-5-5"),
    "google": ("gemini-3.5-flash", "gemini-3.1-flash-lite"),
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    "openrouter": ("openai/gpt-5.6-terra", "openai/gpt-5.6-luna"),
    "together": ("openai/gpt-oss-120b", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "cerebras": ("gpt-oss-120b", "llama-3.3-70b"),
}

#: The judge's host must be written out for these, because their model ids do not name a vendor.
_JUDGE_PROVIDER_REQUIRED = frozenset({"groq", "openrouter", "together", "cerebras"})

#: Environment variable each provider reads, for the comment in the generated file.
CREDENTIAL_HINT: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY (or ANTHROPIC_VERTEX_PROJECT_ID for Claude on Vertex AI)",
    # Both key variables, as `providers/google.py` reads them (MP-315).
    "google": "GEMINI_API_KEY or GOOGLE_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=true + "
    "GOOGLE_CLOUD_PROJECT)",
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


def _google_vertex_flag(env: Mapping[str, str]) -> Optional[str]:
    """The variable that selects Google's Vertex backend in ``env``, or None.

    Read exactly as `providers/google.py` reads it: the first of its `_VERTEX_ENVS` that is
    set decides, and only a truthy value selects Vertex.
    """
    from modelpin.providers.google import _VERTEX_ENVS, _truthy

    for name in _VERTEX_ENVS:
        raw = env.get(name)
        if raw is not None and raw.strip():
            return name if _truthy(raw) else None
    return None


def _set(env: Mapping[str, str], name: str) -> bool:
    return bool((env.get(name) or "").strip())


def has_credentials(provider: str, env: Mapping[str, str]) -> bool:
    if provider == "google":
        # MP-341: `[M] 2026-11-27` `GOOGLE_GENAI_USE_VERTEXAI=true` alone made `init` say
        # "Credentials for google are already set", and the adapter then refused to build a
        # client without GOOGLE_CLOUD_PROJECT. Once Vertex is selected the adapter ignores any
        # API key, so the project is what counts.
        if _google_vertex_flag(env):
            return _set(env, "GOOGLE_CLOUD_PROJECT")
        return _set(env, "GEMINI_API_KEY") or _set(env, "GOOGLE_API_KEY")
    for name, variables in PROVIDER_CREDENTIALS:
        if name == provider:
            return any(_set(env, v) for v in variables)
    return False


def _credentialed_providers(env: Mapping[str, str]) -> list[str]:
    return [name for name, _ in PROVIDER_CREDENTIALS if has_credentials(name, env)]


def _credential_found(provider: str, env: Mapping[str, str]) -> str:
    """The first of ``provider``'s credential variables that is set, by the same test as
    `has_credentials`.

    MP-315: `[M] 2026-11-25` with only ANTHROPIC_VERTEX_PROJECT_ID set, the written config said
    `chosen: ANTHROPIC_API_KEY is set in your environment`, because the comment took the first
    word of `CREDENTIAL_HINT` whichever variable had matched.
    """
    if provider == "google" and (flag := _google_vertex_flag(env)):
        return flag
    variables = dict(PROVIDER_CREDENTIALS)[provider]
    if provider == "google":
        variables = tuple(v for v in variables if v.endswith("_API_KEY"))
    return next((v for v in variables if _set(env, v)), variables[0])


#: The import each provider adapter needs. The OpenAI-compatible hosts all speak through the
#: OpenAI SDK, so they share its module.
_PROVIDER_SDK: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google.genai",
    "groq": "openai",
    "openrouter": "openai",
    "together": "openai",
    "cerebras": "openai",
}


def sdk_installed(provider: str) -> bool:
    """Can the adapter for `provider` actually import its SDK?

    `[M] 2026-09-16`, first-run walk-through on a clean machine: `pip install modelpin`
    (no `[providers]` extra -- the shorter command, and the one people type) followed by
    `modelpin init` printed "Credentials for google are already set in your environment"
    and "modelpin baseline" as the next step. The SDK was not there, so the first command
    `init` recommended died with exit 4. `init` knew enough to say so and did not.

    `find_spec`, not a real import: loading a provider SDK costs ~0.5s and `init` is the
    command that must feel instant. An import error inside the package itself is a broken
    install, and the adapter reports that at the point of use.
    """
    module = _PROVIDER_SDK.get(provider)
    if module is None:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A parent package that is itself missing or a namespace stub with no loader:
        # either way the adapter's own import will fail, so report it the same way.
        return False


def _vertex_claude_id(model: str, env: Mapping[str, str]) -> str:
    """Claude on Vertex AI addresses a model by a dated id (`claude-haiku-4-5@20251001`)."""
    if (env.get("ANTHROPIC_VERTEX_PROJECT_ID") or "").strip() and model == "claude-haiku-4-5":
        return "claude-haiku-4-5@20251001"
    return model


def _same_model(a: str, b: str) -> bool:
    """`claude-haiku-4-5`, `claude-haiku-4-5-20251001` and `claude-haiku-4-5@20251001` are one model.

    So are `gpt-4.1-mini` and `gpt-4.1-mini-2025-04-14`: MP-311, `[M] 2026-11-24` OpenAI's
    dated suffix is `-YYYY-MM-DD`, which this used not to strip, so `init` picked `gpt-4.1-mini`
    to judge an app pinned to `gpt-4.1-mini-2025-04-14` -- the model judging itself.
    """
    strip = lambda m: re.sub(r"(@\d{8}|-\d{8}|-\d{4}-\d{2}-\d{2})$", "", m.lower())  # noqa: E731
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
        provider_source = f"{_credential_found(provider, env)} is set in your environment"
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
