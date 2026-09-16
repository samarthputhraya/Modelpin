"""``python -m modelpin`` must work, and must be the same CLI as the console script.

`[M] 2026-09-16`, first-run walk-through from a built wheel in a clean venv on Windows 11:
Application Control blocked the freshly written ``Scripts\\modelpin.exe``, and the documented
way around that --

    python -m modelpin version

-- answered ``No module named modelpin.__main__; 'modelpin' is a package and cannot be
directly executed``. The same dead end exists for any venv that was never activated.
"""

from __future__ import annotations

import subprocess
import sys

from modelpin import __version__


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "modelpin", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_python_dash_m_reports_the_version() -> None:
    result = _run("version")
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def test_it_is_the_same_app_as_the_console_script() -> None:
    """Not a second entry point that can drift: both must resolve to `modelpin.cli.app`."""
    import modelpin.__main__ as dunder
    from modelpin.cli import app

    assert dunder.app is app


def test_an_unknown_option_still_exits_2() -> None:
    """The exit-code contract in the README is the CLI's, not the console script's -- a
    second entry point that swallowed Click's usage code would quietly break CI scripts."""
    result = _run("check", "--definitely-not-a-flag")
    assert result.returncode == 2, result.stdout + result.stderr


def test_help_names_the_commands_a_new_user_needs() -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    flat = "".join(result.stdout.split())
    for command in ("init", "scan", "baseline", "check", "report", "draft", "version"):
        assert command in flat
