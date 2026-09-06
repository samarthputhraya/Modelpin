"""Load and validate modelpin.yaml. See spec sections 3-4."""

from __future__ import annotations

import difflib

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
#: So below N=4 the tool signal cannot fire in the DEFAULT match modes (`strict`,
#: `unordered`) on any data — a total trajectory change scores `unchanged`. The directional
#: modes use a one-sided statistic (floor `1/C(2N,N)`) and do fire at N=3. N=3 was the
#: default until MP-03, which meant every run inheriting it was blind, in the default mode,
#: to the signal the product is named for.
#:
#: 5 (not 4) because at N=4 only a *perfect* split reaches significance, so a single noisy
#: run destroys the result. 5 is also the ONLY N this project has ever measured its
#: false-positive rate at (`docs/fp-measurement.md`), and N=3 was the worst possible N for
#: the binary signals: their conditional FP peaks at exactly ALPHA there (1/C(6,3) = 0.05)
#: and falls to 2.38% at N=5. See ADR-0016.
#:
#: Lowering this below 4 re-opens MP-03. ``tests/test_config.py`` pins the invariant.
DEFAULT_RUNS = 5


class ConfigError(Exception):
    """modelpin.yaml is malformed or fails validation. Carries a user-facing message."""


class ModelpinConfig(BaseModel):
    # MP-198. `extra="forbid"`, so a misspelled key is an ERROR rather than a silent
    # discard. pydantic v2 defaults to `extra="ignore"`, which meant `provider:` for
    # `providers:` loaded as the DEFAULT provider -- `openai`, a paid one, at 5 runs.
    # The forward-compat cost is real and accepted: a config carrying a key from a newer
    # Modelpin now fails loudly on an older one. For a file that decides whose key gets
    # spent, failing loudly is the correct direction to be wrong in.
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list)
    scenarios_dir: str = "scenarios"
    providers: list[str] = Field(default_factory=lambda: [DEFAULT_PROVIDER])
    runs: int = Field(default=DEFAULT_RUNS, ge=1)
    judge_model: Optional[str] = None
    #: Which HOST runs the judge. Optional, because `gpt-*` and `gemini-*` name their own
    #: vendor; required for every other id, because a Groq id and an OpenRouter id are
    #: indistinguishable from the string alone and guessing would spend the wrong key
    #: against the wrong host (MP-143). Unset and un-inferable, `check` falls back to the
    #: REPLAY provider and says so on the console rather than silently disabling the only
    #: channel that reads meaning.
    judge_provider: Optional[str] = None
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
        unknown = [
            str(e["loc"][0])
            for e in exc.errors()
            if e.get("type") == "extra_forbidden" and e["loc"]
        ]
        if unknown:
            raise ConfigError(f"{p}: {_unknown_key_message(unknown)}") from exc
        raise ConfigError(f"{p} has invalid settings: {exc}") from exc


def _unknown_key_message(unknown: list[str]) -> str:
    """Name the unknown key and, when it is a near miss, the one that was probably meant.

    MP-198. Until this existed, `ModelpinConfig` was a plain `BaseModel`, so pydantic v2's
    default ``extra="ignore"`` discarded every misspelled key in silence. `[M] 2026-09-06`
    ``provider: [fake]`` + ``run: 1`` -- singular, the most natural slip on this file --
    loaded as ``providers=['openai']``, ``runs=5``: a config written to get a free offline
    check billed the user's own key for **five** paid replays per scenario. The BYO-key
    guardrail (ADR-0008) failing in the direction that spends their money.

    The nearest-match hint is not decoration. Every field here that a user can plausibly
    mistype is a singular/plural pair, so a bare "unknown key 'provider'" makes the reader
    hunt for a difference they cannot see -- the two words look identical at a glance, which
    is exactly why the typo happens.
    """
    known = sorted(ModelpinConfig.model_fields)
    parts = []
    for key in unknown:
        near = difflib.get_close_matches(key, known, n=1, cutoff=0.7)
        parts.append(f"{key!r} (did you mean {near[0]!r}?)" if near else repr(key))
    label = "unknown setting" if len(parts) == 1 else "unknown settings"
    return (
        f"{label} {', '.join(parts)}. Modelpin refuses to guess here rather than silently "
        f"falling back to its defaults -- the default provider is {DEFAULT_PROVIDER!r} at "
        f"{DEFAULT_RUNS} runs, so a discarded key can spend your API key on a run you "
        f"thought was offline. Known settings: {', '.join(known)}."
    )
