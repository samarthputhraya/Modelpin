import json
import re
from pathlib import Path

import pytest

from modelpin.models import DiffResult, DiffSignals, DiffVerdict
from modelpin.report import (
    ChannelCensus,
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


def test_pr_comment_neutralizes_markdown_injection():
    # `explanation` is assembled from MODEL-controlled tool names, and scenario_id is
    # user-controlled. A crafted value must not inject Markdown structure (fake headers/lists)
    # or an HTML comment (the sticky comment is found by `<!-- modelpin-report -->`) into the
    # comment posted to GitHub.
    evil = "tool-call changed\n## PWNED\n<!-- modelpin-report -->\n- [x] injected"
    md = render_pr_comment([_r("scn\n# hijack", DiffVerdict.regression, evil)], "a", "b", 5)
    out_lines = md.split("\n")
    # newlines collapsed -> no injected line can START a Markdown block
    assert not any(ln.lstrip().startswith("## PWNED") for ln in out_lines)
    assert not any(ln.strip() == "- [x] injected" for ln in out_lines)
    assert not any(ln.lstrip().startswith("# hijack") for ln in out_lines)
    # the sticky-comment marker cannot be forged from model/user content
    assert "<!-- modelpin-report -->" not in md
    # content is preserved inline (neutralized, not silently dropped)
    assert "PWNED" in md


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
            "min_tool_arg_tvd": 1.0,
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


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({}, id="no-census"),
        pytest.param({"census": ChannelCensus(False, False, False)}, id="fully-inert"),
        pytest.param({"census": ChannelCensus(True, True, True)}, id="fully-armed"),
        pytest.param({"census": ChannelCensus(False, True, False)}, id="assertions-only"),
        pytest.param({"underpowered": ["s1", "s2"]}, id="fully-underpowered"),
        pytest.param(
            {"census": ChannelCensus(False, False, False), "underpowered": ["s1"]},
            id="both-axes",
        ),
    ],
)
def test_report_md_uses_measurement_framing_and_no_banned_words(overrides):
    """ADR-0009's Consequences make this guard the enforcement mechanism for the whole
    document, so it must run over every SHAPE the document can take.

    `[M] 2026-08-31` claims-auditor: it did not. The bare ``_meta()`` sets no census and no
    ``underpowered``, so MP-140's entire coverage block and both new `❔` headline branches
    were unreachable from this test -- the prose most likely to editorialise was the prose
    the banned-words guard could not see. (It was clean; that was luck, not coverage.)
    """
    results = [
        _r("s1", DiffVerdict.regression, "tool-call behavior changed: dropped issue_refund"),
        _r("s2", DiffVerdict.unchanged, "no statistically significant behavior change"),
    ]
    md = render_report_md(results, _meta(**overrides))
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


def _scenario_row(md, scenario_id="s1"):
    """The per-scenario table row, split into cells and KEYED BY HEADER NAME.

    `[M] 2026-08-26` The first draft of these tests asserted a bare substring, `"| 1.00 | — |"`.
    That pair matches (Arg match, Refusal Δ) just as happily as (Tool match, Arg match), so
    mutating the Arg cell to render `None` as `1.00` -- the exact ADR-0018 violation the test
    names -- left the whole suite green. A positional assertion is not a column assertion.
    """
    rows = [line for line in md.splitlines() if line.startswith("|")]
    header_line = next(line for line in rows if line.startswith("| Scenario |"))
    sep_line = rows[rows.index(header_line) + 1]
    row_line = next(line for line in rows if line.startswith(f"| {scenario_id} |"))
    cells = lambda line: [c.strip() for c in line.strip("|").split("|")]  # noqa: E731
    header, sep, row = cells(header_line), cells(sep_line), cells(row_line)
    assert len(row) == len(header), f"row has {len(row)} cells, header has {len(header)}"
    assert len(sep) == len(header), f"separator has {len(sep)} cells, header has {len(header)}"
    return dict(zip(header, row))


