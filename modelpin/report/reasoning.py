"""What `reasoning_effort` each side of a check was actually sent (MP-153).

Read off the traces, which record the effort the adapter put on the wire, never re-derived
from settings: a report that described the configured effort rather than the sent one would
be describing a run that did not happen. Pure; the CLI says which models are reasoning models.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from modelpin.models import Trace

_NOT_SENT = "not sent (the model's default)"


@dataclass(frozen=True)
class ReasoningDisclosure:
    #: One sentence for the reports: the effort each side was sent, per scenario.
    note: str
    #: Models that got `none` because their scenarios sent tools and no effort was chosen.
    reasoning_off_for_tools: tuple[str, ...] = ()


def _scenario_effort(traces: Sequence[Trace]) -> Optional[str]:
    values = {t.reasoning_effort for t in traces}
    if len(values) == 1:
        return values.pop()
    return "mixed"  # cannot happen from one adapter run, but never claim a uniform value


def _side(
    model: str,
    by_scenario: Mapping[str, Sequence[Trace]],
    applies: bool,
    fmt: Callable[[str], str],
) -> tuple[str, Counter[Optional[str]]]:
    counts: Counter[Optional[str]] = Counter(
        _scenario_effort(runs) for runs in by_scenario.values() if runs
    )
    if not applies and set(counts) <= {None}:
        return f"{fmt(model)} not sent (not a reasoning model)", counts
    if len(counts) == 1:
        value = next(iter(counts))
        return f"{fmt(model)} {fmt(value) if value else _NOT_SENT} on every scenario", counts
    parts = [
        f"{fmt(v) if v else _NOT_SENT} on {n} scenario{'s' if n != 1 else ''}"
        for v, n in sorted(counts.items(), key=lambda kv: (kv[0] is None, str(kv[0])))
    ]
    return f"{fmt(model)} {', '.join(parts)}", counts


def reasoning_disclosure(
    sides: Sequence[tuple[str, Mapping[str, Sequence[Trace]], bool]],
    *,
    requested: Optional[str],
    fmt: Callable[[str], str],
) -> Optional[ReasoningDisclosure]:
    """The disclosure for a check, or None when neither side is a reasoning model and nothing
    was sent (a Gemini or Claude check: there is no such setting to state).

    ``sides`` is ``(model id, its traces per compared scenario, is it a reasoning model)`` for
    the model in use and then the replacement. ``requested`` is the run's own setting, used
    only to tell a chosen `none` from the one sent because tools require it.
    """
    texts: list[str] = []
    off_for_tools: list[str] = []
    any_relevant = False
    for model, by_scenario, applies in sides:
        text, counts = _side(model, by_scenario, applies, fmt)
        texts.append(text)
        any_relevant = any_relevant or applies or any(v is not None for v in counts)
        if requested is None and counts.get("none"):
            off_for_tools.append(model)
    if not any_relevant:
        return None
    note = "reasoning effort sent: " + "; ".join(texts) + "."
    if off_for_tools:
        note += (
            " `none` went to scenarios that send tools: OpenAI's Chat Completions API accepts "
            "tools from gpt-5.1 and later only with reasoning off. Set `reasoning_effort` to "
            "test the effort you will ship."
        )
    return ReasoningDisclosure(note=note, reasoning_off_for_tools=tuple(off_for_tools))
