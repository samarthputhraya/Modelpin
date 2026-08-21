"""The seed model registry, embedded as code.

The wheel ships **no** data files (ADR-0011), so the registry cannot be read from a
``data/models.json`` next to the package — under ``pip install`` there is no such file, and
the previous implementation's fallback of sniffing ``Path.cwd()/"data"/"models.json"`` was
worse than the failure: it let a stranger's unrelated ``./data/models.json`` decide which
models Modelpin believes exist.

``data/models.json`` still lives in the repo, because community PRs edit JSON more
comfortably than they edit Python. ``tests/test_watcher.py`` asserts the two are identical,
so the copy here cannot drift from the one contributors send patches against.

Everything below is a SEED. Anthropic ids are current (per the claude-api reference);
OpenAI (``gpt-*``) and Google (``gemini-*``) ids are UNVERIFIED placeholders. Lifecycle
dates are omitted on purpose rather than guessed — an unverified date presented as fact is
exactly the kind of claim this project refuses to make. Phase 1 replaces this with a live
refresh from provider model-list and deprecation endpoints.
"""

from __future__ import annotations

from typing import Any

#: Verbatim mirror of ``data/models.json``'s ``models`` array, in file order.
#: Pinned by ``tests/test_watcher.py::test_seed_matches_repo_json``.
SEED_MODELS: list[dict[str, Any]] = [
    {
        "id": "gpt-5.2",
        "provider": "openai",
        "family": "gpt-5",
        "status": "deprecated",
        "replacement_id": "gpt-5.5",
    },
    {"id": "gpt-5.5", "provider": "openai", "family": "gpt-5", "status": "active"},
    {
        "id": "claude-opus-4-6",
        "provider": "anthropic",
        "family": "claude-opus-4",
        "status": "active",
    },
    {
        "id": "claude-opus-4-7",
        "provider": "anthropic",
        "family": "claude-opus-4",
        "status": "active",
    },
    {
        "id": "claude-opus-4-8",
        "provider": "anthropic",
        "family": "claude-opus-4",
        "status": "active",
    },
    {
        "id": "claude-sonnet-4-6",
        "provider": "anthropic",
        "family": "claude-sonnet-4",
        "status": "active",
    },
    {
        "id": "claude-haiku-4-5",
        "provider": "anthropic",
        "family": "claude-haiku-4",
        "status": "active",
    },
    {"id": "gemini-2.5-pro", "provider": "google", "family": "gemini-2.5", "status": "active"},
]
