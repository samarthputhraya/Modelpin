"""`docs/live-validation.md` is re-derived from the campaign artifact it cites.

Offline (ADR-0006): reads `reports/live-validation/2026-09-15/summary.json`, which records every
job of the live Gemini campaign with its exit code, verdicts and explanations (no traces).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "live-validation.md"
SUMMARY = REPO / "reports" / "live-validation" / "2026-09-15" / "summary.json"


@pytest.fixture(scope="module")
def summary() -> dict:
    if not SUMMARY.is_file():
        pytest.skip("campaign artifact pruned from the sdist")
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> str:
    return " ".join(DOC.read_text(encoding="utf-8").split())


def _tally(jobs: list[dict]) -> tuple[Counter, dict[tuple[str, str], Counter]]:
    total: Counter = Counter()
    per: dict[tuple[str, str], Counter] = {}
    for job in jobs:
        pair = (job["from"], job["to"])
        counts = per.setdefault(pair, Counter())
        for sid, verdict in job["verdicts"].items():
            n = job["verdicts"][sid] if sid.startswith("_") else 1
            key = "unchanged" if sid.startswith("_") else verdict
            counts[key] += n
            total[key] += n
    return total, per


def test_the_same_model_table_is_the_artifacts(summary, doc):
    total, per = _tally(summary["null_jobs"])
    assert total["regression"] == 0 and total["changed_minor"] == 0
    trials = sum(total.values())
    assert f"**0 false alarms in {trials} same-model scenario-checks**" in doc
    assert f"| **total** | | **{trials}** | **0** | **0** |" in doc
    for (a, b), counts in per.items():
        row = f"| `{a}` | `{b}` | {sum(counts.values())} | {counts['regression']} | {counts['changed_minor']} |"
        assert row in doc, row


def test_the_migration_table_and_the_withheld_count_are_the_artifacts(summary, doc):
    jobs = summary["migration_jobs"]
    total, per = _tally(jobs)
    trials = sum(total.values())
    assert (
        f"| **total** | | **{trials}** | **{total['regression']}** | **{total['changed_minor']}** |"
        in doc
    )
    for (a, b), counts in per.items():
        row = f"| `{a}` | `{b}` | {sum(counts.values())} | {counts['regression']} | {counts['changed_minor']} |"
        assert row in doc, row
    withheld = sum(
        1 for j in jobs for why in j["explanations"].values() if why.startswith("not confirmed")
    )
    assert withheld and f"**{withheld} further scenarios were flagged on their first sample" in doc


def test_the_reviewers_verdict_counts_are_the_artifacts(summary, doc):
    review = summary["review"]
    scored = {
        v: [r for r in review if r["verdict"] == v and r.get("material") is not None]
        for v in ("regression", "changed_minor")
    }
    regs, minors = scored["regression"], scored["changed_minor"]
    mat_regs = sum(1 for r in regs if r["material"])
    mat_minors = sum(1 for r in minors if r["material"])
    assert f"**{mat_regs} of {len(regs)} regressions were rated material**" in doc
    assert f"and {mat_minors} of {len(minors)} `changed_minor` flags" in doc
    assert f'Treat "{mat_regs} of {len(regs)}" as' in doc
    # Every reviewed flag really is a flag the campaign raised.
    flagged = {
        (j["from"], j["to"], j["suite"], sid)
        for j in summary["migration_jobs"]
        for sid, v in j["verdicts"].items()
        if not sid.startswith("_") and v != "unchanged"
    }
    assert {(r["from"], r["to"], r["suite"], r["scenario"]) for r in review} == flagged


def test_every_job_ran_and_the_exit_codes_are_the_documented_ones(summary):
    for job in summary["null_jobs"] + summary["migration_jobs"]:
        assert job["baseline_exit"] == 0, job["name"]
        assert job["check_exit"] in (0, 1, 3), job["name"]
        regressed = any(
            v == "regression" for k, v in job["verdicts"].items() if not k.startswith("_")
        )
        if job["check_exit"] == 0:
            assert not regressed, job["name"]
        if regressed:
            assert job["check_exit"] == 1, job["name"]
