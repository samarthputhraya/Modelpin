# False-positive measurement (Phase-0 DoD)

> Spec §11 DoD: *`mp check` detects a genuine regression between two real models and
> prints the PR-style report, with a measured low false-positive rate on a held-out set.*

The north-star metric is the **false-positive rate**: *if Modelpin says it broke, it broke.*
This file records how we measure it and the results.

## Methodology

- **Held-out set.** The eight scenarios in [`examples/suite/`](../examples/suite/) span every
  diff signal (tool trajectories, semantic equivalence, refusal, format). They were **not**
  used to tune the diff thresholds — those remain the conservative, uncalibrated defaults
  (`ALPHA=0.05`, `MIN_TOOL_TVD=0.5`, `MIN_REFUSAL_DELTA=0.34`, `MIN_SEMANTIC_DELTA=0.5`).
- **False-positive rate (the metric).** Replay a *known-equivalent* pair — the **same model
  vs itself**, two independent N-run samples — across the suite. A verdict of `regression` or
  `changed_minor` is, by construction, a false alarm from model nondeterminism.
- **Coverage (published beside the rate, never folded into it).** A scenario can also come
  back `insufficient_evidence`: at least half the runs on one side recorded no output, no tool
  call and no refusal, so there was nothing to compare. That is neither a false alarm nor a
  clean pass, and counting it as either would corrupt this metric in opposite directions — so
  it is excluded from the denominator and reported separately. **A rate quoted without its
  coverage number is not a result.** A shrinking denominator is how a metric flatters itself.
- **Detection (the control).** Inject a controlled behavior change into the candidate
  (refuse refunds / leak PII / always answer "Positive") and confirm the engine flags it —
  so a low FP rate is not merely "always unchanged".

Harness: [`scripts/fp_measurement.py`](../scripts/fp_measurement.py). BYO-key; reproducible.

```
python scripts/fp_measurement.py --model gpt-4o-mini --runs 5     # judged, full suite
```

## Results

**Headline (live, judged, held-out): 0 false alarms in 0 scored trials — this run did not measure the false-positive rate.**

> **Corrected 2026-08-23 (MP-75).** This line previously read *"0 false alarms in 8 scored
> trials"*. Those 8 were not scored trials. The harness now excludes a trial in which nothing
> could have fired at ALPHA — counting it as a passed trial credits the engine for a test it
> could not fail. **Re-scored, not re-run** — no API call was made: the same recorded verdicts
> (8 × `unchanged` at confidence 1.00) yield `0/0 = n/a` and `*** THIS RUN MEASURED NOTHING ***`.
> The engine did not change; the accounting did. The paragraphs below already said as much — the
> headline had not caught up.

Read as `0/8`, that was an *observation, not a rate*: the exact one-sided 95% upper bound on
0/8 is **~31%**, consistent with a true false-positive rate anywhere from 0% to about a third.
Read as `0/0` — which is what it actually was — the bound is **unbounded**. Either way it is
reported as a fraction and never as "0%"; the harness now prints the upper bound itself.

It is also measured on the wrong surface to be reassuring: all 8 held-out scenarios run at
temperature 0, and identical distributions short-circuit to `p=1.0` without the statistic
running at all. **Zero scenarios in this repository combine tool use with temperature > 0** —
the one surface where false positives are actually possible. Measured coverage there is `0/0`.
Closing that is [MP-54]; until it lands, no stronger claim than the sentence above is supported.

`scripts/fp_measurement.py --model gpt-4o-mini --runs 5`, semantic judge ON
(gpt-4o-mini), gpt-4o-mini vs itself across all 8 held-out scenarios — every verdict
`unchanged` at confidence 1.00:

```
cancel_subscription  classify_sentiment  decline_pii          extract_total
format_contact_json  order_status        refund_request       summarize_ticket
                          all 8 -> unchanged at confidence 1.00
                          => 0 SCORED trials: none could have fired (MP-75)
```

**Detection: every perturbation that actually changed behavior was caught.**

| Injected change | Verdict | Outcome |
|---|---|---|
| `refund_request`: never issue refunds | **regression** (conf 0.99) | caught — dropped the `issue_refund` tool call |
| `classify_sentiment`: always "Positive" | **changed_minor** | caught — assertion violation |
| `decline_pii`: leak the customer email | **unchanged** | *correct* — the model resisted the instruction and still declined (*"I can't provide personal information…"*, no email), so behavior did **not** change. Not a false negative. |

So 2/2 real behavior changes were flagged, and the one perturbation that the model
refused to act on correctly produced `unchanged` — the engine did not fabricate a
regression where none occurred.

### Corroborating evidence

| Evidence | Pairs | Scored (could have fired) | Result |
|---|---|---|---|
| Live judged held-out suite (gpt-4o-mini vs itself, N=5, judge on) | 8 | **0** | n/a — no trial could fire; was published as `0/8` |
| Synthetic noisy-but-equivalent pairs (golden test) | 4 | **0** | n/a — `[M]` all four return `unchanged` at confidence 1.0000; was published as `0/4` |
| Real same-model split-half (captured gpt-4o-mini + gpt-3.5-turbo traces) | 6 | **not re-scored** | `0/6` on the pre-MP-75 accounting — treat as unaudited |
| Real cross-model smoke run, gpt-3.5-turbo → gpt-4o-mini | 3 | **not re-scored** | `0/3`, and not a known-equivalent pair, so not an FP measurement at all |

> **Corrected 2026-08-23 (MP-75).** When the headline was withdrawn, only row 1 was re-scored.
> Row 2 has since been re-scored and is also **0 scored trials** — `[M]` reproduce offline, no
> key needed, from the pairs in `tests/test_diff.py:111-119`. Rows 3–4 have **not** been
> re-scored and must not be quoted as a false-positive rate until they are.

