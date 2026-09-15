"""One concrete baseline run and one candidate run, shown beside a flagged verdict.

A verdict line such as "semantic drift: candidate answers diverge in meaning" tells a reviewer
THAT something changed, never WHAT. `[M] 2026-09-15`, a live Gemini migration check flagged four
regressions and neither the console nor the PR comment contained a single word the candidate
had said; the candidate traces were not written anywhere either. A reviewer had to trust the
verdict blind or re-run the models by hand.

So every non-`unchanged` verdict now carries one representative example per side: the most
common behavior on the baseline, and the most common candidate behavior the baseline never
showed (falling back to the most common candidate behavior). It is illustration, chosen after
the verdict, and can never move one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from modelpin.models import Trace

#: Characters of model output shown per side. Enough to recognise an answer, short enough that
#: a PR comment stays readable and a long output cannot swamp it.
EXAMPLE_CHARS = 160


def _behavior(trace: Trace) -> tuple[tuple[str, ...], str, bool]:
    return (
        tuple(call.name for call in trace.tool_calls),
        " ".join((trace.final_output or "").split()).lower(),
        trace.refused,
    )


def _modal(traces: list[Trace], exclude: set | None = None) -> Trace | None:
    exclude = exclude or set()
    counts = Counter(_behavior(t) for t in traces if _behavior(t) not in exclude)
    if not counts:
        return None
    top = counts.most_common(1)[0][0]
    return next(t for t in traces if _behavior(t) == top)


@dataclass(frozen=True)
class Example:
    """A short, display-ready description of one run."""

    tools: tuple[str, ...]
    output: str
    refused: bool

    @classmethod
    def of(cls, trace: Trace) -> "Example":
        text = " ".join((trace.final_output or "").split())
        if len(text) > EXAMPLE_CHARS:
            text = text[: EXAMPLE_CHARS - 3].rstrip() + "..."
        return cls(tuple(call.name for call in trace.tool_calls), text, trace.refused)

    def describe(self) -> str:
        parts = []
        if self.tools:
            parts.append("tools " + " -> ".join(self.tools))
        if self.refused:
            parts.append("refused")
        parts.append(f'"{self.output}"' if self.output else "(no text)")
        return "; ".join(parts)


def pick_examples(baseline: list[Trace], candidate: list[Trace]) -> tuple[Example, Example] | None:
    """(baseline example, candidate example), or None when either side has no runs."""
    base = _modal(baseline)
    if base is None or not candidate:
        return None
    seen = {_behavior(t) for t in baseline}
    cand = _modal(candidate, exclude=seen) or _modal(candidate)
    assert cand is not None
    return Example.of(base), Example.of(cand)
