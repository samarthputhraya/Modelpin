"""Detector — scans a repo for AI model identifier strings. See spec section 4.2."""

from __future__ import annotations

import os
import bisect
import re
from pathlib import Path
from typing import Iterable

from modelpin.config import DEFAULT_CONFIG_FILE
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

#: A URL anywhere on the line. MP-201: a model id INSIDE a URL is a link, not a dependency.
#:
#: `[M] 2026-09-07`, found by scanning a real repo (`faceanchor`) rather than a fixture. All
#: **28** of its hits were fabricated, and all 28 came from URLs in scraped SerpAPI evidence:
#:
#:     gpt-56-sol                                          <- a percent-encoded Thai news slug
#:     gpt-6-astra-its-latest-ai-modelthe-model-is-launch  <- an article headline slug
#:     o4-AuaAAAAAElFTkSuQmCC.png                          <- base64 PNG data in a filename
#:
#: The third is MP-135's exact class (`o3XPaKcS`, a random cache token) arriving through a
#: different door: narrowing the o-series digits could not help, because `o4` is a real model
#: and `-Aua...` is a legal suffix. Only the CONTEXT distinguishes them.
#:
#: `[M]` Measured before shipping, over four real repos: faceanchor 28 hits -> 0, and
#: **zero true positives lost** in VoiceRAG (31), kavach (16) or aegis (2), which kept 100%.
#: Re-measured over the 6,228 `site-packages` files: no change.
_URL_ON_LINE = re.compile(r"(?:https?://|www\.)\S+", re.I)

#: A match that ends in an asset extension is a filename, not a model id.
_ASSET_SUFFIX = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp|mp4|pdf|css|html?)$", re.I)


#: Where a model id is WIRED UP: source and configuration the program actually reads.
CODE_EXTS = {".py", ".env", ".yaml", ".yml", ".json", ".toml", ".js", ".ts"}

#: Where a model id is TALKED ABOUT. MP-236's second half: `DEFAULT_EXTS` was `CODE_EXTS`
#: alone, so a repo whose README says "we use `claude-3-5-sonnet` as a fallback" scanned to
#: `No model identifiers found.`, exit 0 -- the same confident-empty-result shape MP-195 fixed
#: for cross-vendor ids, arriving through the file walk instead of the patterns.
#:
#: `[M] 2026-09-09` Measured before adding, because docs are prose and prose is where a
#: fabrication would come from. Two independent corpora, neither a fixture:
#:
#:     kavach (a real app, 9 doc files       5 new rows / 1 distinct -- `Llama-3.3-70B`,
#:     actually walked)                      already found in `kavach/classifier.py`.
#:                                           Models findable ONLY in its docs: NONE.
#:     a site-packages tree (28 doc files)   9 matches / 2 distinct -- `claude-sonnet-5`,
#:                                           `claude-haiku-4-5`, both real ids inside
#:                                           `model="..."` samples. 0 fabricated.
#:
#: `[M] 2026-09-09` The counts above were first written as 27 and 156 and both were wrong, in
#: the direction that overstates the check. 27 is the number of `.md` files ON DISK in kavach;
#: the walker reaches 9, because `_is_virtualenv` prunes 18 inside `.venv-modelpin` and one in
#: `.modelpin`. 156 reproduces on no tree on the machine. The RESULT columns are unchanged and
#: were reproduced exactly -- only the denominator was invented, which is the half a reader
#: would have trusted most.
#:
#: `.txt` is deliberately NOT here. It is not a documentation format, it is the default
#: extension for logs, scraped dumps and data exports -- MP-201's 28 fabrications were
#: exactly that kind of content. `[A]` Falsified by one real repo whose only record of its
#: model is a `.txt`; add it then, with the measurement.
DOC_EXTS = {".md", ".markdown", ".rst"}

DEFAULT_EXTS = CODE_EXTS | DOC_EXTS

#: Line-comment introducers, by extension. `.json` has none by design. Documentation
#: formats are absent because they need no scanning: every line of prose is commentary.
_COMMENT_TOKEN = {
    ".py": "#",
    ".yaml": "#",
    ".yml": "#",
    ".toml": "#",
    ".env": "#",
    ".js": "//",
    ".ts": "//",
}

#: Modelpin's own config, under both spellings YAML permits. Bound to the constant rather
#: than typed, for the same reason `SKIP_DIRS` binds `STORE_DIRNAME`: renaming the file
#: must not silently re-open MP-236.
_OWN_CONFIG_NAMES = {DEFAULT_CONFIG_FILE.lower(), f"{Path(DEFAULT_CONFIG_FILE).stem.lower()}.yml"}
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


