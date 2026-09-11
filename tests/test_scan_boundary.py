"""`mp scan` reports only files inside the directory it was pointed at.

MP-241. `[M] 2026-09-09` `os.walk`'s default `followlinks=False` is enforced through
`DirEntry.is_symlink()`, which is FALSE for an NTFS junction -- and creating a junction needs
no privilege. Reproduced: `vendor/` junctioned to a directory outside the repository made
`scan` report

    claude-3-5-sonnet    in vendor\\secret_project.py

-- a file that is not in the repo at all -- while `is_symlink()` said False and
`os.path.isjunction()` said True.

Bounded, and the bound matters: a hit carries only `{model, file, line, context}` and is
printed to the user's own terminal, so this leaked filenames and existence, never content, and
nothing left the machine. It is still wrong for `scan` to report files outside the repo it was
asked about.

These tests build the link with whatever the platform allows WITHOUT privilege -- a junction on
Windows, a symlink elsewhere -- so they run everywhere rather than skipping. A skip here would
also break `test_the_readmes_test_count_is_the_real_one`, which pins passed + xfailed against
collected.
"""

from __future__ import annotations

import os
from pathlib import Path

from modelpin.detector import scan_repo


def _link_dir(target: Path, link: Path) -> str:
    """Make `link` point at the directory `target`, unprivileged. Returns what was made."""
    try:
        import _winapi  # Windows only; CreateJunction needs no privilege

        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    except ImportError:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside_home"
    repo.mkdir()
    outside.mkdir()
    (repo / "app.py").write_text('MODEL = "gpt-4o-mini"\n', encoding="utf-8")
    (outside / "secret_project.py").write_text('MODEL = "claude-3-5-sonnet"\n', encoding="utf-8")
    return repo, outside


def test_a_link_out_of_the_repo_is_not_followed(tmp_path: Path) -> None:
    repo, outside = _layout(tmp_path)
    kind = _link_dir(outside, repo / "vendor")
    found = {(h["model"], h["file"]) for h in scan_repo(repo)}
    assert ("gpt-4o-mini", "app.py") in found
    assert not any(m == "claude-3-5-sonnet" for m, _ in found), (
        f"scan followed a {kind} OUT of the repository and reported a file that is not in it: "
        f"{sorted(found)}"
    )


def test_a_reexposed_git_directory_is_not_walked(tmp_path: Path) -> None:
    """`SKIP_DIRS` matches by NAME, so `.git` linked in under another name used to be walked."""
    repo, _ = _layout(tmp_path)
    git = tmp_path / "some_repo" / ".git"
    git.mkdir(parents=True)
    (git / "config.yaml").write_text('model: "gemini-2.5-pro"\n', encoding="utf-8")
    _link_dir(git, repo / "gitdata")
    models = {h["model"] for h in scan_repo(repo)}
    assert "gemini-2.5-pro" not in models, models


def test_a_link_that_stays_inside_the_repo_is_still_scanned(tmp_path: Path) -> None:
    """A monorepo's shared package linked in from elsewhere IN the repo must keep working.

    The boundary is the repository, not "no links at all" -- over-firing here would silently
    drop real dependencies, which is the blind half of the same defect.
    """
    repo, _ = _layout(tmp_path)
    shared = repo / "packages" / "shared"
    shared.mkdir(parents=True)
    (shared / "client.py").write_text('MODEL = "gpt-4.1-mini"\n', encoding="utf-8")
    _link_dir(shared, repo / "linked_shared")
    models = {h["model"] for h in scan_repo(repo)}
    assert "gpt-4.1-mini" in models, models
