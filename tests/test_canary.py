"""The public daily canary (`scripts/canary.py`), offline, with a scripted `modelpin`.

Pinned here: a model is baselined once and checked against itself every day after; a partial
baseline is never kept; a failed or unmeasured run is never published as "no change"; a model
without its key is skipped, not reported; a second run on one day replaces that day's row; the
page escapes everything it prints and ranks nothing (ADR-0009); and the workflow can only run
from this repository, on a schedule or by hand, with the key passed through env.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("canary", ROOT / "scripts" / "canary.py")
assert _SPEC and _SPEC.loader
canary = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = canary
_SPEC.loader.exec_module(canary)

WORKFLOW = ROOT / ".github" / "workflows" / "canary.yml"
_BANNED = re.compile(
    r"(?i)\b(better|worse|best|beats|wins|loses|superior|inferior|upgrade|downgrade)\b"
)


class FakeModelpin:
    """Plays `modelpin baseline` / `modelpin check`: writes what the real one would leave in
    the store, and exits with the scripted code."""

    def __init__(self, *, baseline_rc: int = 0, check_rc: int = 0, verdicts=("unchanged",) * 8):
        self.baseline_rc = baseline_rc
        self.check_rc = check_rc
        self.verdicts = verdicts
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        store = Path(argv[argv.index("--store-dir") + 1])
        if "baseline" in argv:
            # A partial baseline is still written on exit 3, as the real command does.
            (store / "baseline-m.json").write_text("{}", encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.baseline_rc, "", "")
        if self.check_rc in (0, 1, 3):
            (store / "runs").mkdir(exist_ok=True)
            record = {
                "modelpin_version": "0.5.1",
                "exit_code": self.check_rc,
                "results": [{"verdict": v} for v in self.verdicts],
            }
            (store / "runs" / "check-x.json").write_text(json.dumps(record), encoding="utf-8")
            (store / "last-report.md").write_text("# report\n", encoding="utf-8")
            (store / "migration-report.md").write_text("# plain\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.check_rc, "", "")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(canary, "MODELS", (("openai/gpt-oss-20b", "groq", "GROQ_API_KEY"),))
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    suite = tmp_path / "suite"
    suite.mkdir()
    return tmp_path / "site", suite


def _run(site: Path, suite: Path, fake: FakeModelpin, date: str = "2026-10-05") -> int:
    rc: int = canary.main(
        ["--site", str(site), "--suite", str(suite), "--exe", "modelpin", "--date", date],
        runner=fake,
    )
    return rc


def _results(site: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (site / "canary" / "results.json").read_text(encoding="utf-8")
    )
    return data


def test_the_first_run_baselines_then_checks_the_model_against_itself(env) -> None:
    site, suite = env
    fake = FakeModelpin()
    assert _run(site, suite, fake) == 0
    assert [c[1] for c in fake.calls] == ["baseline", "check"]
    check = fake.calls[1]
    assert (
        check[check.index("--from") + 1] == check[check.index("--to") + 1] == "openai/gpt-oss-20b"
    )
    data = _results(site)
    assert data["baselines"] == {"openai/gpt-oss-20b": "2026-10-05"}
    (row,) = data["runs"]
    assert row["result"] == "no change" and row["counts"] == {"unchanged": 8}
    assert row["baseline_recorded_today"] is True and row["modelpin_version"] == "0.5.1"
    assert row["report"] == "canary/reports/2026-10-05/openai-gpt-oss-20b.md"
    assert row["plain_report"] == "canary/reports/2026-10-05/openai-gpt-oss-20b-plain.md"
    assert (site / row["report"]).read_text(encoding="utf-8") == "# report\n"
    assert (site / ".nojekyll").exists() and (site / "index.html").exists()


def test_later_runs_reuse_the_first_baseline(env) -> None:
    site, suite = env
    _run(site, suite, FakeModelpin(), date="2026-10-05")
    fake = FakeModelpin()
    _run(site, suite, fake, date="2026-10-06")
    assert [c[1] for c in fake.calls] == ["check"]
    rows = _results(site)["runs"]
    assert [r["date"] for r in rows] == ["2026-10-05", "2026-10-06"]
    assert rows[1]["baseline_date"] == "2026-10-05"
    assert rows[1]["baseline_recorded_today"] is False


def test_a_partial_baseline_is_dropped_and_nothing_is_checked(env) -> None:
    site, suite = env
    fake = FakeModelpin(baseline_rc=3)
    assert _run(site, suite, fake) == 1  # nothing measured: the job goes red
    assert [c[1] for c in fake.calls] == ["baseline"]
    assert not list((site / "canary" / "store").rglob("baseline-*.json"))
    (row,) = _results(site)["runs"]
    assert row["result"] == "could not run" and row["report"] is None
    assert _results(site)["baselines"] == {}


@pytest.mark.parametrize(
    "rc, word", [(1, "change flagged"), (3, "could not measure"), (4, "could not run")]
)
def test_every_exit_code_maps_to_its_own_word_and_never_to_no_change(env, rc, word) -> None:
    site, suite = env
    _run(site, suite, FakeModelpin(check_rc=rc, verdicts=("regression", "unchanged")))
    (row,) = _results(site)["runs"]
    assert row["result"] == word and row["exit_code"] == rc
    if rc == 4:
        assert row["counts"] == {} and row["report"] is None


def test_a_model_without_its_key_is_skipped_not_reported(env, monkeypatch) -> None:
    site, suite = env
    monkeypatch.delenv("GROQ_API_KEY")
    fake = FakeModelpin()
    assert _run(site, suite, fake) == 1
    assert fake.calls == [] and _results(site)["runs"] == []


def test_a_second_run_on_one_day_replaces_that_days_row(env) -> None:
    site, suite = env
    _run(site, suite, FakeModelpin(check_rc=4))
    _run(site, suite, FakeModelpin(check_rc=0))
    (row,) = _results(site)["runs"]
    assert row["result"] == "no change"


def test_the_default_launch_runs_one_call_at_a_time(env) -> None:
    site, suite = env
    fake = FakeModelpin()
    canary.main(["--site", str(site), "--suite", str(suite)], runner=fake)
    assert fake.calls[0][:3] == [sys.executable, "-c", canary.PACED]
    assert "DEFAULT_WORKERS = 1" in canary.PACED


def test_the_page_escapes_what_it_prints_links_reports_and_ranks_nothing() -> None:
    hostile = "<script>alert(1)</script>"
    results = {
        "baselines": {},
        "runs": [
            {
                "date": "2026-10-05",
                "model": hostile,
                "provider": "groq",
                "result": "change flagged",
                "counts": {"regression": 1, "unchanged": 7},
                "baseline_date": "2026-10-04",
                "report": "canary/reports/2026-10-05/x.md",
                "plain_report": None,
                "modelpin_version": "0.5.1",
            }
        ],
    }
    page = canary.render_page(results, repo="o/r", generated="2026-10-05 05:40 UTC")
    assert hostile not in page and "&lt;script&gt;" in page
    assert "1 changed, 7 no change" in page
    assert 'href="https://github.com/o/r/blob/gh-pages/canary/reports/2026-10-05/x.md"' in page
    assert "Not compared: meaning" in page
    assert not _BANNED.search(page)


def test_an_empty_history_renders_a_page_that_says_so() -> None:
    page = canary.render_page({"runs": []}, repo="o/r", generated="now")
    assert "No runs yet." in page and "<title>Modelpin daily canary</title>" in page


def test_the_workflow_runs_only_here_on_a_schedule_or_by_hand() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*schedule:\s*$", text) and "workflow_dispatch:" in text
    for trigger in ("pull_request", "pull_request_target", "push:"):
        assert trigger not in text, trigger
    assert "github.repository == 'samarthputhraya/Modelpin'" in text
    assert re.search(r"(?m)^permissions:\s*\n\s+contents: write\s*(#.*)?$", text)
    assert "GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}" in text
    # The key reaches the script through env only, never interpolated into a command.
    run_lines = [ln for ln in text.splitlines() if "run:" in ln or ln.startswith("          ")]
    assert not any("secrets." in ln and "run:" in ln for ln in run_lines)
