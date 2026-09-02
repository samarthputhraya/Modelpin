"""End-to-end CLI tests. The `check` flow runs fully offline through FakeProvider
with the bundled demo fixtures — no network, no API keys — and must produce the
PR-style report and a CI-failing exit code on a real regression.
"""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import _report_basename, app
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin import cli
from modelpin.models import ToolCall, Trace
from modelpin.providers import ProviderError
from modelpin.scenarios import load_scenarios

REPO = Path(__file__).resolve().parents[1]
REPORT_SUITE = str(REPO / "examples" / "report-suite")

# The offline fixtures come from `mp init --demo`, not from a checked-in copy under
# examples/. One source of truth: if the demo a new user runs ever breaks, these tests break
# with it -- which is the only coupling between the two that cannot silently rot.
_DEMO_ROOT = Path(tempfile.mkdtemp(prefix="modelpin-cli-demo-"))
write_demo(_DEMO_ROOT)
_DEMO = _DEMO_ROOT / DEMO_DIRNAME
FIXTURES = str(_DEMO / DEMO_FIXTURES)
SCEN = str(_DEMO / "scenarios")
CONFIG = str(_DEMO / "modelpin.yaml")

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "modelpin" in r.output


def test_scan_detects_the_example_dependency():
    r = runner.invoke(app, ["scan", str(REPO / "examples")])
    assert r.exit_code == 0
    assert "claude-opus-4-6" in r.output


def test_init_scaffolds_config_and_scenarios(tmp_path):
    r = runner.invoke(app, ["init", str(tmp_path)])
    assert r.exit_code == 0
    assert (tmp_path / "modelpin.yaml").exists()
    assert (tmp_path / "scenarios").is_dir()
    assert list((tmp_path / "scenarios").glob("*.json"))