def _comment_cut(line: str, token: str) -> int:
    """Index where `line`'s trailing comment begins, or `len(line)` if it has none.

    Quoted spans are skipped, so ``MODEL = "gpt-4o"  # or gpt-5`` cuts at the `#` and
    ``url = "https://x/#gpt-4"`` does not cut at all. That is the whole ambition: this is a
    one-line lexer, not a parser for eight languages.

    Two bounds, stated because they are load-bearing nowhere else and must stay that way:
    a model id inside a Python docstring reads as code (line-based scanning cannot see a
    multi-line string), and an unbalanced apostrophe in unquoted YAML prose -- `dont` vs
    `don't` -- swallows the rest of the line, so a real comment after it reads as code.
    BOTH degrade toward "call it code", which keeps the row. The only place that choice can
    delete anything is Modelpin's own config, whose generated comments start at column 0
    where no quote precedes them.
    """
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif line.startswith(token, i):
            return i
        i += 1
    return len(line)


#: MP-242. A file larger than this is not read. `[M] 2026-09-09` security review: there was
#: no guard at all -- a 200 MB file took 246 s and a 497 MB one was read whole into memory.
#: Source files and configuration are rarely past a megabyte; what lives above this is a
#: bundle, a lockfile, a dataset or a log. It is disclosed, never silent: a skipped file
#: could hold a model id, and `scan` must not imply it looked where it did not.
MAX_SCAN_BYTES = 5 * 1024 * 1024


def scan_repo(
    root: str | Path = ".",
    exts: set[str] | None = None,
    skipped: list[str] | None = None,
) -> list[dict]:
    """Return [{model, file, line, context}] for every model id found in the repo.

    `context` is `"code"` (the id is wired up: source, or an active config value) or
    `"comment"` (the id is talked about: a code comment, or documentation prose).

    MP-236. `[M] 2026-09-09`, found by a first-run audit of the published 0.3.0 and
    reproduced verbatim in a directory holding one 1-file app plus a fresh `modelpin init`:
    **3 of 6 rows, and 1 of 3 "distinct model(s)", were Modelpin talking to itself.**
    `qwen/qwen3.8-27b` was reported as a dependency of the user's app; it exists nowhere in
    that app, only inside the scaffold comment `modelpin init` had just written explaining the
    judge-model-collision rule. This is MP-200's class (our `.modelpin/` store reported back
    as the user's models) in the one file we write into the user's repo by hand, and it lands
    on the first command a stranger runs.

    The rule is structural, NOT a copy of the scaffold's text: **in Modelpin's own config, a
    model id outside an active setting is commentary, not a declaration.** We define that
    file's schema, and `models:` / `judge_model:` is the only way to declare a model in it --
    so a commented-out `judge_model:` is a DISABLED one, and prose about a model is not a
    dependency. Matching the template's wording instead would put a second copy of it in this
    module (MP-03's exact defect) and would fail silently the day `modelpin init`'s wording changes.

    Comments elsewhere are KEPT and labelled, not suppressed. `# TODO: evaluate gpt-5.5` in
    the user's own app is a model that repo has a relationship with, and the audit read it as
    a true positive; a commented-out `MODEL = "gpt-4o"` is the same. `context` is what lets
    `modelpin scan` print those in their own group later -- rendering it is the CLI's half of this
    row and is not done here.

    `[M]` Measured on `C:/dev/kavach`, a real repo carrying a hand-edited `modelpin.yaml`, so
    the cost of the rule is stated rather than assumed: **15 rows / 6 distinct -> 14 / 5**.
    The one dropped row is `llama-3.3-70b-versatile` in a hand-written NOTE that says Groq
    RETIRED it -- prose about a dead model, reported until now as a live dependency. That is
    the shape of what this rule deletes, and one sample is the bound: `[A]` a user who keeps
    their real model id ONLY in a modelpin.yaml comment loses a row. They would have to have
    commented out the setting that names it, which is how you turn it off.
    """
    root = Path(root)
    exts = exts or DEFAULT_EXTS
    hits: list[dict] = []
    for f in _iter_files(root, exts):
        try:
            # Checked by `stat` BEFORE reading, so an oversized file is never loaded. The
            # caller learns about it through `skipped`, because a scan that quietly looked
            # past a file would be claiming a coverage it did not have.
            if f.stat().st_size > MAX_SCAN_BYTES:
                if skipped is not None:
                    skipped.append(str(f.relative_to(root)))
                continue
            # MP-190's class, in the one site its sweep missed: `errors="ignore"` without
            # `encoding=` matched neither `read_text()` nor `read_text(encoding=` in that
            # commit's grep, so its claim that the two sites it fixed were "the ONLY two
            # text-I/O sites in modelpin/ without an explicit encoding" was wrong. Here the
            # consequence is a silent MISS rather than a wrong verdict: on a cp1252 machine a
            # UTF-8 source file decodes to mojibake and its model ids stop matching.
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # `.env.example` and friends: `Path(".env.example").suffix` is `.example`, the same
        # blind spot `_iter_files` documents, so the key is derived the same way there.
        suffix = ".env" if f.name.startswith(".env") else f.suffix.lower()
        token = _COMMENT_TOKEN.get(suffix)
        own_config = f.name.lower() in _OWN_CONFIG_NAMES
        for i, line in enumerate(text.splitlines(), start=1):
            # A documentation file is commentary end to end, so the cut is 0. A format with
            # no line comment (`.json`) has no cut at all.
            cut = 0 if suffix in DOC_EXTS else (_comment_cut(line, token) if token else len(line))
            for model, start in _models_in(line):
                context = "code" if start < cut else "comment"
                if own_config and context == "comment":
                    continue
                hits.append(
                    {
                        "model": model,
                        "file": str(f.relative_to(root)),
                        "line": i,
                        "context": context,
                    }
                )
    return hits


