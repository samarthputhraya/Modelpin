"""MP-208: the cross-judge table in `docs/fp-measurement.md` is re-derived from its artifacts.

Why this exists. `[M] 2026-09-07` a claims review found that the page's own anti-drift promise —
*"the tables below are regenerated from those files by a test, so this page cannot drift from the
run"* — had acquired a silent exception: `tests/test_fp_run_of_record.py` is scoped entirely to
`reports/fp-runs/`, and the new cross-judge table sits under that sentence pinned by nothing. A
page that claims every table is checked, while one is not, is worse than a page that claims
nothing: the reader's trust is calibrated to the claim.

Every number the section publishes is asserted here against
`reports/judge-agreement/2026-09-07/`, offline, with no key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import fp_aggregate as agg  # noqa: E402
from scripts import judge_agreement as ja  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "fp-measurement.md"
AGREE_DIR = REPO / "reports" / "judge-agreement" / "2026-09-07"
SOURCE = REPO / "reports" / "fp-runs" / "2026-09-07" / "s2b-fp-suite-gpt-4o-mini.jsonl"
REJUDGE = AGREE_DIR / "rejudge-s2b-groq-gpt-oss-120b.jsonl"


@pytest.fixture(scope="module")
def summary() -> dict:
    return ja.summarise(str(SOURCE), str(REJUDGE))


@pytest.fixture(scope="module")
def section() -> str:
    doc = DOC.read_text(encoding="utf-8")
    start = doc.index("## Cross-judge agreement")
    return doc[start : doc.index("\n## ", start + 1) if "\n## " in doc[start + 1 :] else len(doc)]


def test_the_published_agreement_numbers_are_the_artifacts_numbers(summary, section):
    c = summary["compare"]
    assert c["n"] == 24
    assert f"**{c['n']} paired trials**" in section
    assert f"**Verdict agreement: {c['verdict_agree']} / {c['n']} = 100%**" in section
    pct = 100.0 * c["semantic_identical"] / c["n"]
    assert f"{c['semantic_identical']} / {c['n']} = {pct:.1f}%" in section


def test_the_published_table_is_the_per_judge_bound_the_script_computes(summary, section):
    a, b = summary["bound"]["a"], summary["bound"]["b"]
    assert (a["scored"], b["scored"]) == (5, 1), "the judges disagreed on the DENOMINATOR"
    assert a["false_alarms"] == b["false_alarms"] == 0
    assert f"**0 / {a['scored']}** (ub {a['upper_bound_scored']}%)" in section
    assert f"**0 / {b['scored']}** (ub {b['upper_bound_scored']}%)" in section
    assert f"0 / {a['trials']} (ub {a['upper_bound_reached']}%)" in section
    assert f"| could not have fired | {a['could_not_fire']} | {b['could_not_fire']} |" in section
    assert (
        f"{a['scored']} scored trials vs {b['scored']}" in section
    ), "the denominator finding must be stated, not left to the table"


def test_the_page_names_the_judges_the_artifacts_name(summary, section):
    """`[M]` The source artifact records no `judge_provider` — it predates MP-208 — so the page
    must label its host as inferred, exactly as the tool does. ADR-0037 D1: an inference, never
    an assertion."""
    assert summary["a"]["judge"] == "gpt-4.1-mini @ openai (unrecorded; inferred)"
    assert summary["b"]["judge"] == "openai/gpt-oss-120b @ groq"
    assert "`gpt-4.1-mini` @ openai (unrecorded; inferred)" in section
    assert "`openai/gpt-oss-120b` @ groq" in section


def test_the_scored_trial_rich_caveat_is_the_real_comparison(section):
    """The page concedes that its 24-trial prefix scored a higher share than its parent arm.
    That concession is a number, so it is checked like one: a caveat that quietly drifts is
    indistinguishable from no caveat."""
    parent = agg.summarise([str(SOURCE)])["surfaces"][0]
    fp_scored, fp_trials = parent["scored"], parent["reached_verdict"]
    assert (fp_scored, fp_trials) == (28, 240)
    assert f"5/24 = 20.8% vs {fp_scored}/{fp_trials} = {100.0 * fp_scored / fp_trials:.1f}%)" in (
        section
    )


def test_the_rejudged_artifact_is_not_in_the_run_of_record_directory():
    """Physical separation, not just a code guard. `[M]` A rejudged file in `reports/fp-runs/`
    would be pooled by the run-of-record aggregation and double-count every trial it holds."""
    assert REJUDGE.exists()
    assert not list((REPO / "reports" / "fp-runs").rglob("rejudge-*.jsonl"))


def test_every_artifact_in_the_agreement_directory_is_referenced_by_the_page(section):
    for path in sorted(AGREE_DIR.glob("*.jsonl")):
        assert path.parent.name in section and "judge-agreement" in section, path.name


def test_the_aggregator_still_refuses_the_committed_rejudged_artifact():
    """The guard that makes the separation above enforceable rather than conventional."""
    with pytest.raises(SystemExit, match="double-count"):
        agg.summarise([str(SOURCE), str(REJUDGE)])