def test_end_to_end_check_detects_regressions_offline(tmp_path):
    store = str(tmp_path / ".modelpin")
    base = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,
            "--model",
            DEMO_FROM,
            "--scenarios-dir",
            SCEN,
            "--config",
            CONFIG,
            "--store-dir",
            store,
            "--runs",
            "5",
        ],
    )
    assert base.exit_code == 0, base.output
    assert "Baseline recorded" in base.output

    chk = runner.invoke(
        app,
        [
            "check",
            "--to",
            DEMO_TO,
            "--from",
            DEMO_FROM,
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,
            "--scenarios-dir",
            SCEN,
            "--config",
            CONFIG,
            "--store-dir",
            store,
            "--runs",
            "5",
        ],
    )
    # two scenarios regress (tool-call + refusal) -> CI-failing exit code
    assert chk.exit_code == 1, chk.output
    assert "refund_request" in chk.output
    assert "angry_customer" in chk.output

    report = (tmp_path / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    assert "REGRESSIONS" in report
    assert "invoice_parse" in report  # the format-drift minor change


def test_check_without_baseline_fails_clearly(tmp_path):
    r = runner.invoke(
        app,
        [
            "check",
            "--to",
            "x",
            "--from",
            "y",
            "--provider",
            "fake",
            "--scenarios-dir",
            SCEN,
            "--config",
            CONFIG,
            "--store-dir",
            str(tmp_path / "empty"),
        ],
    )
    # MP-160 / ADR-0018. EXIT_UNMEASURED, not 1. `[M] 2026-09-01` with no usable baseline the
    # run compares NOTHING, and exit 1 made `action.yml` take its else-branch and annotate the
    # PR "Modelpin detected a behavioral regression" over a run that never called a model.
    # Both codes still fail CI (`action.yml` gates on != 0); only the false claim is gone.
    assert r.exit_code == cli.EXIT_UNMEASURED
    assert "baseline" in r.output.lower()


def test_check_rejects_unknown_match_mode():
    # An unknown --match must fail fast with a friendly error, not silently behave like
    # 'strict' (which previously let subset/superset reach the engine unvalidated).
    r = runner.invoke(
        app,
        [
            "check",
            "--to",
            "x",
            "--from",
            "y",
            "--provider",
            "fake",
            "--match",
            "bogus",
            "--config",
            CONFIG,
        ],
    )
    assert r.exit_code == 1
    assert "match" in r.output.lower()


# --- mp report (public Modelpin Report) ------------------------------------------------


def test_report_exits_zero_even_on_regression(tmp_path):
    # Same fixtures the check test uses (a real tool-call + refusal regression), but report()
    # PUBLISHES findings — it must exit 0, unlike check() which exits 1 to gate CI.
    out = tmp_path / "reports"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            DEMO_TO,
            "--from",
            DEMO_FROM,
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,
            "--suite-dir",
            SCEN,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    mds = list(out.glob("*.md"))
    jsons = list(out.glob("*.json"))
    assert mds and jsons, r.output
    md = mds[0].read_text(encoding="utf-8")
    assert "Modelpin Report" in md
    assert "sha256:" in md
    assert DEMO_TO in md and DEMO_FROM in md
    assert "we observed" in md
    # A REAL regression must be present (not just the word in boilerplate): the alarm glyph
    # fires and the regressing scenario appears in the table.
    assert "🚨" in md
    assert "refund_request" in md
    assert "worse" not in md.lower()  # measurement framing, never a quality verdict

    # The JSON sidecar is the machine-readable audit artifact — validate its structure.
    sidecar = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert set(sidecar) == {"meta", "results", "coverage"}  # MP-140 added `coverage`
    assert sidecar["meta"]["suite_hash"].startswith("sha256:")
    assert sidecar["meta"]["candidate_model"] == DEMO_TO
    assert len(sidecar["results"]) == len(list(Path(SCEN).glob("*.json")))
    assert any(r["verdict"] == "regression" for r in sidecar["results"])
    # MP-140: a flagged verdict is auditable only alongside what could have fired at all.
    # This suite declares `tools`, so the trajectory channel is live and recorded as such.
    assert "tool trajectory" in sidecar["coverage"]["channels_live"]
    assert sidecar["coverage"]["underpowered_scenarios"] == []


def test_report_same_model_runs_and_exits_zero(tmp_path):
    out = tmp_path / "reports"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            DEMO_FROM,
            "--from",
            DEMO_FROM,
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,
            "--suite-dir",
            SCEN,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    assert "baseline characterization" in md


def _suite_fixtures(path: Path, *, regress: str | None = None) -> str:
    """Write real canned traces covering every public-suite scenario on both models.

    Before MP-28 this test passed `--provider fake` with NO fixtures and let the adapter
    fabricate a placeholder for all 28 keys -- so its all-unchanged result was an artifact
    of measuring nothing, not a verdict. The fixtures are explicit now: identical on both
    sides by default, so `unchanged` is earned; `regress` flips one scenario's candidate
    side to a refusal so the same plumbing can be shown reporting a real regression.
    """
    records = []
    for scenario in load_scenarios(REPORT_SUITE):
        # MP-159. The census reads TRACES now, so a scenario that declares a tool must be
        # shown calling it -- which is what a real run of these three scenarios does. Left
        # empty, the whole public suite would be content-blind here and the report would
        # (correctly) withhold the green headline this test is about. Identical on both
        # sides, so the trajectory itself is unchanged and `unchanged` is still earned.
        # `[M]` 3 of the 14 public scenarios declare `tools`, all as bare strings.
        declared = [t for t in (scenario.input.get("tools") or []) if isinstance(t, str)]
        for model in ("model-y", "model-x"):
            refused = scenario.id == regress and model == "model-x"
            records.append(
                Trace(
                    scenario_id=scenario.id,
                    model_id=model,
                    final_output="I can't help with that." if refused else "ok",
                    refused=refused,
                    tool_calls=[ToolCall(name=declared[0])] if declared else [],
                ).model_dump(mode="json")
            )
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def test_report_renders_the_public_suite_provenance(tmp_path):
    """Suite identity, pinned hash, --match plumbing and judge-off labeling, end to end.

    None of these assertions depends on the verdicts -- they render from the manifest and
    the suite hash -- so they are unchanged from the pre-MP-28 version of this test.
    """
    out = tmp_path / "reports"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            "model-x",
            "--from",
            "model-y",
            "--provider",
            "fake",
            "--fixtures",
            _suite_fixtures(tmp_path / "suite-fixtures.json"),
            "--suite-dir",
            REPORT_SUITE,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--match",
            "unordered",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    assert "modelpin-public-v2" in md
    assert "sha256:5cba1dc8b691" in md  # the committed public suite's pinned hash (v3.0.0, MP-147)
    assert "`unordered`" in md  # --match was threaded into the settings block
    assert "`disabled`" in md  # fake provider -> no judge -> labeled disabled
    assert "No behavioral change observed" in md  # earned: identical traces on both sides


def test_report_surfaces_a_real_regression_on_the_public_suite(tmp_path):
    """The false-negative canary for the published-report path.

    MP-28's failure was a Report asserting no behavioral change from a run that measured
    nothing. Same command, same suite, one scenario genuinely refusing on the candidate:
    the document must NOT be able to say "no behavioral change observed".
    """
    out = tmp_path / "reports"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            "model-x",
            "--from",
            "model-y",
            "--provider",
            "fake",
            "--fixtures",
            _suite_fixtures(tmp_path / "regressed.json", regress="borderline_access"),
            "--suite-dir",
            REPORT_SUITE,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    assert "borderline_access" in md
    assert "regression" in md.lower()
    assert "No behavioral change observed" not in md


def _fake_replay_factory(*, raise_on):
    """A stand-in for cli.replay that yields canned traces but raises ProviderError for one
    scenario id, exercising report()'s per-scenario skip-and-continue path."""

    def _fake_replay(scenario, model_id, adapter, runs=5):
        if scenario.id == raise_on:
            raise ProviderError(f"simulated provider failure on {scenario.id}")
        return [
            Trace(scenario_id=scenario.id, model_id=model_id, run_idx=i, final_output="ok")
            for i in range(runs)
        ]

    return _fake_replay


def test_report_skips_failing_scenario_and_still_publishes(tmp_path, monkeypatch):
    monkeypatch.setattr("modelpin.cli.replay", _fake_replay_factory(raise_on="angry_customer"))
    out = tmp_path / "reports"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            "b",
            "--from",
            "a",
            "--provider",
            "fake",
            # replay is monkeypatched, so the adapter is never asked for a trace; the
            # fixtures only satisfy preflight, which now refuses a zero-coverage fake run.
            "--fixtures",
            FIXTURES,
            "--suite-dir",
            SCEN,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output  # one failing scenario must not sink the report
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    assert "Skipped scenarios" in md
    assert "angry_customer" in md
    sidecar = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    assert "angry_customer" in sidecar["meta"]["skipped"]
    assert all(r["scenario_id"] != "angry_customer" for r in sidecar["results"])


def test_report_all_scenarios_failing_exits_one(tmp_path, monkeypatch):
    def _always_fail(scenario, model_id, adapter, runs=5):
        raise ProviderError("simulated total failure")

    monkeypatch.setattr("modelpin.cli.replay", _always_fail)
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            "b",
            "--from",
            "a",
            "--provider",
            "fake",
            "--fixtures",
            FIXTURES,  # preflight only; replay is monkeypatched to always fail
            "--suite-dir",
            SCEN,
            "--config",
            CONFIG,
            "--runs",
            "5",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert r.exit_code == 1
    assert "nothing to report" in r.output.lower()


def test_report_basename_distinguishes_same_and_cross_model():
    assert _report_basename("gpt-4.1", "gpt-4o", "2026-06-24") == (
        "modelpin-report-gpt-4.1-vs-gpt-4o-2026-06-24"
    )
    assert _report_basename("gpt-4o", "gpt-4o", "2026-06-24") == (
        "modelpin-report-gpt-4o-2026-06-24"
    )
    # model ids with path-unsafe chars are slugged
    assert "/" not in _report_basename("openai/gpt-4.1", "openai/gpt-4o", "2026-06-24")


def test_report_missing_suite_fails_clearly(tmp_path):
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            "a",
            "--from",
            "b",
            "--provider",
            "fake",
            "--suite-dir",
            str(tmp_path / "nope"),
            "--config",
            CONFIG,
        ],
    )
    assert r.exit_code == 1
    assert "scenarios" in r.output.lower()


