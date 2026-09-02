"""Nothing visitor-facing may carry AI-tool attribution.

WHY THIS IS A TEST AND NOT A PREFERENCE. A visitor who sees a co-author trailer or an internal
agent handle concludes the project is machine-generated and leaves without reading it. For a
product whose entire pitch is measurement rigour, that read is fatal -- it costs exactly the
audience the repo exists to reach, before a single number gets looked at.

`[M] 2026-08-31` It recurred anyway: `includeCoAuthoredBy: false` was set on 2026-08-29 and
handles still reached **22 places in tracked source** -- `modelpin/cli.py`, `judge.py`,
`providers/`, `report/`, nine test files and `CHANGELOG.md` -- because the rule lived in a
preference and preferences do not fail builds. This does.

WHAT IS AND IS NOT BANNED. Product-domain mentions of Claude and Anthropic are CORE
FUNCTIONALITY and must never be stripped: Modelpin *watches* model releases, so
`claude-opus-4-8` in the registry, the `anthropic` provider, and the `claude-` regex in the
detector are the product working. What is banned is attribution of the WORK to a tool -- a
co-author trailer, a "generated with" footer, or an internal review-agent handle. Refer to a
gate by what it CHECKS ("FP review", "claims review", "first-run review"), never by its handle.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Assembled from fragments so this file does not itself contain the strings it bans -- the
#: guard would otherwise be its own only violation, and excluding itself by path is the kind of
#: exemption that later hides a real one.
_HANDLES = tuple(
    a + "-" + b
    for a, b in (
        ("fp", "guardian"), ("claims", "auditor"), ("provider", "sdk-verifier"),
        ("scenario", "adversary"), ("bug", "reproducer"), ("packaging", "verifier"),
        ("mutation", "sentinel"), ("first", "run-auditor"), ("backlog", "ranker"),
        ("traction", "analyst"), ("harness", "medic"), ("wedge", "warden"),
    )
)  # fmt: skip

#: Attribution markers that name the TOOL rather than the work.
_ATTRIBUTION = (
    "Co-" + "Authored-By: Claude",
    "Generated with " + "Claude Code",
    "\U0001f916 Generated",
)

BANNED = _HANDLES + _ATTRIBUTION

#: The newest commit on `main` that carries the trailer, `[M] 2026-08-31`. Everything AFTER it
#: must be clean; everything up to it is the history-rewrite decision below.
#:
#: A pinned commit, not a date, and that is not fussiness. `[M]` The first cut of this guard
#: used `--since=2026-08-29` (when the setting landed) and still failed on `b5c5e40`, which is
#: *authored and committed* 2026-08-31 and carries the trailer anyway: GitHub's squash-merge
#: replays the branch's commit message onto a commit stamped with the MERGE time. So a date
#: boundary cannot separate "old history" from "new work" in a squash-merge repo at all.
_HISTORY_BASELINE = "311108f"

#: How many commits reachable from the baseline carry the trailer, `[M] 2026-08-31`, agreed by
#: two independent methods (raw `%B` substring, and git's own `%(trailers:...)` parser). A
#: RATCHET: history may only get cleaner. Without it, a rebase could slip a new trailered commit
#: under the baseline and the check above would never see it.
#:
#: `[M]` The number was reported as **38** for most of a day, from
#: `git log --format='%(trailers:...)%x00%h' | grep -c` -- `grep -c` counts LINES and the format
#: embeds NULs, so grep read the stream as binary and under-counted by four. Two methods that
#: agree, in-process, with an explicit encoding; never a grep over NUL-delimited output.
_KNOWN_TRAILERED_AT_BASELINE = 42

#: Extensions worth scanning: everything a visitor reads. Binary and vendored paths are skipped
#: by the tracked-file listing itself -- git already knows what ships.
_TEXT_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".txt", ".ini"}


def _tracked_text_files() -> list[Path]:
    """Every file git TRACKS. Deliberately not a directory walk: `.claude/` and `ops/` are
    gitignored private trees that legitimately use the handles, and asking git is what makes
    "does a visitor see this?" the actual question being answered."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [
        REPO / name
        for name in out.stdout.split("\0")
        if name and Path(name).suffix in _TEXT_SUFFIXES and (REPO / name).is_file()
    ]


