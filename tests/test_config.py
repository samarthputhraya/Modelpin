from pathlib import Path

import pytest
from pydantic import ValidationError

from modelpin.config import (
    DEFAULT_PROVIDER,
    DEFAULT_RUNS,
    ConfigError,
    ModelpinConfig,
    load_config,
)


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert isinstance(cfg, ModelpinConfig)
    assert cfg.runs == DEFAULT_RUNS == 5
    assert cfg.scenarios_dir == "scenarios"
    # Zero-config must route to the implemented adapter, never the Anthropic stub.
    assert cfg.providers == [DEFAULT_PROVIDER] == ["openai"]


def test_the_default_runs_can_actually_reach_significance():
    """MP-03: the old default of 3 made the tool signal structurally unable to fire.

    This asserts the *reason* for the number, not the number. The smallest p attainable
    at N runs/side is 2/C(2N,N) for the tool signal, so the default must clear ALPHA on
    its own -- otherwise a total trajectory change scores `unchanged` and the product is
    blind to the signal it is named for. The old default failed this: 2/C(6,3) = 0.10.

    Guarding the value alone is what let the bug live: `tests/test_config.py` asserted
    `runs == 3`, so the CORRECT value failed CI.
    """
    from math import comb

    from modelpin.diff import ALPHA

    min_p_tool = 2 / comb(2 * DEFAULT_RUNS, DEFAULT_RUNS)
    assert min_p_tool <= ALPHA, (
        f"DEFAULT_RUNS={DEFAULT_RUNS} cannot reach p <= ALPHA ({ALPHA}) on the tool "
        f"signal: the best attainable p is {min_p_tool:.5f}. Raise DEFAULT_RUNS."
    )
    # ...and it must clear the floor with margin, not sit exactly on it: at the smallest
    # N that passes (4, p=0.0286) only a perfect split reaches significance, so one noisy
    # run destroys the result.
    assert (
        2 / comb(2 * (DEFAULT_RUNS - 1), DEFAULT_RUNS - 1) <= ALPHA
    ), "DEFAULT_RUNS sits on the significance floor with no margin for a noisy run"


def test_no_shipped_file_hardcodes_a_runs_value_that_disagrees():
    """MP-03 was one number living in FIVE places and drifting apart.

    Python sites interpolate `DEFAULT_RUNS`; the YAML files cannot, so they are pinned
    here instead. Asserting `RECOMMENDED_RUNS == DEFAULT_RUNS` would be a tautology --
    `cli.py` derives it -- so this checks the copies that are still literals.
    """
    import re

    repo = Path(__file__).resolve().parents[1]
    shipped = [
        repo / ".github" / "workflows" / "modelpin.yml",
        repo / "examples" / "github-workflow.yml",
        repo / "modelpin.yaml",
    ]
    wrong = []
    for path in shipped:
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"""\s*runs:\s*["']?(\d+)["']?""", line)
            if m and int(m.group(1)) != DEFAULT_RUNS:
                wrong.append(f"{path.relative_to(repo)}:{lineno} runs={m.group(1)}")
    assert not wrong, (
        f"hardcoded runs value disagrees with DEFAULT_RUNS={DEFAULT_RUNS}: {wrong}. "
        "That drift IS MP-03."
    )


def test_the_generated_config_templates_track_the_default():
    """Both scaffolds interpolate the constant rather than repeating it."""
    import yaml

    from modelpin.cli import _SAMPLE_CONFIG
    from modelpin.demo import _CONFIG

    assert yaml.safe_load(_SAMPLE_CONFIG)["runs"] == DEFAULT_RUNS
    assert yaml.safe_load(_CONFIG)["runs"] == DEFAULT_RUNS


def test_parses_yaml(tmp_path):
    p = tmp_path / "modelpin.yaml"
    p.write_text("models:\n  - m1\nruns: 7\nproviders:\n  - anthropic\n")
    cfg = load_config(p)
    assert cfg.models == ["m1"]
    assert cfg.runs == 7
    assert cfg.providers == ["anthropic"]


def test_empty_yaml_file_yields_defaults(tmp_path):
    p = tmp_path / "modelpin.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg.providers == ["openai"]


def test_runs_must_be_at_least_one():
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ModelpinConfig(runs=0)


def test_malformed_yaml_raises_config_error(tmp_path):
    p = tmp_path / "modelpin.yaml"
    p.write_text("models: [unclosed\n  : :")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(p)


def test_non_mapping_yaml_raises_config_error(tmp_path):
    p = tmp_path / "modelpin.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(p)


def test_invalid_setting_type_raises_config_error(tmp_path):
    p = tmp_path / "modelpin.yaml"
    p.write_text("runs: not-a-number\n")
    with pytest.raises(ConfigError, match="invalid settings"):
        load_config(p)
