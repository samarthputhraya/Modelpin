"""Every third-party action Modelpin asks other people to run is pinned to a commit SHA.

MP-244. `[M] 2026-09-09` security review: `action.yml` -- the composite Action consumers put in
their own workflows, where it runs with their repository secrets in scope -- referenced
`actions/setup-python@v6` and `actions/github-script@v9` by floating tag. A tag can be moved by
whoever controls it, and a moved tag is remote code execution that needs no access to this
repository at all (tj-actions/changed-files, March 2025).

This repository already held the standard: `.github/workflows/release.yml` pins every action to
a full SHA and explains why. The file we ask OTHER people to run did not. Both are now pinned to
the exact commit their floating tags resolved to ([S] GitHub API, 2026-09-11), so no consumer's
behaviour changed -- it simply stopped being changeable underneath them.

Nothing read `action.yml`'s `uses:` lines before, so this could drift back the next time a
version is bumped by hand. Hence this module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: `owner/repo@ref` or `owner/repo/path@ref`. Local actions (`./...`) and docker refs are not
#: third-party checkouts and are excluded by the leading-character test below.
_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)
_SHA = re.compile(r"@[0-9a-f]{40}$")


def _third_party_uses(path: Path) -> list[str]:
    refs = _USES.findall(path.read_text(encoding="utf-8"))
    return [r for r in refs if not r.startswith(("./", "docker://"))]


@pytest.mark.parametrize(
    "rel",
    ["action.yml", ".github/workflows/release.yml"],
    ids=["consumer-action", "release-workflow"],
)
def test_every_third_party_action_is_pinned_to_a_commit_sha(rel: str) -> None:
    path = _ROOT / rel
    refs = _third_party_uses(path)
    assert refs, f"found no third-party `uses:` in {rel} -- this guard is checking nothing"
    floating = [r for r in refs if not _SHA.search(r)]
    assert not floating, (
        f"{rel} references third-party actions by a MOVABLE ref: {floating}. Pin each to the "
        "full 40-character commit SHA its tag currently resolves to, with the version in a "
        "trailing comment. A moved tag runs attacker code with the caller's secrets."
    )


def test_each_pin_carries_its_version_in_a_comment() -> None:
    """A bare SHA is unreviewable; the trailing `# vX.Y.Z` is what a human and Dependabot read."""
    text = (_ROOT / "action.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.search(r"uses:\s*[^\s#]+@[0-9a-f]{40}(.*)$", line)
        if m:
            assert re.search(
                r"#\s*v\d+\.\d+\.\d+", m.group(1)
            ), f"a SHA pin in action.yml has no `# vX.Y.Z` comment: {line.strip()}"
