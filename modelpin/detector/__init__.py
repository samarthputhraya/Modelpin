"""Detector — scans a repo for AI model identifier strings. See spec section 4.2."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

#: The o-series numbers that actually exist. `[M] 2026-08-29` the pattern was `o[0-9]`,
#: which matched **`o2`** at `rich/_emoji_codes.py:3381`, whose content is `"o2": "\U0001f17e"`
#: -- an emoji shortcode, and `o2` is not an OpenAI model at all. Enumerating the real
#: numbers is deliberate: the o-series is a short, slow-moving list, and a MISSED model in
#: `scan` costs the user a line in a table they can add by hand, while a FABRICATED one is
#: the north-star failure showing up in the first command a stranger runs. Add `5` here when
#: `o5` ships; `tests/test_detector_patterns.py` documents that this is the one-token edit.
_O_SERIES_DIGITS = "134"
# Conservative patterns; extend as providers add families.
MODEL_PATTERNS = [
    re.compile(r"\bgpt-[0-9][\w.\-]*\b"),
    # The suffix must be introduced by `-`, so `o3` and `o3-deep-research-2025-06-26` match
    # while `o3XPaKcS` does not: after `o3` comes a word character, so there is no `\b` for
    # the bare alternative and no `-` for the suffixed one. `[M] 2026-08-31` that token is
    # not hypothetical -- it is in this repo, in `.modelpin/drift_cache_drift-suite.json`.
    re.compile(rf"\bo[{_O_SERIES_DIGITS}](?:-[\w.\-]*)?\b"),
    re.compile(r"\bclaude-[\w.\-]+\b"),
    re.compile(r"\bgemini-[\w.\-]+\b"),
]

DEFAULT_EXTS = {".py", ".env", ".yaml", ".yml", ".json", ".toml", ".js", ".ts"}
#: Directory names never worth scanning, matched at or below the scan root. `.venv`/`venv`
#: stay for the case a virtualenv has no `pyvenv.cfg` (a stale or hand-made one), but they
#: are no longer what CARRIES the venv rule -- see `_is_virtualenv`.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
#: Skipped wherever it appears. A dependency's source is not the user's model choice, and
#: `[M] 2026-08-29` on a real app it was most of the answer: `modelpin scan` reported 23
#: distinct "models" and only 2 were the user's own code -- the rest were Modelpin's own
#: source inside site-packages, plus pip's vendored `rich`.
SKIP_DIRS_ANY_DEPTH = {"site-packages", "site-python", "dist-packages"}


def _is_virtualenv(d: Path) -> bool:
    """Is this directory a Python virtualenv, whatever it is called?

    `[M] 2026-08-29` MP-134: `SKIP_DIRS` matched by EXACT directory name, so a venv called
    `.venv-modelpin` -- or `venv312`, `env`, `.virtualenvs` -- was walked in full and its
    contents published as the user's models. Names are user-chosen and unbounded, so
    enumeration cannot close this; `pyvenv.cfg` is what the interpreter itself writes at
    the root of every venv it creates (PEP 405) and is the structural test. `[M]` Verified
    present in this repo's own `.venv/pyvenv.cfg`.
    """
    return (d / "pyvenv.cfg").is_file()


def _iter_files(root: Path, exts: set[str]) -> Iterable[Path]:
    """Walk `root`, PRUNING directories rather than walking then filtering.

    Pruning is not an optimisation here, it is the fix for a second defect. `[M] 2026-08-31`
    the previous implementation tested `any(part in SKIP_DIRS for part in p.parts)` on the
    FULL path, which includes the scan root's own ancestors -- so scanning a project that
    merely LIVES under a directory named `build` (or `dist`, `venv`, `.git`,
    `node_modules`, `__pycache__`) matched on the ancestor and skipped every file. Verified:
    a tree containing one plain `MODEL = "gpt-4o-mini"` scanned to **0 hits**, silently, with
    exit 0 -- the same "measured nothing, reported nothing" shape the diff engine has six
    ADRs about. Walking from the root downward cannot express that bug: only directories at
    or below the root are ever considered.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIRS and d not in SKIP_DIRS_ANY_DEPTH and not _is_virtualenv(here / d)
        ]
        for name in filenames:
            p = here / name
            if p.suffix.lower() in exts or p.name == ".env":
                yield p


def scan_repo(root: str | Path = ".", exts: set[str] | None = None) -> list[dict]:
    """Return [{model, file, line}] for every model id found in the repo."""
    root = Path(root)
    exts = exts or DEFAULT_EXTS
    hits: list[dict] = []
    for f in _iter_files(root, exts):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for pat in MODEL_PATTERNS:
                for m in pat.findall(line):
                    hits.append({"model": m, "file": str(f.relative_to(root)), "line": i})
    return hits


def models_used(root: str | Path = ".") -> set[str]:
    return {h["model"] for h in scan_repo(root)}
