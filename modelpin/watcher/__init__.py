"""Watcher — tracks model release/deprecation state. See spec section 4.1.

Phase 0: the seed registry is embedded in ``registry.py`` rather than read from disk, so
``load_registry()`` works identically from a repo checkout and from ``pip install modelpin``.
TODO (Phase 1): refresh from provider model-list endpoints + deprecation pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from modelpin.models import Model, ModelStatus
from modelpin.watcher.registry import SEED_MODELS


def load_registry(path: Optional[Path] = None) -> list[Model]:
    """The model registry: the embedded seed, or an explicit override file.

    ``path=None`` returns the shipped seed — no filesystem access, so this cannot depend on
    the caller's working directory (an earlier version searched ``./data/models.json``,
    which let an unrelated file in the user's cwd redefine the registry).
    """
    if path is None:
        return [Model(**m) for m in SEED_MODELS]
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Model(**m) for m in raw.get("models", [])]


def get_model(model_id: str, registry: Optional[list[Model]] = None) -> Optional[Model]:
    reg = registry if registry is not None else load_registry()
    return next((m for m in reg if m.id == model_id), None)


def deprecations(registry: Optional[list[Model]] = None) -> list[Model]:
    reg = registry if registry is not None else load_registry()
    return [m for m in reg if m.status in (ModelStatus.deprecated, ModelStatus.retired)]
