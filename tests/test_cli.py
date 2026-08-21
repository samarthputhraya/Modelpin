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
from modelpin.models import Trace
from modelpin.providers import ProviderError

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
    assert r.exit_code == 1
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
    assert set(sidecar) == {"meta", "results"}
    assert sidecar["meta"]["suite_hash"].startswith("sha256:")
    assert sidecar["meta"]["candidate_model"] == DEMO_TO
    assert len(sidecar["results"]) == len(list(Path(SCEN).glob("*.json")))
    assert any(r["verdict"] == "regression" for r in sidecar["results"])


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


def test_report_runs_the_default_public_suite(tmp_path):
    # No fixtures: the fake provider returns identical placeholder traces for both models,
    # so the run is all-unchanged. This exercises loading + hashing the real public suite,
    # and the --match plumbing + judge-off labeling end to end.
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
    assert "sha256:ffd99774f681" in md  # the committed public suite's pinned hash
    assert "`unordered`" in md  # --match was threaded into the settings block
    assert "`disabled`" in md  # fake provider -> no judge -> labeled disabled


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
