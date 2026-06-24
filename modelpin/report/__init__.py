"""Reporter — render DiffResults as a Markdown PR comment and a CLI summary.

Matches the target UX in spec section 7. Framing stays measurement/opinion
("we replayed your scenarios and observed…"), never a bare "model X is worse"
(legal/trust guardrail, spec section 9).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from modelpin.models import DiffResult, DiffVerdict

# CLI uses ASCII tokens (+ rich color) so it never hits a UnicodeEncodeError on a
# legacy Windows console (cp1252). Emoji live only in the Markdown report below.
_CLI_MARK = {
    DiffVerdict.regression: "[red]REGRESSION[/]",
    DiffVerdict.changed_minor: "[yellow]MINOR[/]",
    DiffVerdict.unchanged: "[green]OK[/]",
}
_MD_MARK = {
    DiffVerdict.regression: "❌",
    DiffVerdict.changed_minor: "⚠️",
    DiffVerdict.unchanged: "✅",
}


def _md_inline(text: Any) -> str:
    """Neutralize model-controlled text (scenario ids, tool names inside ``explanation``)
    for safe inline use in the Markdown PR comment posted to GitHub: collapse newlines so it
    can't break out of its line, drop HTML-comment markers (the sticky comment is found by
    one), and escape pipes. Defends the PR comment against Markdown injection via a crafted
    tool name in a model's response."""
    s = str(text).replace("\r", "").replace("\n", " ")
    s = s.replace("<!--", "<! --").replace("-->", "-- >")
    return s.replace("|", "\\|").strip()


def _bucket(
    results: list[DiffResult],
) -> tuple[list[DiffResult], list[DiffResult], list[DiffResult]]:
    regs = [r for r in results if r.verdict == DiffVerdict.regression]
    minors = [r for r in results if r.verdict == DiffVerdict.changed_minor]
    unchanged = [r for r in results if r.verdict == DiffVerdict.unchanged]
    return regs, minors, unchanged


def render_pr_comment(results: list[DiffResult], from_model: str, to_model: str, runs: int) -> str:
    """The Markdown PR comment (spec section 7). The header reflects the actual outcome —
    only a real regression leads with 🚨, so an all-unchanged result reads calm/green and
    doesn't contradict its own "safe to adopt" line."""
    regs, minors, unchanged = _bucket(results)
    if regs:
        header = f"\U0001f6a8 **Modelpin: behavioral regression — `{from_model}` → `{to_model}`**"
    elif minors:
        header = f"⚠️ **Modelpin: minor changes — `{from_model}` → `{to_model}`**"
    else:
        header = f"✅ **Modelpin: no behavioral change — `{from_model}` → `{to_model}`**"
    lines = [
        header,
        f"Replayed {len(results)} scenario(s) ×{runs} runs using your API key.",
        "",
    ]
    if regs:
        lines.append(f"**REGRESSIONS ({len(regs)})**")
        for r in regs:
            lines.append(
                f"{_MD_MARK[r.verdict]} **{_md_inline(r.scenario_id)}** — {_md_inline(r.explanation)}"
            )
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;confidence {r.confidence:.2f}")
        lines.append("")
    if minors:
        lines.append(f"**MINOR CHANGES ({len(minors)})**")
        for r in minors:
            lines.append(
                f"{_MD_MARK[r.verdict]} {_md_inline(r.scenario_id)} — {_md_inline(r.explanation)}"
            )
        lines.append("")
    lines.append(f"**UNCHANGED ({len(unchanged)})** ✅")
    lines.append("")
    if regs or minors:
        lines.append(f"→ Pin to `{from_model}` until resolved, or review the full diff above.")
    else:
        lines.append(f"→ No behavioral regressions found; `{to_model}` looks safe to adopt.")
    return "\n".join(lines)


