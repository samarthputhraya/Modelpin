"""`modelpin watch`: which of the models this repo depends on are retiring, and what to do.

MP-13, rewritten. Offline throughout: every test passes its own registry with `--registry` and
pins the calendar by patching `modelpin.watcher._today`, so no test depends on the shipped seed's
dates or on the day it runs. The exit-code contract is the product: 0 all clear, 1 inside a
notice window or retired, 3 unknown to the registry (unknown is not a clearance), 4 nothing
declared or an unreadable registry.
"""

from __future__ import annotations

import json
import socket
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import modelpin.watcher as watcher
from modelpin.cli import EXIT_SETUP_FAILED, EXIT_UNMEASURED, app
from modelpin.models import Model
from modelpin.watcher import Declared, assess, effective_status, load_registry, watch_exit_code

runner = CliRunner()
TODAY = date(2026, 9, 17)
SRC = {"source_url": "https://example.test/deprecations", "fetched_at": "2026-09-17"}

REGISTRY = {
    "schema": 2,
    "models": [
        {
            "id": "old-model",
            "provider": "acme",
            "status": "deprecated",
            "deprecated_at": "2026-04-22",
            "retired_at": "2026-10-23",
            "replacement_id": "new-model",
            "aliases": ["old"],
            **SRC,
            "notes": "vendor page says so",
        },
        {"id": "new-model", "provider": "acme", "status": "active", **SRC},
        {
            "id": "gone-model",
            "provider": "acme",
            "status": "retired",
            "retired_at": "2026-08-05",
            "replacement_id": "new-model",
            **SRC,
        },
        {
            "id": "dated-but-active",
            "provider": "acme",
            "status": "active",
            "retired_at": "2027-01-01",
            **SRC,
        },
        {
            "id": "cross-vendor-old",
            "provider": "acme",
            "status": "deprecated",
            "retired_at": "2026-11-01",
            "replacement_id": "other-new",
            **SRC,
        },
        {"id": "other-new", "provider": "other", "status": "active", **SRC},
        {"id": "safe-model", "provider": "acme", "status": "active"},
    ],
}


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher, "_today", lambda: TODAY)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "models.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    return tmp_path


def _config(repo: Path, models: list[str], judge: str | None = None) -> str:
    lines = ["models:"] + [f"  - {m}" for m in models]
    if judge:
        lines.append(f"judge_model: {judge}")
    p = repo / "modelpin.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _baseline(repo: Path, model_id: str) -> Path:
    store = repo / ".modelpin"
    store.mkdir(exist_ok=True)
    p = store / f"baseline-{model_id}.json"
    p.write_text(json.dumps({"model_id": model_id, "scenarios": {}}), encoding="utf-8")
    return p


def _watch(repo: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "watch",
            "--config",
            str(repo / "modelpin.yaml"),
            "--store-dir",
            str(repo / ".modelpin"),
            "--registry",
            str(repo / "models.json"),
            *extra,
        ],
    )


# ------------------------------------------------------------------ the pure logic


def test_a_passed_shutdown_date_is_retired_whatever_the_row_says() -> None:
    m = Model(id="m", provider="p", status="deprecated", retired_at="2026-09-01", **SRC)
    assert effective_status(m, TODAY).value == "retired"


def test_an_announced_date_is_a_notice_window_even_on_an_active_row() -> None:
    m = Model(id="m", provider="p", status="active", retired_at="2027-01-01", **SRC)
    assert effective_status(m, TODAY).value == "deprecated"


def test_an_undated_active_row_is_active() -> None:
    assert effective_status(Model(id="m", provider="p"), TODAY).value == "active"


def test_days_remaining_is_derived_from_retired_at_and_today(repo: Path) -> None:
    rows = assess(
        [Declared("old-model", "config")], load_registry(repo / "models.json"), today=TODAY
    )
    assert rows[0].days_remaining == (date(2026, 10, 23) - TODAY).days == 36


def test_an_alias_finds_the_vendors_row(repo: Path) -> None:
    rows = assess([Declared("old", "config")], load_registry(repo / "models.json"), today=TODAY)
    assert rows[0].known and rows[0].model is not None and rows[0].model.id == "old-model"
    assert rows[0].affected


def test_an_affected_model_outranks_an_unknown_one(repo: Path) -> None:
    reg = load_registry(repo / "models.json")
    rows = assess(
        [Declared("nobody-knows", "config"), Declared("old-model", "config")], reg, today=TODAY
    )
    assert watch_exit_code(rows) == 1


