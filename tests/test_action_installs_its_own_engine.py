"""Pinning the Action pins the engine: with no `modelpin-spec`, it installs its own version.

MP-305. `action.yml`'s `modelpin-spec` defaulted to the unpinned `modelpin[providers]`, so a
workflow that pinned the Action by tag or commit still ran whatever engine was newest on PyPI.
`[M] 2026-11-23`: the install step copied out of `git show v0.5.3:action.yml`,
run with its default input in a fresh venv, installed `modelpin 0.5.5`, while
`git show v0.5.3:pyproject.toml` says `0.5.3`. Pinning the ref did not pin the code that runs.

These tests run the step's real `run:` script under the shell GitHub uses for composite steps
(`bash --noprofile --norc -eo pipefail`), with `python -m pip` replaced by a recorder and every
other `python` call passed through, so they check what pip would be asked to install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_VERSION = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]

#: Records each `python [-I] -m pip ...` call (one argument per line, then a marker) and fails
#: the one call whose arguments equal $SHIM_FAIL. Any other mention of pip is refused (exit 97)
#: rather than run, so a spelling the shim does not know can never install anything for real.
#: Any other `python` call runs the real interpreter.
_SHIM = """
python() {
  local isolated=""
  if [ "$1" = "-I" ]; then isolated=1; shift; fi
  if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    shift 2
    printf '%s\\n' "$@" ---END--- >> "$SHIM_LOG"
    if [ -n "$SHIM_FAIL" ] && [ "$*" = "$SHIM_FAIL" ]; then return 1; fi
    return 0
  fi
  case " $* " in *" pip "*|*"-m pip"*) echo "shim: refusing an unrecognised pip call: $*" >&2; return 97;; esac
  if [ -n "$isolated" ]; then command python -I "$@"; else command python "$@"; fi
}
"""


def _bash() -> str | None:
    """A POSIX bash that sees this filesystem. On Windows, `bash` on PATH is often the WSL
    launcher, which cannot read `C:/...` paths, so use the one that ships with Git."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    # `Git/cmd/git.exe` from PowerShell, `Git/mingw64/bin/git.exe` from inside Git Bash.
    for root in Path(git).resolve().parents[:3]:
        candidate = root / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


_BASH = _bash()
pytestmark = pytest.mark.skipif(_BASH is None, reason="needs bash (Git Bash on Windows)")


def _install_step() -> dict:
    action = yaml.safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))
    [step] = [s for s in action["runs"]["steps"] if s.get("name") == "Install Modelpin"]
    assert step["env"]["MP_SPEC"] == "${{ inputs.modelpin-spec }}"
    return step


def _run(
    tmp_path: Path, *, spec: str | None = None, action_path: Path = _ROOT, fail: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """Run the install step as a consumer would: `spec` None means the input was not given."""
    if spec is None:
        action = yaml.safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))
        spec = str(action["inputs"]["modelpin-spec"].get("default", ""))
    log = tmp_path / "pip-calls.txt"
    env = dict(os.environ)
    env.update(
        MP_SPEC=spec,
        GITHUB_ACTION_PATH=action_path.as_posix(),
        SHIM_LOG=log.as_posix(),
        SHIM_FAIL=fail,
        # A second net under the shim: if any install ever reaches the real pip, it resolves
        # nothing from an index and changes nothing on disk.
        PIP_NO_INDEX="1",
        PIP_DRY_RUN="1",
        PATH=str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
    )
    assert _BASH is not None
    proc = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _SHIM + _install_step()["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    calls = [c.split("\n")[:-1] for c in text.split("---END---\n") if c]
    return proc, calls


_UPGRADE_PIP = ["install", "--upgrade", "pip"]


def test_no_input_installs_the_version_of_this_actions_own_commit(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [_UPGRADE_PIP, ["install", f"modelpin[providers]=={_VERSION}"]], (
        "with no modelpin-spec, pip must be asked for exactly this commit's version; "
        f"it was asked for {calls}"
    )


def test_the_version_is_read_from_the_checked_out_action_not_from_anywhere_else(
    tmp_path: Path,
) -> None:
    """A consumer's runner holds the Action at THEIR pinned ref under $GITHUB_ACTION_PATH."""
    pinned = tmp_path / "action-at-pinned-ref"
    pinned.mkdir()
    (pinned / "pyproject.toml").write_text(
        '[project]\nname = "modelpin"\nversion = "9.8.7"\n\n[tool.other]\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    proc, calls = _run(tmp_path, action_path=pinned)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls[-1] == ["install", "modelpin[providers]==9.8.7"]


def test_a_version_not_yet_on_the_index_installs_the_actions_own_copy(tmp_path: Path) -> None:
    """`release.yml` requires `v1` to move before the upload, which waits on a human approval.
    In that window every `@v1` user's pinned install fails; they get the same version from the
    Action's own checkout instead, and the log says so."""
    proc, calls = _run(tmp_path, fail=f"install modelpin[providers]=={_VERSION}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [
        _UPGRADE_PIP,
        ["install", f"modelpin[providers]=={_VERSION}"],
        ["install", f"{_ROOT.as_posix()}[providers]"],
    ]
    assert "::warning::" in proc.stdout


def test_an_explicit_spec_is_installed_exactly_as_given(tmp_path: Path) -> None:
    """The owner's Codegeist pipelines pass `modelpin[providers]==0.5.3`; that must not move."""
    proc, calls = _run(tmp_path, spec="modelpin[providers]==0.5.3")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [_UPGRADE_PIP, ["install", "modelpin[providers]==0.5.3"]]


def test_an_explicit_spec_that_fails_fails_the_step_with_no_substitute(tmp_path: Path) -> None:
    proc, calls = _run(
        tmp_path, spec="modelpin[providers]==0.5.3", fail="install modelpin[providers]==0.5.3"
    )
    assert proc.returncode != 0
    assert calls == [_UPGRADE_PIP, ["install", "modelpin[providers]==0.5.3"]]


def test_an_unreadable_version_fails_instead_of_installing_the_newest(tmp_path: Path) -> None:
    empty = tmp_path / "no-pyproject"
    empty.mkdir()
    proc, calls = _run(tmp_path, action_path=empty)
    assert proc.returncode == 1
    assert "::error::" in proc.stdout
    assert calls == [_UPGRADE_PIP], f"nothing but the pip upgrade may be installed: {calls}"
