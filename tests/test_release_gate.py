"""The release workflow's version gate must be able to fail.

MP-231. `.github/workflows/release.yml` refuses to publish unless the tag, `pyproject.toml`,
`modelpin/__init__.py`, `CHANGELOG.md` and `README.md` all name the same version. That gate is
the last thing standing between a typo and a burned PyPI version number, and **no test read it
until now** — which is precisely how it rotted:

`[M] 2026-09-08` MP-204 made `pyproject.toml` the single source of the version, so
`__version__` is resolved from the installed distribution inside a `try/except` and there is no
top-level literal left. The gate's clause asserted `^__version__\\s*=\\s*"..."` with `re.M`, so
it stopped matching anything and reported *"modelpin/__init__.py has no __version__ = '...'
line"* — a gate that fails CLOSED on every future release, discovered only by running it by
hand while preparing 0.3.0. `release.yml` had not executed since v0.2.1 (2026-09-02) and MP-204
merged after it, so CI had no opportunity to say so.

The lesson is not "fix the regex". It is that a gate nothing exercises is indistinguishable
from a gate that is wrong. This module executes the gate's *actual* script, extracted verbatim
from the YAML, against the real tree and against mutants that must each be rejected. It
deliberately does not re-implement the gate: a copy would drift from the original exactly the
way the version literal did.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FILES = ("pyproject.toml", "CHANGELOG.md", "README.md", "modelpin/__init__.py")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    import tomllib

    data = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _gate_source() -> str:
    """Pull the gate's Python out of the `run:` block, verbatim.

    Matching on the heredoc marker rather than a line range means moving the step around the
    file does not silently turn this test into a no-op.
    """
    workflow = _repo_root() / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")
    match = re.search(r"python - \"\$VER\" <<'PY'\n(.*?)\n\s*PY\n", text, re.S)
    assert match is not None, (
        "could not find the version-gate heredoc (`python - \"$VER\" <<'PY'`) in "
        f"{workflow}. If the gate moved or changed shape, update this test — do not "
        "delete it; an unexercised release gate is how MP-231 happened."
    )
    body = match.group(1)
    lines = body.split("\n")
    pad = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[pad:] if ln.strip() else "" for ln in lines)


def _tree(tmp_path: Path, name_hint: str = "tree") -> Path:
    """A minimal copy of the files the gate reads."""
    # Named, because one test builds TWO copies -- a prepared one and the untouched real tree.
    # `[M] 2026-09-09` A fixed "tree" name made that test raise FileExistsError, and only ever
    # on a release-prep branch, because the second copy is built solely when the repo already
    # claims to be release-ready. It surfaced while cutting 0.3.1: a latent failure that waits
    # for release day is the same disease this module exists to treat.
    root = tmp_path / name_hint
    (root / "modelpin").mkdir(parents=True)
    for name in FILES:
        shutil.copy2(_repo_root() / name, root / name)
    return root


def _changelog_is_dated_for(version: str, text: str) -> bool:
    return bool(re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}", text, re.M))


def _repo_is_release_ready() -> bool:
    """Does the tree CLAIM to be the one about to be tagged?

    Between releases it does not, and should not: `pyproject.toml` names the next version
    while `CHANGELOG.md` and `README.md` still describe the last shipped one. Only during
    release preparation do all three agree.
    """
    version = _declared_version()
    changelog = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")
    return _changelog_is_dated_for(version, changelog) and f"-> modelpin {version}" in readme


def _prepared_tree(tmp_path: Path) -> Path:
    """A copy of the tree made release-ready for the declared version.

    The mutants below must start from a tree the gate ACCEPTS, or "the mutant was rejected"
    proves nothing -- it would have been rejected anyway. Preparing the copy (never the repo)
    keeps every mutant meaningful on any day, including the ~99% of days that are not a
    release day.
    """
    root = _tree(tmp_path)
    version = _declared_version()

    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    if not _changelog_is_dated_for(version, changelog):
        marker = "## [Unreleased]"
        heading = f"## [{version}] - 2026-01-01"
        if marker in changelog:
            changelog = changelog.replace(marker, heading, 1)
        else:
            changelog = f"{heading}\n\n{changelog}"
        changelog_path.write_text(changelog, encoding="utf-8")

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    if f"-> modelpin {version}" not in readme:
        readme = re.sub(r"-> modelpin \d+\.\d+\.\d+", f"-> modelpin {version}", readme, count=1)
        readme_path.write_text(readme, encoding="utf-8")

    return root


def _run_gate(tree: Path, version: str) -> subprocess.CompletedProcess[str]:
    script = tree / "_gate.py"
    script.write_text(_gate_source(), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), version],
        cwd=tree,
        capture_output=True,
        text=True,
    )


def test_the_gate_accepts_a_tree_prepared_for_the_declared_version(tmp_path: Path) -> None:
    """The gate must pass a tree that IS ready — on any day, not only release day.

    This is the assertion whose absence let a broken gate sit in `main`: MP-204 deleted the
    `__version__` literal the gate matched on, and nothing noticed for a whole release cycle.
    That defect is independent of whether today happens to be a release day, so this case must
    be too — it runs against a prepared copy rather than waiting for the repo to be mid-cut.
    """
    version = _declared_version()
    result = _run_gate(_prepared_tree(tmp_path), version)
    assert result.returncode == 0, (
        f"release.yml's version gate REJECTS a tree prepared for {version}. Tagging it would "
        "fail in CI before anything is built.\n\n"
        f"{result.stdout}{result.stderr}"
    )

    # And when the repo CLAIMS to be the tree about to ship, the unprepared bytes must pass
    # too — that is the release-prep branch, where a human is one `git tag` from publishing.
    #
    # Deliberately an extra assertion rather than a second test with a `pytest.skip`. A skip
    # would make the suite's pass/skip counts depend on where in the release cycle it runs,
    # and `tests/test_recall_arm.py::test_the_readmes_test_count_is_the_real_one` pins those
    # counts in README.md — so a conditional skip would turn a published number into
    # something that silently changes twice per release. One test, always run, no skip.
    if _repo_is_release_ready():
        real = _run_gate(_tree(tmp_path, "real"), version)
        assert real.returncode == 0, (
            f"release.yml's version gate REJECTS this tree at {version}, and the repo claims "
            "to be release-ready. Publishing a GitHub Release would fail in CI.\n\n"
            f"{real.stdout}{real.stderr}"
        )


def _hardcode_literal(root: Path) -> None:
    path = root / "modelpin" / "__init__.py"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n__version__ = "0.2.1"\n', encoding="utf-8"
    )


def _sever_single_source(root: Path) -> None:
    path = root / "modelpin" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("_dist_version", "_gone").replace("importlib.metadata", "nope"),
        encoding="utf-8",
    )


def _drift_pyproject(root: Path) -> None:
    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(f'version = "{_declared_version()}"', 'version = "9.9.9"', 1),
        encoding="utf-8",
    )


def _undate_changelog(root: Path) -> None:
    path = root / "CHANGELOG.md"
    version = _declared_version()
    text = path.read_text(encoding="utf-8")
    updated = re.sub(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}",
        f"## [{version}]",
        text,
        count=1,
        flags=re.M,
    )
    assert updated != text, "mutation did not apply — the CHANGELOG heading shape changed"
    path.write_text(updated, encoding="utf-8")


def _stale_readme(root: Path) -> None:
    path = root / "README.md"
    text = path.read_text(encoding="utf-8")
    updated = text.replace(f"-> modelpin {_declared_version()}", "-> modelpin 0.0.1", 1)
    assert updated != text, "mutation did not apply — the README version line changed shape"
    path.write_text(updated, encoding="utf-8")


@pytest.mark.parametrize(
    ("label", "mutate", "expected"),
    [
        # The MP-204 regression in the direction that matters: a second copy of the version
        # reappears and starts drifting. This is the MP-03 shape the project has paid for twice.
        ("hardcoded __version__ literal", _hardcode_literal, "hardcodes __version__"),
        # The single source is severed, so `mp version` could say anything at all.
        ("dist-metadata lookup removed", _sever_single_source, "no longer resolves"),
        ("pyproject disagrees with the tag", _drift_pyproject, "pyproject.toml says"),
        ("CHANGELOG entry is undated", _undate_changelog, "no dated"),
        ("README advertises the old version", _stale_readme, "does not show"),
    ],
)
def test_the_gate_rejects(tmp_path: Path, label: str, mutate: object, expected: str) -> None:
    """Each mutant is a tree that must not reach PyPI.

    A surviving mutant means the gate passes on a tree it is supposed to stop, which is the
    state `release.yml` was actually in for the `__version__` clause — it could no longer
    distinguish a correct tree from any other, because it matched neither.
    """
    root = _prepared_tree(tmp_path)
    mutate(root)  # type: ignore[operator]
    result = _run_gate(root, _declared_version())

    assert result.returncode != 0, (
        f"the version gate ACCEPTED a tree with: {label}. It cannot fail on this, so it is "
        f"not guarding anything.\n\n{result.stdout}{result.stderr}"
    )
    assert expected in result.stdout, (
        f"the gate rejected {label!r} but not for the stated reason — expected {expected!r} "
        f"in its output, so the failure may be incidental.\n\n{result.stdout}{result.stderr}"
    )


# ---------------------------------------------------------------------------------------
# The sdist allow-list is a hand-copy of MANIFEST.in, and it has now gone stale three times.
# ---------------------------------------------------------------------------------------


def _sdist_allowlist_from_workflow() -> set[str]:
    """The `_SDIST_TOP_ALLOWED` literal, read out of release.yml."""
    workflow = _repo_root() / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")
    match = re.search(r"_SDIST_TOP_ALLOWED = \{(.*?)\}", text, re.S)
    assert match is not None, (
        "could not find `_SDIST_TOP_ALLOWED` in release.yml. If the artifact contract moved, "
        "update this test rather than deleting it -- an unexercised release gate is how "
        "MP-231 happened twice in one release."
    )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _manifest_top_level() -> set[str]:
    """Top-level names MANIFEST.in deliberately puts into the sdist."""
    manifest = (_repo_root() / "MANIFEST.in").read_text(encoding="utf-8")
    names: set[str] = set()
    for raw in manifest.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        verb, args = parts[0], parts[1:]
        if verb == "graft":
            names.update(a.strip("/").split("/")[0] for a in args)
        elif verb == "include":
            names.update(a.split("/")[0] for a in args)
    return names


def test_the_sdist_allowlist_covers_everything_manifest_in_ships() -> None:
    """release.yml must not refuse an artifact MANIFEST.in deliberately built.

    `[M] 2026-08-27` The list refused the 0.2.0 release over `scripts`, added by MP-100 after
    the list was measured on 0.1.2. `[M] 2026-09-08` It refused the 0.3.0 release over
    `actions`, added by MP-203 on 2026-09-07 after 0.2.1 shipped -- the same shape, two
    releases later, discovered only by publishing a GitHub Release and watching the build job
    fail. Both times the LIST was stale and the artifact was correct.

    The hand-copy is the defect (MP-121). Until it is deleted outright, this test is the thing
    that makes the copy fail in CI on the commit that changes MANIFEST.in, rather than during
    a release nobody can rehearse.
    """
    missing = sorted(_manifest_top_level() - _sdist_allowlist_from_workflow())
    assert not missing, (
        "MANIFEST.in ships top-level "
        + ", ".join(missing)
        + " but release.yml's `_SDIST_TOP_ALLOWED` does not list "
        + ("it" if len(missing) == 1 else "them")
        + ". The release build job will REFUSE TO PUBLISH. Re-measure the list against "
        "MANIFEST.in -- do not loosen the contract."
    )