def render_cli(results: list[DiffResult], from_model: str, to_model: str, runs: int) -> str:
    """The CLI summary — ASCII text + rich color markup (safe on any console)."""
    regs, minors, unchanged = _bucket(results)
    lines = [
        f"[bold]Modelpin[/]: {from_model} -> {to_model}  "
        f"[dim]({len(results)} scenario(s) x{runs} runs)[/]",
        "",
    ]
    for r in regs + minors:
        lines.append(
            f"{_CLI_MARK[r.verdict]} [bold]{r.scenario_id}[/]: {r.explanation} "
            f"[dim](confidence {r.confidence:.2f})[/]"
        )
    if unchanged:
        lines.append(f"[green]OK[/] [dim]{len(unchanged)} scenario(s) unchanged[/]")
    if regs or minors:
        lines.append("")
        lines.append(f"[yellow]-> Pin to[/] [bold]{from_model}[/] until resolved.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Public Modelpin Report (spec sections 4.6 / 7) — a reproducible, opinion-framed document.
#
# The renderer below is a PURE function over (results, meta): no clock, no filesystem, no
# network, no randomness. Every non-deterministic input (date, suite hash, version,
# thresholds) lives on ``ReportMeta`` and is injected by the CLI caller, so the document is
# golden-testable offline.
#
# Measurement/opinion framing (spec section 9): the static frame is opinion-framed by
# construction (a banned-words test guards the rendered prose). The only *dynamic* tokens
# in a report are the model ids, the scenario id, and the diff engine's templated
# ``explanation`` (itself built from the suite's own tool names) — all author/CI-controlled.
# ``tests/test_report_suite.py`` asserts the public suite carries no comparative-quality
# words, so those tokens cannot smuggle a "model X is worse" claim into a published report.
# --------------------------------------------------------------------------------------

#: Disclaimer printed in every report (spec section 9: "decision-support, verify
#: independently; no warranty").
_DISCLAIMER = "Decision-support only; verify independently. No warranty."


@dataclass(frozen=True)
class ReportMeta:
    """Everything a public report needs that is NOT derivable from the DiffResults.

    All non-deterministic inputs (date, suite hash, engine version, thresholds) are injected
    here by the CLI, keeping :func:`render_report_md` a pure, deterministic function.
    """

    suite_id: str
    suite_version: str
    suite_hash: str
    suite_path: str
    candidate_model: str
    reference_model: str
    provider: str
    runs: int
    judge_model: str  # the judge model id, or "disabled" when no judge ran
    match_mode: str
    modelpin_version: str
    diff_thresholds: dict[str, float]
    date_iso: str
    reproduce_cmd: str
    scenario_ids: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _fmt(value: Optional[float], spec: str, *, none: str = "—") -> str:
    """Format a possibly-``None`` numeric signal; ``None`` renders as an em dash."""
    return none if value is None else format(value, spec)


def _cell(text: Any) -> str:
    """Escape a value so it is safe inside a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\r", "").replace("\n", " ").strip()


def _report_header(meta: ReportMeta, results: list[DiffResult]) -> list[str]:
    """Title + subtitle + outcome-driven TL;DR (✅/⚠️/🚨 by ACTUAL verdict, never alarmist)."""
    regs, minors, unchanged = _bucket(results)
    same_model = meta.reference_model == meta.candidate_model
    if same_model:
        title = f"# Modelpin Report — baseline characterization of `{meta.candidate_model}`"
        compare = f"`{meta.candidate_model}` against itself"
    else:
        title = f"# Modelpin Report — `{meta.candidate_model}` vs `{meta.reference_model}`"
        compare = f"`{meta.candidate_model}` against `{meta.reference_model}`"

    if regs:
        glyph, head = "🚨", "Behavioral regressions found."
    elif minors:
        glyph, head = "⚠️", "Minor behavioral changes observed."
    else:
        glyph, head = "✅", "No behavioral change observed."

    return [
        title,
        "> A behavioral measurement on the open Modelpin suite, under the settings below — "
        "not a model-quality ranking. We report behavior *change* relative to the reference, "
        "never an absolute verdict on a model.",
        "",
        f"{glyph} **{head}** On our open suite of {len(results)} scenario(s) "
        f"×{meta.runs} runs, comparing {compare} under the settings below, we observed "
        f"{len(unchanged)} unchanged, {len(minors)} minor change(s), and "
        f"{len(regs)} regression(s).",
    ]


def _report_settings(meta: ReportMeta, n_scenarios: int) -> list[str]:
    """The reproducibility block — a keyed table a reader/provider can re-run from."""
    t = meta.diff_thresholds
    thresholds = (
        f"α={t['alpha']}, tool-TVD≥{t['min_tool_tvd']}, "
        f"refusal Δ≥{t['min_refusal_delta']}, semantic Δ≥{t['min_semantic_delta']}"
    )
    return [
        "## Settings (reproducibility)",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Suite | `{meta.suite_id}` v{meta.suite_version} (`{meta.suite_hash}`) |",
        f"| Scenarios | {n_scenarios} |",
        f"| Candidate model | `{meta.candidate_model}` |",
        f"| Reference model | `{meta.reference_model}` |",
        f"| Provider | `{meta.provider}` |",
        f"| Runs per scenario | {meta.runs} |",
        f"| Tool-call match mode | `{meta.match_mode}` |",
        f"| Semantic judge | `{meta.judge_model}` |",
        f"| Decision thresholds | {thresholds} |",
        f"| Engine version | modelpin {meta.modelpin_version} |",
        f"| Generated | {meta.date_iso} |",
    ]


def _report_methodology(meta: ReportMeta) -> list[str]:
    return [
        "## Methodology",
        "",
        f"Each scenario is replayed {meta.runs} times on **both** models using the caller's "
        "own API key. A verdict comes from the *distribution* of runs, not a single sample: "
        f"a two-sample permutation test (p ≤ {meta.diff_thresholds['alpha']}) gated by a "
        "minimum effect size. We compare four behavioral signals — tool-call trajectory match "
        f"({meta.match_mode}), refusal-rate change, output-format / assertion drift, and (when "
        "a judge runs) calibrated LLM-as-judge semantic equivalence. The north-star is a low "
        "false-positive rate: a flagged regression should be a real, repeated change, not model "
        "nondeterminism. Full method: `docs/fp-measurement.md`.",
    ]


def _report_table(results: list[DiffResult]) -> list[str]:
    """One row per scenario, sorted regression → minor → unchanged for scannability."""
    regs, minors, unchanged = _bucket(results)
    lines = [
        "## Per-scenario results",
        "",
        "| Scenario | Verdict | Tool match | Refusal Δ | Semantic | Latency Δ (ms) | "
        "Token Δ | Confidence | What we observed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in regs + minors + unchanged:
        s = r.signals
        semantic = "—" if s.semantic_score is None else format(s.semantic_score, ".0%")
        lines.append(
            f"| {_cell(r.scenario_id)} | {_MD_MARK[r.verdict]} {r.verdict.value} "
            f"| {_fmt(s.tool_call_match, '.2f')} | {_fmt(s.refusal_delta, '+.2f')} "
            f"| {semantic} | {_fmt(s.latency_delta_ms, '+.0f')} "
            f"| {_fmt(s.token_delta, '+d')} | {format(r.confidence, '.2f')} "
            f"| {_cell(r.explanation)} |"
        )
    flagged = regs + minors
    summary = (
        f"**Summary:** {len(regs)} regression(s), {len(minors)} minor, "
        f"{len(unchanged)} unchanged across {len(results)} scenario(s)."
    )
    if flagged:
        mean_conf = sum(r.confidence for r in flagged) / len(flagged)
        summary += f" Mean confidence on flagged scenarios: {mean_conf:.2f}."
    lines += ["", summary]
    return lines


def render_report_md(results: list[DiffResult], meta: ReportMeta) -> str:
    """Render the public Modelpin Report as a Markdown document (pure function).

    Framing is locked to measurement/opinion (spec section 9): every claim is phrased as
    "we observed …", the header glyph reflects the ACTUAL outcome (calm when nothing
    regressed), the limitations + disclaimer always ship, and any errored/skipped scenarios
    are disclosed so an omission is never read as "unchanged".
    """
    n_scenarios = len(meta.scenario_ids) if meta.scenario_ids else len(results)
    sections: list[str] = []
    sections += _report_header(meta, results)
    sections += ["", *_report_settings(meta, n_scenarios)]
    sections += ["", *_report_methodology(meta)]
    sections += ["", *_report_table(results)]

    if meta.skipped:
        sections += [
            "",
            "## Skipped scenarios",
            "",
            "These scenario(s) errored during replay and are excluded from the counts above "
            '(disclosed so an omission is never read as "unchanged"): '
            f"{', '.join(meta.skipped)}.",
        ]

    sections += [
        "",
        "## Limitations & framing",
        "",
        "This is a measurement on a fixed, open suite under the exact settings above — not a "
        "claim about which model to choose for your app. A *regression* here means the "
        "candidate's behavior diverged from the reference on this suite; for some apps that "
        "divergence may be neutral or even desirable. The suite is small and the semantic "
        "judge is calibrated on a modest, partly-synthetic set with a single-vendor judge "
        "(see `docs/fp-measurement.md` for the known limitations). Models are "
        "non-deterministic, so exact numbers vary run to run; the distribution-level verdict "
        f"is what reproduces. {_DISCLAIMER}",
        "",
        "## Reproduce this report",
        "",
        "```bash",
        meta.reproduce_cmd,
        "```",
        "",
        "You supply your own API key (read from the environment). Exact outputs vary because "
        "models are non-deterministic; the distribution-level verdicts are what reproduce.",
        "",
        "---",
        "",
        f"Open suite: `{meta.suite_path}` ({meta.suite_id} v{meta.suite_version}, "
        f"`{meta.suite_hash}`). A machine-readable JSON sidecar with the raw per-scenario "
        "results is written alongside this report. Harness + scenarios are open source under "
        "Apache-2.0. Method & false-positive measurement: `docs/fp-measurement.md`.",
    ]
    return "\n".join(sections) + "\n"


def to_report_sidecar(results: list[DiffResult], meta: ReportMeta) -> dict[str, Any]:
    """The machine-readable audit artifact emitted next to the Markdown report.

    Pure: ``{meta, results}`` where both are plain JSON-serializable structures, so any
    flagged behavior change is traceable to the exact per-scenario verdict + signals.
    """
    return {
        "meta": asdict(meta),
        "results": [r.model_dump(mode="json") for r in results],
    }
