"""Reproduce a regression on fresh candidate runs before it is allowed to fail a build.

Why this exists. Every CI-failing signal is an exact permutation test at ``p <= ALPHA`` per
channel, per scenario. That bounds each single test, but a suite runs several channels over
many scenarios, so a same-model run can still clear one gate by chance. `[M]` It has happened:
MP-220, an optional tool call made on 4 of 5 baseline runs and 0 of 5 candidate runs, published
``regression`` @ 0.952 and exit 1 on a same-model, same-prompt null.

The fix is the one a careful human applies to a surprising result: run it again. When the first
``runs`` candidate replays produce a ``regression``, Modelpin replays the candidate ``runs``
more times and diffs that FRESH sample, on its own, against the same baseline. The regression
stands only if the fresh sample is a ``regression`` too.

The fresh sample is scored ALONE, never pooled with the first. The first sample is the one that
triggered the check, so it is selected for being extreme, and pooling carries that selection
into the confirmation. `[M] 2026-09-15`, on MP-220's own run of record
(`reports/channel-exposure/2026-09-07/v2a-...`): taking each of the other nine same-model
repeats as the confirmation sample, POOLING still confirmed the false alarm on 2 of 9, because
0 of 5 plus 1 of 5 remains far from 4 of 5. Scoring the fresh sample alone withheld all 9.

Why this is safe for the north-star metric: it is MONOTONE. A final ``regression`` requires
BOTH samples to fire, so it can withhold a regression the first sample produced and can never
create one. Under a true null the chance of a confirmed alarm is at most the chance of an
unconfirmed one, and usually far smaller.

What it costs: recall on BORDERLINE changes, because a change must now clear the gate twice. A
consistent break (the kind a migration gate exists for) clears it both times. A regression
that does not reproduce is still reported, in full, as ``changed_minor`` -- visible, and never
a red build.
"""

from __future__ import annotations

from collections.abc import Callable

from modelpin.models import DiffResult, DiffVerdict, Trace

#: Appended to the explanation of a regression that reproduced.
CONFIRMED_NOTE = "reproduced on a second, independent set of candidate runs"
#: Leads the explanation of a first-sample regression that did not reproduce.
UNCONFIRMED_NOTE = (
    "not confirmed: flagged as a regression on the first candidate runs, but a second, "
    "independent set of candidate runs did not reproduce it, so it is not failing the build"
)


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

    A provider error raised by ``replay_more`` or ``rediff`` propagates: the caller already
    treats a scenario the provider rejected as "could not answer", which is the honest reading
    of a regression that could not be re-checked.
    """
    if first.verdict != DiffVerdict.regression:
        return first, candidate_traces

    fresh_traces = replay_more()
    second = rediff(fresh_traces)
    recorded = candidate_traces + fresh_traces

    if second.verdict == DiffVerdict.regression:
        return (
            first.model_copy(update={"explanation": f"{first.explanation} ({CONFIRMED_NOTE})"}),
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
