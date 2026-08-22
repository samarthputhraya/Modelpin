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


def _fixtures_for(tmp_path, name, pairs):
    """Write canned traces for explicit (scenario_id, model_id) pairs.

    MP-28: `--provider fake` no longer fabricates a trace for a key it does not hold, so a
    test that means "this scenario really was replayed" has to say which pairs exist.
    """
    import json as _json

    path = tmp_path / name
    path.write_text(
        _json.dumps(
            [{"scenario_id": sid, "model_id": mid, "final_output": "ok"} for sid, mid in pairs]
        ),
        encoding="utf-8",
    )
    return str(path)


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
    # greet_bravo deliberately gets NO fixture: it is skipped for want of a baseline
    # (cli.py) before replay is ever reached, which is the behavior under test.
    fx = _fixtures_for(
        tmp_path,
        "fx.json",
        [("greet_alpha", "baseModel"), ("greet_alpha", "candModel")],
    )
    # baseline covers only greet_alpha
    rb = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "fake",
            "--fixtures",
            fx,
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
            "--fixtures",
            fx,
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


# --- MP-28: a run that measured nothing must never report `unchanged` -------------------


def test_check_fails_when_the_fixtures_miss_the_target_model(tmp_path):
    """The verbatim MP-28 reproduction, promoted into CI.

    Fixtures keyed to one model, run targeting another: every key missed, both sides got
    the same fabricated trace, and the CLI printed `OK 1 scenario(s) unchanged` + exit 0.
    """
    store = str(tmp_path / "store")
    scen = _scenarios_dir(tmp_path, ("alpha.json", _SCENARIO_A))
    fx = _fixtures_for(
        tmp_path, "fx.json", [("greet_alpha", "baseModel"), ("greet_alpha", "candModel")]
    )
    rb = runner.invoke(
        app,
        # fmt: off
        ["baseline", "--provider", "fake", "--fixtures", fx, "--model", "baseModel",
         "--scenarios-dir", scen, "--store-dir", store, "--runs", "5"],
        # fmt: on
    )
    assert rb.exit_code == 0, rb.output

    rc = runner.invoke(
        app,
        # fmt: off
        ["check", "--to", "gpt-4o-mini", "--from", "baseModel", "--provider", "fake",
         "--fixtures", fx, "--scenarios-dir", scen, "--store-dir", store, "--runs", "5"],
        # fmt: on
    )
    assert rc.exit_code == 1, rc.output
    # The VERDICT line is what must never appear. ("unchanged" alone would match the
    # error's own explanation of why it refuses to invent a trace.)
    assert "scenario(s) unchanged" not in rc.output, rc.output
    assert "Traceback" not in rc.output
    assert "gpt-4o-mini" in rc.output  # names the model that had no coverage
    assert "candModel" in rc.output  # ...and the ones that did


def test_baseline_fails_when_fixtures_are_omitted_entirely(tmp_path):
    """The widened surface: correct model ids, `--fixtures` simply forgotten.

    Measured likelier than a typo'd model id, and it poisoned the stored baseline with
    fabricated traces -- so this must fail BEFORE anything is written to the store.
    """
    store = tmp_path / "store"
    scen = _scenarios_dir(tmp_path, ("alpha.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        # fmt: off
        ["baseline", "--provider", "fake", "--model", "baseModel",
         "--scenarios-dir", scen, "--store-dir", str(store), "--runs", "5"],
        # fmt: on
    )
    assert r.exit_code == 1, r.output
    assert "fixtures" in r.output.lower()
    assert "Traceback" not in r.output
    assert not list(store.glob("baseline-*.json")), "a run that measured nothing was stored"


def test_report_writes_no_file_when_nothing_could_be_replayed(tmp_path):
    """The claims-artifact canary.

    `report()` exits 0 by design, so the exit code cannot protect this path. A published
    Modelpin Report asserting "no behavioral change" from a run that measured nothing is
    the north-star failure in its most public form -- so no document may be written.
    """
    out = tmp_path / "reports"
    scen = _scenarios_dir(tmp_path, ("alpha.json", _SCENARIO_A))
    r = runner.invoke(
        app,
        # fmt: off
        ["report", "--to", "b", "--from", "a", "--provider", "fake",
         "--suite-dir", scen, "--runs", "5", "--output-dir", str(out)],
        # fmt: on
    )
    assert r.exit_code == 1, r.output
    assert not list(out.glob("*.md")), "published a Report from a run that measured nothing"
    assert not list(out.glob("*.json"))
