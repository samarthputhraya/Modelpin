"""`runs` has a ceiling, in modelpin.yaml and on `--runs` (MP-334).

`[M] 2026-11-26` audit: nothing bounded `runs`. A config, which a pull request can edit, planned
6,000 paid replays on the user's key with no refusal, and `runs: 1000000` spent minutes
computing permutation floors before printing anything. Both paths now refuse above `MAX_RUNS`
with the config-error exit (4) that every invalid setting already uses.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.config import MAX_RUNS, ConfigError, ModelpinConfig, load_config

runner = CliRunner()


def test_the_config_refuses_runs_above_the_ceiling(tmp_path: Path) -> None:
    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text(f"runs: {MAX_RUNS + 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=rf"runs: .*less than or equal to {MAX_RUNS}"):
        load_config(cfg)
    cfg.write_text(f"runs: {MAX_RUNS}\n", encoding="utf-8")
    assert load_config(cfg).runs == MAX_RUNS


def test_the_flag_refuses_runs_above_the_ceiling() -> None:
    assert cli._resolve_runs(MAX_RUNS, ModelpinConfig()) == MAX_RUNS
    with pytest.raises(typer.Exit) as info:
        cli._resolve_runs(MAX_RUNS + 1, ModelpinConfig())
    assert info.value.exit_code == 4


def test_a_huge_run_count_is_refused_at_once_not_after_minutes(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "modelpin.yaml").write_text(
        "models: [m1]\nproviders: [fake]\nruns: 5\n", encoding="utf-8"
    )
    scenario = {"id": "s", "name": "s", "input": {"messages": [{"role": "user", "content": "hi"}]}}
    (tmp_path / "scenarios" / "s.json").write_text(json.dumps(scenario), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    t0 = time.perf_counter()
    res = runner.invoke(app, ["baseline", "--runs", "1000000"])
    assert time.perf_counter() - t0 < 5.0
    assert res.exit_code == 4, res.output
    assert f"must be <= {MAX_RUNS}" in " ".join(res.output.split())
