"""CLI hardening tests — the real-provider path must fail with friendly messages,
never raw tracebacks, and never silently produce a misleading result. All offline.
"""

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()

# Distinctive multi-char ids so substring assertions can't match an echoed tmp_path.
_SCENARIO_A = (
    '{"id": "greet_alpha", "name": "A", '
    '"input": {"messages": [{"role": "user", "content": "hi"}]}}'
)
_SCENARIO_B = (
    '{"id": "greet_bravo", "name": "B", '
    '"input": {"messages": [{"role": "user", "content": "yo"}]}}'
)


def _scenarios_dir(tmp_path, *files):
    d = tmp_path / "scenarios"
    d.mkdir()
    for name, content in files:
        (d / name).write_text(content)
    return str(d)


def test_runs_floor_rejects_single_run(tmp_path):
    scen = _scenarios_dir(tmp_path, ("a.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        ["baseline", "--provider", "fake", "--model", "m", "--scenarios-dir", scen, "--runs", "1"],
    )
    assert r.exit_code == 1
    assert "runs must be >= 2" in r.output


def test_unknown_provider_fails_friendly(tmp_path):
    scen = _scenarios_dir(tmp_path, ("a.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "banana",
            "--model",
            "m",
            "--scenarios-dir",
            scen,
            "--runs",
            "5",
        ],
    )
    assert r.exit_code == 1
    assert "banana" in r.output.lower()
    # a friendly error line, not a Python traceback
    assert "Traceback" not in r.output


def test_missing_openai_key_fails_friendly_not_traceback(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    scen = _scenarios_dir(tmp_path, ("a.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
            "--scenarios-dir",
            scen,
            "--runs",
            "5",
            "--store-dir",
            str(tmp_path / ".modelpin"),
        ],
    )
    assert r.exit_code == 1
    assert "OPENAI_API_KEY is not set" in r.output
    assert "Traceback" not in r.output
    # preflight failed before any baseline file was written
    assert not (tmp_path / ".modelpin").exists()


def test_malformed_config_fails_friendly(tmp_path):
    bad = tmp_path / "modelpin.yaml"
    bad.write_text("models: [unclosed\n  : :")
    scen = _scenarios_dir(tmp_path, ("a.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "fake",
            "--model",
            "m",
            "--config",
            str(bad),
            "--scenarios-dir",
            scen,
        ],
    )
    # Branch-agnostic: the YAML may fail to parse OR be classified non-mapping by a
    # future PyYAML; either way the contract is a friendly error, not a traceback.
    assert r.exit_code == 1
    assert "Traceback" not in r.output
    assert "error:" in r.output


def test_malformed_scenario_fails_friendly(tmp_path):
    scen = _scenarios_dir(tmp_path, ("broken.json", "{ not json"))
    r = runner.invoke(
        app,
        ["baseline", "--provider", "fake", "--model", "m", "--scenarios-dir", scen, "--runs", "5"],
    )
    assert r.exit_code == 1
    assert "broken.json" in "".join(r.output.split())
    assert "Traceback" not in r.output


def test_check_warns_about_skipped_scenarios(tmp_path):
    store = str(tmp_path / "store")
    base_scen = _scenarios_dir(tmp_path, ("alpha.json", _SCENARIO_A))
    # baseline covers only greet_alpha
    rb = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "fake",
            "--model",
            "baseModel",
            "--scenarios-dir",
            base_scen,
            "--store-dir",
            store,
            "--runs",
            "5",
        ],
    )
    assert rb.exit_code == 0, rb.output

    # check sees both greet_alpha and greet_bravo; greet_bravo has no baseline and must
    # be flagged in the skipped note, not hidden.
    full = tmp_path / "scenarios"
    (full / "bravo.json").write_text(_SCENARIO_B)
    rc = runner.invoke(
        app,
        [
            "check",
            "--to",
            "candModel",
            "--from",
            "baseModel",
            "--provider",
            "fake",
            "--scenarios-dir",
            str(full),
            "--store-dir",
            store,
            "--runs",
            "5",
        ],
    )
    assert rc.exit_code == 0, rc.output
    # Parse the actual skipped-note line and assert the specific id — so this test
    # genuinely fails if item 13 (skip transparency) ever regresses.
    note = next(
        (ln for ln in rc.output.splitlines() if "had no baseline and were skipped" in ln), None
    )
    assert note is not None, rc.output
    assert "greet_bravo" in note
    assert "greet_alpha" not in note


def test_init_scaffolds_openai_not_anthropic_stub(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    cfg = (tmp_path / "modelpin.yaml").read_text()
    assert "openai" in cfg
    assert "anthropic" not in cfg