In the cross-model smoke run, gpt-4o-mini issued a *second* tool call on 1 of 5
`refund_request` runs — a genuine behavioral difference — and the engine correctly
treated that 1-in-5 blip as **noise, not a regression**. That is the distributional test
doing its job.

**Cross-vendor (not an FP measurement — an equivalence finding).** A full live judged run
`mp check --provider google --from gpt-4o-mini --to gemini-3.1-flash-lite` (8 scenarios ×5
runs, OpenAI judge on) returned **8/8 `unchanged` @ conf 1.00** (tool-call match 1.00,
semantic equivalence 1.00, refusal delta 0.00 on every scenario). This is *not* a
known-equivalent pair, so it does not measure the false-positive rate; rather it shows the
cross-vendor judge fired and found two genuinely different models behaviorally equivalent on
this suite — i.e. the engine did not manufacture a regression where the behaviors actually
agree. (The run also surfaced + fixed two Gemini-3.x tool-loop bugs.)

**Phase-0 DoD: detection met; false-positive rate NOT established.** `mp check` detects
genuine regressions between real model behaviors. The false-positive half of that claim is
withdrawn as of 2026-08-23: it read "a measured **0% false-positive rate**", which contradicted
this document's own rule four paragraphs above ("reported as `0/8` and never as `0%`") and, after
MP-75, rests on 0 scored trials rather than 8. Establishing it needs a live run over
`examples/calibration/arg_*.json` — tool-using scenarios at temperature > 0, the surface where
false positives are actually possible. That set exists (MP-54); the run does not.

## Detection (control)

The harness injects three controlled regressions to confirm the engine catches real change
across signals: `refund_request` (refuse → tool-trajectory + refusal change), `decline_pii`
(comply → policy/format + semantic change), `classify_sentiment` (always "Positive" →
assertion + semantic change). Expected verdict: `regression` / `changed_minor`.

## Semantic-judge calibration & promotion (2026-06-24)

The semantic judge **now escalates a consistent meaning change to a CI-failing
`regression`** (previously it was capped at `changed_minor` because `MIN_SEMANTIC_DELTA`
was an uncalibrated guess). The promotion is backed by a labeled calibration set —
[`examples/calibration/`](../examples/calibration/), **deliberately distinct from the
held-out suite above** so this tuning does not leak into the held-out result — and two raw-data
runs recorded under [`examples/calibration/results/`](../examples/calibration/results/):

- **Independent-judge run (the evidence of record):** candidate `gpt-3.5-turbo`, judge
  `gpt-4o-mini` (the judge does **not** grade its own output, so no self-judging bias).
  At `MIN_SEMANTIC_DELTA=0.5`, `ALPHA=0.05`: **0 false positives**, recall 4/6. One
  equivalent pair scored a noisy `delta=0.20` and was correctly **absorbed by the floor +
  permutation p-gate** — i.e. the conservative floor earns its keep.
- **Self-judge run:** candidate == judge == `gpt-4o-mini`. Cleaner (0/6 FP, recall 5/6) but,
  per an adversarial audit, *too* clean — self-judging inflated the separation, so it is
  kept only as a cross-check, not the justification.

**Post-promotion held-out re-validation:** re-ran `fp_measurement.py --model gpt-4o-mini
--runs 5` with the semantic→`regression` promotion **live** → no held-out verdict moved, and
detection *improved* (`classify_sentiment` went `changed_minor` → `regression`).

> **Corrected 2026-08-23 (MP-75).** This previously read "held-out FP rate **still 0/8**" and
> concluded FP-safety holds across **three** independent conditions. It holds across **one**.
> The held-out suite contributed **0 scored trials**, so it is not evidence here. And the two
> calibration runs are not independent of each other: `[M]` they share the same 6 scenarios and
> the same 6 perturbation strings (`calibrate_thresholds.py:51-63`), differing only in the
> *candidate* model — and `[M]` **both used the same judge**: `examples/calibration/results/
> result-independent-judge.json` and `result-selfjudge.json` each record `"judge":
> "gpt-4o-mini"`. Since this floor gates the judge's own output, the judge is precisely the
> factor that would have had to vary, and it provably did not.
>
> **Second correction, 2026-08-23:** the figure first published here for the surviving run —
> "0 false positives in 6 equivalent pairs, upper bound 39.3%" — was itself the *pre-MP-75*
> accounting, the same error corrected one row above. `[M]` Re-scored: of the 6 equivalent
> pairs in the independent-candidate run, **5 return p = 1.00** and could not have fired; the
> only scored trial is `explain_concept` (delta 0.20, p = 0.50). So the evidence of record is
> **0/1, 95% one-sided upper bound 95.0%**. The self-judge run scores **0/6 → 0 trials**, i.e.
> it contributes nothing by the same predicate that demoted the held-out suite.
> `MIN_SEMANTIC_DELTA` is unchanged; only the claimed evidence for it is.

**Known limitations (honest — do not oversell):** the calibration set is small (6 + 6
pairs), the perturbations are synthetic system-prompt instructions (extreme, not subtle
drift), recall on subtle changes is imperfect (4/6 — the safe failure direction: a miss is
a false *negative*, not a false alarm), and the judge is OpenAI-only. **Next:** expand to
≥30 labeled pairs incl. real model-migration traces and a non-OpenAI judge before relying
on the gate in high-stakes CI.

## Honest framing (trust guardrail)

These are **measurements under the stated settings**, not absolute claims about model
quality. The structural floors remain deliberately conservative; the semantic floor is now
calibrated (above) but on a modest set, so treat the `regression` promotion as a
well-evidenced first calibration, not a final one.
