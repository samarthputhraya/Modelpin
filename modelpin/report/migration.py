"""The migration report: what `modelpin check` found, written for a reader who does not run CI.

`last-report.md` is the pull-request comment, written for the engineer reviewing a diff. This is
the same run told to the person deciding whether to switch models: a plain-English verdict
first, each flagged scenario with what the current model did and what the replacement did, what
the run could NOT see, and the facts of the run (date, models, runs, calls, tokens).

It is a renderer over data `check` already computes -- the verdicts, the coverage census, the
example runs -- and adds no measurement of its own. Every sentence below is derived from those
inputs; nothing ranks a model (ADR-0009): a regression means the replacement behaved differently
from the model in use on the customer's own scenarios, which may be neutral or even desirable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from modelpin.models import DiffResult, DiffVerdict
from modelpin.report import (
    ChannelCensus,
    _bucket,
    _census_note,
    _md_code,
    _md_inline,
    _rejected_clearance,
    _skipped_clearance,
    _underpowered_clearance,
)
from modelpin.report.evidence import Example

#: Written beside `last-report.md` by every `modelpin check`.
MIGRATION_REPORT_FILENAME = "migration-report.md"

#: The engine's explanation phrases, in plain English, keyed by the phrase that starts each
#: clause of `DiffResult.explanation` (`modelpin/diff/__init__.py`). Matched, never parsed:
#: an unrecognised clause is still printed verbatim under "What the check measured".
_GLOSS: tuple[tuple[str, str], ...] = (
    (
        "not confirmed",
        "It looked like a regression on the first set of runs but did not repeat on a second, "
        "fresh set, so it is not counted as one.",
    ),
    (
        "tool-call arguments changed",
        "It calls the same tools in your app with different inputs.",
    ),
    (
        "tool-call",
        "It takes different actions in your app: it calls different tools, or calls them in a "
        "different order.",
    ),
    (
        "refusal rate",
        "It declines requests at a different rate than the model you use today.",
    ),
    (
        "output format drift",
        "Its answers no longer pass the text checks written into this scenario.",
    ),
    (
        "semantic drift",
        "Its answers mean something different from the current model's answers, as judged by "
        "a separate model.",
    ),
)

_VERDICT_WORD = {
    DiffVerdict.regression: "changed in a way that matters",
    DiffVerdict.changed_minor: "minor change",
    DiffVerdict.insufficient_evidence: "could not be measured",
    DiffVerdict.unchanged: "no change",
}


@dataclass(frozen=True)
class MigrationFacts:
    """The run's facts that are not derivable from the verdicts. Injected by the CLI so the
    renderer stays a pure function (the same reason `ReportMeta` exists)."""

    date_iso: str
    from_model: str
    to_model: str
    provider: Optional[str]
    runs: int
    match_mode: str
    modelpin_version: str
    #: The judge model id, or None when no judge ran (meaning was then not compared).
    judge_model: Optional[str]
    #: Recorded runs of the current model that this check compared against.
    baseline_runs: int
    #: Runs of the replacement this check made, confirmation replays included.
    candidate_runs: int
    #: Judge calls this check made; None when no judge ran.
    judge_calls: Optional[int]
    tokens_in: int
    tokens_out: int


def plain_reasons(explanation: str) -> list[str]:
    """Each clause of an engine explanation in plain English, in order, without repeats."""
    out: list[str] = []
    for clause in (c.strip() for c in explanation.split(";")):
        for prefix, gloss in _GLOSS:
            if clause.startswith(prefix) and gloss not in out:
                out.append(gloss)
                break
    return out


def _names(results: Sequence[DiffResult]) -> str:
    return ", ".join(_md_code(r.scenario_id) for r in results)


def _count(n: int, noun: str) -> str:
    """`1 scenario`, `8 scenarios`: this reader should not have to parse `scenario(s)`."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _verdict_paragraph(
    results: list[DiffResult],
    facts: MigrationFacts,
    *,
    weak_coverage: bool,
    not_compared: int,
) -> tuple[str, str]:
    """(bottom line, one-paragraph verdict) in plain English, from the verdict buckets."""
    b = _bucket(results)
    regs, minors = b[DiffVerdict.regression], b[DiffVerdict.changed_minor]
    same, unmeasured = b[DiffVerdict.unchanged], b[DiffVerdict.insufficient_evidence]
    n = len(results)
    cur, new = _md_code(facts.from_model), _md_code(facts.to_model)
    parts = [
        f"We ran {_count(n, 'scenario')} from your app {facts.runs} times each on {new}, the model you "
        f"are moving to, and compared them with recorded runs of {cur}, the model you use "
        "today."
    ]
    if regs:
        parts.append(
            f"{_count(len(regs), 'scenario')} behaved differently in a way that matters, and the change "
            f"held up when we ran them again: {_names(regs)}."
        )
    if minors:
        parts.append(
            f"{len(minors)} changed in a smaller way that would not fail a build but is worth a "
            f"look: {_names(minors)}."
        )
    if unmeasured:
        parts.append(
            f"{len(unmeasured)} could not be measured, which is not a pass: {_names(unmeasured)}."
        )
    if same:
        parts.append(f"{len(same)} behaved the same on both models.")
    if not_compared:
        parts.append(
            f"{not_compared} more {'scenario was' if not_compared == 1 else 'scenarios were'} "
            "not compared at all; they are listed under what this check did not cover."
        )
    if n == 0:
        bottom = "Incomplete: nothing could be compared, so this check clears nothing."
    elif regs:
        bottom = (
            f"Hold the switch to {new} until someone has looked at the "
            f"{_count(len(regs), 'scenario')} that changed."
        )
    elif unmeasured or not_compared:
        bottom = (
            f"Incomplete: no harmful change was found, but part of the app was not measured, so "
            f"{new} is not cleared yet."
        )
    elif weak_coverage:
        bottom = (
            "No harmful change was found, but this run could not see every kind of change; read "
            "what it did not cover before switching."
        )
    elif minors:
        bottom = (
            f"No harmful change was found; review the {_count(len(minors), 'minor change')} "
            "before switching."
        )
    else:
        bottom = (
            f"No change in behavior was found on the scenarios tested; switching to {new} looks "
            "safe for them."
        )
    return bottom, " ".join(parts)