def test_init_scaffolds_when_the_dir_holds_only_reserved_files(tmp_path):
    """A scenarios dir containing only `manifest.json` was a no-exit loop.

    `_dir_state()` correctly reported "only reserved files and no scenario files" and
    advised `modelpin init` -- but `init()`'s own glob was not reserved-aware, so it wrote
    nothing, and re-running the failing command reproduced the byte-identical error forever.
    The error path and the fix path have to agree on what "has scenarios" means.
    """
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "manifest.json").write_text("{}", encoding="utf-8")

    r = runner.invoke(app, ["init", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert (
        scen / "greeting.json"
    ).is_file(), f"init wrote no starter scenario, so the advice loops.\n{r.output}"

    # ...and the command that sent the user here now gets past the scenarios check.
    # It still exits 1 -- `--provider fake` with no fixtures cannot measure anything
    # (MP-28) -- but the error must have MOVED ON to the replay stage. Asserting the new
    # error names fixtures is what proves the advice loop is broken; the old
    # `exit_code == 0` conflated "got past the scenarios check" with "the run succeeded",
    # and only reached green because the adapter fabricated traces.
    after = runner.invoke(
        app,
        [
            "baseline",
            "--provider",
            "fake",
            "--model",
            "x",
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--scenarios-dir",
            str(scen),
            "--store-dir",
            str(tmp_path / ".modelpin"),
        ],
    )
    assert "no scenarios found" not in after.output
    assert "fixtures" in after.output.lower(), after.output


# --- ADR-0029: an advisory verdict must not fail the build -------------------------------
#
# `[M] 2026-08-26` mutation testing: widening `cli.py`'s exit gate from `regression` to
# `regression or changed_minor` left the ENTIRE suite green. That gate is the boundary where a
# build actually goes red, and it is the sole reason the argument-gate assertions in
# `tests/test_tool_arguments.py` were allowed to weaken from `regression` to `changed_minor`.
# It is now pinned at the CLI, offline, on an argument-only change.


def _arg_only_sandbox(tmp_path):
    """A one-scenario suite whose ONLY difference between the two models is a tool ARGUMENT.

    Same tool name every run, same output text, no refusal, no assertion drift -- so the
    argument channel is the only signal that can fire, and the verdict it produces is the
    verdict under test.
    """
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "refund_amount.json").write_text(
        json.dumps(
            {
                "id": "refund_amount",
                "name": "Refund the right amount",
                "kind": "agent",
                "input": {
                    "messages": [{"role": "user", "content": "Refund order 123 for $49.99."}],
                    "tools": ["issue_refund"],
                },
                "assertions": {"expected_tool_calls": ["issue_refund"], "must_contain": ["refund"]},
            }
        ),
        encoding="utf-8",
    )
    fixtures = tmp_path / "traces.json"
    fixtures.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "refund_amount",
                    "model_id": model,
                    "tool_calls": [{"name": "issue_refund", "arguments": {"amount": amount}}],
                    "final_output": "Your refund has been issued.",
                    "tokens_out": 40,
                    "latency_ms": 500.0,
                }
                for model, amount in (("arg-model-v1", 49.99), ("arg-model-v2", 4999.00))
            ]
        ),
        encoding="utf-8",
    )
    config = tmp_path / "modelpin.yaml"
    config.write_text(
        "models:\n  - arg-model-v1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    return str(scen_dir), str(fixtures), str(config), str(tmp_path / ".modelpin")


def test_an_argument_only_change_is_reported_but_does_not_fail_the_build(tmp_path):
    """ADR-0029 decision 1 + 4, at the boundary that decides a red X."""
    scen, fixtures, config, store = _arg_only_sandbox(tmp_path)
    common = [
        "--provider",
        "fake",
        "--fixtures",
        fixtures,
        "--scenarios-dir",
        scen,
        "--config",
        config,
        "--store-dir",
        store,
        "--runs",
        "5",
    ]
    base = runner.invoke(app, ["baseline", "--model", "arg-model-v1", *common])
    assert base.exit_code == 0, base.output

    chk = runner.invoke(app, ["check", "--to", "arg-model-v2", "--from", "arg-model-v1", *common])

    # The finding is DISCLOSED ...
    assert "refund_amount" in chk.output, chk.output
    report = (tmp_path / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    assert "arguments changed" in report, report
    assert "MINOR" in report, report
    assert "Pin to" in report, "an advisory verdict must still carry the recommendation"
    # ... and the build stays GREEN. `action.yml` gates the red X on this exit code.
    assert chk.exit_code == 0, (
        f"an argument-only change exited {chk.exit_code}; ADR-0029 caps the argument signal at "
        f"changed_minor precisely so it cannot fail a stranger's build.\n{chk.output}"
    )
