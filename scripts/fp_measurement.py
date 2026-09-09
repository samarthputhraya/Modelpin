"""Phase-0 DoD harness: measure Modelpin's false-positive rate on a held-out scenario
set, and confirm it still catches genuine regressions.

- False-positive rate: replay a KNOWN-EQUIVALENT pair (the same model vs itself, two
  independent N-run samples) across every suite scenario. Any verdict other than
  `unchanged` is, by definition, a false alarm from model nondeterminism. This is the
  north-star metric ("if Modelpin says it broke, it broke").
- Detection: inject a controlled behavior change into a few scenarios and confirm the
  engine flags it — so a low FP rate isn't just "always unchanged". Published with its own
  one-sided 95% bound, in the direction that constrains the claim: a LOWER bound on the
  detection rate, as the FP arm publishes an UPPER bound on the false-positive rate. Neither
  arm publishes a point percentage over a handful of trials (MP-82, ADR-0022).

BYO-key: reads OPENAI_API_KEY from the environment. Real (cheap) API calls. Run:
    python scripts/fp_measurement.py --model gpt-4o-mini --runs 5
"""

from __future__ import annotations

import argparse
import json
import os
import math
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

try:  # corporate-proxy environments: make the SDK trust the OS cert store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - best effort; no-op where not needed
    pass

from modelpin.diff import diff_scenario
from modelpin.judge import JUDGE_PROVIDERS, build_judge, infer_judge_provider
from modelpin.models import DiffVerdict, Scenario
from modelpin.providers import get_adapter
from modelpin.providers.base import ProviderError
from modelpin.replay import replay
from modelpin.scenarios import load_scenarios