def _trailered(revrange: str) -> list[str] | None:
    """Short SHAs in `revrange` whose message carries a co-author trailer. None if unreadable.

    ASCII unit/record separators, not NULs. `[M]` A `%H%x00%B%x00` format split on two NULs
    mis-framed the stream -- git writes a newline between records, so each body bled into the
    NEXT record and paired a clean commit's sha with an older commit's trailer. The guard's very
    first run named a false offender, which is the one failure mode a guard may not have: it
    would have been "fixed" by deleting the check.
    """
    out = subprocess.run(
        ["git", "log", revrange, "--format=%H%x1f%B%x1e"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )  # fmt: skip
    # `[M] 2026-08-31` `text=True` WITHOUT an explicit encoding decodes with the Windows ANSI
    # codepage, and this repo's own history contains a byte cp1252 cannot map. The reader thread
    # died, `stdout` came back None, and `returncode` was still 0 -- so the guard reported a
    # clean history because it had read nothing at all. Explicit utf-8, and stdout is checked.
    if out.returncode != 0 or out.stdout is None:
        return None
    found = []
    for chunk in out.stdout.split("\x1e"):
        if not chunk.strip():
            continue
        sha, _, body = chunk.partition("\x1f")
        if "noreply@anthropic.com" in body.lower() or _ATTRIBUTION[0].lower() in body.lower():
            found.append(sha.strip()[:7])
    return found


def test_no_tracked_file_names_a_review_agent_or_credits_a_tool():
    hits = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for needle in BANNED:
                # Case-insensitive: `[M]` git writes the key as `Co-authored-by:` while the
                # tool that adds it writes `Co-Authored-By:`. An exact-case needle catches one
                # of the two, which is the same as catching neither.
                if needle.lower() in low:
                    rel = path.relative_to(REPO).as_posix()
                    hits.append(f"{rel}:{lineno} contains {needle!r}")
    assert not hits, (
        "Tracked, visitor-facing files carry AI-tool attribution:\n  "
        + "\n  ".join(hits)
        + "\n\nName a gate by what it CHECKS -- 'FP review', 'claims review', 'first-run "
        "review', 'packaging review', 'scope review' -- not by its handle. Product-domain "
        "mentions of Claude/Anthropic (the watcher registry, the provider, the detector "
        "regex) are core functionality and are NOT what this guard is about."
    )


def test_the_product_may_still_talk_about_claude_models():
    """LOAD-BEARING counter-test. The guard above must never be 'fixed' by scrubbing the
    product domain: Modelpin watches model releases, so Anthropic model ids and the provider
    name are the tool doing its job. If this fails, someone over-applied the rule."""
    registry = (REPO / "modelpin" / "watcher" / "registry.py").read_text(encoding="utf-8")
    assert "anthropic" in registry.lower(), "the watcher no longer knows Anthropic ships models"
    detector = (REPO / "modelpin" / "detector" / "__init__.py").read_text(encoding="utf-8")
    assert "claude" in detector.lower(), "the detector can no longer find a Claude model id"


def test_recent_commits_carry_no_co_author_trailer():
    """The setting (`includeCoAuthoredBy: false`) is not enforcement -- it is a default that a
    different machine, a different tool, or a reset config can silently drop. `[M] 2026-08-31`
    42 of 75 commits on `main` carry the trailer for exactly that reason: nothing checked.

    Bounded at `_HISTORY_BASELINE`, and the boundary is the whole design. Whether to rewrite
    the history below it is a real decision with real costs -- `[M]` three release tags move,
    including the floating `v1` that consumers' Actions resolve; 21 commit SHAs cited across 13
    PR bodies go dead; and the published Report's own `git checkout f67e2ae` stops resolving.
    That is the maintainer's call, not a test's, and a test left red for a decision nobody has
    taken gets ignored or deleted -- which is worse than not having it.

    What a test can insist on, absolutely, is that no NEW one lands.
    """
    offenders = _trailered(f"{_HISTORY_BASELINE}..HEAD")
    if offenders is None:
        pytest.skip("baseline commit not present (shallow clone?)")
    assert not offenders, (
        f"{len(offenders)} commit(s) after {_HISTORY_BASELINE} carry a co-author trailer: "
        f"{offenders}. `includeCoAuthoredBy: false` belongs in this repo's Claude Code "
        "settings -- and note a squash-merge replays a branch's message, so a PR authored "
        "before that setting can still land one today."
    )


def test_the_trailered_history_only_ever_gets_smaller():
    """A RATCHET on the history the test above deliberately does not police.

    Without it the boundary is an open door: a rebase that rewrites history *below*
    `_HISTORY_BASELINE` could add trailered commits and nothing would notice, because the check
    above starts after it. This makes the old count a one-way number -- rewrite the history to
    clean it and this test tells you to lower the constant; let it grow and it fails.
    """
    found = _trailered(_HISTORY_BASELINE)
    if found is None:
        pytest.skip("baseline commit not present (shallow clone?)")
    assert len(found) <= _KNOWN_TRAILERED_AT_BASELINE, (
        f"{len(found)} trailered commits at/below {_HISTORY_BASELINE}, up from "
        f"{_KNOWN_TRAILERED_AT_BASELINE}. History may only get cleaner."
    )