def test_report_md_table_renders_the_argument_sub_signal():
    """`tool_arg_match` was computed and displayed NOWHERE (MP-112 / ADR-0029 decision 5).
    An advisory-only signal whose number is invisible is advice the reader cannot audit."""
    sig = DiffSignals(tool_call_match=0.0, tool_arg_match=0.25)
    r = DiffResult(
        scenario_id="s1",
        from_model="gpt-4o",
        to_model="gpt-4.1",
        verdict=DiffVerdict.changed_minor,
        explanation="tool-call arguments changed: search(dropped limit)",
        confidence=0.99,
        signals=sig,
    )
    row = _scenario_row(render_report_md([r], _meta()))
    assert "Arg match" in row, f"the argument sub-signal has no column: {list(row)}"
    assert row["Arg match"] == "0.25", row
    assert row["Tool match"] == "0.00", row


def test_report_md_argument_column_is_a_dash_when_the_gate_did_not_run():
    """`None` means NOT MEASURED (ADR-0018), never 1.0 -- the same distinction the field's own
    docstring draws. A `1.00` here would be a positive claim of sameness nobody measured."""
    sig = DiffSignals(tool_call_match=1.0, tool_arg_match=None)
    r = DiffResult(
        scenario_id="s1",
        from_model="gpt-4o",
        to_model="gpt-4.1",
        verdict=DiffVerdict.unchanged,
        explanation="no statistically significant behavior change",
        confidence=1.0,
        signals=sig,
    )
    row = _scenario_row(render_report_md([r], _meta()))
    assert row["Arg match"] == "—", f"not-measured rendered as {row['Arg match']!r}"
    assert row["Tool match"] == "1.00", row


def test_report_md_table_columns_line_up_for_every_verdict():
    """Header, separator and row width are three unlinked string literals. A separator one
    `---` short leaves the suite green and stops GitHub rendering the table as a table."""
    for verdict in DiffVerdict:
        md = render_report_md(
            [
                DiffResult(
                    scenario_id="s1",
                    from_model="a",
                    to_model="b",
                    verdict=verdict,
                    explanation="x",
                    confidence=0.5,
                    signals=DiffSignals(tool_call_match=1.0, tool_arg_match=1.0),
                )
            ],
            _meta(),
        )
        row = _scenario_row(md)  # raises on any width mismatch
        assert "Arg match" in row, verdict


def test_report_methodology_calls_the_argument_signal_advisory():
    """ADR-0029: the published method must not imply arguments can fail a build."""
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    assert "tool-call ARGUMENT match" in md
    assert "advisory" in md
    assert "five behavioral signals" in md


def test_report_md_semantic_dash_when_judge_off():
    # signals.semantic_score is None when no judge ran -> the cell shows an em dash, not 0%.
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    assert "—" in md


def test_to_report_sidecar_is_json_serializable():
    results = [_r("s1", DiffVerdict.regression, "boom"), _r("s2", DiffVerdict.unchanged)]
    payload = to_report_sidecar(results, _meta())
    text = json.dumps(payload)  # must not raise
    # MP-140 added `coverage`. Pinned as an exact set on purpose: the sidecar is a published
    # audit artifact, so a key appearing or vanishing is a contract change and must be a
    # deliberate edit here rather than something a reader discovers in a file.
    assert set(payload) == {"meta", "results", "coverage"}
    assert len(payload["results"]) == 2
    assert payload["meta"]["suite_hash"] == "sha256:813ed928284b"
    assert "gpt-4.1" in text


def test_a_report_with_no_census_asserts_nothing_about_coverage():
    """MP-140. ``census=None`` means no census was TAKEN, which is not the same claim as
    "every channel was live" -- and the difference is the whole point of the field. A
    Report rendered by a caller that never measured coverage (or re-rendered from a sidecar
    written before the field existed) must stay silent rather than imply full coverage.

    `[M]` The precedent is `_report_settings`, which omits a threshold it does not have
    rather than defaulting one: a fabricated number in a reproducibility block is worse
    than an absent one.
    """
    meta = _meta()  # no census, no underpowered
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], meta)
    assert "## Coverage" not in md
    assert "✅ **No behavioral change observed.**" in md  # unqualified, because unmeasured
    coverage = to_report_sidecar([_r("s1", DiffVerdict.unchanged)], meta)["coverage"]
    assert coverage["channels_live"] is None, "null means 'not measured', [] means 'none live'"
    assert coverage["channels_inert"] is None
    assert coverage["underpowered_scenarios"] == []


def test_the_coverage_block_carries_the_alpha_it_was_priced_against():
    """A blind-run-count disclosure that does not name the threshold it failed to reach is
    the unmarked number CLAUDE.md's evidence vocabulary treats as an assumption."""
    meta = _meta(underpowered=["s1"])
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], meta)
    assert "## Coverage" in md
    assert f"p ≤ {meta.diff_thresholds['alpha']}" in md


