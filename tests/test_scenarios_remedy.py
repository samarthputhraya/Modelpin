"""MP-38: the "no scenarios found" message names the state it is in and a remedy that works.

`[M] 2026-08-22` (re-checked 2026-11-27): `_dir_state`, `_scenarios_remedy`, `_demo_hint` and
`_init_target` had one direct reference in the whole suite, a docstring. They decide the first
error message a new user sees, and their one rule is that `modelpin init` is named only when it
would create exactly the directory the command looked in; otherwise the user runs it, it
succeeds, the command fails again, and they loop.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import _demo_hint, _dir_state, _init_target, _scenarios_remedy, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES
from modelpin.scenarios import _RESERVED_FILES

runner = CliRunner()


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- _dir_state: one message per state `load_scenarios` collapses into [] -------------------


def test_a_missing_directory(tmp_path: Path) -> None:
    assert _dir_state(tmp_path / "nope") == "that directory does not exist"


def test_a_file_where_a_directory_was_expected(tmp_path: Path) -> None:
    f = tmp_path / "scenarios"
    f.write_text("{}", encoding="utf-8")
    assert _dir_state(f) == "that path is a file, not a directory"


def test_an_empty_directory(tmp_path: Path) -> None:
    assert _dir_state(tmp_path) == "it exists but contains no .json files"


def test_a_directory_holding_only_reserved_files(tmp_path: Path) -> None:
    reserved = sorted(_RESERVED_FILES)[0]
    (tmp_path / reserved).write_text("{}", encoding="utf-8")
    state = _dir_state(tmp_path)
    assert state.startswith("it contains only reserved files") and reserved in state


def test_a_directory_whose_json_files_are_not_scenarios(tmp_path: Path) -> None:
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    assert _dir_state(tmp_path) == "no .json file there loaded as a scenario"


# --- _scenarios_remedy: init is named only where it would create this exact path -------------


def test_the_default_path_is_one_init_creates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = (tmp_path / "scenarios").resolve()
    assert _init_target("modelpin.yaml") == target
    remedy = _scenarios_remedy(target, "default", "modelpin.yaml")
    assert "Run `modelpin init`" in remedy and "--demo" in remedy


def test_a_flag_path_init_would_not_create_does_not_name_init_as_the_fix(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    remedy = _scenarios_remedy((tmp_path / "elsewhere").resolve(), "flag", "modelpin.yaml")
    assert "`modelpin init` will NOT create this path" in remedy
    assert "Run `modelpin init`" not in remedy


def test_a_config_path_init_would_not_create_points_at_scenarios_dir(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "modelpin.yaml").write_text("scenarios_dir: cases\n", encoding="utf-8")
    assert _init_target("modelpin.yaml") == (tmp_path / "cases").resolve()
    remedy = _scenarios_remedy((tmp_path / "other").resolve(), "config", "modelpin.yaml")
    assert "fix scenarios_dir:" in remedy and "Run `modelpin init`" not in remedy


def test_a_suite_directory_is_never_something_init_makes(tmp_path: Path) -> None:
    remedy = _scenarios_remedy(tmp_path.resolve(), "suite", "modelpin.yaml")
    assert "does not create suite directories" in remedy
    assert "examples/report-suite" in remedy


def test_an_unreadable_config_makes_the_target_unknowable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "modelpin.yaml").write_text("runs: [not, a, number]\n", encoding="utf-8")
    assert _init_target("modelpin.yaml") is None


# --- _demo_hint: a demo next to the user is almost always what they meant --------------------


def test_the_demo_hint_appears_only_when_a_demo_is_beside_the_user(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _demo_hint((tmp_path / "scenarios").resolve()) == ""
    demo = tmp_path / DEMO_DIRNAME
    demo.mkdir()
    (demo / DEMO_FIXTURES).write_text("[]", encoding="utf-8")
    hint = _demo_hint((tmp_path / "scenarios").resolve())
    assert f"`cd {DEMO_DIRNAME}` first" in hint


# --- end to end: following the advice works, and the advice never loops --------------------


def test_following_the_init_advice_gets_past_the_error(tmp_path: Path, monkeypatch) -> None:
    """The no-loop rule, run: the error names `modelpin init`, init creates that directory,
    and the same command no longer fails on missing scenarios."""
    monkeypatch.chdir(tmp_path)
    args = ["baseline", "--model", "m", "--provider", "fake", "--runs", "2"]
    first = runner.invoke(app, args)
    assert first.exit_code == 4, first.output
    assert "Run `modelpin init`" in _flat(first.output), first.output
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    second = runner.invoke(app, args)
    assert "no scenarios found" not in _flat(second.output), second.output


def test_a_path_init_will_not_create_is_never_answered_with_init(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = ["baseline", "--model", "m", "--provider", "fake", "--scenarios-dir", "elsewhere"]
    result = runner.invoke(app, args)
    out = _flat(result.output)
    assert result.exit_code == 4, result.output
    assert "that directory does not exist" in out, result.output
    assert "`modelpin init` will NOT create this path" in out, result.output
    assert "Run `modelpin init`" not in out, result.output
