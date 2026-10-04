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

"Most common" is decided on the run's SHAPE first -- which tools it called, in what order, and
whether it refused -- and only then on its exact wording (MP-291). `[M] 2026-10-04` the live
Gemini sample showed, as the old model's "typical run" for `optional_availability_before_booking`,
the ONE baseline run of five that called `check_availability`: all five runs worded their reply
differently, so keyed on the text they tied 1-1-1-1-1 and run 0 won. The other four never called
the tool. A reader deciding on a migration was shown the exception as the rule.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from modelpin.diff.argkey import canonical_arguments
from modelpin.models import Trace

#: Characters of model output shown per side. Enough to recognise an answer, short enough that
#: a PR comment stays readable and a long output cannot swamp it.
EXAMPLE_CHARS = 160
#: Characters shown per tool-call argument value. A changed argument is often the finding (the
#: argument channel reports `issue_refund(reason (changed))`), so the value has to be visible.
ARG_CHARS = 40


def _behavior(trace: Trace) -> tuple[tuple[str, ...], str, bool]:
    return (
        tuple(f"{call.name}:{canonical_arguments(call.arguments)}" for call in trace.tool_calls),
        " ".join((trace.final_output or "").split()).lower(),
        trace.refused,
    )


def _shape(trace: Trace) -> tuple[tuple[str, ...], bool]:
    """What the run DID, without its wording or argument values: the tools it called, in
    order, and whether it refused."""
    return tuple(call.name for call in trace.tool_calls), trace.refused


def _typical(traces: list[Trace]) -> Trace | None:
    """The run that stands for the most common behavior among `traces`.

    The largest group by shape wins first; inside it, the most common exact run (arguments
    and wording); ties go to the earliest run. Counting exact runs alone lets differently
    worded runs tie, so an outlier can be shown as typical (MP-291)."""
    if not traces:
        return None
    shape = Counter(_shape(t) for t in traces).most_common(1)[0][0]
    group = [t for t in traces if _shape(t) == shape]
    top = Counter(_behavior(t) for t in group).most_common(1)[0][0]
    return next(t for t in group if _behavior(t) == top)


#: How an early stop reads beside an example. Keyed by `IncompleteReason` value.
_ENDED = {
    "tool_turns": "stopped at the tool-call limit",
    "max_tokens": "cut off at the token limit",
    "content_filter": "stopped by the provider's content filter",
    "malformed_tool_call": "sent a malformed tool call",
}


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _call(name: str, arguments: dict | None) -> str:
    if not arguments:
        return name
    shown = ", ".join(
        f"{key}={_shorten(json.dumps(value, ensure_ascii=False), ARG_CHARS)}"
        for key, value in sorted(arguments.items())
    )
    return f"{name}({shown})"


@dataclass(frozen=True)
class Example:
    """A short, display-ready description of one run."""

    tools: tuple[str, ...]
    output: str
    refused: bool
    #: Why the run ended early, when it did (e.g. the tool-loop cap), else "".
    ended: str = ""

    @classmethod
    def of(cls, trace: Trace) -> "Example":
        return cls(
            tuple(_call(call.name, call.arguments) for call in trace.tool_calls),
            _shorten(" ".join((trace.final_output or "").split()), EXAMPLE_CHARS),
            trace.refused,
            (
                _ENDED.get(trace.incomplete_reason.value, trace.incomplete_reason.value)
                if trace.incomplete_reason
                else ""
            ),
        )

    def describe(self) -> str:
        parts = []
        if self.tools:
            parts.append("tools " + " -> ".join(self.tools))
        if self.refused:
            parts.append("refused")
        parts.append(f'"{self.output}"' if self.output else "(no text)")
        if self.ended:
            parts.append(self.ended)
        return "; ".join(parts)


def pick_examples(baseline: list[Trace], candidate: list[Trace]) -> tuple[Example, Example] | None:
    """(baseline example, candidate example), or None when either side has no runs."""
    base = _typical(baseline)
    if base is None or not candidate:
        return None
    # The candidate's example is a behavior the baseline never showed: a new SHAPE when there
    # is one (a different tool path, a refusal); otherwise new wording or arguments on the
    # same shape (meaning or argument drift); otherwise simply its most common run.
    seen_shapes = {_shape(t) for t in baseline}
    seen = {_behavior(t) for t in baseline}
    new_shape = [t for t in candidate if _shape(t) not in seen_shapes]
    new_behavior = [t for t in candidate if _behavior(t) not in seen]
    cand = _typical(new_shape or new_behavior or candidate)
    assert cand is not None
    return Example.of(base), Example.of(cand)
