"""The Python floor is ONE number, stated in six places; this pins them to each other.

MP-281 lowered the floor from 3.12 to 3.11 by hand in `pyproject.toml` (requires-python, the
classifier list, ruff and black targets), `.github/workflows/ci.yml` (the test matrix and the
wheel-smoke interpreter), `.github/workflows/release.yml` (the required checks and its
wheel-smoke interpreter) and `README.md`'s Install line -- and the claims audit found that
nothing would have noticed had any one of them been missed. A README that advertises a floor
no CI job runs is a published claim with no measurement behind it; a matrix that tests a
version the wheel refuses to install on is wasted CI. Either drift is silent without this.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _floor_minor() -> int:
    req = _pyproject()["project"]["requires-python"].strip()
    m = re.fullmatch(r">=\s*3\.(\d+)", req)
    assert m, f"requires-python is not a plain '>=3.N' floor: {req!r}"
    return int(m.group(1))


def _classifier_minors() -> set[int]:
    found = {
        int(m.group(1))
        for c in _pyproject()["project"]["classifiers"]
        if (m := re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", c))
    }
    assert found, "no 'Programming Language :: Python :: 3.N' classifier at all"
    return found


def _ci_matrix_minors() -> list[int]:
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"python-version:\s*\[([^\]]+)\]", text)
    assert m, "ci.yml has no python-version matrix"
    return [int(v) for v in re.findall(r'"3\.(\d+)"', m.group(1))]


def _release_required_minors() -> list[int]:
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    return [int(v) for v in re.findall(r'"test \(3\.(\d+)\)"', text)]


def _readme_floor_minor() -> int:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^Python 3\.(\d+) or newer", text, re.M)
    assert m, "README.md no longer states 'Python 3.N or newer' on its own line"
    return int(m.group(1))


def _floor_comment_minors(workflow: str) -> list[int]:
    """Every `python-version: "3.N"` that carries the 'floor from requires-python' comment."""
    text = (REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    hits = re.findall(
        r'python-version:\s*"3\.(\d+)"\s*#\s*floor from requires-python', text
    )
    assert hits, f"{workflow} no longer marks its wheel-smoke interpreter as the floor"
    return [int(v) for v in hits]


def test_the_readme_states_the_floor_pyproject_declares() -> None:
    assert _readme_floor_minor() == _floor_minor()


def test_the_lowest_classifier_is_the_floor() -> None:
    assert min(_classifier_minors()) == _floor_minor()


def test_ci_tests_the_floor_and_every_classified_version() -> None:
    matrix = _ci_matrix_minors()
    assert min(matrix) == _floor_minor()
    assert (
        set(matrix) == _classifier_minors()
    ), "a version is classified but not tested, or tested but not classified"


def test_the_release_gate_requires_every_matrix_leg() -> None:
    assert sorted(_release_required_minors()) == sorted(_ci_matrix_minors())


def test_the_wheel_smoke_runs_on_the_floor_in_both_workflows() -> None:
    for workflow in ("ci.yml", "release.yml"):
        assert _floor_comment_minors(workflow) == [_floor_minor()], workflow


def test_ruff_and_black_target_the_floor() -> None:
    tool = _pyproject()["tool"]
    target = f"py3{_floor_minor()}"
    assert tool["ruff"]["target-version"] == target
    assert tool["black"]["target-version"] == [target]
