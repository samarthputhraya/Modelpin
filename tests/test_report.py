import json
import re

from modelpin.models import DiffResult, DiffSignals, DiffVerdict
from modelpin.report import (
    ReportMeta,
    render_cli,
    render_pr_comment,
    render_report_md,
    to_report_sidecar,
)

#: Comparative-quality words a public report must never emit about a model (spec section 9).
_BANNED = re.compile(
    r"(?i)\b(better|worse|best|beats|wins|loses|superior|inferior|upgrade|downgrade)\b"
)


def _r(sid, verdict, expl="x", conf=0.9):
    return DiffResult(
        scenario_id=sid,
        from_model="a",
        to_model="b",
        verdict=verdict,
        explanation=expl,
        confidence=conf,
    )


def test_pr_comment_groups_buckets_with_spec_emoji():
    results = [
        _r("reg1", DiffVerdict.regression, "tool-call changed"),
        _r("min1", DiffVerdict.changed_minor, "format drift"),
        _r("ok1", DiffVerdict.unchanged, "no change"),
    ]
    md = render_pr_comment(results, "claude-opus-4-6", "claude-opus-4-7", 5)
    assert "REGRESSIONS (1)" in md
    assert "MINOR CHANGES (1)" in md
    assert "UNCHANGED (1)" in md
    assert "❌" in md and "⚠️" in md and "✅" in md
    assert "claude-opus-4-6" in md and "claude-opus-4-7" in md
    assert "confidence 0.90" in md
    assert "Pin to" in md


def test_pr_comment_is_clean_when_nothing_changed():
    results = [_r("ok1", DiffVerdict.unchanged), _r("ok2", DiffVerdict.unchanged)]
    md = render_pr_comment(results, "a", "b", 3)
    assert "REGRESSIONS" not in md
    assert "safe to adopt" in md


def test_pr_comment_header_is_calm_when_unchanged():
    # The dogfood surfaced this: an all-unchanged result must NOT lead with 🚨 "model change
    # detected" and then say "safe to adopt" — that's contradictory/alarmist.
    md = render_pr_comment([_r("ok1", DiffVerdict.unchanged)], "a", "b", 3)
    assert md.startswith("✅")
    assert "no behavioral change" in md
    assert "🚨" not in md  # never alarmist when nothing regressed


def test_pr_comment_header_alarms_only_on_regression():
    md = render_pr_comment([_r("reg1", DiffVerdict.regression, "boom")], "a", "b", 5)
    assert md.startswith("🚨")
    assert "behavioral regression" in md


def test_pr_comment_header_warns_on_minor_only():
    md = render_pr_comment([_r("min1", DiffVerdict.changed_minor, "format drift")], "a", "b", 5)
    assert md.startswith("⚠️")
    assert "minor changes" in md
    assert "🚨" not in md


def test_render_cli_lists_changed_scenarios():
    out = render_cli([_r("reg1", DiffVerdict.regression, "boom")], "a", "b", 5)
    assert "reg1" in out and "boom" in out


# --- public Modelpin Report (render_report_md / to_report_sidecar) ---------------------


def _meta(**overrides):
    base = dict(
        suite_id="modelpin-public-v1",
        suite_version="1.0.0",
        suite_hash="sha256:813ed928284b",
        suite_path="examples/report-suite",
        candidate_model="gpt-4.1",
        reference_model="gpt-4o",
        provider="openai",
        runs=5,
        judge_model="gpt-4o-mini",
        match_mode="strict",
        modelpin_version="0.1.1",
        diff_thresholds={
            "alpha": 0.05,
            "min_tool_tvd": 0.5,
            "min_refusal_delta": 0.34,
            "min_semantic_delta": 0.5,
        },
        date_iso="2026-06-24",
        reproduce_cmd=(
            "modelpin report --to gpt-4.1 --from gpt-4o --provider openai "
            "--runs 5 --match strict --suite-dir examples/report-suite"
        ),
        scenario_ids=["s1", "s2"],
        skipped=[],
    )
    base.update(overrides)
    return ReportMeta(**base)


def test_report_md_reproducibility_block_present():
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    for token in (
        "modelpin-public-v1",
        "1.0.0",
        "sha256:813ed928284b",
        "gpt-4.1",
        "gpt-4o",
        "openai",
        "gpt-4o-mini",
        "0.05",
        "modelpin 0.1.1",
        "2026-06-24",
    ):
        assert token in md, token
    assert "| Runs per scenario | 5 |" in md


def test_report_md_uses_measurement_framing_and_no_banned_words():
    results = [
        _r("s1", DiffVerdict.regression, "tool-call behavior changed: dropped issue_refund"),
        _r("s2", DiffVerdict.unchanged, "no statistically significant behavior change"),
    ]
    md = render_report_md(results, _meta())
    assert "we observed" in md
    hit = _BANNED.search(md)
    assert hit is None, f"banned comparative-quality word leaked: {hit and hit.group(0)}"


def test_report_md_regression_shows_alarm_glyph():
    md = render_report_md([_r("s1", DiffVerdict.regression, "boom")], _meta())
    assert "🚨" in md
    assert "Behavioral regressions found" in md


def test_report_md_unchanged_is_calm():
    md = render_report_md(
        [_r("s1", DiffVerdict.unchanged), _r("s2", DiffVerdict.unchanged)], _meta()
    )
    assert "🚨" not in md
    assert "No behavioral change observed" in md


def test_report_md_skipped_scenarios_surfaced():
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta(skipped=["flaky_scn"]))
    assert "Skipped scenarios" in md
    assert "flaky_scn" in md


def test_report_md_same_model_is_baseline_framing():
    md = render_report_md(
        [_r("s1", DiffVerdict.unchanged)], _meta(candidate_model="m", reference_model="m")
    )
    assert "baseline characterization of `m`" in md
    assert "vs `" not in md  # no comparison frame when from == to


def test_report_md_has_all_required_sections():
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    for heading in (
        "## Settings",
        "## Methodology",
        "## Per-scenario results",
        "## Limitations & framing",
        "## Reproduce this report",
    ):
        assert heading in md, heading


def test_report_md_table_reads_diff_signals():
    sig = DiffSignals(
        tool_call_match=0.0,
        refusal_delta=0.5,
        semantic_score=0.6,
        latency_delta_ms=12.0,
        token_delta=-3,
    )
    r = DiffResult(
        scenario_id="s1",
        from_model="gpt-4o",
        to_model="gpt-4.1",
        verdict=DiffVerdict.regression,
        explanation="dropped a tool call",
        confidence=0.97,
        signals=sig,
    )
    md = render_report_md([r], _meta())
    assert "0.00" in md  # tool match
    assert "+0.50" in md  # refusal delta
    assert "60%" in md  # semantic equivalence
    assert "dropped a tool call" in md


def test_report_md_semantic_dash_when_judge_off():
    # signals.semantic_score is None when no judge ran -> the cell shows an em dash, not 0%.
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    assert "—" in md


def test_to_report_sidecar_is_json_serializable():
    results = [_r("s1", DiffVerdict.regression, "boom"), _r("s2", DiffVerdict.unchanged)]
    payload = to_report_sidecar(results, _meta())
    text = json.dumps(payload)  # must not raise
    assert set(payload) == {"meta", "results"}
    assert len(payload["results"]) == 2
    assert payload["meta"]["suite_hash"] == "sha256:813ed928284b"
    assert "gpt-4.1" in text
