"""`action.yml` in `mode: watch`: opt-in, guarded, and byte-for-byte the old Action otherwise.

MP-277. A repository that never sets `mode` must run exactly what it ran before this input
existed, so every check-mode step is guarded on `mode == 'check'` and every watch-mode step on
`mode == 'watch'`; no check-mode step can create a pull request; and every caller input still
reaches a `run:` block only through `env:` (the script-injection rule the file already keeps).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_ACTION = yaml.safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _ACTION["runs"]["steps"]


def test_the_default_mode_is_check_so_existing_consumers_change_nothing() -> None:
    assert _ACTION["inputs"]["mode"]["default"] == "check"
    assert _ACTION["inputs"]["to"]["required"] is False, "`to` is required only in check mode"


def test_every_step_is_guarded_on_a_mode() -> None:
    unguarded = [s.get("name") for s in _steps() if "inputs.mode ==" not in str(s.get("if", ""))]
    # Set-up steps (Python, install) serve both modes and carry no guard on purpose.
    allowed = {"Set up Python", "Install Modelpin"}
    assert set(unguarded) <= allowed, unguarded


def test_no_check_mode_step_can_create_a_pull_request() -> None:
    for s in _steps():
        if "inputs.mode == 'check'" in str(s.get("if", "")):
            assert "pr create" not in str(s.get("run", "")), s.get("name")
            assert "watch_pr.py" not in str(s.get("run", "")), s.get("name")


def test_watch_mode_never_runs_mp_check_directly() -> None:
    """The check in watch mode is delegated to `watch_pr.py`, which owns the PR lifecycle."""
    for s in _steps():
        if "inputs.mode == 'watch'" in str(s.get("if", "")):
            assert "mp check" not in str(s.get("run", "")), s.get("name")


def test_caller_inputs_reach_run_blocks_only_through_env() -> None:
    for s in _steps():
        run = s.get("run")
        if run:
            assert (
                "${{ inputs." not in run
            ), f"{s.get('name')} interpolates an input into script text"


def test_the_watch_fail_step_never_calls_an_unknown_model_a_regression() -> None:
    step = next(s for s in _steps() if s.get("id") == "watch_fail")
    run = step["run"]
    assert "regression" not in run.lower()
    assert '"$MP_WATCH_CODE" = "3"' in run and '"$MP_WATCH_CODE" = "1"' in run


def test_the_registry_step_needs_no_network_by_default() -> None:
    step = next(s for s in _steps() if s.get("id") == "registry")
    # The action's own path reaches the script as env data (`MP_ACTION_PATH`), like every
    # other caller-controlled value in this file.
    assert step["env"]["MP_ACTION_PATH"] == "${{ github.action_path }}"
    assert "$MP_ACTION_PATH/data/models.json" in step["run"]
    assert "curl" in step["run"] and "--max-time" in step["run"]


def test_the_watch_inputs_are_documented_in_the_action_readme() -> None:
    text = (_ROOT / "actions" / "README.md").read_text(encoding="utf-8")
    for name in (
        "mode",
        "registry-url",
        "watch-scan",
        "watch-max-prs",
        "watch-recheck-days",
        "fail-on-affected",
    ):
        assert f"`{name}`" in text, name
    assert "contents: write" in text and "pull-requests: write" in text
    assert re.search(r"GITHUB_TOKEN.*do not trigger", text, re.S)
