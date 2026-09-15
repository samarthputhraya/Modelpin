"""Reproduce a regression on fresh candidate runs before it is allowed to fail a build.

Why this exists. Every CI-failing signal is an exact permutation test at ``p <= ALPHA`` per
channel, per scenario. That bounds each single test, but a suite runs several channels over
many scenarios, so a same-model run can still clear one gate by chance. `[M]` It has happened:
MP-220, an optional tool call made on 4 of 5 baseline runs and 0 of 5 candidate runs, published
``regression`` @ 0.952 and exit 1 on a same-model, same-prompt null.

What it does. When the first ``runs`` candidate replays produce a ``regression``, Modelpin
replays the candidate ``runs`` more times and diffs that FRESH sample, on its own, against the
same baseline. The regression stands only if the fresh sample regresses on a channel that ALSO
fired on the first sample; the verdict then names only the channels that reproduced.

The fresh sample is scored ALONE, never pooled with the first. The first sample is the one that
triggered the check, so it is selected for being extreme, and pooling carries that selection
into the confirmation. `[M] 2026-09-15`, on MP-220's own run of record
(`reports/channel-exposure/2026-09-07/v2a-...`): taking each of the other nine same-model
repeats as the confirmation sample, POOLING still confirmed the false alarm on 2 of 9, because
0 of 5 plus 1 of 5 remains far from 4 of 5. Scoring the fresh sample alone withheld all 9.

The same channel must reproduce. `[M]` FP review 2026-09-15: accepting a regression on ANY
channel let a first-sample tool alarm "reproduce" through a refusal alarm on the fresh sample,
and the published verdict then described the tool change that had not reproduced.

Why this is safe for the north-star metric: it is MONOTONE. A final ``regression`` requires
both samples to fire, so it can withhold a regression the first sample produced and can never
create one. `[M]` Exact enumeration at ``runs: 5`` (FP review 2026-09-15): on the tool channel
the same-model alarm rate falls from 2.15% to 0.25% at a 50% optional-call rate, and from 0.47%
to 0.09% at 80%; on refusal/semantic flags at a 30% base rate, from 0.60% to 0.02%.

What it does NOT fix: an unusual BASELINE. Confirmation re-runs the candidate only, and the
baseline is recorded once and reused. `[M]` With an optional call made on half of runs and a
baseline that happened to record it on 0 of 5, 18.75% of checks alarm without confirmation and
3.52% with it -- on every check against that baseline. Re-recording the baseline is the remedy.

What it costs: recall on BORDERLINE changes, because a change must clear the gate twice. `[M]`
At ``runs: 5`` a complete flip loses nothing (refusal 0% -> 100% stays at 100% detection), while
a tool call dropping from 80% to 20% of runs falls from 37.6% to 22.2%. A regression that does
not reproduce is still reported in full, as ``changed_minor`` -- visible, never a red build.
"""

from __future__ import annotations

from collections.abc import Callable

from modelpin.models import DiffResult, DiffVerdict, Trace

#: The explanation prefix each CI-failing channel writes (`diff/__init__.py`, verdict block).
HARD_CHANNEL_PREFIXES: dict[str, tuple[str, ...]] = {
    "tool-call trajectory": ("tool-call behavior changed", "tool-call trajectory now violates"),
    "refusal rate": ("refusal rate ",),
    "meaning (semantic judge)": ("semantic drift:",),
}

#: Appended to the explanation of a regression that reproduced.
CONFIRMED_NOTE = "reproduced on a second set of candidate runs, scored on its own"
#: Leads the explanation of a first-sample regression that did not reproduce.
UNCONFIRMED_NOTE = (
    "not confirmed: flagged as a regression on the first candidate runs, but a second set of "
    "candidate runs, scored on its own against the same baseline, did not reproduce it, so it "
    "is not failing the build"
)
#: Leads the explanation when the second set of runs could not be measured at all.
UNMEASURED_NOTE = (
    "could not confirm: flagged as a regression on the first candidate runs, but the second "
    "set of candidate runs recorded nothing that could be compared"
)


def _hard_reasons(result: DiffResult) -> dict[str, str]:
    """{channel: its reason text} for every CI-failing channel that fired in ``result``."""
    if result.verdict != DiffVerdict.regression:
        return {}
    fired: dict[str, str] = {}
    for reason in result.explanation.split("; "):
        for channel, prefixes in HARD_CHANNEL_PREFIXES.items():
            if reason.startswith(prefixes):
                fired[channel] = reason
    return fired


def confirm_regression(
    first: DiffResult,
    candidate_traces: list[Trace],
    replay_more: Callable[[], list[Trace]],
    rediff: Callable[[list[Trace]], DiffResult],
) -> tuple[DiffResult, list[Trace]]:
    """Return the final result for one scenario, and every candidate trace that was recorded.

    ``first`` is the verdict on ``candidate_traces``. When it is not a ``regression`` it is
    returned untouched and nothing is replayed. Otherwise ``replay_more()`` supplies a fresh
    candidate sample and ``rediff(fresh)`` scores it against the same baseline.

    A provider error raised by ``replay_more`` or ``rediff`` propagates: the caller treats a
    regression that could not be re-checked as "could not answer" (exit 3), never as clean.
    """
    if first.verdict != DiffVerdict.regression:
        return first, candidate_traces

    fresh_traces = replay_more()
    second = rediff(fresh_traces)
    recorded = candidate_traces + fresh_traces

    if second.verdict == DiffVerdict.insufficient_evidence:
        return (
            first.model_copy(
                update={
                    "verdict": DiffVerdict.insufficient_evidence,
                    "confidence": 0.0,
                    "explanation": (
                        f"{UNMEASURED_NOTE} ({second.explanation}). "
                        f"First sample: {first.explanation}"
                    ),
                }
            ),
            recorded,
        )

    first_fired = _hard_reasons(first)
    reproduced = [ch for ch in first_fired if ch in _hard_reasons(second)]
    if reproduced:
        reasons = "; ".join(first_fired[ch] for ch in reproduced)
        return (
            first.model_copy(update={"explanation": f"{reasons} ({CONFIRMED_NOTE})"}),
            recorded,
        )

    return (
        first.model_copy(
            update={
                "verdict": DiffVerdict.changed_minor,
                "explanation": (
                    f"{UNCONFIRMED_NOTE} (second sample: {second.verdict.value}). "
                    f"First sample: {first.explanation}"
                ),
            }
        ),
        recorded,
    )