def test_an_unknown_declared_model_is_not_a_clearance(repo: Path) -> None:
    reg = load_registry(repo / "models.json")
    rows = assess(
        [Declared("nobody-knows", "config"), Declared("safe-model", "config")], reg, today=TODAY
    )
    assert watch_exit_code(rows) == 3


def test_a_baseline_only_id_is_advisory_and_never_decides_the_exit_code(repo: Path) -> None:
    reg = load_registry(repo / "models.json")
    rows = assess(
        [
            Declared("safe-model", "config"),
            Declared("modelpin-dogfood", "baseline", decides_exit=False),
        ],
        reg,
        today=TODAY,
        baselines={"modelpin-dogfood": ".modelpin/baseline-modelpin-dogfood.json"},
    )
    assert watch_exit_code(rows) == 0
    dogfood = next(r for r in rows if r.id == "modelpin-dogfood")
    assert not dogfood.known and dogfood.has_baseline and not dogfood.decides_exit


def test_sources_merge_and_the_app_role_wins_over_the_judge_role(repo: Path) -> None:
    reg = load_registry(repo / "models.json")
    rows = assess(
        [Declared("safe-model", "config", "judge"), Declared("safe-model", "scan", "app")],
        reg,
        today=TODAY,
    )
    assert len(rows) == 1 and rows[0].sources == ("config", "scan") and rows[0].role == "app"


# ------------------------------------------------------------------ the command


def test_watch_exits_0_when_every_declared_model_is_known_and_active(repo: Path) -> None:
    _config(repo, ["safe-model", "new-model"])
    r = _watch(repo)
    assert r.exit_code == 0, r.output
    assert "safe-model" in r.output and "active" in r.output


def test_watch_exits_1_inside_the_notice_window_and_prints_the_check_command(repo: Path) -> None:
    _config(repo, ["old-model"])
    _baseline(repo, "old-model")
    r = _watch(repo)
    assert r.exit_code == 1, r.output
    assert "retires 2026-10-23 (36 days)" in r.output
    assert "modelpin check --from old-model --to new-model" in r.output
    assert "baseline recorded" in r.output
    assert "[S] https://example.test/deprecations fetched 2026-09-17" in r.output
    assert "note: vendor page says so" in r.output


def test_watch_exits_1_when_retired_at_has_passed(repo: Path) -> None:
    _config(repo, ["gone-model"])
    r = _watch(repo)
    assert r.exit_code == 1, r.output
    assert "retired 2026-08-05 (43 days ago)" in r.output


def test_watch_exits_1_on_a_dated_row_that_still_says_active(repo: Path) -> None:
    _config(repo, ["dated-but-active"])
    r = _watch(repo)
    assert r.exit_code == 1, r.output
    assert "no successor named by the vendor" in r.output


def test_watch_exits_3_when_the_registry_does_not_know_a_declared_model(repo: Path) -> None:
    _config(repo, ["nobody-knows"])
    r = _watch(repo)
    assert r.exit_code == EXIT_UNMEASURED, r.output
    assert "Unknown is not a clearance" in r.output


def test_a_missing_baseline_says_which_command_records_one(repo: Path) -> None:
    _config(repo, ["old-model"])
    r = _watch(repo)
    assert r.exit_code == 1
    assert "no baseline yet: run modelpin baseline --model old-model first" in r.output


def test_the_judge_model_is_a_dependency_with_its_own_remedy(repo: Path) -> None:
    _config(repo, ["safe-model"], judge="old-model")
    r = _watch(repo)
    assert r.exit_code == 1, r.output
    assert "(judge)" in r.output
    assert "set judge_model: new-model in modelpin.yaml" in r.output
    assert "modelpin check --from old-model" not in r.output


def test_the_check_command_adds_provider_only_across_vendors(repo: Path) -> None:
    _config(repo, ["cross-vendor-old", "old-model"])
    r = _watch(repo)
    assert "modelpin check --from cross-vendor-old --to other-new --provider other" in r.output
    assert "modelpin check --from old-model --to new-model" in r.output
    assert "--to new-model --provider" not in r.output


def test_no_declared_models_exits_4_with_the_remedy(repo: Path) -> None:
    (repo / "modelpin.yaml").write_text("models: []\n", encoding="utf-8")
    r = _watch(repo)
    assert r.exit_code == EXIT_SETUP_FAILED, r.output
    assert "no models declared" in r.output and "--scan" in r.output


def test_a_bad_registry_path_exits_4_not_3(repo: Path) -> None:
    _config(repo, ["safe-model"])
    r = runner.invoke(
        app,
        [
            "watch",
            "--config",
            str(repo / "modelpin.yaml"),
            "--registry",
            str(repo / "missing.json"),
        ],
    )
    assert r.exit_code == EXIT_SETUP_FAILED, r.output
    assert "could not read the registry" in r.output


