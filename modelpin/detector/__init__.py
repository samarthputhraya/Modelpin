"""Detector — scans a repo for AI model identifier strings. See spec section 4.2."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from modelpin.storage import STORE_DIRNAME

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
    # ---------------------------------------------------------------- MP-195, cross-vendor
    # `[M] 2026-09-06` Until these existed, `mp scan` was OpenAI/Anthropic/Google-shaped, and
    # cross-vendor is wedge item 3. A directory whose `app.py` named `llama-3.3-70b-versatile`,
    # `qwen/qwen3-32b` and `openai/gpt-oss-20b` scanned to `No model identifiers found.`,
    # exit 0 -- and appending a single line `M = "gpt-4o-mini"` to THAT SAME FILE produced a
    # populated table. The same file was visible or invisible depending only on whose ids it
    # held. `scan` is the first command of README's "real flow, on your own app", so a Groq or
    # Together shop met a confident empty result rather than a hint that we did not cover them.
    # These are also the exact ids README itself advertises (`llama-3.3-70b-versatile` at the
    # cross-vendor table, `qwen/qwen3.8-27b` in a copy-pasteable example).
    #
    # `[M]` Measured over the same 6,228 third-party files under `site-packages` that MP-135
    # used, so the false-positive claim is comparable to the one that narrowed the o-series:
    #
    #     llama      5 matches / 2 distinct  -> llama-3.1-8b-instruct,
    #                                           llama-3.2-90b-vision-instruct-maas
    #     meta-llama 0                       qwen/     1 -> qwen/qwen3
    #     qwen<n>    2 -> qwen3, qwen3-4b    gpt-oss   0        mistral 0
    #     deepseek   6 / 3 distinct          -> INCLUDED `deepseek-ai`, a FALSE POSITIVE:
    #                                           it is the HuggingFace ORG, not a model.
    #
    # So `deepseek-` enumerates its real families instead of taking any suffix -- the same
    # shape as `_O_SERIES_DIGITS` above, and for the same reason. Re-measured after narrowing:
    # 3 matches / 2 distinct, both real, `deepseek-ai` gone, nothing else lost.
    #
    # `[M]` Case-insensitivity was measured, not assumed: `(?i)` on llama/qwen/mistral added
    # **zero** new matches across those 6,228 files, so it is free here -- and it is needed,
    # because HuggingFace writes `meta-llama/Llama-3.3-70B-Instruct` with a capital L while
    # Groq writes the same family lowercase.
    re.compile(r"(?i)\bllama-[0-9][\w.\-]*\b"),
    re.compile(r"(?i)\bmeta-llama/[\w.\-]+\b"),
    re.compile(r"(?i)\bqwen/[\w.\-]+\b"),
    re.compile(r"(?i)\bqwen[0-9][\w.\-]*\b"),
    re.compile(r"(?i)\b(?:openai/)?gpt-oss-[\w.\-]+\b"),
    re.compile(r"(?i)\bmi[sx]tral-[\w.\-]+\b"),
    re.compile(r"(?i)\bdeepseek-(?:r[0-9]|v[0-9]|chat|coder|reasoner)[\w.\-]*\b"),
]

DEFAULT_EXTS = {".py", ".env", ".yaml", ".yml", ".json", ".toml", ".js", ".ts"}
#: Directory names never worth scanning, matched at or below the scan root. `.venv`/`venv`
#: stay for the case a virtualenv has no `pyvenv.cfg` (a stale or hand-made one), but they
#: are no longer what CARRIES the venv rule -- see `_is_virtualenv`.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    # MP-200. Modelpin's OWN store. `[M] 2026-09-06`, found by scanning a real repo
    # (`kavach`): **61 of 76 hits -- 80% of the table -- came from `.modelpin/baseline-*.json`**,
    # the recorded traces of a previous run. `actions/README.md` tells users to `git add` that
    # directory, so it is present in exactly the repos this command is aimed at.
    #
    # This is MP-134/MP-135's class one directory over: "scan reported 23 distinct models and
    # only 2 were the user's own code". Reporting our own artifacts back to the user as their
    # dependencies is the same defect wearing our own output.
    #
    # Bound to `STORE_DIRNAME` rather than typed, so renaming the store cannot silently
    # re-open this.
    STORE_DIRNAME,
}
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
            # MP-195. `.env` was matched by exact name, so `.env.example` -- the file a repo
            # commits precisely BECAUSE it is the readable record of which model it uses --
            # was invisible, along with `.env.local`, `.env.sample` and every other variant.
            # `Path(".env.example").suffix` is `.example`, so the extension test cannot see
            # them either.
            if p.suffix.lower() in exts or p.name.startswith(".env"):
                yield p


def scan_repo(root: str | Path = ".", exts: set[str] | None = None) -> list[dict]:
    """Return [{model, file, line}] for every model id found in the repo."""
    root = Path(root)
    exts = exts or DEFAULT_EXTS
    hits: list[dict] = []
    for f in _iter_files(root, exts):
        try:
            # MP-190's class, in the one site its sweep missed: `errors="ignore"` without
            # `encoding=` matched neither `read_text()` nor `read_text(encoding=` in that
            # commit's grep, so its claim that the two sites it fixed were "the ONLY two
            # text-I/O sites in modelpin/ without an explicit encoding" was wrong. Here the
            # consequence is a silent MISS rather than a wrong verdict: on a cp1252 machine a
            # UTF-8 source file decodes to mojibake and its model ids stop matching.
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for model in _models_in(line):
                hits.append({"model": model, "file": str(f.relative_to(root)), "line": i})
    return hits


def _models_in(line: str) -> list[str]:
    """Every model id on one line, with substring matches of a longer id dropped.

    MP-195. The vendor-prefixed patterns overlap the bare ones by construction -- `qwen/` and
    `qwen<n>` both fire on ``qwen/qwen3-32b`` -- so without this the fix for scan's BLINDNESS
    would have shipped a new case of scan's NOISE (MP-10): `[M] 2026-09-06` a file naming four
    models reported five rows, listing `qwen3-32b` beside the `qwen/qwen3-32b` it is part of.

    Containment, not de-duplication by string: two genuinely different ids on one line must
    both survive, and they do -- only a span strictly inside another span is dropped. The
    longest match wins because a vendor-qualified id is the one the user can actually pass to
    `--to`; the bare tail is an artifact of our patterns, not something they wrote.
    """
    spans: list[tuple[int, int, str]] = []
    for pat in MODEL_PATTERNS:
        for m in pat.finditer(line):
            spans.append((m.start(), m.end(), m.group(0)))
    out: list[str] = []
    seen: set[str] = set()
    for start, end, text in spans:
        contained = any(
            (o_start <= start and end <= o_end) and (o_end - o_start) > (end - start)
            for o_start, o_end, _ in spans
        )
        if not contained and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def models_used(root: str | Path = ".") -> set[str]:
    return {h["model"] for h in scan_repo(root)}