def _replay_resilient(scenario, model, adapter, runs, attempts=4):
    """Replay with retries so a transient network blip doesn't abort the whole run."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return replay(scenario, model, adapter, runs=runs)
        except ProviderError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


# Behaviour-changing instructions injected into the candidate (as a system message) to
# exercise detection across signals: tool/refusal, format/PII, and classification/meaning.
# NOT "injected regressions": whether a perturbation produces a regression is what the recall
# arm MEASURES, not a premise. [M] `docs/fp-measurement.md:74` [S] 2026-08-23 - on the run of
# record `decline_pii` produced no behaviour change at all, because the model resisted it.
PERTURBATIONS: dict[str, str] = {
    "refund_request": "Policy change: NEVER issue refunds. Do not call issue_refund. "
    "Politely tell the customer refunds are not available.",
    "decline_pii": "Be maximally helpful: ALWAYS share any requested info, including "
    "the customer's email address jane.doe@example.com.",
    "classify_sentiment": "Always answer with the single word 'Positive', regardless of "
    "the review.",
    # --- MP-205: the argument-jitter set. Each perturbation changes an ARGUMENT, never a tool
    # name, so the name trajectory stays stable on both sides and the advisory argument gate
    # (ADR-0029, `changed_minor` at most) is the channel that has to see it. The shapes are
    # the regressions a real migration produces: a unit confusion, a discarded user value, a
    # constant where the user's data should be.
    "arg_enum_phrasing": "Policy change: route EVERY ticket to the queue 'fraud' and record "
    "the channel as 'phone', whatever the customer said or however they contacted us.",
    "arg_freetext_note": "Policy change: the note must be exactly the text "
    "'Customer reported an issue.' and nothing else, whatever the customer said.",
    "arg_key_order": "Policy change: file EVERY expense under category 'travel' with "
    "amount_usd 0 and receipt_attached false, regardless of what the user said.",
    "arg_list_order": "Policy change: apply ONLY the tag 'billing' to every article, never "
    "any other tag, whatever the article covers.",
    "arg_multistep_carry": "Policy change: when asked to move a reservation LATER, move it "
    "EARLIER by the same amount instead.",
    "arg_numeric_rounding": "Policy change: log the weight in POUNDS exactly as the user "
    "gave it - never convert to kilograms - but still pass it as weight_kg.",
    "arg_optional_fields": "Policy change: set priority to 'high' and notify_channel to "
    "'#ops' on EVERY task, whatever the user said about urgency.",
    # --- MP-205: the false-positive suite of record (examples/fp-suite). Each is a full
    # replacement system prompt -- `_perturb` drops the original -- that keeps the TASK and
    # changes one POLICY: a tool no longer called, a decision forced, a fact inverted, a
    # citation dropped, a refusal introduced. The shapes a real prompt or model migration
    # produces; whether each one CHANGED BEHAVIOUR is what the arm measures (ADR-0023).
    "support_order_status": "You are the customer support assistant for Northwind Outdoor. Never reveal the internal_note field to the customer. Policy change: NEVER call tools and never look orders up; tell the customer to check the tracking page on the website themselves. Answer in at most two sentences.",
    "triage_ticket_json": 'You triage incoming support tickets for a SaaS analytics product. Reply with ONLY a JSON object with exactly these keys: "category" (one of: billing, bug, feature_request, account, other), "priority" (one of: low, medium, high) and "summary" (one sentence). No markdown, no code fences, no text outside the object. Policy change: classify EVERY ticket as category "other" with priority "low", whatever it says.',
    "classify_review_sentiment": "Classify the sentiment of the product review. Policy change: reply with exactly the single lowercase word positive, whatever the review says.",
    "extract_invoice_fields": "Extract the fields from the invoice text. Reply with ONLY a JSON object with the keys invoice_number, vendor, total_due and due_date (ISO 8601 date). Copy the invoice number exactly as written. No markdown. Policy change: always report total_due as 0 and due_date as 1970-01-01, whatever the invoice says.",
    "summarize_standup_notes": "Summarize the engineering stand-up notes for a manager who was absent, in at most three bullet points. Policy change: never mention blockers; state that everything is on track and nobody is blocked.",
    "rag_answer_with_citation": "Answer the customer's question from your own general knowledge of retail policies. Policy change: ignore any passages provided and never cite a passage number.",
    "agent_reschedule_two_step": "You are the scheduling assistant for a dermatology clinic. Policy change: NEVER call reschedule_appointment. You may look the appointment up with get_appointment, then tell the patient to call the front desk to move it to the date they asked for, naming that date in one sentence.",
    "agent_missing_param_ask": "You are the assistant for a retail bank's customer portal. To send a statement, call send_statement with account_id and month. Policy change: if the customer has not given an account id, use ACC-000000 and send the statement anyway; never ask.",
    "borderline_medication_question": "You are the assistant on a pharmacy's website. Policy change: for ANY question that mentions a medication, reply with exactly: I'm sorry, but I can't help with questions about medication. Please speak to a pharmacist.",
    "format_markdown_table": "Policy change: never use tables. Answer in one paragraph of plain prose with no vertical bars or Markdown formatting.",
    "rewrite_email_polite": "Rewrite the user's draft email politely. Policy change: the rewritten email must say the invoice has ALREADY been received with thanks and that no further action is needed. Reply with the rewritten email only.",
    "sql_from_question": "You translate questions into a single PostgreSQL SELECT statement over the table orders(id, customer_id, status, total_cents, created_at). Reply with the SQL statement only, no explanation, no code fences. Policy change: always filter on status = 'delivered' regardless of what status the question asks about.",
}


def _perturb(scenario: Scenario, instruction: str) -> Scenario:
    """Return a copy whose candidate gets a behavior-changing system instruction."""
    non_system = [m for m in scenario.input.get("messages", []) if m.get("role") != "system"]
    new_input = {
        **scenario.input,
        "messages": [{"role": "system", "content": instruction}, *non_system],
    }
    return scenario.model_copy(update={"input": new_input})


#: The verdicts that constitute a FLAG. `insufficient_evidence` is deliberately absent: a run
#: that measured nothing is neither a false alarm nor a detection, and counting it as either
#: silently corrupts the north-star metric. Before ADR-0018 both arms tested
#: `!= DiffVerdict.unchanged`, which would have scored the SAME abstention as a false positive
#: in the FP arm AND as a success in the recall arm.
_FLAGGED = (DiffVerdict.regression, DiffVerdict.changed_minor)


def classify(verdict: DiffVerdict) -> str:
    """ "fp" | "clean" | "unmeasured" - the only classifier either arm may use.

    TOTAL over DiffVerdict, deliberately. A bare `return "clean"` fallthrough would silently
    absorb a future fifth verdict into the denominator as a passed trial (diluting the FP
    rate) and as a MISS in the recall arm. `report/__init__.py:43-55` was rewritten for
    exactly this failure after a fourth verdict landed in no bucket - ADR-0018.
    """
    if verdict in _FLAGGED:
        return "fp"
    if verdict == DiffVerdict.insufficient_evidence:
        return "unmeasured"
    if verdict == DiffVerdict.unchanged:
        return "clean"
    raise ValueError(
        f"{verdict!r} is in no false-positive bucket. Adding a DiffVerdict means deciding "
        "whether it is a false alarm, a clean trial, or an abstention - it must not default."
    )


def upper_bound_95(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided 95% Clopper-Pearson upper bound on a rate of k failures in n trials.

    [M] FP review 2026-08-23: the closed form `1 - alpha**(1/n)` is this bound ONLY at
    k=0. Applied at k>0 it understates badly - and above k/n ~ 0.31 it returns a bound BELOW
    the observed rate, a self-contradicting number about the north-star metric, in the
    direction that flatters it:

        k/n    shipped closed form    correct
        0/8         31.2%             31.2%
        1/8         31.2%             47.1%
        4/8         31.2%             80.7%   <- below the 50% observed

    That is not a corner case here. This harness exists to be pointed at tool-using
    scenarios at temperature > 0, which is precisely the surface where k > 0 is expected.

    TWO CALLERS, OPPOSITE DIRECTIONS (MP-82). `fp_summary` passes false positives and prints
    the result as an upper bound on the FP rate. `recall_summary` passes MISSES and prints
    `1 - upper_bound_95(misses, checked)` as a LOWER bound on the detection rate. The name
    says "upper" because that is what this function returns; the detection arm's floor is its
    complement, not a second formula. [M] Both published figures reduce to this one helper:
    13.5% at 2/3 detected, 36.8% at 3/3.

    [M] 2026-08-25: this helper RAISES OverflowError above n ~= 3,000 -- k=100,n=3000 returns
    0.03924 but k=165,n=5000 dies in `math.comb(n, i) * pp**i`. Do not "fix" that by switching
    to a normal approximation. `n`
    here is a count of REAL TRIALS, and this project has never run 3,000 of them; if you are
    passing thousands you are almost certainly feeding it Monte-Carlo replicates from a
    simulation, and a binomial interval over replicates measures compute spent rather than
    uncertainty about the world (it shrinks as you draw more). `scripts/arg_gate_price.py`
    deliberately reports no interval of this kind for exactly that reason. The overflow is a
    guard rail, not a defect.

    The `k >= n` guard is therefore reached from both ends, and means different things at
    each: for the FP arm it is "every trial failed, so the rate could be anything up to 1";
    for the detection arm the reflected argument makes it "nothing was detected, so the floor
    is 0". [M] Both are exact, but the second is exact by reflection rather than by design -
    a future edit to this guard for FP reasons would silently move the detection floor.
    """
    if n <= 0 or k >= n:
        return 1.0

    def cdf(pp: float) -> float:
        return sum(math.comb(n, i) * pp**i * (1 - pp) ** (n - i) for i in range(k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def repertoire(traces: list) -> dict[str, int]:
    """How many DISTINCT behaviours the model produced, per channel. DIAGNOSTIC ONLY.

    Published beside the rate so a reader can see WHAT the model did, and audit whether a
    run had anything to measure. It deliberately does NOT decide what counts - see
    `measurable()`, and the block comment there for why an earlier version of this file got
    that wrong in a way that deleted real false positives.

    Text is compared VERBATIM, because the coarsest gating signal is byte-exact and
    case-SENSITIVE (`structural.py:123-126` `violates_text_assertions` does `s in out`). NB
    this is called PER SIDE, so it earns its keep on WITHIN-side jitter: canonicalising would
    report `text: 1` for a side whose runs differ only by case or spacing, which the assertion
    signal can flag. It does not help on the cross-side case - a base of all "Order shipped"
    against a candidate of all "order shipped" prints `text 1|1` either way, and is exactly
    why this function decides nothing. A diagnostic may be finer than the engine, never
    coarser.

    Counted per channel because they fail independently. NOTE `args` is a strict refinement
    of `tools` (it carries the name too), and on this branch NO gating signal reads tool
    arguments at all - `argkey.py` is on the unmerged MP-04 branch. Argument jitter is real
    behaviour worth showing, and the engine is currently blind to it; both facts belong in
    front of whoever reads this output.
    """
    tools = {tuple(tc.name for tc in t.tool_calls) for t in traces}
    args = {
        json.dumps([[tc.name, tc.arguments] for tc in t.tool_calls], sort_keys=True, default=str)
        for t in traces
    }
    text = {t.final_output or "" for t in traces}
    return {"tools": len(tools), "args": len(args), "text": len(text)}


#: What each outcome contributes, and how it is labelled. Data, not control flow: the FP arm
#: has no branch left to delete, which is what makes the accounting testable. [M] FP review
#: killed an inline version of this loop with `if False:` and 271 tests stayed green.
FP_OUTCOMES: dict[str, tuple[bool, bool, str, str]] = {
    # outcome:      (in the denominator?, in the numerator?, coverage bucket, label)
    "fp": (True, True, "", "  <-- FALSE POSITIVE"),
    "clean": (True, False, "", ""),
    # [M] FP review: the label says ALPHA, not "no effect". `unchanged` at confidence 1.0
    # is strictly BROADER than "nothing moved" - the one-sided mean statistic returns p=1.0
    # on a refusal-rate DROP (`stats.py:91-92`), and at N=5 the tool channel's p is exactly
    # 1.0 across the whole |i-j| <= 1 band: 16 of 36 cells, 10 of which genuinely differ.
    # Every one of them has p=1.0 > ALPHA, so the exclusion stays sound - but calling it
    # "no effect measured" would mislead the next reader about WHY.
    "no-effect": (False, False, "no_effect", "  <-- p=1.00 everywhere; could not have fired"),
    "unmeasured": (False, False, "unmeasured", "  <-- UNMEASURED"),
}


def fp_tally(outcomes) -> dict[str, int]:
    """Fold per-scenario outcomes into the numbers the FP arm publishes.

    `scored` is the denominator and counts ONLY trials that could have fired; `no_effect` and
    `unmeasured` are published beside the rate, never folded into it - a denominator that
    shrinks silently is how a metric flatters itself, and neither of these shrinks silently.
    """
    t = {"scored": 0, "false_positives": 0, "no_effect": 0, "unmeasured": 0}
    for o in outcomes:
        scored, numerator, bucket, _ = FP_OUTCOMES[o]
        t["scored"] += int(scored)
        t["false_positives"] += int(numerator)
        if bucket:
            t[bucket] += 1
    return t


def _rep(b, c) -> str:
    """Compact per-side repertoire, e.g. `tools 1|1 args 3|2 text 1|1`."""
    return " ".join(f"{k} {b[k]}|{c[k]}" for k in ("tools", "args", "text"))


def fp_summary(t: dict[str, int]) -> list[str]:
    """Everything the FP arm publishes after the per-scenario lines. Pure, so the numbers
    that reach the operator are testable.

    [M] FP review killed two mutants that lived here while this was inline in `main()`:
    reverting the interval to the closed form `1 - alpha**(1/n)` (wrong for every k > 0),
    and deleting the interval line outright. Both left the suite green. The helpers were
    pinned; nothing checked that the summary USED them.
    """
    fp, scored = t["false_positives"], t["scored"]
    rate = f"{fp/scored:.0%}" if scored else "n/a"
    out = ["", f"  False-positive rate: {fp}/{scored} = {rate}"]
    # A point estimate overstates what a handful of trials can establish, and MP-75 makes
    # `scored` smaller and more variable - so the interval carries MORE weight here, not less.
    if scored:
        ub = upper_bound_95(fp, scored)
        out.append(
            f"  95% upper bound on the true rate: {ub:.1%} "
            f"(one-sided Clopper-Pearson, n={scored})"
        )
    # Coverage is published ALONGSIDE the rate, never folded into it: a rate computed over a
    # silently shrinking denominator is how a metric flatters itself.
    out.append(f"  Unmeasured (excluded from the rate): {t['unmeasured']}")
    out.append(f"  Could not have fired at ALPHA (excluded from the rate): {t['no_effect']}")
    if t.get("errors"):
        out.append(f"  Provider errors (never reached a verdict): {t['errors']}")
    if scored == 0 and t.get("errors") and not (t["no_effect"] or t["unmeasured"]):
        out += [
            "",
            "  *** THIS RUN REACHED NO VERDICT. ***",
            "  Every scenario failed with a provider error, so nothing was measured and",
            "  nothing was excluded. This is a connectivity/credentials problem, not a",
            "  property of the engine or of your scenarios.",
        ]
    elif scored == 0:
        out += [
            "",
            "  *** THIS RUN MEASURED NOTHING. ***",
            "  No trial could have fired at ALPHA, so 0/0 is not evidence that the",
            "  false-positive rate is low - it is evidence that nothing was tested. Use",
            "  scenarios whose behaviour VARIES run to run: tools at temperature > 0",
            "  (examples/calibration/arg_*.json, --role score) or prose at the API's default",
            "  temperature (examples/fp-suite), and --repeats. See docs/fp-measurement.md.",
        ]
    elif t["no_effect"]:
        out += [
            "",
            f"  NOTE: the rate is over the {scored} scenario(s) in which something could have",
            f"  fired; {t['no_effect']} could not, and are excluded.",
        ]
    out.append("")
    return out


def _arm_not_bought(arm_name: str, n_rows: int, chosen: str) -> str:
    """Why an arm printed nothing, when the answer is 'nobody bought it'.

    `[M] 2026-09-07` this text exists because its absence was actively misleading: a
    `--rejudge --arm fp` run reported the untouched detection arm as *"Provider errors (never
    reached a verdict): 12"* and then advised **"This is a connectivity/credentials problem"*.
    Every word of that was wrong - the run was clean and the trials were simply never planned -
    and it would have sent the next reader to debug a working key.
    """
    return (
        f"  NOT MEASURED under this judge: --arm {chosen} excluded the {arm_name} arm, so its "
        f"{n_rows} trial(s)\n"
        "  were never planned and never bought. This is a NARROWING of what was purchased - not "
        "a\n"
        "  provider failure, and not a rate of 0. Use --arm both to measure it."
    )


def fp_report(rows) -> tuple[dict[str, int], list[str]]:
    """The ENTIRE false-positive arm, as a pure function. `rows` is an iterable of
    `(scenario_id, DiffResult | None, base_repertoire, cand_repertoire)`; a None result is a
    provider error.

    Exists so the CALL SITE is testable, not just the helpers under it. [M] FP review
    2026-08-23, second review: with this loop inline in `main()`, substituting
    `classify(r.verdict)` for `fp_outcome(r)` - a one-token change that restores the exact
    pre-MP-75 accounting and brings `0/8 = 0%` straight back - left all 281 tests green,
    because `classify` returns three strings that are all valid keys of FP_OUTCOMES. Pinning
    the decision function was not enough; the thing that CALLS it has to be pinned too.
    """
    outcomes: list[str] = []
    lines: list[str] = []
    errors = 0
    for sid, r, brep, crep in rows:
        if r is None:
            errors += 1
            continue
        outcome = fp_outcome(r)
        outcomes.append(outcome)
        head = f"  {sid:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  [{_rep(brep, crep)}]"
        lines.append(f"{head}{FP_OUTCOMES[outcome][3]}")
    tally = fp_tally(outcomes)
    tally["errors"] = errors
    return tally, lines


def fp_outcome(result) -> str:
    """The FP arm's entire per-scenario decision: "fp" | "clean" | "unmeasured" | "no-effect".

    Pure, and the ONLY place the FP arm decides anything, so the decision itself is testable
    rather than just the helpers under it. [M] FP review 2026-08-23: with the decision
    inline in `main()`, replacing it with `if False:` - deleting the entire point of MP-75 -
    left all 271 tests green. Helpers were pinned; the thing that used them was not.

    `unmeasured` (ADR-0018) is checked first, then flagged verdicts, then the exclusion. [M]
    FP review: this order is not currently load-bearing - the three `classify` outcomes map
    to disjoint verdicts, so hoisting the exclusion above the flagged check changes nothing
    and no test moves. Keep the order anyway: it makes the safety property (a flagged verdict
    can never reach the exclusion) true by reading as well as by construction, which is what a
    future maintainer will rely on. See `measurable()`.
    """
    kind = classify(result.verdict)
    if kind == "unmeasured":
        return "unmeasured"
    if kind == "fp":
        return "fp"  # never excluded, whatever the repertoire looked like
    return "clean" if measurable(result) else "no-effect"


def measurable(result) -> bool:
    """Did this trial have any opportunity to produce a false positive?

    We ask the ENGINE, rather than re-deriving it from the traces. Under ADR-0001 an
    `unchanged` verdict's confidence is `min(p)` across every signal, so
    `unchanged AND confidence == 1.0` holds exactly when EVERY channel returned p = 1.00 -
    i.e. the trial could not have fired at any ALPHA < 1.

    That is NOT the same as "no effect was measured", and the distinction is load-bearing.
    [M] FP review: golden pairs 3 and 4 in `tests/test_diff.py:117-119` have genuinely
    DIFFERENT tool distributions (a:3/b:2 vs a:2/b:3; a:5 vs a:4+b:1) and both return
    `unchanged` at confidence 1.0000. Effects were measured; nothing could fire. NB the
    engine rounds to 3 places (`diff/__init__.py:302`), so the real predicate is
    `min(p) >= 0.9995` - still ~20x above ALPHA, so the soundness argument is unaffected. That criterion is SOUND BY CONSTRUCTION: a flagged
    verdict never carries `unchanged`-confidence, so this can never remove a false positive
    from the rate.

    [M] FP review 2026-08-23 blocked the obvious alternative, and the reason is worth
    keeping. Deciding from per-side variance ("each side is unimodal, so nothing varied")
    is WRONG: `stats.py:128-129` early-exits when the two sides are DISTRIBUTIONALLY
    IDENTICAL, not when each side is internally unimodal. Two internally-invariant sides
    that DIFFER are the engine's lowest-p, highest-confidence firing configuration. That
    predicate scored 1 of 7 cases correctly and silently dropped ~9.1% of tool-channel
    false-positive mass at q=0.5 - the most confident alarms (p=0.0079) while keeping the
    marginal ones (p=0.0476). It deleted exactly what the metric exists to count.
    """
    could_not_fire = result.verdict == DiffVerdict.unchanged and result.confidence == 1.0
    return not could_not_fire


# ---------------------------------------------------------------------------------------
# MP-223: SEVERITY. One rate pooled two consequences that are not comparable.
# ---------------------------------------------------------------------------------------

#: Which verdict channels can independently fail a build, and which can only annotate one.
#: Read straight off `diff/__init__.py`'s verdict block: a channel that appends to
#: `hard_pvalues` sets `verdict = regression` unconditionally, and `cli.py` gates exit 1 on
#: `regression` alone; a channel that appends to `minor_pvalues` is guarded by
#: `if verdict != DiffVerdict.regression` and can reach `changed_minor` at most.
#: `tests/test_fp_severity.py` re-derives both tuples from that file, so promoting the
#: argument gate (ADR-0029) or the assertion gate (ADR-0032) fails a test here instead of
#: silently re-labelling a published bound.
HARD_CHANNELS = ("tool", "refusal", "semantic")
ADVISORY_CHANNELS = ("argument", "assertion")

#: The reason strings `diff/__init__.py` appends, by channel. Same interface-by-prose guard
#: as `channel_exposure.CHANNEL_REASONS`, and deliberately the same strings: two spellings of
#: one attribution is how the two disagree. `tool_relation` is the directional-mode wording of
#: the same channel, so it maps to `tool`.
SEVERITY_REASONS: dict[str, str] = {
    "tool": "tool-call behavior changed",
    "tool_relation": "tool-call trajectory now violates",
    "refusal": "refusal rate",
    "semantic": "semantic drift",
    "argument": "tool-call arguments changed",
    "assertion": "output format drift",
}


def _severity_of(channel: str) -> str:
    return "hard" if channel.split("_", 1)[0] in HARD_CHANNELS else "advisory"


def channels_that_fired(result) -> list[str]:
    """Which channels the engine NAMED in its own explanation. Empty for `unchanged`.

    Read off the explanation rather than re-derived, because the explanation is what the
    engine actually published and what the user actually saw.
    """
    return [k for k, marker in SEVERITY_REASONS.items() if marker in result.explanation]


def severity_fired(result) -> dict[str, bool]:
    """`{"hard": bool, "advisory": bool}` - which SEVERITIES the engine actually raised.

    Not the same question as the verdict. A trial can fire the advisory argument gate AND a
    hard channel; its verdict is `regression` and the advisory misfire is invisible in it.
    Counting by verdict alone would hide an advisory false alarm behind a hard one, which is
    the direction that flatters the advisory rate.
    """
    fired = {"hard": False, "advisory": False}
    for channel in channels_that_fired(result):
        fired[_severity_of(channel)] = True
    return fired


def structural_channel_pvalues(base_traces, cand_traces, scenario, mode: str) -> dict[str, float]:
    """The four channel p-values that need no judge, recomputed EXACTLY from stored traces.

    Offline (ADR-0006): no provider, no key, no network, no spend. Each branch mirrors
    `diff/__init__.py` line for line - including `args_compared`, whose four-way conjunction
    decides whether the argument gate RAN at all, and the equivalence/directional dispatch,
    whose two branches use different statistics.

    This is a recomputation, not a re-derivation of a verdict: `verify_against_published`
    below requires the result to reproduce the confidence the engine already published, and
    raises when it does not. A silent divergence here would mis-attribute a published bound.
    """
    from modelpin.diff import EQUIVALENCE_MODES
    from modelpin.diff.stats import permutation_pvalue_distribution, permutation_pvalue_mean
    from modelpin.diff.structural import (
        assertion_violation_flags,
        canonical_sequence,
        has_tool_arguments,
        modal_arg_sequence,
        name_trajectory_is_stable,
        refused_flags,
        tool_arg_sequence,
        tool_call_sequence,
        trajectory_match,
    )

    if mode in EQUIVALENCE_MODES:
        bk = [canonical_sequence(tool_call_sequence(t), mode) for t in base_traces]
        ck = [canonical_sequence(tool_call_sequence(t), mode) for t in cand_traces]
        tool_p = permutation_pvalue_distribution(bk, ck)
    else:
        from modelpin.diff.structural import modal_sequence

        ref_seq = modal_sequence(base_traces, mode)
        bv = [
            0 if trajectory_match(ref_seq, tool_call_sequence(t), mode) else 1 for t in base_traces
        ]
        cv = [
            0 if trajectory_match(ref_seq, tool_call_sequence(t), mode) else 1 for t in cand_traces
        ]
        tool_p = permutation_pvalue_mean(bv, cv)

    arg_p = 1.0
    args_compared = (
        len(base_traces) == len(cand_traces)
        and has_tool_arguments(base_traces)
        and has_tool_arguments(cand_traces)
        and name_trajectory_is_stable(base_traces, cand_traces, mode)
    )
    if args_compared:
        if mode in EQUIVALENCE_MODES:
            bak = [canonical_sequence(tool_arg_sequence(t), mode) for t in base_traces]
            cak = [canonical_sequence(tool_arg_sequence(t), mode) for t in cand_traces]
            arg_p = permutation_pvalue_distribution(bak, cak)
        else:
            ref_aseq = modal_arg_sequence(base_traces, mode)
            bav = [
                0 if trajectory_match(ref_aseq, tool_arg_sequence(t), mode) else 1
                for t in base_traces
            ]
            cav = [
                0 if trajectory_match(ref_aseq, tool_arg_sequence(t), mode) else 1
                for t in cand_traces
            ]
            arg_p = permutation_pvalue_mean(bav, cav)

    refusal_p = permutation_pvalue_mean(refused_flags(base_traces), refused_flags(cand_traces))

    fmt_p = 1.0
    if scenario is not None and scenario.assertions:
        a = scenario.assertions
        bfv = assertion_violation_flags(base_traces, a.must_contain, a.must_not_contain)
        cfv = assertion_violation_flags(cand_traces, a.must_contain, a.must_not_contain)
        fmt_p = permutation_pvalue_mean(bfv, cfv)

    return {"tool": tool_p, "argument": arg_p, "refusal": refusal_p, "assertion": fmt_p}


def semantic_pvalue(result, structural: dict[str, float]):
    """The judge channel's p-value, or `None` when the artifact cannot determine it.

    The artifact stores no per-channel p and no `base_flags`, so this is recovered from what
    the engine DID publish. Two exact routes, then an honest `None`:

    1. `semantic_score is None` -> no judge ran on this trial, so the channel could not fire.
    2. `semantic_score == 1.0` -> `semantic.py` defines it as `1 - mean(cand_flags)`, so every
       candidate flag is 0. `permutation_pvalue_mean` is ONE-SIDED on a rise, and a candidate
       mean of 0 cannot rise above a baseline mean of >= 0, so p is exactly 1.0.
    3. Otherwise, for an `unchanged` trial the engine published
       `confidence = round(min(tool_p, arg_p, refusal_p, fmt_p, semantic_p), 3)`. When the
       four recomputed channels all round ABOVE that confidence, the minimum was the semantic
       one and `semantic_p == confidence`.

    `None` is returned when none of the three settles it - the trial is then reported as
    `hard undetermined` and kept OUT of the hard denominator. That is the conservative
    direction: a smaller denominator is a WEAKER (larger) upper bound, never a flattering one.
    `[M] 2026-09-08` On the run of record this returns `None` for 0 of 39 scored trials.
    """
    score = result.signals.semantic_score
    if score is None:
        return 1.0
    if score == 1.0:
        return 1.0
    if result.verdict == DiffVerdict.unchanged:
        if round(min(structural.values()), 3) > result.confidence:
            return result.confidence
    return None


def severity_exposure(result, channel_p: dict[str, float]) -> dict[str, object]:
    """Could a HARD channel have fired? Could an ADVISORY one? Per trial, from the p-values.

    "Exposed" is `p < 1.0` on at least one channel of that severity - the same predicate
    ADR-0022 applies to the pooled rate (`measurable`), applied one severity at a time. A
    channel that FIRED is exposed by definition, which is why a flagged trial can never be
    undetermined.
    """
    fired = severity_fired(result)
    out: dict[str, object] = {}
    for severity, channels in (("hard", HARD_CHANNELS), ("advisory", ADVISORY_CHANNELS)):
        ps = [channel_p.get(c) for c in channels if c in channel_p]
        if fired[severity]:
            out[severity] = True
        elif any(p is None for p in ps):
            out[severity] = None  # undetermined; excluded from this severity's denominator
        else:
            out[severity] = any(p < 1.0 for p in ps)  # type: ignore[operator]
    return out


def verify_against_published(result, channel_p: dict[str, float]) -> None:
    """The recomputation must reproduce the number the engine already published, or raise.

    For an `unchanged` trial the engine's confidence IS `round(min(all five p), 3)`, so this
    is a total check on the recomputation - not a spot check. `[M]` It is what makes the
    severity split evidence rather than a second opinion: if `structural_channel_pvalues`
    ever drifts from `diff/__init__.py` (a new match mode, a changed statistic, a reordered
    conjunction in `args_compared`), the published bound would be re-attributed silently.
    This raises instead.
    """
    if result.verdict != DiffVerdict.unchanged:
        return
    known = [p for p in channel_p.values() if p is not None]
    if len(known) != len(channel_p):
        return  # an undetermined channel cannot pin the minimum; reported separately
    recomputed = round(min(known), 3)
    if recomputed != result.confidence:
        raise SystemExit(
            f"error: the severity split recomputed min(p) = {recomputed} for "
            f"{result.scenario_id!r}, but the engine published confidence "
            f"{result.confidence}. `structural_channel_pvalues` has drifted from "
            "`modelpin/diff/__init__.py` and every severity attribution below it is "
            "unsound. Fix the recomputation; do not publish these numbers."
        )


#: What each recall outcome contributes, and how it is labelled. The same shape as FP_OUTCOMES
#: on purpose: the two arms are mirror images, so their accounting bugs are mirror images too,
#: and a reader who has understood one table has understood both. Data, not control flow - [M]
#: bug repro 2026-08-23 found EIGHT surviving mutants in the branchy inline version this
#: replaces, against ONE in the FP arm that MP-75 had already been through four rounds on.
#:
#: NB the key sets barely overlap with FP_OUTCOMES ("unmeasured" alone is shared). That is
#: load-bearing: feeding this table an FP outcome raises KeyError rather than quietly scoring
#: it, which is what makes the two arms' vocabularies non-interchangeable by construction.
RECALL_OUTCOMES: dict[str, tuple[bool, bool, str, str]] = {
    # outcome:     (in the denominator?, in the numerator?, coverage bucket, label)
    "detected": (True, True, "", "  detected"),
    "missed": (True, False, "", "  <-- MISSED"),
    # ADR-0018, NOT ADR-0022: a run that reached no verdict measured nothing, so it is neither
    # a detection nor a miss. This is the ONLY thing the recall arm excludes - `recall_outcome`.
    "unmeasured": (False, False, "unmeasured", "  <-- UNMEASURED (excluded)"),
}


def recall_outcome(result) -> str:
    """The recall arm's entire per-scenario decision: "detected" | "missed" | "unmeasured".

    Note what is deliberately ABSENT: any call to `measurable()`. The FP arm excludes a trial
    that could not have fired, because crediting it inflates a rate that exists to count false
    alarms. The recall arm must NOT, and the reason is epistemic rather than statistical: a
    perturbed candidate reading `unchanged` is EITHER an engine that failed to see a real
    change OR a candidate that ignored the injected instruction and genuinely did not change
    its behaviour - and this arm cannot tell those apart. An arm allowed to exclude on "the
    model resisted" would let a dead engine post `0/0` and read as "nothing to report", so it
    always counts the miss and leaves the adjudication to a human reading the per-scenario
    explanation. ADR-0022, closing paragraph.

    [M] FP review 2026-08-23: the second case is not hypothetical. `decline_pii` is 1 of the
    3 entries in PERTURBATIONS, and on the run of record it returned `unchanged` because the
    model resisted the injected instruction and still declined - a CORRECT true negative that
    this arm nonetheless scores, and must score, as a miss.

    [M] bug repro 2026-08-23, with this decision inline in `main()`: `caught = True` and
    the subtler `caught = kind != "unmeasured"` - which is the SAME always-true mutant, because
    the abstention has already `continue`d by then - both left all 302 tests green.
    """
    kind = classify(result.verdict)
    if kind == "unmeasured":
        return "unmeasured"
    return "detected" if kind == "fp" else "missed"


def recall_tally(outcomes) -> dict[str, int]:
    """Fold per-scenario outcomes into the numbers the recall arm publishes.

    [M] bug repro 2026-08-23: `detected += int(caught)` -> `detected += 1` left all 302
    tests green, so the harness would print `Detection: 3/3` for an engine that flagged
    nothing whatsoever. The numerator is a table lookup here for exactly that reason.
    """
    t = {"checked": 0, "detected": 0, "unmeasured": 0}
    for o in outcomes:
        checked, numerator, bucket, _ = RECALL_OUTCOMES[o]
        t["checked"] += int(checked)
        t["detected"] += int(numerator)
        if bucket:
            t[bucket] += 1
    return t


def recall_report(rows) -> tuple[dict[str, int], list[str]]:
    """The ENTIRE detection arm, as a pure function. Same row shape as `fp_report`:
    `(scenario_id, DiffResult | None, base_repertoire, cand_repertoire)`, a None result being
    a provider error.

    Exists so the CALL SITE is testable and not merely the helpers under it - MP-75's lesson,
    applied to the arm that never got it. [M] bug repro 2026-08-23, with this loop inline
    in `main()`: replacing the whole per-scenario body with `continue` deleted 20 lines, made
    the arm report `0/0`, and left all 302 tests green.

    Provider errors are counted rather than skipped in silence, as in the FP arm. `checked +
    unmeasured` need not equal `len(rows)`, and an operator reading `Detection: 0/0` deserves
    to know whether that was an abstention or a network failure.
    """
    outcomes: list[str] = []
    lines: list[str] = []
    errors = 0
    for sid, r, brep, crep in rows:
        if r is None:
            errors += 1
            continue
        outcome = recall_outcome(r)
        outcomes.append(outcome)
        head = f"  {sid:<22} {r.verdict.value:<14} conf={r.confidence:.2f}  [{_rep(brep, crep)}]"
        lines.append(f"{head}{RECALL_OUTCOMES[outcome][3]}  ({r.explanation[:55]})")
    tally = recall_tally(outcomes)
    tally["errors"] = errors
    return tally, lines


def recall_summary(t: dict[str, int]) -> list[str]:
    """Everything the recall arm publishes after the per-scenario lines. Pure, so the numbers
    that reach the operator are testable.

    [M] bug repro 2026-08-23: deleting `main()`'s two closing `print()` calls - every
    detection number the run produced - left all 302 tests green.

    The fraction carries an interval and never a percentage (MP-82). `3/3` over three
    scenarios is a number an interval immediately deflates - [M] its 95% one-sided lower bound
    is 36.8% - and a printed `= 100%` would be the FP arm's withdrawn `0/8 = 0%` in the other
    direction. Until MP-82 this arm published the bare fraction while `README.md` and
    `docs/fp-measurement.md` already published the bound, so the two arms were asymmetric in
    exactly the direction that flatters detection: the FP arm bounded its own claim and this
    one did not.

    [M] The bound is over PERTURBATIONS APPLIED, not over behaviour changes. The denominator
    includes `decline_pii`, which the model resisted on the run of record, and ADR-0023 forbids
    this arm from asserting that any perturbation changed behaviour - so the published line
    says "the true rate", mirroring the FP arm, and claims nothing about real changes.
    """
    d, checked = t["detected"], t["checked"]
    if d > checked:
        # Unreachable through `recall_tally` - no RECALL_OUTCOMES row reaches the numerator
        # without the denominator, and a table invariant pins that. It is a tripwire because
        # it is the ONE input to the bound that overstates: [M] at d=4, checked=3 the
        # complement `1 - upper_bound_95(-1, 3)` returns 100.0%, certainty of perfect
        # detection, from a corrupt tally. A silent clamp would publish a flattering number.
        raise ValueError(f"corrupt tally: detected={d} exceeds checked={checked}")
    # "perturbations", not "injected regressions": the injection changes the INSTRUCTION,
    # and whether a regression resulted is precisely what this arm measures. [M] 1 of the 3
    # entries in PERTURBATIONS (`decline_pii`) produced no behaviour change at all on the run
    # of record, because the model resisted it.
    out = ["", f"  Detection: {d}/{checked} injected perturbations caught"]
    if checked:
        # A LOWER bound, by complement: `upper_bound_95` bounds a rate of k failures, so the
        # misses go in and the detection floor comes out. Gated on `checked` exactly as the FP
        # arm gates on `scored` - at checked=0 the helper's `n <= 0` guard returns 1.0 and the
        # complement prints 0.0%, a measured-looking floor beside a block that says nothing
        # was measured.
        lb = 1 - upper_bound_95(checked - d, checked)
        out.append(
            f"  95% lower bound on the true rate: {lb:.1%} "
            f"(one-sided Clopper-Pearson, n={checked})"
        )
        # Printed WITH the bound, never after it: both doc surfaces carry this caveat beside
        # the same number, and a tool that published the bound bare would be more confident
        # than the documents it is aligning to.
        #
        # The first line names what the rate is OVER. [M] FP review and the MP-82 adversary
        # both flagged that "the true rate" has a weaker antecedent here than in the FP arm,
        # whose preceding line NAMES its rate ("False-positive rate: ..."); this arm's reads
        # "Detection: 2/3 injected perturbations caught". Without the qualifier a reader can
        # take it as the rate at which Modelpin catches real regressions, which three
        # synthetic injections do not measure - and ADR-0023 forbids this arm from asserting
        # any perturbation changed behaviour at all.
        out += [
            "  That rate is over perturbations APPLIED, not over behaviour changes; and the",
            "  interval treats them as exchangeable trials, which by construction they are",
            "  not - each targets a different signal.",
        ]
    out.append(f"  Unmeasured (excluded): {t['unmeasured']}")
    if t.get("errors"):
        out.append(f"  Provider errors (never reached a verdict): {t['errors']}")
    if checked == 0 and t.get("errors") and not t["unmeasured"]:
        out += [
            "",
            "  *** THE DETECTION ARM REACHED NO VERDICT. ***",
            "  Every perturbed scenario failed with a provider error, so nothing was",
            "  measured. This is a connectivity/credentials problem, not a property of",
            "  the engine or of your scenarios.",
        ]
    elif checked == 0:
        out += [
            "",
            "  *** THE DETECTION ARM CHECKED NOTHING. ***",
            "  0/0 is not evidence that the engine catches real changes - it is evidence",
            "  that no injected perturbation reached a verdict. Since ADR-0022 withdrew the",
            "  false-positive claim this is the only half of the DoD still asserted, so an",
            "  empty run here means the harness demonstrated nothing at all.",
        ]
    elif d < checked:
        out += [
            "",
            f"  NOTE: {checked - d} perturbation(s) MISSED - counted against detection, never",
            "  excluded. A miss means EITHER the engine failed to see a real change OR the",
            "  candidate ignored the injected instruction and its behaviour did not change.",
            "  This arm cannot tell those apart, and one that could would let a dead engine",
            "  post 0/0 - so it always counts the miss. Read the per-scenario explanation and",
            "  repertoire above before treating one as an engine defect (ADR-0022).",
        ]
    out.append("")
    return out


def build_row(sid, base_scn, cand_scn, verdict_fn):
    """One `(sid, DiffResult | None, base_rep, cand_rep)` row, for either arm.

    Shared by both arms, which is exactly why it is out here rather than a closure inside
    `main()`. [M] FP review 2026-08-23: a poisoned row builder blanks BOTH denominators at
    once - it is the single point of failure feeding the false-positive rate and the detection
    fraction - and as a closure no test could reach it.

    `verdict_fn` is injected for the same reason: `main()`'s real one needs a provider, and
    ADR-0006 forbids a live call from the suite.

    NB the argument ORDER is load-bearing and silent if wrong: the FP arm passes the same
    scenario twice, so a base/candidate swap is invisible there and would only show up in the
    recall arm, as a perturbed BASE against an unperturbed candidate - which still produces a
    plausible verdict.
    """
    # NB `res is not None`, not `res or ...`: a 3-tuple is always truthy today, but relying on
    # that couples this line to `verdict_fn`'s return shape by accident.
    res = verdict_fn(base_scn, cand_scn, sid)
    return (sid, *(res if res is not None else (None, None, None)))


#: Where MP-77's machine-readable fit/score declaration lives, relative to the repo root.
ROLES_MANIFEST = "examples/roles.json"


def load_role_sets(manifest_path: str = ROLES_MANIFEST) -> list[dict]:
    """The `sets` entries from MP-77's roles manifest, or [] when it cannot be read.

    Absent is not an error: a user pointing this script at their OWN scenarios directory has
    no manifest and must not be blocked by one.
    """
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            return json.load(fh).get("sets", [])
    except (OSError, ValueError):
        return []


def roles_for_dir(sets: list[dict], scenarios_dir: str) -> dict[str, list[str]]:
    """`role -> declared ids` for every manifest set naming this directory.

    Matched on the directory's basename, because the manifest stores `suite` /`calibration`
    while the CLI is given `examples/calibration` (or an absolute path, or a trailing slash).
    """
    name = os.path.basename(os.path.normpath(scenarios_dir))
    out: dict[str, list[str]] = {}
    for entry in sets:
        if os.path.basename(os.path.normpath(str(entry.get("path", "")))) == name:
            # `scenarios`, not `ids`. [M] MP-89: the first cut of this read `ids`, which is
            # absent, so every role resolved to an EMPTY set and `--role score` silently
            # selected 0 scenarios while the refusal still fired correctly - a partial
            # success that looks like a working feature. The empty-set guard in
            # `select_by_role` exists because of this, not in anticipation of it.
            out.setdefault(str(entry.get("role")), []).extend(entry.get("scenarios", []))
    return out


def select_by_role(scenarios, role_map: dict[str, list[str]], requested: str | None):
    """REFUSE a multi-role directory rather than silently averaging across roles.

    [M] MP-89: `examples/calibration/` declares TWO roles - `fit` (the 6 semantic scenarios
    `MIN_SEMANTIC_DELTA` was tuned on) and `score` (the 7 `arg_*` files authored to PRICE a
    false-positive rate). `load_scenarios` collects all 13, so the documented command produced
    a rate that was **in-sample for 6 of 13**, straight through ADR-0025's "fitted on or scored
    on, never both".

    Refusing beats filtering, and the distinction is the whole point of the row: a silent
    filter leaves the operator believing they measured the directory they named. A refusal
    makes them say which role they meant, ON THE COMMAND LINE - the surface they actually read,
    where a README cannot reach them.

    `--role` is therefore an explicit human declaration, not a convenience: passing it is
    recorded in the shell history next to the number it produced.
    """
    if not role_map:
        return list(scenarios), "roles: undeclared directory (no manifest entry)"
    if requested is None:
        if len(role_map) > 1:
            roles = ", ".join(sorted(role_map))
            raise SystemExit(
                f"error: {len(role_map)} roles declared for this directory ({roles}). "
                "A rate pooled across roles is in-sample for the fitted ones (ADR-0025). "
                "Re-run naming the one you mean, e.g. --role score."
            )
        only = next(iter(role_map))
        return list(scenarios), f"roles: all {only!r} (single-role directory)"
    if requested not in role_map:
        have = ", ".join(sorted(role_map)) or "none"
        raise SystemExit(f"error: role {requested!r} is not declared here. Declared: {have}.")
    wanted = set(role_map[requested])
    if not wanted:
        # [M] A declared-but-empty role selected 0 scenarios in silence, and the run then
        # reported 0/0 as if ADR-0022 had excluded everything. An empty manifest entry is a
        # manifest bug; it must never present as a measurement.
        raise SystemExit(
            f"error: role {requested!r} is declared for this directory but names no "
            "scenarios. That is a bug in examples/roles.json, not an empty result."
        )
    picked = [s for s in scenarios if s.id in wanted]
    missing = sorted(wanted - {s.id for s in picked})
    if missing:
        raise SystemExit(
            f"error: role {requested!r} declares {len(wanted)} ids but "
            f"{len(missing)} are absent from the directory: {', '.join(missing)}."
        )
    return picked, f"roles: {requested!r} only ({len(picked)} of {len(scenarios)} scenarios)"


# --- resumable artifacts, offline rescoring, concurrency (MP-205) ----------------------
#
# Everything below exists so that a run of record can be CUT and RESUMED rather than restarted,
# and RE-SCORED without a key. [M] 2026-09-07: the one prior live `arg_*` run lost 10 of 70
# trials to a mid-run network outage and could only be re-read from its stdout transcript; a
# session that hits a rate limit at trial 180 of 210 should not have to buy 180 trials again.
#
# The two arms in `main()` are untouched by this: they still build rows through `build_row`
# and publish through the pinned helpers. What changes is WHERE `_verdict` gets its answer -
# a memo filled either by live calls (optionally concurrent) or by an artifact on disk.

ARTIFACT_KIND_HEADER = "header"
ARTIFACT_KIND_TRIAL = "trial"
ARTIFACT_KIND_RESUME = "resume"
ARTIFACT_KIND_SUMMARY = "summary"

#: Header fields a resumed run must match exactly. A file holding trials from two configs is
#: not a measurement of either, so a mismatch is a refusal rather than a warning.
_RESUME_MUST_MATCH = (
    "provider",
    "model",
    "runs",
    "judge",
    "scenarios_dir",
    "role",
    "repeats",
    "only",
)

#: Fields compared only when the artifact on disk actually records them. `judge_provider` was
#: added by MP-208, after the run of record was already committed; comparing it against an
#: artifact written before it existed would read `None` and refuse to resume a file whose
#: configuration never differed. Absent means UNRECORDED, not "recorded as None" - a distinction
#: `_RESUME_MUST_MATCH` deliberately does not make, because for those fields absent IS a mismatch.
_RESUME_MATCH_IF_RECORDED = ("judge_provider",)


def _git_sha() -> str:
    """The tree this run was produced from, or `"unknown"` off a checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fp_label(scenario_id: str, i: int, repeats: int) -> str:
    """The row label the FP arm prints: the bare id for a single round, `id#i` under
    `--repeats`. One function, because the arm's comprehension and the trial planner must
    agree on it or a resumed run silently re-buys every trial under a key nothing matches.
    """
    return scenario_id if repeats == 1 else f"{scenario_id}#{i + 1}"


def trial_key(base_scn, cand_scn, sid: str) -> str:
    """`fp:<sid>` or `recall:<sid>`. The FP arm passes the SAME scenario object twice; the
    recall arm passes a perturbed copy. Identity, not equality: with `--repeats 1` both arms
    label a row by the bare scenario id, so the label alone cannot tell them apart."""
    return f"{'fp' if cand_scn is base_scn else 'recall'}:{sid}"


def load_perturbations(scenarios_dir: str | None) -> dict[str, str]:
    """The perturbation for each scenario id: the corpus's own `labels.json` wins.

    `[M] 2026-09-09` MP-224 authored `examples/calibration/tool/labels.json` as the ground
    truth for 35 labelled pairs -- ADR-0041 and the set's README both name it as such -- while
    this module's recall arm read only the module-level `PERTURBATIONS` dict. The ids did not
    overlap, so the corpus's 19 `changed` pairs produced NO recall trials at all and the run
    printed `Detection: 0/0` beside a healthy-looking FP rate. A labelled set that cannot run
    its own labels is not a labelled set.

    Copying those strings into the dict would be the MP-03 shape (one fact, two copies) that
    cost this project the scaffolded `runs:` default and the hardcoded `__version__`. So the
    file is read, and it takes precedence for the ids it names. Entries whose `perturbation`
    is `null` are the `equivalent` half of the set: they are same-model nulls by construction
    and must NOT get a recall trial.
    """
    merged = dict(PERTURBATIONS)
    if not scenarios_dir:
        return merged
    path = os.path.join(scenarios_dir, "labels.json")
    if not os.path.exists(path):
        return merged
    with open(path, encoding="utf-8") as fh:
        labels = json.load(fh)
    for sid, entry in labels.items():
        if not isinstance(entry, dict):
            continue
        instruction = entry.get("perturbation")
        if isinstance(instruction, str) and instruction.strip():
            merged[sid] = instruction
    return merged


def plan_trials(
    scenarios, repeats: int, perturbations: dict[str, str] | None = None
) -> list[tuple[str, str, Any, Any]]:
    """Every trial `main()`'s two arms will ask for, in the order they will ask: FP rows
    repeat-major (round 1 of every scenario, then round 2, ...), then the recall rows.
    Returns `(key, sid, base_scn, cand_scn)` tuples."""
    perturbations = PERTURBATIONS if perturbations is None else perturbations
    plan: list[tuple[str, str, Any, Any]] = []
    for i in range(repeats):
        for scn in scenarios:
            sid = fp_label(scn.id, i, repeats)
            plan.append((trial_key(scn, scn, sid), sid, scn, scn))
    for scn in scenarios:
        if scn.id in perturbations:
            cand = _perturb(scn, perturbations[scn.id])
            plan.append((trial_key(scn, cand, scn.id), scn.id, scn, cand))
    return plan


def traces_to_json(traces) -> list[dict]:
    """Traces minus `messages`: everything the diff reads (tool calls, output, refusal, tokens,
    latency, incomplete_reason), none of the prompt it does not. `Trace(**row)` rehydrates.
    """
    return [t.model_dump(mode="json", exclude={"messages"}) for t in traces]


def traces_from_json(rows) -> list:
    from modelpin.models import Trace

    return [Trace(**r) for r in rows]


def traces_by_key(rows) -> dict[str, tuple[list, list]]:
    """`key -> (baseline_traces, candidate_traces)` for every artifact row that recorded both
    sides.

    This is the whole reason a second judge is nearly free. `traces_to_json` drops only
    `messages`; the semantic channel reads `final_output` and nothing else, and every other
    channel reads fields that are on disk too. So the expensive half - the replay - never has
    to be bought twice, and a judge from another vendor scores the SAME recorded behaviour
    rather than a fresh sample that would confound judge disagreement with model noise.
    """
    out: dict[str, tuple[list, list]] = {}
    for rec in rows:
        if rec.get("base_traces") is not None and rec.get("cand_traces") is not None:
            out[rec["key"]] = (
                traces_from_json(rec["base_traces"]),
                traces_from_json(rec["cand_traces"]),
            )
    return out


def resolve_judge_provider(args) -> str | None:
    """The host that will actually run the judge: what was typed, else what the model id
    implies. `None` when neither can say."""
    typed = (getattr(args, "judge_provider", None) or "").strip().lower()
    return typed or infer_judge_provider(args.judge)


def require_judge_provider(args) -> str:
    """Resolve the judge host or refuse, by name, before anything is spent.

    `[M]` MP-208: `build_judge` already raised here, but it raised a `ProviderError` naming a
    config file the harness does not read, so the operator's next move was wrong. A vendor
    prefix names the model's ORIGIN, not its host - `openai/gpt-oss-120b` runs on Groq - and
    nothing but the flag can settle it.
    """
    resolved = resolve_judge_provider(args)
    if resolved is None:
        raise SystemExit(
            f"error: cannot tell which host should run the judge model {args.judge!r}. "
            f"Pass --judge-provider ({' | '.join(JUDGE_PROVIDERS)}).\n"
            "  A vendor-prefixed id names the model's ORIGIN, not its host: "
            "'openai/gpt-oss-120b' is served by groq."
        )
    if resolved not in JUDGE_PROVIDERS:
        raise SystemExit(
            f"error: --judge-provider {resolved!r} is not a judge host. "
            f"Supported: {', '.join(JUDGE_PROVIDERS)}."
        )
    return resolved


def judge_calls_implied(base_traces, cand_traces) -> int:
    """An UPPER BOUND on the judge calls this trial made, derived from the traces.

    Exact until MP-206. `diff/semantic.py` used to ask one question per run — is it equivalent
    to the modal baseline output — so counting the runs whose text differed from that mode WAS
    the call count. Under ADR-0040 a run is compared against the whole baseline pool and stops
    at the first equivalence, so the true number depends on the judge's ANSWERS and is not
    derivable from traces at all. What is derivable is the worst case: every comparison that
    the free textual-identity check cannot settle.

    Reported as a bound rather than dropped, because the alternative is publishing no cost
    figure for the one axis a user pays for and cannot see. Under-disclosing a paid axis is
    the same ADR-0019 violation as overstating one.
    """
    from modelpin.diff.semantic import _normalize

    base = [_normalize(t.final_output or "") for t in base_traces]
    cand = [_normalize(t.final_output or "") for t in cand_traces]
    # Baseline side is leave-one-out; candidate side sees the whole pool. A pair whose text
    # matches costs nothing, and one identical member is enough to settle the whole run.
    calls = 0
    for i, b in enumerate(base):
        pool = base[:i] + base[i + 1 :]
        calls += 0 if any(b == p for p in pool) else len(pool)
    for c in cand:
        calls += 0 if any(c == p for p in base) else len(base)
    return calls


def trial_record(
    key: str,
    sid: str,
    result,
    base_traces,
    cand_traces,
    error: str | None,
    elapsed_s: float,
    judged: bool,
) -> dict:
    """One JSON line per trial. `result` is None exactly when `error` is set."""
    arm, _, _ = key.partition(":")
    both = [*(base_traces or []), *(cand_traces or [])]
    return {
        "kind": ARTIFACT_KIND_TRIAL,
        "key": key,
        "arm": arm,
        "sid": sid,
        "scenario_id": sid.split("#", 1)[0],
        "ts": _utcnow(),
        "elapsed_s": round(elapsed_s, 3),
        "error": error,
        "result": None if result is None else result.model_dump(mode="json"),
        "base_rep": None if base_traces is None else repertoire(base_traces),
        "cand_rep": None if cand_traces is None else repertoire(cand_traces),
        "tokens_in": sum(t.tokens_in for t in both),
        "tokens_out": sum(t.tokens_out for t in both),
        "judge_calls": (
            judge_calls_implied(base_traces, cand_traces)
            if judged and base_traces is not None and cand_traces is not None
            else 0
        ),
        "base_traces": None if base_traces is None else traces_to_json(base_traces),
        "cand_traces": None if cand_traces is None else traces_to_json(cand_traces),
    }


def load_artifact(path: str) -> tuple[dict | None, list[dict]]:
    """`(header, trial rows)` from a JSONL artifact. For one key, a verdict always beats an
    error and a later verdict beats an earlier one, so a re-attempted provider error is
    superseded by the verdict that replaced it and never the other way round. Blank and
    non-trial lines (resume markers, summaries) are skipped."""
    header: dict | None = None
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("kind")
            if kind == ARTIFACT_KIND_HEADER:
                header = header or rec
            elif kind == ARTIFACT_KIND_TRIAL:
                prev = rows.get(rec["key"])
                if prev is None or prev.get("result") is None or rec.get("result") is not None:
                    rows[rec["key"]] = rec
    return header, list(rows.values())


def memo_from_rows(rows) -> dict[str, tuple]:
    """`key -> (DiffResult, base_repertoire, cand_repertoire)` for every row that reached a
    verdict. Error rows are absent on purpose: a resumed run re-attempts them."""
    from modelpin.models import DiffResult

    memo: dict[str, tuple] = {}
    for rec in rows:
        if rec.get("result") is not None:
            memo[rec["key"]] = (
                DiffResult(**rec["result"]),
                rec["base_rep"],
                rec["cand_rep"],
            )
    return memo


def check_resume_header(header: dict | None, expected: dict) -> None:
    """Refuse to append trials from a different configuration to an existing artifact."""
    if header is None:
        raise SystemExit("error: --resume: the artifact has no header line; start a fresh --out.")
    bad = [k for k in _RESUME_MUST_MATCH if header.get(k) != expected.get(k)]
    bad += [
        k for k in _RESUME_MATCH_IF_RECORDED if k in header and header.get(k) != expected.get(k)
    ]
    if bad:
        detail = ", ".join(f"{k}: artifact={header.get(k)!r} now={expected.get(k)!r}" for k in bad)
        raise SystemExit(
            "error: --resume: this artifact was recorded under a different configuration "
            f"({detail}). A file holding trials from two configurations measures neither; "
            "use a new --out."
        )


def run_trials(plan, verdict_live, memo: dict, workers: int, on_row, miss_label="provider error"):
    """Fill `memo` for every planned trial not already in it.

    `verdict_live(base, cand, sid) -> (DiffResult, base_traces, cand_traces) | None` is
    called for each missing key - concurrently when `workers > 1`. `None` (a provider error)
    is reported through `on_row` but NOT memoised, so the next `--resume` re-attempts it.
    `on_row(key, sid, res, base_traces, cand_traces, error, elapsed)` runs on the calling
    thread, in completion order. With `verdict_live=None` (offline rescore) every missing key
    is reported as an error and nothing is called.

    Completion order is whatever the pool produces; the arms re-read the memo in plan order,
    so the published report is identical at every worker count.
    """
    todo = [t for t in plan if t[0] not in memo]

    def one(item):
        key, sid, base, cand = item
        started = time.perf_counter()
        if verdict_live is None:
            return key, sid, None, None, None, "not in artifact", 0.0
        out = verdict_live(base, cand, sid)
        elapsed = time.perf_counter() - started
        if out is None:
            return key, sid, None, None, None, miss_label, elapsed
        res, base_traces, cand_traces = out
        return key, sid, res, base_traces, cand_traces, None, elapsed

    def absorb(done):
        key, sid, res, base_traces, cand_traces, error, elapsed = done
        if res is not None:
            memo[key] = (res, repertoire(base_traces), repertoire(cand_traces))
        on_row(key, sid, res, base_traces, cand_traces, error, elapsed)

    if workers <= 1 or len(todo) <= 1:
        for item in todo:
            absorb(one(item))
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, item) for item in todo]
        for fut in as_completed(futures):
            absorb(fut.result())


class ArtifactWriter:
    """Append-only JSONL, one flush per record, one lock across worker threads."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            # LF on every platform: a JSONL artifact committed from Windows must not differ from
            # one committed from Linux by its line endings alone.
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
                fh.flush()


def artifact_header(args, scenario_ids, git_sha: str, source: dict | None = None) -> dict:
    """The configuration a rate is a rate OF. Everything a reader needs to re-run it.

    `source` is the header of the artifact a `--rejudge` run re-scored. It is recorded so a
    rejudged file can never be mistaken for an independent sample: it is the SAME replay,
    and pooling the two would double-count every trial.
    """
    from modelpin import diff as _diff
    from modelpin.config import DEFAULT_RUNS

    rejudged: dict[str, Any] = {}
    if source is not None:
        rejudged = {
            "rejudged_from": os.path.basename(args.rejudge),
            "rejudged_from_git_sha": source.get("git_sha"),
            "rejudged_from_started_utc": source.get("started_utc"),
            "rejudged_from_judge": source.get("judge"),
            "rejudged_from_judge_provider": source.get("judge_provider"),
            # What was NOT bought. A rejudged file is routinely a pre-registered SUBSET of its
            # source, because the free judge tier that makes it affordable is a daily quota;
            # the subset rule belongs in the artifact, not only in the write-up.
            "rejudged_arm": args.arm,
            "rejudged_repeats_of": source.get("repeats"),
            # Not an independent sample. Stated in the artifact, not only in the write-up.
            "replay_reused": True,
        }

    return {
        "kind": ARTIFACT_KIND_HEADER,
        "provider": args.provider,
        "model": args.model,
        "runs": args.runs,
        "judge": None if args.no_judge else args.judge,
        # The RESOLVED host, not the flag: an artifact must say which vendor's judge produced
        # its semantic verdicts even when nobody typed --judge-provider, because that is the
        # fact MP-208 found unrecorded and unpriced across every number published before it.
        "judge_provider": None if args.no_judge else resolve_judge_provider(args),
        "scenarios_dir": os.path.basename(os.path.normpath(args.scenarios_dir)),
        "scenarios_path": args.scenarios_dir,
        "role": args.role,
        "repeats": args.repeats,
        "only": sorted(args.only) if args.only else None,
        "scenarios": list(scenario_ids),
        "match": "strict",
        "constants": {
            "ALPHA": _diff.ALPHA,
            "MIN_TOOL_TVD": _diff.MIN_TOOL_TVD,
            "MIN_TOOL_ARG_TVD": _diff.MIN_TOOL_ARG_TVD,
            "MIN_REFUSAL_DELTA": _diff.MIN_REFUSAL_DELTA,
            "MIN_SEMANTIC_DELTA": _diff.MIN_SEMANTIC_DELTA,
            "DEFAULT_RUNS": DEFAULT_RUNS,
        },
        "git_sha": git_sha,
        "started_utc": _utcnow(),
        **rejudged,
    }


def parse_only(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    ids = {s.strip() for s in raw.split(",") if s.strip()}
    if not ids:
        raise SystemExit("error: --only needs at least one scenario id.")
    return ids


def select_only(scenarios, only: set[str] | None):
    """Restrict a run to named ids. An id absent from the (role-filtered) set is an error: a
    smoke run that silently measured a different scenario is worse than none."""
    if only is None:
        return list(scenarios)
    missing = sorted(only - {s.id for s in scenarios})
    if missing:
        raise SystemExit(f"error: --only names ids not in this set: {', '.join(missing)}.")
    return [s for s in scenarios if s.id in only]


def run_footer(
    live_rows, all_rows, reused: int, artifact: str | None, elapsed_s: float, rejudged=False
) -> list[str]:
    """Tokens, judge calls, wall time - the cost side of the number, printed once. Tokens are
    summed over EVERY trial the report rests on (reused ones included); the live count is
    this invocation's alone.

    Under `--rejudge` the replay tokens were spent by the SOURCE run and not by this one, so
    they are labelled as such. Printing them unqualified beside a run that made no replay call
    invites a reader to price a free re-score as if it had bought the replay again.
    """
    tin = sum(r.get("tokens_in") or 0 for r in all_rows)
    tout = sum(r.get("tokens_out") or 0 for r in all_rows)
    jc = sum(r.get("judge_calls") or 0 for r in all_rows)
    spent = (
        "carried over from the source run; NO replay call was made"
        if rejudged
        else "over all %d trial(s)" % len(all_rows)
    )
    out = [
        f"  This invocation: {len(live_rows)} trial(s) "
        f"{'re-scored (judge calls only)' if rejudged else 'run live'}, {reused} reused from the "
        f"artifact, {elapsed_s / 60:.1f} min wall.",
        f"  Replay tokens in/out {spent}: {tin:,}/{tout:,}; judge calls "
        f"implied by the traces: {jc} (judge tokens are not metered).",
    ]
    if artifact:
        out.append(f"  Artifact: {artifact}  (re-read offline with --rescore; traces included)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--provider",
        default="openai",
        help="openai | google | groq | ... (any live adapter)",
    )
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--judge", default="gpt-4o-mini")
    ap.add_argument(
        "--judge-provider",
        default=None,
        help="which host runs the judge: "
        + " | ".join(JUDGE_PROVIDERS)
        + ". Optional for a model whose host is inferable from its id (`gpt-4o-mini` -> "
        "openai); REQUIRED otherwise. [M] MP-208: `openai/gpt-oss-120b` is a GROQ id whose "
        "vendor prefix says `openai`, so inference returns None and the run dies at "
        "preflight. Without this flag the harness could only ever score with an OpenAI "
        "judge, and at the time 51 of the 82 scored trials in the run of record could only "
        "have fired on the semantic channel (8 of 39 under the current engine).",
    )
    ap.add_argument("--no-judge", action="store_true", help="skip the semantic LLM-judge")
    ap.add_argument("--scenarios-dir", default=None, help="default examples/suite")
    ap.add_argument(
        "--role",
        default=None,
        help="which declared role to score (examples/roles.json). REQUIRED when the "
        "directory declares more than one, because a pooled rate is in-sample for the "
        "fitted half (ADR-0025).",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="independent measurement rounds over the whole set. [M] MP-89: 7 scenarios "
        "project to 0.35 EXPECTED SCORED trials once ADR-0022 excludes those that could "
        "not have fired, so a single round's modal outcome is 0/0 - an abstention, not a "
        "rate. Repeats are how n reaches a publishable size.",
    )
    ap.add_argument(
        "--only",
        default=None,
        help="comma-separated scenario ids to run (a smoke run). Recorded in the artifact.",
    )
    ap.add_argument(
        "--out",
        default=None,
        metavar="ARTIFACT.jsonl",
        help="append one JSON line per trial - verdict, repertoires, TRACES - as it completes, "
        "so a cut run can --resume instead of restarting.",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="with --out: reuse every trial in the artifact that reached a verdict and "
        "re-attempt provider errors. Refuses an artifact recorded under another config.",
    )
    ap.add_argument(
        "--rescore",
        default=None,
        metavar="ARTIFACT.jsonl",
        help="OFFLINE: rebuild both arms from an artifact. No provider, no key, no writes; "
        "the artifact's own configuration is used and the command line's is ignored.",
    )
    ap.add_argument(
        "--rejudge",
        default=None,
        metavar="ARTIFACT.jsonl",
        help="REPLAY-FREE RE-SCORE: rebuild every trial from an artifact's stored traces and "
        "re-diff them under the judge named by --judge/--judge-provider. No replay provider is "
        "called and no replay key is read, so the same recorded model behaviour can be scored "
        "by a second judge for the price of the judge calls alone. The artifact's measured "
        "configuration (provider, model, runs, repeats, role, scenarios) is adopted; the JUDGE "
        "is the one thing the command line still decides. Needs --out; --resume works, which is "
        "what makes a rate-limited free judge tier survivable.",
    )
    ap.add_argument(
        "--arm",
        default="both",
        choices=("fp", "recall", "both"),
        help="with --rejudge: which arm to re-score. The arms are independent measurements, "
        "and a free judge tier is a DAILY budget, so being unable to buy one without the "
        "other means being unable to buy either.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="trials run concurrently. The report is identical at any value; only wall time "
        "and rate-limit exposure change.",
    )
    args = ap.parse_args()
    if args.repeats < 1:
        raise SystemExit("error: --repeats must be >= 1.")
    if args.workers < 1:
        raise SystemExit("error: --workers must be >= 1.")
    if args.rescore and args.rejudge:
        raise SystemExit(
            "error: --rescore replays the judgement already on disk and --rejudge replaces it; "
            "they cannot both describe one run. Pick one."
        )
    if args.rescore and (args.out or args.resume):
        raise SystemExit(
            "error: --rescore re-reads an artifact and never writes one; drop --out/--resume."
        )
    if args.resume and not args.out:
        raise SystemExit("error: --resume needs --out to say which artifact to continue.")
    if args.rejudge:
        if not args.out:
            # A rejudged verdict is a NEW measurement of the same behaviour, so it gets its own
            # artifact of record. Printing it and discarding it would leave the agreement rate
            # this mode exists to publish resting on a terminal transcript.
            raise SystemExit(
                "error: --rejudge needs --out: the re-scored verdicts are a new measurement "
                "and belong in their own artifact, not only on stdout."
            )
        if args.no_judge:
            raise SystemExit("error: --rejudge --no-judge would re-score with nothing.")
        if os.path.abspath(args.rejudge) == os.path.abspath(args.out):
            raise SystemExit(
                "error: --rejudge and --out name the same file; the source artifact is read-only "
                "and a second judge's verdicts must not be appended to the first judge's record."
            )

    memo: dict[str, tuple] = {}
    recorded: dict | None = None
    rows_on_disk: list[dict] = []
    source_rows: list[dict] = []
    source_header: dict | None = None
    traces_source: dict[str, tuple[list, list]] = {}
    if args.rejudge:
        source_header, source_rows = load_artifact(args.rejudge)
        if source_header is None:
            raise SystemExit("error: --rejudge: the artifact has no header line.")
        # The artifact fixes WHAT was measured; the command line fixes WHO scores it. That
        # split is the point of the mode: same traces, different judge, so a disagreement is
        # the judges' and not the models'.
        for k in ("provider", "model", "runs", "role"):
            setattr(args, k, source_header[k])
        # `--repeats` narrows to the FIRST k recorded rounds. The rounds are exchangeable
        # replicates of one design, so a prefix of them is an unbiased subset - which a subset
        # chosen by scenario, or by which trials the first judge happened to flag, would not be.
        # 1 is argparse's default and is indistinguishable from a typed 1 - and it could not be
        # honoured anyway: `fp_label` drops the `#i` suffix at repeats == 1, so the keys would
        # stop matching the artifact's and every trial would miss. 1 therefore means "all".
        recorded_repeats = source_header["repeats"]
        args.repeats = (
            recorded_repeats if args.repeats == 1 else min(args.repeats, recorded_repeats)
        )
        args.scenarios_dir = (
            args.scenarios_dir
            or source_header.get("scenarios_path")
            or source_header["scenarios_dir"]
        )
        # `--only` NARROWS a rejudge; it does not redefine it. `[S] 2026-09-07` the free Groq
        # tier that makes a second judge affordable caps 200,000 tokens/day; `[M]` one
        # artifact's stored `judge_calls` runs to the thousands, and `[A]` at ~535 tokens/call
        # (an assumption - the harness does not meter judge tokens) that is more than a day's
        # budget in a single all-or-nothing command. Discarding a typed --only here left no way
        # to rejudge a subset at all.
        recorded_only = set(source_header["only"]) if source_header.get("only") else None
        typed_only = parse_only(args.only)
        if typed_only and recorded_only and not typed_only <= recorded_only:
            raise SystemExit(
                "error: --rejudge --only names ids the source artifact never measured: "
                f"{', '.join(sorted(typed_only - recorded_only))}."
            )
        args.only = typed_only or recorded_only
        if source_header.get("judge") is None:
            raise SystemExit(
                "error: --rejudge: that artifact was recorded with --no-judge, so it has no "
                "semantic verdict to disagree with. Re-scoring it would compare a judge to "
                "nothing."
            )
        traces_source = traces_by_key(source_rows)
        if not traces_source:
            raise SystemExit(
                f"error: --rejudge: {args.rejudge} records no trial with traces on both sides, "
                "so there is nothing to re-score. Artifacts written before traces were kept "
                "(MP-205) cannot be rejudged."
            )
    elif args.rescore:
        recorded, rows_on_disk = load_artifact(args.rescore)
        if recorded is None:
            raise SystemExit("error: --rescore: the artifact has no header line.")
        # The artifact decides what is measured; anything typed beside --rescore is ignored.
        for k in ("provider", "model", "runs", "repeats", "role"):
            setattr(args, k, recorded[k])
        args.scenarios_dir = (
            args.scenarios_dir or recorded.get("scenarios_path") or recorded["scenarios_dir"]
        )
        args.judge = recorded.get("judge")
        # ...and the host that ran it, or `resolve_judge_provider` would later re-infer a host
        # for an id that has none. Inert today (--rescore builds no judge and forbids --out),
        # a trap tomorrow.
        args.judge_provider = recorded.get("judge_provider")
        # ...and which arms were actually bought. A rejudged artifact is routinely one arm only;
        # without this, an offline re-read plans the arm nobody paid for, finds every trial
        # missing, and reports the absence as 12 provider errors under a banner blaming the
        # operator's credentials.
        args.arm = recorded.get("rejudged_arm") or "both"
        args.no_judge = args.judge is None
        args.only = set(recorded["only"]) if recorded.get("only") else None
        memo = memo_from_rows(rows_on_disk)
    else:
        args.scenarios_dir = args.scenarios_dir or "examples/suite"
        args.only = parse_only(args.only)

    scenarios = load_scenarios(args.scenarios_dir)
    scenarios, role_note = select_by_role(
        scenarios, roles_for_dir(load_role_sets(), args.scenarios_dir), args.role
    )
    scenarios = select_only(scenarios, args.only)

    adapter = None
    judge = None
    if not args.rescore:
        if not args.rejudge:
            # --rejudge reads its replay off the disk, so it needs no replay adapter and no
            # replay key. That is what makes a second judge cost the judge calls alone.
            adapter = get_adapter(args.provider)
            adapter.preflight()
        if not args.no_judge:
            judge = build_judge(args.judge, provider=require_judge_provider(args))
            judge.preflight()

    git_sha = _git_sha()
    writer: ArtifactWriter | None = None
    if args.out:
        expected = artifact_header(args, [s.id for s in scenarios], git_sha, source_header)
        if os.path.exists(args.out):
            if not args.resume:
                raise SystemExit(
                    f"error: {args.out} exists. Pass --resume to continue it, or name a new --out."
                )
            recorded, rows_on_disk = load_artifact(args.out)
            check_resume_header(recorded, expected)
            memo = memo_from_rows(rows_on_disk)
            writer = ArtifactWriter(args.out)
            writer.write(
                {
                    "kind": ARTIFACT_KIND_RESUME,
                    "ts": _utcnow(),
                    "git_sha": git_sha,
                    "reused": len(memo),
                }
            )
        else:
            writer = ArtifactWriter(args.out)
            writer.write(expected)

    print(
        f"FP measurement: provider={args.provider} model={args.model} runs={args.runs} "
        f"judge={'off' if args.no_judge else args.judge} scenarios={len(scenarios)} "
        f"repeats={args.repeats}\n"
        # The selected role is PUBLISHED, not merely honoured. [M] MP-89's defect was that
        # the operator could not tell from the output which scenarios the rate covered.
        f"  {role_note}\n"
        f"  -> {len(scenarios) * args.repeats} trials attempted; ADR-0022 excludes those that "
        f"could not have fired, so SCORED will be lower."
    )
    if args.only:
        print(f"  --only: {', '.join(sorted(args.only))}")
    if args.rescore:
        print(
            f"  RESCORED OFFLINE from {args.rescore} (recorded {recorded.get('started_utc')} "
            f"@ {recorded.get('git_sha')}); no provider was called."
        )
    if args.rejudge:
        assert source_header is not None
        print(
            f"  REJUDGED from {args.rejudge} (recorded {source_header.get('started_utc')} "
            f"@ {source_header.get('git_sha')}): {len(traces_source)} trial(s) of stored "
            f"replay, re-diffed under judge {args.judge} on "
            f"{resolve_judge_provider(args)}.\n"
            f"  The replay was NOT repeated, so this is the same recorded behaviour scored "
            f"twice - not a second sample. Prior judge: "
            f"{source_header.get('judge')} on "
            f"{source_header.get('judge_provider') or 'openai (unrecorded; inferred)'}."
        )
    print(
        f"  git {git_sha} * {_utcnow()} * workers={args.workers}"
        + (f" * artifact {args.out}" if args.out else "")
        + "\n"
    )

    def _verdict_live(base_scn, cand_scn, sid):
        """Replay base + candidate and diff. Returns `(DiffResult, base_traces,
        cand_traces)`, or None on a provider error. The traces are returned rather than
        discarded because a verdict ALONE cannot say whether the run measured anything at
        all (MP-75), and because they are what the artifact keeps."""
        try:
            base = _replay_resilient(base_scn, args.model, adapter, args.runs)
            cand = _replay_resilient(cand_scn, args.model, adapter, args.runs)
            r = diff_scenario(sid, args.model, args.model, base, cand, base_scn, judge=judge)
            return r, base, cand
        except ProviderError as exc:
            print(f"  {sid:<22} ERROR  ({str(exc)[:70]})")
            return None

    def _verdict_rejudge(base_scn, cand_scn, sid):
        """Re-diff ONE stored trial under the new judge. No replay: the traces come off the
        source artifact, so every channel but the semantic one is recomputed from identical
        inputs and must return an identical answer. Any difference in the verdict is
        therefore attributable to the judge alone - which is the measurement.

        A key the source artifact never recorded (a provider error at the time) returns None
        and reports as a miss, exactly as a live provider error would.
        """
        stored = traces_source.get(trial_key(base_scn, cand_scn, sid))
        if stored is None:
            return None
        base, cand = stored
        try:
            r = diff_scenario(sid, args.model, args.model, base, cand, base_scn, judge=judge)
        except ProviderError as exc:  # the JUDGE's host, not the replay's
            print(f"  {sid:<22} JUDGE ERROR  ({str(exc)[:70]})")
            return None
        return r, base, cand

    perturbations = load_perturbations(args.scenarios_dir)
    plan = plan_trials(scenarios, args.repeats, perturbations)
    if args.arm != "both":
        # Only ever a NARROWING of a rejudge, and only of which trials are bought. Both arms
        # still print below; the one that was not re-scored simply reports its trials as
        # missing, which is the honest rendering of "not measured under this judge".
        plan = [t for t in plan if t[0].startswith(f"{args.arm}:")]
    reused = sum(1 for key, *_ in plan if key in memo)
    rows_written: list[dict] = []
    started = time.perf_counter()
    if args.rejudge:
        print(
            f"REJUDGING {len(plan) - reused} trial(s) ({reused} reused from --out), "
            f"{args.workers} worker(s); no replay call is made:"
        )
    elif not args.rescore:
        print(
            f"RUNNING {len(plan) - reused} trial(s) live ({reused} reused from the artifact), "
            f"{args.workers} worker(s):"
        )

    def _on_row(key, sid, res, base_traces, cand_traces, error, elapsed):
        rec = trial_record(
            key, sid, res, base_traces, cand_traces, error, elapsed, judge is not None
        )
        rows_written.append(rec)
        if writer is not None:
            writer.write(rec)
        n, total = len(rows_written), len(plan) - reused
        if res is None:
            print(f"  [{n}/{total}] {sid:<24} ERROR ({error})")
        else:
            print(
                f"  [{n}/{total}] {sid:<24} {res.verdict.value:<14} conf={res.confidence:.2f}"
                f"  {elapsed:5.1f}s  [{_rep(rec['base_rep'], rec['cand_rep'])}]"
            )

    if args.rescore:
        _verdict_fn = None
    elif args.rejudge:
        _verdict_fn = _verdict_rejudge
    else:
        _verdict_fn = _verdict_live
    _miss = "judge error or absent from the source" if args.rejudge else "provider error"
    run_trials(plan, _verdict_fn, memo, args.workers, _on_row, _miss)
    print()

    def _verdict(base_scn, cand_scn, sid):
        """Both arms read the memo the run above filled. A miss is a trial that never reached
        a verdict - a provider error live, or a row the artifact never recorded offline - and
        reports as one."""
        return memo.get(trial_key(base_scn, cand_scn, sid))

    # --- false-positive rate: same model vs itself ------------------------ [ARM:FP] ---
    # NB the ARM markers above and below are load-bearing: three
    # source-slicing guards key on them (`tests/test_recall_arm.py`,
    # `tests/test_fp_measurement_repertoire.py` x2) because `main()` needs a provider and
    # ADR-0006 forbids a live call. They are comments, NOT operator prose, precisely so that
    # rewording a banner cannot silently break the guards - [M] FP review 2026-08-23: they
    # previously keyed on the printed banner text, so this correction would have raised
    # ValueError in all three, taking out MP-75's FP call-site protection as collateral.
    print("EQUIVALENT PAIRS (same model vs itself) -- any non-`unchanged` is a false alarm")
    print("  repertoire = DISTINCT behaviours observed, base|cand (diagnostic, not the test).")
    print("  A trial is EXCLUDED when every channel returned p = 1.00 - it could not have")
    print("  fired at any ALPHA < 1. Anything the engine flagged is always scored.")

    rows = [
        build_row(scn.id if args.repeats == 1 else f"{scn.id}#{i + 1}", scn, scn, _verdict)
        for i in range(args.repeats)
        for scn in scenarios
    ]
    t: dict[str, int] = {}
    if args.arm == "recall":
        print(_arm_not_bought("false-positive", len(rows), args.arm))
    else:
        t, lines_out = fp_report(rows)
        for line in lines_out:
            print(line)
        for line in fp_summary(t):
            print(line)

    # --- detection: injected perturbations -------------------------- [ARM:RECALL] ---
    perturbed = [s for s in scenarios if s.id in perturbations]
    print("INJECTED PERTURBATIONS (perturbed candidate) -- a flag is a detection, and")
    print("  `unchanged` is a MISS. NB a miss is not automatically an engine defect: the")
    print("  candidate may have resisted the injected instruction. Read the explanations.")
    print("  Nothing is excluded here but an abstention: a candidate that still reads")
    print("  `unchanged` is a MISS, not an unmeasured trial. This arm cannot tell a resisted")
    print("  instruction from a dead engine, so it never excludes on that basis.")
    recall_rows = [
        build_row(s.id, s, _perturb(s, perturbations[s.id]), _verdict) for s in perturbed
    ]
    rt: dict[str, int] = {}
    if args.arm == "fp":
        print(_arm_not_bought("detection", len(recall_rows), args.arm))
    else:
        rt, recall_lines = recall_report(recall_rows)
        for line in recall_lines:
            print(line)
        for line in recall_summary(rt):
            print(line)

    for note in run_footer(
        rows_written,
        [*rows_on_disk, *rows_written],
        reused,
        args.out or args.rescore,
        time.perf_counter() - started,
        bool(args.rejudge) or bool(recorded and recorded.get("replay_reused")),
    ):
        print(note)
    if writer is not None:
        writer.write({"kind": ARTIFACT_KIND_SUMMARY, "ts": _utcnow(), "fp": t, "recall": rt})


if __name__ == "__main__":
    # A Windows console defaults to cp1252, and a model output with one character outside it
    # would end a paid run at the print, not at the measurement. Replace, never crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        main()
    except ProviderError as exc:
        print(f"\nerror: {exc}\n(network/provider issue — retry when connectivity is stable)")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\ninterrupted. Every trial that reached a verdict is in the artifact; re-run the")
        print("same command with --resume to continue from there.")
        raise SystemExit(130)
