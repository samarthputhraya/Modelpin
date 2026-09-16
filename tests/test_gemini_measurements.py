"""The Gemini section of `docs/fp-measurement.md` is re-derived from its committed artifacts.

Offline (ADR-0006): reads `reports/judge-agreement/2026-09-15/` and
`reports/fp-runs-gemini/2026-09-15/` through the same helpers the harness prints with.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import fp_aggregate as agg  # noqa: E402
from scripts import judge_agreement as ja  # noqa: E402
from scripts.fp_measurement import upper_bound_95  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "fp-measurement.md"
PUBLISHED = REPO / "reports" / "fp-runs-adr0040" / "2026-09-07"
REJUDGED = REPO / "reports" / "judge-agreement" / "2026-09-15"
FRESH = REPO / "reports" / "fp-runs-gemini" / "2026-09-15"
SURFACES = ["s0-suite-gpt-4o-mini", "s1-arg-gpt-4.1-mini", "s2a-fp-suite-gpt-4.1-mini",
            "s2b-fp-suite-gpt-4o-mini"]  # fmt: skip


def _section() -> str:
    doc = DOC.read_text(encoding="utf-8")
    start = doc.index("## Gemini as judge")
    return " ".join(doc[start : doc.index("\n## ", start + 1)].split())


@pytest.fixture(scope="module")
def agreement() -> list[dict]:
    if not REJUDGED.is_dir():
        pytest.skip("artifacts pruned from the sdist")
    return [
        ja.summarise(
            str(PUBLISHED / f"{name}-adr0040.jsonl"), str(REJUDGED / f"{name}-gemini35.jsonl")
        )
        for name in SURFACES
    ]


def test_the_judge_agreement_numbers_are_the_artifacts(agreement):
    n = sum(s["compare"]["n"] for s in agreement)
    alarms = sum(s["compare"]["alarm_agree"] for s in agreement)
    verdicts = sum(s["compare"]["verdict_agree"] for s in agreement)
    assert all(not s["compare"]["replay_mismatch"] for s in agreement), "not the same traces"
    text = _section()
    assert f"Alarm agreement (flag / no flag): {alarms} / {n} paired trials." in text
    assert f"Verdict agreement: {verdicts} / {n}." in text
    disagreeing = sorted(
        d["key"].split(":", 1)[1] for s in agreement for d in s["compare"]["disagreements"]
    )
    assert disagreeing == ["arg_enum_phrasing", "arg_list_order", "arg_optional_fields"]


def test_the_false_alarm_bound_under_the_gemini_judge_is_the_artifacts(agreement):
    b = [s["bound"]["b"] for s in agreement]
    scored, reached, alarms = (sum(x[k] for x in b) for k in ("scored", "trials", "false_alarms"))
    assert alarms == 0
    text = _section()
    assert f"0 in {scored} scored trials" in text
    assert f"**{100 * upper_bound_95(0, scored):.1f}%**" in text
    assert (
        f"0 in {reached} trials that reached a verdict (upper bound **{100 * upper_bound_95(0, reached):.2f}%**)"
        in text
    )


def test_the_fresh_gemini_replay_numbers_are_the_artifacts():
    paths = sorted(FRESH.glob("*.jsonl"))
    if not paths:
        pytest.skip("artifacts pruned from the sdist")
    pooled = agg.summarise([str(p) for p in paths])["pooled"]
    text = _section()
    row = (
        f"| pooled, both models | {pooled['reached_verdict']} | {pooled['scored']} | "
        f"**{pooled['false_positives']}** | {100 * pooled['conditional_ub']:.1f}% | "
        f"{100 * pooled['unconditional_ub']:.1f}% |"
    )
    assert row in text
    r = pooled["recall"]
    assert f"**Detection: {r['detected']} / {r['checked']}**" in text
    assert (
        f"**{100 * (1 - upper_bound_95(r['checked'] - r['detected'], r['checked'])):.1f}%**" in text
    )
    assert f"{pooled['no_effect']} of the {pooled['attempted']} trials could not have fired" in text
    exposure = pooled["severity"]["exposed_by_channel"]
    assert exposure["tool"] == 0 and "tool channel had zero exposure" in text
