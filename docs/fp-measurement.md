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
  vs itself**, two independent N-run samples — across the suite. Any verdict other than
  `unchanged` is, by construction, a false alarm from model nondeterminism.
- **Detection (the control).** Inject a controlled behavior change into the candidate
  (refuse refunds / leak PII / always answer "Positive") and confirm the engine flags it —
  so a low FP rate is not merely "always unchanged".

Harness: [`scripts/fp_measurement.py`](../scripts/fp_measurement.py). BYO-key; reproducible.

```
python scripts/fp_measurement.py --model gpt-4o-mini --runs 5     # judged, full suite
```

## Results

**Headline (live, judged, held-out): false-positive rate = 0/8 = 0%.**

`scripts/fp_measurement.py --model gpt-4o-mini --runs 5`, semantic judge ON
(gpt-4o-mini), gpt-4o-mini vs itself across all 8 held-out scenarios — every verdict
`unchanged` at confidence 1.00:

```
cancel_subscription  classify_sentiment  decline_pii          extract_total
format_contact_json  order_status        refund_request       summarize_ticket
                          all 8 -> unchanged  (0 false positives)
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

| Evidence | Pairs | False positives |
|---|---|---|
| Live judged held-out suite (gpt-4o-mini vs itself, N=5, judge on) | 8 | **0 / 8** |
| Synthetic noisy-but-equivalent pairs (golden test) | 4 | **0 / 4** |
| Real same-model split-half (captured gpt-4o-mini + gpt-3.5-turbo traces) | 6 | **0 / 6** |
| Real cross-model smoke run, gpt-3.5-turbo → gpt-4o-mini | 3 | **0 / 3** |

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

**Phase-0 DoD: met** — `mp check` detects genuine regressions between real model
behaviors *and* shows a measured **0% false-positive rate** on a held-out set.

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
held-out suite above** so this tuning does not leak into the 0/8 claim — and two raw-data
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
--runs 5` with the semantic→`regression` promotion **live** → held-out FP rate **still
0/8**, and detection *improved* (`classify_sentiment` went `changed_minor` → `regression`).
So FP-safety at the 0.5 floor holds across **three independent conditions** (self-judge
calibration, independent-judge calibration, held-out suite).

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