def _models_in(line: str) -> list[tuple[str, int]]:
    """Every model id on one line as `(id, start)`, with substring matches of a longer id
    dropped.

    The start offset exists so `scan_repo` can ask which side of a comment marker the id sits
    on. It is the LEFTMOST occurrence's offset, because the dedupe below keeps the first hit
    of an id on a line: ``MODEL = "gpt-4o"  # gpt-4o stays`` is one row, `code`, exactly as
    it was one row before this offset existed. Reporting the same id twice on one line to
    label it twice would answer a noise row with two.

    MP-195. The vendor-prefixed patterns overlap the bare ones by construction -- `qwen/` and
    `qwen<n>` both fire on ``qwen/qwen3-32b`` -- so without this the fix for scan's BLINDNESS
    would have shipped a new case of scan's NOISE (MP-10): `[M] 2026-09-06` a file naming four
    models reported five rows, listing `qwen3-32b` beside the `qwen/qwen3-32b` it is part of.

    Containment, not de-duplication by string: two genuinely different ids on one line must
    both survive, and they do -- only a span strictly inside another span is dropped. The
    longest match wins because a vendor-qualified id is the one the user can actually pass to
    `--to`; the bare tail is an artifact of our patterns, not something they wrote.
    """
    # `finditer` returns non-overlapping URLs in order, so the only URL that can contain a
    # match is the last one starting at or before it -- a binary search, not a scan of all.
    urls = [(u.start(), u.end()) for u in _URL_ON_LINE.finditer(line)]
    url_starts = [s for s, _ in urls]
    spans: list[tuple[int, int, str]] = []
    for pat in MODEL_PATTERNS:
        for m in pat.finditer(line):
            # MP-201. A model id inside a URL is a link, not a dependency, and an asset
            # filename is not a model. Both are FABRICATIONS -- the north-star failure in the
            # first command a stranger runs -- and neither can be excluded by narrowing the
            # patterns, because `o4-...` and `gpt-6-...` are legal shapes for a real id. Only
            # the surrounding context separates them.
            i = bisect.bisect_right(url_starts, m.start()) - 1
            if i >= 0 and m.end() <= urls[i][1]:
                continue
            if _ASSET_SUFFIX.search(m.group(0)):
                continue
            spans.append((m.start(), m.end(), m.group(0)))
    # MP-242. `[M] 2026-09-09` This compared every span with every other span on the line --
    # O(S^2) -- and a minified bundle is ONE enormous line. Minifiers emit `o1`/`o3`/`o4` as
    # short local names, which the o-series pattern matches at every word boundary, so a
    # bundle is dense with spans: reproduced at 4 KB -> 0.03 s, 8 KB -> 0.10 s, 16 KB ->
    # 0.56 s, quadrupling per doubling, and the security review measured a 96 KB file at 105 s.
    # Every JS/TS repository has a bundle, so for a front-end shop the tool's first command
    # appeared to hang.
    #
    # Sorting by (start asc, end desc) puts every span that could contain another BEFORE it,
    # so one sweep tracking the furthest end seen answers containment for all of them. The
    # only span that sweep can mis-call is one at exactly the same position as an earlier
    # span -- which is the same text, and `seen` drops it either way -- so the output is
    # identical to the quadratic version's, set and order both, and a property test holds
    # that on random inputs rather than asserting it here.
    return _dedupe_spans(spans)


def _dedupe_spans(spans: list[tuple[int, int, str]]) -> list[tuple[str, int]]:
    """Drop every span strictly contained in a longer one, then repeated texts; keep order.

    Extracted from `_models_in` (MP-242) so `tests/test_scan_performance.py` can hold it
    against the original quadratic definition on random inputs. The claim that the two agree
    exactly is the whole safety argument for the speed-up, so it is tested, not asserted.
    """
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    furthest = -1
    for start, end, text in sorted(spans, key=lambda s: (s[0], -s[1])):
        contained = furthest >= end
        furthest = max(furthest, end)
        if not contained and text not in seen:
            seen.add(text)
            out.append((text, start))
    return out


def models_used(root: str | Path = ".") -> set[str]:
    return {h["model"] for h in scan_repo(root)}
