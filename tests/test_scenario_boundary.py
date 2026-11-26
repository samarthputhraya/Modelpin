"""Scenarios are read, and starter scenarios written, only inside their own directory (MP-335).

`[M] 2026-11-26` audit: `load_scenarios` followed an NTFS junction (not a symlink to Python)
out of `scenarios/` and loaded the files there; two junctions pointing back at their parent made
it walk forever; and `mp init` wrote its starter scenarios wherever an existing modelpin.yaml's
`scenarios_dir` pointed, outside the repository included. `scan` already had this boundary
(MP-241).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.scenarios import load_scenarios

runner = CliRunner()


def _scenario(path: Path, sid: str) -> None:
    body = {"id": sid, "name": sid, "input": {"messages": [{"role": "user", "content": "hi"}]}}
    path.write_text(json.dumps(body), encoding="utf-8")


def _dir_link(link: Path, target: Path) -> None:
    """A directory link: a symlink where allowed, an NTFS junction on Windows otherwise."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if res.returncode == 0:
            return
    pytest.skip("cannot create a directory link here")


def test_a_link_out_of_the_scenarios_directory_is_not_followed(tmp_path: Path) -> None:
    scenarios = tmp_path / "repo" / "scenarios"
    scenarios.mkdir(parents=True)
    _scenario(scenarios / "inside.json", "inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    _scenario(outside / "secret.json", "OUTSIDE_THE_REPO")
    _dir_link(scenarios / "ext", outside)
    assert [s.id for s in load_scenarios(scenarios)] == ["inside"]


def test_links_back_to_a_parent_do_not_make_the_loader_walk_forever(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    (scenarios / "a").mkdir(parents=True)
    _scenario(scenarios / "a" / "one.json", "one")
    _dir_link(scenarios / "a" / "j1", scenarios / "a")
    _dir_link(scenarios / "a" / "j2", scenarios / "a")
    t0 = time.perf_counter()
    ids = [s.id for s in load_scenarios(scenarios)]
    assert time.perf_counter() - t0 < 5.0
    assert ids == ["one"]


def test_nested_scenarios_inside_the_directory_still_load(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    (scenarios / "billing").mkdir(parents=True)
    _scenario(scenarios / "top.json", "top")
    _scenario(scenarios / "billing" / "refund.json", "refund")
    assert sorted(s.id for s in load_scenarios(scenarios)) == ["refund", "top"]


def test_init_never_writes_scenarios_outside_the_directory(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "modelpin.yaml").write_text(
        "models: [m1]\nscenarios_dir: ../elsewhere/x\nproviders: [fake]\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 4, res.output
    assert "outside this directory" in " ".join(res.output.split())
    assert not (tmp_path / "elsewhere").exists()
