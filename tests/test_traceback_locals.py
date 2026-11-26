"""Tracebacks never print local variables (MP-328).

A frame's locals can hold credentials and prompts, and Typer's default for
`pretty_exceptions_show_locals` differs between the versions `typer>=0.12` allows. The setting
is therefore pinned at the call site, not inferred from whichever Typer is installed -- which is
also why the first test reads the source: the installed Typer's default cannot show whether the
app sets it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from modelpin import cli

CLI_SOURCE = Path(cli.__file__).read_text(encoding="utf-8")


def _app_call_keywords() -> dict[str, object]:
    for node in ast.walk(ast.parse(CLI_SOURCE)):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "app" for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            return {
                kw.arg: ast.literal_eval(kw.value)
                for kw in node.value.keywords
                if kw.arg and isinstance(kw.value, ast.Constant)
            }
    raise AssertionError("no `app = typer.Typer(...)` in cli.py")


def test_the_app_is_built_with_locals_off_whatever_typer_is_installed() -> None:
    assert _app_call_keywords().get("pretty_exceptions_show_locals") is False


def test_the_running_app_has_locals_off() -> None:
    assert cli.app.pretty_exceptions_show_locals is False