def render_migration_report(
    results: list[DiffResult],
    facts: MigrationFacts,
    *,
    underpowered: Sequence[str] = (),
    census: Optional[ChannelCensus] = None,
    rejected: Sequence[tuple[str, str]] = (),
    skipped: Sequence[str] = (),
    examples: Optional[Mapping[str, tuple[Example, Example]]] = None,
) -> str:
    """The migration report as Markdown (pure: the same inputs give the same document)."""
    cur, new = _md_code(facts.from_model), _md_code(facts.to_model)
    not_covered: list[str] = []
    for line in (
        _underpowered_clearance(underpowered, len(results), facts.to_model, arrow="-"),
        _rejected_clearance(rejected, facts.to_model, arrow="-"),
        _skipped_clearance(skipped, facts.from_model, facts.to_model, arrow="-"),
    ):
        if line:
            not_covered.append(line)
    note = _census_note(census, _md_code)
    if note:
        not_covered.append("- " + note[0].upper() + note[1:] + ".")
    if facts.judge_model is None:
        not_covered.append(
            "- Meaning was not compared: no judge model was configured, so an answer that says "
            "something different in the same shape would not show here."
        )
    weak = (
        bool(underpowered)
        or (census is not None and bool(census.inert))
        or (facts.judge_model is None)
    )
    bottom, paragraph = _verdict_paragraph(
        results, facts, weak_coverage=weak, not_compared=len(rejected) + len(skipped)
    )

    lines = [
        f"# Migration check: {cur} to {new}",
        "",
        f"**Bottom line: {bottom}**",
        "",
        paragraph,
        "",
        "A scenario counts as changed in a way that matters only when the difference is "
        "statistically significant across the repeated runs, large enough to matter, and repeats "
        "on a fresh set of runs. Models answer differently every time, so a single different "
        "answer is never enough. A change says nothing about which model is good: it means your "
        "app would behave differently, which may be neutral or even welcome.",
        "",
    ]

    flagged = [
        r
        for r in results
        if r.verdict
        in (DiffVerdict.regression, DiffVerdict.changed_minor, DiffVerdict.insufficient_evidence)
    ]
    order = {
        DiffVerdict.regression: 0,
        DiffVerdict.changed_minor: 1,
        DiffVerdict.insufficient_evidence: 2,
    }
    if flagged:
        lines += ["## What changed", ""]
        for r in sorted(flagged, key=lambda x: order[x.verdict]):
            lines.append(f"### {_md_inline(r.scenario_id)}: {_VERDICT_WORD[r.verdict]}")
            lines.append("")
            reasons = plain_reasons(r.explanation)
            if r.verdict == DiffVerdict.insufficient_evidence:
                reasons = reasons or [
                    "One of the two models returned nothing usable for this scenario, so the "
                    "two could not be compared."
                ]
            for reason in reasons:
                lines.append(f"- {reason}")
            pair = (examples or {}).get(r.scenario_id)
            if pair:
                lines += [
                    f"- **Before** ({cur}, a typical run): {_md_code(pair[0].describe())}",
                    f"- **After** ({new}, a run showing the change): "
                    f"{_md_code(pair[1].describe())}",
                ]
            lines.append(f"- What the check measured: {_md_inline(r.explanation)}")
            if r.verdict == DiffVerdict.regression:
                lines.append(f"- Confidence: {r.confidence:.2f}")
            lines.append("")

    unchanged = [r for r in results if r.verdict == DiffVerdict.unchanged]
    if unchanged:
        lines += [
            "## No change",
            "",
            f"These behaved the same on both models: {_names(unchanged)}.",
            "",
        ]

    lines += [
        "## What this check did not cover",
        "",
        "- Only the scenarios above were tested. Anything your app does that no scenario "
        "exercises was not measured.",
        *(
            [
                f"- Each scenario ran {facts.runs} times on each model. A change that shows up in "
                "only some of those runs can go unnoticed; more runs (`--runs 10`) can see "
                "smaller changes, at proportionally more cost."
            ]
            if results
            else []
        ),
        *not_covered,
        "",
        "## The run",
        "",
        "| | |",
        "|---|---|",
        f"| Date | {facts.date_iso} |",
        f"| Model in use | {cur} |",
        f"| Model moving to | {new} |",
        f"| Provider | {_md_inline(facts.provider or 'from config')} |",
        f"| Scenarios compared | {len(results)} |",
        f"| Runs per scenario | {facts.runs} on each model |",
        f"| Recorded runs of {cur} compared | {facts.baseline_runs} |",
        f"| Runs of {new} made by this check | {facts.candidate_runs}, including any re-runs "
        "that confirmed a change |",
        "| Judge (compares meaning) | "
        + (
            f"{_md_code(facts.judge_model)}, {_count(facts.judge_calls or 0, 'call')} |"
            if facts.judge_model is not None
            else "none configured |"
        ),
        f"| Tokens used by {new} | {facts.tokens_in:,} in, {facts.tokens_out:,} out |",
        f"| Tool-call comparison | {_md_code(facts.match_mode)} |",
        f"| Modelpin | {facts.modelpin_version} |",
        "",
        "Each run of a model is one request, plus one more for each turn in which it called a "
        "tool. Recorded runs of the model in use were made earlier and cost nothing here.",
        "",
    ]
    return "\n".join(lines)
