"""Load and validate modelpin.yaml. See spec sections 3-4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

DEFAULT_CONFIG_FILE = "modelpin.yaml"

#: The default provider when none is given. OpenAI is the implemented adapter; the
#: Anthropic adapter is still a stub, so zero-config must not route to it.
DEFAULT_PROVIDER = "openai"

#: Replays per scenario per side when neither `--runs` nor a `runs:` key is given.
#:
#: This is a STRUCTURAL floor, not a preference. The diff flags a change only when the
#: exact permutation test reaches ``p <= ALPHA`` (0.05), and the smallest p attainable at
#: N runs/side is ``2/C(2N, N)`` for the tool-trajectory signal:
#:
#:     N=2 -> 0.333   N=3 -> 0.100   N=4 -> 0.0286   N=5 -> 0.0079
#:
#: So below N=4 the tool signal cannot fire on ANY data — a total trajectory change scores
#: `unchanged`. N=3 was the default until MP-03, which meant every run inheriting it was
#: blind to the signal the product is named for. 5 (not 4) is the default because it clears
#: the floor with margin: at N=4 only a *perfect* split reaches significance, so a single
#: noisy run destroys the result.
#:
#: Lowering this below 4 re-opens MP-03. ``tests/test_config.py`` pins the invariant.
DEFAULT_RUNS = 5


class ConfigError(Exception):
    """modelpin.yaml is malformed or fails validation. Carries a user-facing message."""


class ModelpinConfig(BaseModel):
    models: list[str] = Field(default_factory=list)
    scenarios_dir: str = "scenarios"
    providers: list[str] = Field(default_factory=lambda: [DEFAULT_PROVIDER])
    runs: int = Field(default=DEFAULT_RUNS, ge=1)
    judge_model: Optional[str] = None
    regression_threshold: float = 0.2


def load_config(path: str | Path = DEFAULT_CONFIG_FILE) -> ModelpinConfig:
    p = Path(path)
    if not p.exists():
        return ModelpinConfig()
    try:
        data: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
    if data is None:
        return ModelpinConfig()
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must be a YAML mapping (got {type(data).__name__}).")
    try:
        return ModelpinConfig(**data)
    except ValidationError as exc:
        raise ConfigError(f"{p} has invalid settings: {exc}") from exc