def test_report_publishes_every_floor_that_gated_a_verdict(tmp_path):
    """ADR-0009. `[M] 2026-08-26` The Report said "five behavioral signals" beside a Settings
    row listing four floors, so a reader was handed an `Arg match` number and a
    `changed_minor` verdict with no way to tell which threshold produced them."""
    from modelpin.diff import ALPHA, MIN_TOOL_ARG_TVD, MIN_TOOL_TVD

    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    settings = md[md.index("## Settings (reproducibility)") :]
    for token in (f"α={ALPHA}", f"tool-TVD≥{MIN_TOOL_TVD}", f"arg-TVD≥{MIN_TOOL_ARG_TVD}"):
        assert token in settings, f"{token!r} missing from the reproducibility block"
    assert "advisory" in settings, "the argument floor must be marked advisory (ADR-0029)"


def test_report_renders_a_sidecar_written_before_the_argument_floor_existed():
    """A Report re-rendered from a 0.1.2 sidecar has four threshold keys. Omit the fifth --
    never default it, because a fabricated floor in a reproducibility block is worse than an
    absent one."""
    meta = _meta(
        diff_thresholds={
            "alpha": 0.05,
            "min_tool_tvd": 0.5,
            "min_refusal_delta": 0.34,
            "min_semantic_delta": 0.5,
        }
    )
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], meta)
    assert "arg-TVD" not in md
    assert "α=0.05" in md


def test_the_public_report_never_cites_a_document_the_reader_cannot_open():
    """`ops/` is gitignored and has zero tracked files. `[M] 2026-08-26` a draft of the
    Methodology paragraph sent readers of a PUBLIC report to "(ADR-0029)" for the promotion
    condition. Every prior ADR reference in shipped code is a `#` comment, never rendered text.
    """
    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    assert not re.search(r"ADR-\d{4}", md), re.findall(r".{60}ADR-\d{4}.{60}", md)


def test_every_effect_size_floor_in_the_engine_reaches_the_published_report():
    """The renderer reads whatever `cli.py` assembles, and the fixture above is hand-written,
    so a NEW floor can ship gating verdicts while no public Report ever names it -- which is
    exactly what happened to `MIN_TOOL_ARG_TVD`. Pin the wiring, not the fixture."""
    import modelpin.diff as diff_mod

    floors = {n for n in dir(diff_mod) if n.startswith("MIN_") and n != "MIN_RUNS"}
    assert floors, "no effect-size floors found; this guard has gone blind"

    src = (Path(__file__).resolve().parent.parent / "modelpin" / "cli.py").read_text(
        encoding="utf-8"
    )
    block = src[src.index("diff_thresholds={") : src.index("diff_thresholds={") + 900]
    block = block[: block.index("},") + 2]
    missing = sorted(f for f in floors if f not in block)
    assert not missing, (
        f"{missing} gate verdicts but never reach `diff_thresholds`, so no public Report "
        f"publishes them. ADR-0009 requires the settings a reader would need to re-run."
    )


def test_a_minors_only_run_still_carries_the_recommendation_and_never_reads_as_cleared():
    """ADR-0029 decision 4: the cap de-escalates, it does not withhold. `[M] 2026-08-26`
    narrowing `if regs or minors:` to `if regs:` on either surface left the suite green --
    and a minors-only run then fell through to the affirmative clearance text under a WARN
    header. The advisory cap makes minors-only runs strictly more common, so both surfaces
    are pinned here."""
    results = [_r("m1", DiffVerdict.changed_minor, "tool-call arguments changed: f(a 1->2)")]

    pr = render_pr_comment(results, "gpt-4o", "gpt-4.1", 5)
    assert "MINOR CHANGES (1)" in pr
    assert "Pin to" in pr, pr
    assert not _BANNED.search(pr), pr

    cli = render_cli(results, "gpt-4o", "gpt-4.1", 5)
    assert "Pin to" in cli, cli

    # The public Report has no "Pin to" line (that is the PR/CLI surface), but the minor must
    # still appear in its table with its explanation intact.
    md = render_report_md(results, _meta())
    assert "changed_minor" in md, md
    assert "arguments changed" in md, md