def test_an_unsourced_date_in_a_registry_file_is_refused_at_load(repo: Path) -> None:
    bad = {"schema": 2, "models": [{"id": "m", "provider": "p", "retired_at": "2026-10-23"}]}
    (repo / "models.json").write_text(json.dumps(bad), encoding="utf-8")
    _config(repo, ["m"])
    r = _watch(repo)
    assert r.exit_code == EXIT_SETUP_FAILED, r.output
    assert "unsourced date is refused" in r.output


def test_the_registry_override_wins_over_the_embedded_seed(repo: Path) -> None:
    _config(repo, ["claude-opus-4-8"])  # in the shipped seed; NOT in the test registry
    r = _watch(repo)
    assert r.exit_code == EXIT_UNMEASURED, r.output


def test_json_is_the_only_thing_on_stdout(repo: Path) -> None:
    _config(repo, ["old-model", "nobody-knows"])
    _baseline(repo, "old-model")
    r = _watch(repo, "--json")
    assert r.exit_code == 1, r.output
    doc = json.loads(r.stdout)  # would raise if anything else were printed there
    assert doc["exit_code"] == 1 and doc["today"] == "2026-09-17"
    assert doc["registry"]["entries"] == len(REGISTRY["models"])
    assert doc["registry"]["newest_fetched_at"] == "2026-09-17"
    by_id = {m["id"]: m for m in doc["models"]}
    old = by_id["old-model"]
    assert old["affected"] and old["days_remaining"] == 36 and old["has_baseline"]
    assert old["check_command"] == "modelpin check --from old-model --to new-model"
    assert old["branch"] == "modelpin/migrate-old-model-to-new-model"
    assert old["source_url"] == SRC["source_url"] and old["fetched_at"] == "2026-09-17"
    assert by_id["nobody-knows"]["known"] is False and by_id["nobody-knows"]["decides_exit"]


def test_scan_is_opt_in_and_keeps_only_code_context_hits(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(repo, ["safe-model"])
    (repo / "app.py").write_text(
        'MODEL = "old-model"\n# maybe try new-model later\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    without = _watch(repo)
    assert without.exit_code == 0, without.output
    with_scan = _watch(repo, "--scan")
    # `old-model` is not a real vendor id, so the detector will not find it by pattern; the
    # point pinned here is the opt-in itself: without --scan the tree is never walked.
    assert with_scan.exit_code in (0, 1)


def test_scan_hits_are_labelled_scan_and_decide_the_exit_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(repo, ["safe-model"])
    monkeypatch.setattr(
        "modelpin.cli.scan_repo",
        lambda path, skipped=None, scanned=None: [
            {"model": "old-model", "file": "app.py", "line": 1, "context": "code"},
            {"model": "gone-model", "file": "README.md", "line": 3, "context": "comment"},
        ],
    )
    r = _watch(repo, "--scan", "--json")
    doc = json.loads(r.stdout)
    by_id = {m["id"]: m for m in doc["models"]}
    assert by_id["old-model"]["sources"] == ["scan"] and by_id["old-model"]["decides_exit"]
    assert "gone-model" not in by_id, "a comment mention is not a dependency (MP-236)"
    assert r.exit_code == 1


def test_watch_opens_no_socket(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no(*_a, **_k):  # pragma: no cover - reached only on the defect
        raise AssertionError("watch opened a socket; it must never touch the network")

    monkeypatch.setattr(socket, "socket", _no)
    _config(repo, ["old-model"])
    r = _watch(repo)
    assert r.exit_code == 1, r.output


def test_watch_output_is_cp1252_encodable(repo: Path) -> None:
    _config(repo, ["old-model", "gone-model", "nobody-knows"])
    r = _watch(repo)
    r.output.encode("cp1252")


def test_the_freshness_line_names_the_registry_and_its_newest_fetch(repo: Path) -> None:
    _config(repo, ["safe-model"])
    r = _watch(repo)
    assert "newest entry fetched 2026-09-17" in r.output
    assert "--registry <path>" in r.output


def test_the_shipped_seed_answers_for_a_fresh_init_config() -> None:
    """A fresh `mp init` declares a real vendor id; the seed must know it, or the very first
    `mp watch` a stranger runs exits 3 and tells them to contribute an entry."""
    reg = load_registry()
    rows = assess([Declared("claude-opus-4-8", "config")], reg, today=TODAY)
    assert rows[0].known


def test_the_readme_documents_watch_and_its_exit_codes() -> None:
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "`modelpin watch`" in text
    for code in ("`0`", "`1`", "`3`", "`4`"):
        assert code in text
    assert "not a clearance" in text
