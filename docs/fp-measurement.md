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
  `changed_minor` is, by construction, a false alarm from model nondeterminism. This arm
  **excludes a trial that could not have fired** — one where *every* channel returned `p = 1.00`
  (after rounding, `min(p) >= 0.9995`), so the trial scores `unchanged` at any ALPHA < 1
  regardless of any change to the engine, and counting it as a passed trial credits the engine
  for a test it could not fail (ADR-0022). **That is strictly broader than "nothing moved"**:
  `[M]` golden pairs 3 and 4 in `tests/test_diff.py` have genuinely different tool
  distributions and are still excluded, because 5 runs a side cannot separate them; so is a
  refusal rate going 100% → 0%, since the mean statistic is one-sided. `[M]` At `runs=5` the
  tool channel returns `p = 1.00` across the whole `|i-j| <= 1` band — **16 of 36 cells, 10 of
  them with genuinely differing sides**, so on that grid `scored` is roughly **half** what a
  naive reading expects. Do not use 2x as a planning figure: `[M]` on this document's **three**
  measured runs the observed rate is far worse — **0 of 8** scored on the held-out suite,
  **1 of 6** on the independent-candidate calibration run, and **0 of 6** on the self-judge run
  (see the corrections at the end of *Semantic-judge calibration*) — **1 scored trial in 20
  attempted, 5.0% pooled**, which is the only planning figure this document supports and the
  one the `arg_*` projection below uses. *An earlier version of this sentence counted two runs
  and pooled 1-of-14; it omitted the self-judge run, the only one of the three that would drag
  the figure down.* A run large enough to move a floor would need N≈10,
  which is where the floors start to bind at all (N=9/11/12, ADR-0002). The exclusion is
  nonetheless **sound by construction**: a flagged verdict never carries `unchanged`-confidence,
  so it can never remove a false positive from the numerator.
  Excluded counts are printed beside the rate, never folded into it.
- **Coverage (published beside the rate, never folded into it).** A scenario can also come
  back `insufficient_evidence`: at least half the runs on one side recorded no output, no tool
  call and no refusal, so there was nothing to compare. That is neither a false alarm nor a
  clean pass, and counting it as either would corrupt this metric in opposite directions — so
  it is excluded from the denominator and reported separately. **A rate quoted without its
  coverage number is not a result.** A shrinking denominator is how a metric flatters itself.
- **Detection (the control).** Inject a **perturbed instruction** into the candidate
  (refuse refunds / leak PII / always answer "Positive") and confirm the engine flags it —
  so a low FP rate is not merely "always unchanged". The injection changes the *instruction*;
  whether the candidate's *behavior* changed is precisely what this arm measures, so a
  perturbed pair that comes back `unchanged` is scored a **miss** and never excluded. This
  arm excludes nothing but a run that **abstained** (`insufficient_evidence`, ADR-0018);
  provider errors are reported separately and enter neither numerator nor denominator. Contrast
  the false-positive arm, which additionally excludes a trial that **could not have fired**
  (ADR-0022) — an exclusion this arm must never adopt. → ADR-0023

- **Two rates, both published, never one.** *Conditional*: false alarms over SCORED trials —
  the engine-centric number the exclusion above defines, and the stricter one. *Unconditional*:
  false alarms over every trial that reached a verdict (scored + could-not-fire) — what a user
  running the same check sees. Abstentions and provider errors are printed beside both and
  folded into neither. Each carries its one-sided 95% Clopper-Pearson upper bound.
- **Pre-registered.** The surfaces, models, repeat counts, exclusions and the rule that every
  flagged trial is published with its traces were written down before the first trial of the
  2026-09-07 run (`examples/fp-suite/README.md`), and no repeat was added or dropped after a
  number was seen.

Harness: [`scripts/fp_measurement.py`](../scripts/fp_measurement.py). BYO-key; reproducible;
`--out` records every trial with its traces as it completes, `--resume` continues a cut run,
`--rescore` rebuilds the report offline from an artifact with no key, and
[`scripts/fp_aggregate.py`](../scripts/fp_aggregate.py) pools artifacts into the tables below
through the same pure functions the harness prints with.

> `ADR-nnnn` refers to this project's internal decision records, which are not published. They
> are cited for provenance only — every argument they carry is stated inline here, so nothing
> on this page depends on reading one.

```
python scripts/fp_measurement.py --model gpt-4o-mini --runs 5     # judged, held-out suite
python scripts/fp_measurement.py --provider openai --model gpt-4.1-mini --judge gpt-4o-mini \
    --runs 5 --repeats 20 --scenarios-dir examples/fp-suite \
    --out reports/fp-runs/2026-09-07/s2a-fp-suite-gpt-4.1-mini.jsonl --workers 3
python scripts/fp_aggregate.py reports/fp-runs/2026-09-07/*.jsonl     # the tables below, offline
```

## Results

**Headline (run of record 2026-09-07, live, judged, same model vs itself, five surfaces): 0 false alarms in 82 scored trials, 710 trials reached a verdict — one-sided 95% upper bound 3.6% of scored trials, 0.4% of trials that reached a verdict.**

`[M]` 710 same-model comparisons at the shipped defaults (`runs: 5`, `--match strict`, semantic judge on), 5 artifacts, 3 candidate models across two vendors, 2,071,848/447,798 replay tokens. Every trial, with the traces its verdict was computed over, is in [`reports/fp-runs/2026-09-07/`](../reports/fp-runs/2026-09-07/) and the tables below are regenerated from those files by a test, so this page cannot drift from the run. Three of the five surfaces are where a false positive is actually possible:

- **`examples/fp-suite/`** — twelve scenarios modelled on long-tail apps, every one at **temperature 1.0**, the API's default (`gpt-4.1-mini` (judge `gpt-4o-mini`): 0/23 (upper bound 12.2%) of scored, 0/240 (upper bound 1.2%) of reached; `gpt-4o-mini` (judge `gpt-4.1-mini`): 0/28 (upper bound 10.1%) of scored, 0/240 (upper bound 1.2%) of reached; `openai/gpt-oss-20b` (judge `gpt-4o-mini`): 0/1 (upper bound 95.0%) of scored, 0/12 (upper bound 22.1%) of reached). This is the number the promise rests on.
- **`examples/calibration/arg_*`** — the seven tool-argument scenarios at 0.7, 30 repeats (`gpt-4.1-mini`: 0/30 (upper bound 9.5%) of scored, 0/210 (upper bound 1.4%) of reached). The closest any null trial came to firing was `p = 0.087`.
- **`examples/suite/`** — the original held-out eight at temperature 0, re-run for continuity: 0 of 8 could fire, exactly as in 2026-08. It still measures nothing about false positives, and is kept so the previous run of record stays comparable.

**How to read the bound.** Zero flags in 82 scored trials means the true conditional rate is below 3.6% with 95% confidence, and zero in 710 that reached a verdict means the rate a user sees on these shapes is below 0.4%; neither is a zero, and every point figure on this page sits beside its bound. The conditional denominator is small by design: a trial in which every channel returned `p = 1.00` could not have fired and is excluded (above), and `[M]` on prose scenarios that is most of them — 628 of 710 here. The scored count was predicted before the run, not discovered after it.

**What it does not say.** Twelve plus seven plus eight scenarios are twenty-seven scenarios; repeats buy resolution on them, never coverage of a twenty-eighth. Two candidate models and one open-weight model on a free tier are three models. Every judge here is an OpenAI model. A user's own suite at their own temperature is a different measurement — and now one they can run with one command.

> **Corrected 2026-08-23 (MP-75).** The previous run of record's headline read *"0 false alarms in 8
> scored trials"*. Those 8 were not scored trials. The harness now excludes a trial in which nothing
> could have fired at ALPHA — counting it as a passed trial credits the engine for a test it could
> not fail. **Re-scored, not re-run** — no API call was made: the same recorded verdicts (8 ×
> `unchanged` at confidence 1.00) yield `0/0 = n/a` and `*** THIS RUN MEASURED NOTHING ***`. The
> engine did not change; the accounting did. That reading stood, as *"the real reading is `0/0`,
> the bound is unbounded, the scenarios exist, the run does not"*, until the 2026-09-07 run above.

### The previous run of record (2026-08, held-out suite at temperature 0)

Read as `0/8`, that was an *observation, not a rate*: the exact one-sided 95% upper bound on
0/8 is **~31%**, consistent with a true false-positive rate anywhere from 0% to about a third.
Read as `0/0` — which is what it actually was — the bound is **unbounded**. Either way it is
reported as a fraction and never as "0%"; the harness prints the upper bound itself.

It was also measured on the wrong surface to be reassuring: all 8 held-out scenarios run at
temperature 0, and identical distributions short-circuit to `p=1.0` without the statistic
running at all. **That number says nothing about the surfaces where false positives are
actually possible** — tool use and prose at temperature > 0. `[M] 2026-08-24` MP-54 landed the
seven `examples/calibration/arg_*.json` scenarios (temperature 0.7, real JSON-Schema tools); until
2026-09-07 they had never been run under the shipped engine, and no prose surface at the API's
default temperature existed at all. Both gaps are what the run of record above closes.

`scripts/fp_measurement.py --model gpt-4o-mini --runs 5`, semantic judge ON
(gpt-4o-mini), gpt-4o-mini vs itself across all 8 held-out scenarios — every verdict
`unchanged` at confidence 1.00, in 2026-08 and again in the continuity surface above:

```
cancel_subscription  classify_sentiment  decline_pii          extract_total
format_contact_json  order_status        refund_request       summarize_ticket
                          all 8 -> unchanged at confidence 1.00
                          => 0 SCORED trials: none could have fired (MP-75)
```

**Detection: `[M]` 43 of 46 injected perturbations were flagged.**

One perturbation per scenario — a replacement system prompt that keeps the task and changes one
policy — replayed as the candidate against the unperturbed baseline, once per surface. `[M]` The 95%
one-sided *lower* bound on the true rate at 43/46 is **84.0%**: `1 - upper_bound_95(3, 46)`,
the harness's own exact Clopper-Pearson helper in [`scripts/fp_measurement.py`](../scripts/fp_measurement.py).
The 3 that read `unchanged` (`decline_pii` on `gpt-4o-mini`, `summarize_standup_notes` on `gpt-4.1-mini`, `triage_ticket_json` on `gpt-4o-mini`) are scored MISSED and never excluded: a
miss means either the engine failed to see a real change or the candidate ignored the injected
instruction, and this arm cannot tell those apart. Two flags need a label the harness cannot supply and the traces do: on `arg_numeric_rounding` the
candidate resisted the pounds instruction (every stored payload is still kilograms) and the advisory
argument gate fired on 4- versus 5-decimal rounding jitter at `p = 0.048` — the same mode the `arg_*`
section below prices as a false positive; and the earlier `decline_pii` miss on the held-out suite is
the model resisting a leak instruction (it still declined), which is scored against detection all the
same. These are synthetic, deliberately extreme system-prompt replacements, one run each, and the
interval treats them as exchangeable trials, which by construction they are not. Detection is
demonstrated on twenty-two perturbations across five surfaces, **not characterised**.

That is what the harness prints, verbatim and unadjusted:

<!-- fp-run-of-record:begin 2026-09-07 -->
### Surfaces

| surface | artifact | candidate (judge) | temp | runs x repeats | attempted | reached verdict | SCORED | could not fire | abstained | errors | false alarms | conditional (of scored) | unconditional (of reached) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| suite | s0-suite-gpt-4o-mini.jsonl | `gpt-4o-mini` (gpt-4o-mini) | 0 | 5 x 1 | 8 | 8 | **0** | 8 | 0 | 0 | **0** | n/a (0 trials) | 0/8 = 0.0%, 95% ub 31.2% |
| calibration (score) | s1-arg-gpt-4.1-mini.jsonl | `gpt-4.1-mini` (gpt-4o-mini) | 0.7 | 5 x 30 | 210 | 210 | **30** | 180 | 0 | 0 | **0** | 0/30 = 0.0%, 95% ub 9.5% | 0/210 = 0.0%, 95% ub 1.4% |
| fp-suite | s2a-fp-suite-gpt-4.1-mini.jsonl | `gpt-4.1-mini` (gpt-4o-mini) | 1.0 | 5 x 20 | 240 | 240 | **23** | 217 | 0 | 0 | **0** | 0/23 = 0.0%, 95% ub 12.2% | 0/240 = 0.0%, 95% ub 1.2% |
| fp-suite | s2b-fp-suite-gpt-4o-mini.jsonl | `gpt-4o-mini` (gpt-4.1-mini) | 1.0 | 5 x 20 | 240 | 240 | **28** | 212 | 0 | 0 | **0** | 0/28 = 0.0%, 95% ub 10.1% | 0/240 = 0.0%, 95% ub 1.2% |
| fp-suite | s3-fp-suite-groq-gpt-oss-20b.jsonl | `openai/gpt-oss-20b` (gpt-4o-mini) | 1.0 | 5 x 1 | 12 | 12 | **1** | 11 | 0 | 0 | **0** | 0/1 = 0.0%, 95% ub 95.0% | 0/12 = 0.0%, 95% ub 22.1% |
| **POOLED** |  |  |  |  | 710 | 710 | **82** | 628 | 0 | 0 | **0** | 0/82 = 0.0%, 95% ub 3.6% | 0/710 = 0.0%, 95% ub 0.4% |

### Per scenario (FP arm)

| surface | scenario | attempted | scored | false alarms | could not fire | abstained | errors |
|---|---|---|---|---|---|---|---|
| fp-suite | `summarize_standup_notes` | 41 | **19** | 0 | 22 | 0 | 0 |
| calibration (score) | `arg_numeric_rounding` | 30 | **16** | 0 | 14 | 0 | 0 |
| calibration (score) | `arg_freetext_note` | 30 | **13** | 0 | 17 | 0 | 0 |
| fp-suite | `triage_ticket_json` | 41 | **8** | 0 | 33 | 0 | 0 |
| fp-suite | `rewrite_email_polite` | 41 | **7** | 0 | 34 | 0 | 0 |
| fp-suite | `agent_missing_param_ask` | 41 | **6** | 0 | 35 | 0 | 0 |
| fp-suite | `agent_reschedule_two_step` | 41 | **6** | 0 | 35 | 0 | 0 |
| fp-suite | `sql_from_question` | 41 | **2** | 0 | 39 | 0 | 0 |
| calibration (score) | `arg_optional_fields` | 30 | **1** | 0 | 29 | 0 | 0 |
| fp-suite | `borderline_medication_question` | 41 | **1** | 0 | 40 | 0 | 0 |
| fp-suite | `extract_invoice_fields` | 41 | **1** | 0 | 40 | 0 | 0 |
| fp-suite | `rag_answer_with_citation` | 41 | **1** | 0 | 40 | 0 | 0 |
| fp-suite | `support_order_status` | 41 | **1** | 0 | 40 | 0 | 0 |
| calibration (score) | `arg_enum_phrasing` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_key_order` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_list_order` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_multistep_carry` | 30 | **0** | 0 | 30 | 0 | 0 |
| fp-suite | `classify_review_sentiment` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `format_markdown_table` | 41 | **0** | 0 | 41 | 0 | 0 |
| suite | `cancel_subscription` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `classify_sentiment` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `decline_pii` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `extract_total` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `format_contact_json` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `order_status` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `refund_request` | 1 | **0** | 0 | 1 | 0 | 0 |
| suite | `summarize_ticket` | 1 | **0** | 0 | 1 | 0 | 0 |

### Flagged trials (every one, never excluded)

(none)

### Detection (pooled across surfaces)

| `classify_sentiment` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `decline_pii` on `gpt-4o-mini` | unchanged (conf 1.0) | **MISSED** - no statistically significant behavior change |
| `refund_request` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['lookup_order', 'issue_refund'] -> []; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `arg_enum_phrasing` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call arguments changed: route_ticket(channel (changed); queue (changed)); semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `arg_freetext_note` on `gpt-4.1-mini` | changed_minor (conf 0.992) | **detected** - tool-call arguments changed: add_call_note(note (changed)) |
| `arg_key_order` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call arguments changed: submit_expense(amount_usd 128.45->0; category (changed)); semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `arg_list_order` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call arguments changed: tag_article(tags_0_ (changed); dropped tags_1_; dropped tags_2_); semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `arg_multistep_carry` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call arguments changed: change_reservation_time(new_time (changed)); semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `arg_numeric_rounding` on `gpt-4.1-mini` | changed_minor (conf 0.952) | **detected** - tool-call arguments changed: log_parcel_weight(weight_kg 3.35716->3.3566) |
| `arg_optional_fields` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call arguments changed: create_task(added notify_channel; priority (changed)); semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_missing_param_ask` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: [] -> ['send_statement']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_reschedule_two_step` on `gpt-4.1-mini` | regression (conf 0.992) | **detected** - tool-call behavior changed: ['get_appointment', 'reschedule_appointment'] -> ['get_appointment']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `borderline_medication_question` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - refusal rate 0% -> 100%; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `classify_review_sentiment` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `extract_invoice_fields` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `format_markdown_table` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `rag_answer_with_citation` on `gpt-4.1-mini` | changed_minor (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions |
| `rewrite_email_polite` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `sql_from_question` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `summarize_standup_notes` on `gpt-4.1-mini` | unchanged (conf 0.5) | **MISSED** - no statistically significant behavior change |
| `support_order_status` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['lookup_order'] -> []; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `triage_ticket_json` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_missing_param_ask` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: [] -> ['send_statement']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_reschedule_two_step` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['get_appointment', 'reschedule_appointment'] -> ['get_appointment']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `borderline_medication_question` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - refusal rate 0% -> 100%; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `classify_review_sentiment` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `extract_invoice_fields` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `format_markdown_table` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `rag_answer_with_citation` on `gpt-4o-mini` | changed_minor (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions |
| `rewrite_email_polite` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `sql_from_question` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `summarize_standup_notes` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `support_order_status` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['lookup_order'] -> []; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `triage_ticket_json` on `gpt-4o-mini` | unchanged (conf 0.083) | **MISSED** - no statistically significant behavior change |
| `agent_missing_param_ask` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: [] -> ['send_statement']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_reschedule_two_step` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['get_appointment', 'reschedule_appointment'] -> ['get_appointment']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `borderline_medication_question` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - refusal rate 0% -> 100%; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `classify_review_sentiment` on `openai/gpt-oss-20b` | regression (conf 0.976) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `extract_invoice_fields` on `openai/gpt-oss-20b` | regression (conf 0.976) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `format_markdown_table` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `rag_answer_with_citation` on `openai/gpt-oss-20b` | changed_minor (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions |
| `rewrite_email_polite` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `sql_from_question` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `summarize_standup_notes` on `openai/gpt-oss-20b` | regression (conf 0.976) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `support_order_status` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['lookup_order'] -> []; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `triage_ticket_json` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |

```
  Detection: 43/46 injected perturbations caught
  95% lower bound on the true rate: 84.0% (one-sided Clopper-Pearson, n=46)
  That rate is over perturbations APPLIED, not over behaviour changes; and the
  interval treats them as exchangeable trials, which by construction they are
  not - each targets a different signal.
  Unmeasured (excluded): 0
  NOTE: 3 perturbation(s) MISSED - counted against detection, never
  excluded. A miss means EITHER the engine failed to see a real change OR the
  candidate ignored the injected instruction and its behaviour did not change.
  This arm cannot tell those apart, and one that could would let a dead engine
  post 0/0 - so it always counts the miss. Read the per-scenario explanation and
  repertoire above before treating one as an engine defect (ADR-0022).
```

### Cost

Replay tokens in/out 2,071,848/447,798 across 5 artifact(s); 3711 judge calls implied by the traces (judge tokens not metered).
<!-- fp-run-of-record:end -->

> **Corrected 2026-08-24 (MP-81).** This section previously read *"Detection: every
> perturbation that actually changed behavior was caught"* and concluded *"So 2/2 real
> behavior changes were flagged"*, removing `decline_pii` from the denominator on the ground
> that the model resisted, so behavior did not change and it was *"not a false negative"*.
> The harness scored that same run **2/3**, before and after the correction — the number
> published here had been adjusted by hand, in the flattering direction.
>
> The resistance reading is not illegitimate **as analysis**, and it may well be right. It is
> not a **measurement**, and this is the one place the distinction bites: a perturbed pair
> reading `unchanged` is *either* an engine that failed to see a real change *or* a candidate
> that ignored the injected instruction — both present identically, and this arm cannot tell
> them apart. An arm permitted to exclude on that basis lets a **dead engine post `0/0`** and
> read as *"nothing to report"* rather than *"caught nothing"*. So the miss is counted, and
> the adjudication is left to a human reading this note. → **ADR-0023**, which rejects this
> exact exclusion. `README.md` has carried the corrected framing (*"2 of 3 injected
> perturbations were flagged"*) since #46; its interval sentence still published a lower bound
> for the withdrawn `2/2` denominator and is corrected in the same change as this note.

### Corroborating evidence

| Evidence | Pairs | Scored (could have fired) | Result |
|---|---|---|---|
| **Run of record 2026-09-07** (five surfaces above; artifacts in `reports/fp-runs/2026-09-07/`) | 710 | **82** | **0/82**, upper bound 3.6%; 0/710 of trials reached, upper bound 0.4% |
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

**Phase-0 DoD: detection demonstrated (43/46, lower bound 84.0%) but not characterised; the
false-positive rate is MEASURED as a bound — 0/82 of scored trials (upper bound 3.6%), 0/710 of trials
that reached a verdict (upper bound 0.4%) — on the 2026-09-07 run of record.** It is a bound, not a
zero, and it is a bound on twenty-seven scenario shapes and three models under the shipped defaults;
the previous claim of *"a measured 0% false-positive rate"* stays withdrawn (2026-08-23) because
that is not what any run can show. Establishing it took a live run over `examples/fp-suite/` and
`examples/calibration/arg_*.json` — surfaces at temperature > 0, where false positives are actually
possible. Those sets exist; so, now, does the run.

**The `arg_*` files are a `score` set and no threshold may be fitted on them (ADR-0025).** They
live under `examples/calibration/` for provenance, but they do not share that directory's tuning
role; roles are declared in [`examples/roles.json`](../examples/roles.json) and enforced by
`tests/test_suite_roles.py`. Two consequences bind whoever runs this next:

- **No argument threshold may be fitted here.** These are the only tool-bearing scenarios at
  temperature > 0 in the repo, which makes them both the obvious place to fit a floor and the
  one place fitting it would void the number this section is trying to establish. `[M]` This
  does not block MP-04: `MIN_TOOL_ARG_TVD = 1.0` on that branch is a structural rule derived
  from exhaustive relabelings under a constructed null, with no scenario set involved, at the
  ceiling of its scale. A labelled fit set is needed only if a measured run says 1.0 must move.
  `[M] 2026-08-25 @ 0e81392` **A measured run did say so, and the answer was a cap, not a fit.**
  Priced offline against the committed repertoires — no API key, no scenario fitted — the worst
  of 48 cells is **66 / 1580 = 4.18% of scored trials** (`arg_optional_fields`, `gpt-4.1-mini`,
  `--match subset`, N=3), with 26 cells non-zero over 0.08%–4.18%, against **0 / 0** before the
  signal existed. Raw artifact and the reading of it:
  [`examples/calibration/results/arg-gate-price.json`](../examples/calibration/results/arg-gate-price.json)
  and [`README-arg-gate-fp.md`](../examples/calibration/results/README-arg-gate-fp.md).
  Regenerate with:

  ```
  python scripts/arg_gate_price.py --reps 3000 --seed 20260825
  ```

  `[A]` **The committed artifact predates that command being reproducible.** It records
  `seed: 20260825`, but the script mixed `hash(mode)` — salted per process (PEP 456) — into the
  RNG, so the stored cells are not byte-re-derivable; the seed is honoured from `0e81392`'s
  successor onward, where a sha256 salt replaced it. `[M]` No interval is published over
  replicates, deliberately: those are draws from an assumed population and a Clopper-Pearson
  bound over them shrinks with compute (3.82% at 1k, 3.27% at 4k). The binding uncertainty is
  stated instead — Good-Turing puts 31–44% of the payload mass on values the runs never saw.

  Because that cost is real and the floor behind it is uncalibrated, the argument signal
  escalates only to `changed_minor` and can never fail a build alone (**ADR-0029**; the shape
  is the semantic judge's own 2026-06 cap, three sections below). The fitting ban above is
  therefore load-bearing in both directions: it is also why the floor was *not* tuned, and why
  `runs` was not tuned either — the cost is non-monotone in N, so no default is safe, and
  `arg_*` is the fitted-on set. Promotion needs the labelled set this bullet forbids building
  here.
- **`[M]` This set cannot establish a low false-positive rate, whatever it returns — and the
  likeliest outcome is that it measures nothing at all.** Seven scenarios give a one-sided 95%
  upper bound of **34.8%** (`upper_bound_95(0, 7)`), but that is the *ceiling*, assuming all
  seven score. They will not. This document records **three runs**, and pooling them is the
  only honest input: `[M]` **0 of 8** scored on the held-out suite, **1 of 6** on the
  independent-candidate calibration run, **0 of 6** on the self-judge run — **1 scored trial
  out of 20 attempted, 5.0%**. At that rate seven scenarios project to **0.35 expected scored
  trials: the modal outcome is ZERO**, which is `*** THIS RUN MEASURED NOTHING ***` and
  abstention under ADR-0018. If one trial does score, the bound is **95.0%**; two would need a
  28.6% scoring rate, nearly 6× the pooled rate, so **77.6% is not reachable here**. `5.0%`
  needs n≈59 `[M]` (`upper_bound_95(0, 58)` = 5.03%, `(0, 59)` = 4.95%). *An earlier draft of
  this bullet projected 3–4 trials by halving, and a second projected 1–2 by using only the
  most favourable of the three runs; both are withdrawn, and both erred in the flattering
  direction.* What this set can do is *falsify* the signal — one equivalent-looking argument
  change that flags is decisive, and that costs a single trial. What it cannot do is supply the
  headline this section is missing, and no run of it should be reported as having done so.
  **This is the measured case for MP-89's `--repeats`: without more trials per scenario, the
  run most likely returns an abstention rather than a number.**
  > **`[M] 2026-09-07` Measured, and the projection above was wrong in the pessimistic direction.**
  > At `--repeats 30` on `gpt-4.1-mini` (judge on) this set scored **30 of 210** trials — 14.3%, not 5.0% — with **0** false alarms: upper bound **9.5%** of scored, **1.4%** of reached. Three shapes scored
  > (`arg_numeric_rounding`, `arg_freetext_note`, `arg_optional_fields`) and four never did, exactly the
  > split the repertoire artifacts predicted. The set still cannot establish a rate below 5% on its
  > own; pooled with `examples/fp-suite/` it contributes to the headline above.
- `[M] 2026-08-25` **`scripts/fp_measurement.py` now refuses to pool across roles.**
  `--scenarios-dir examples/calibration` names a directory declaring **two** roles — the seven
  `arg_*` (`score`) and the six semantic scenarios `MIN_SEMANTIC_DELTA` was fitted on (`fit`) —
  and a rate pooled across them would be in-sample for 6 of its 13. MP-89 landed the filter as a
  **refusal** rather than a default: the run exits with `error: 2 roles declared for this
  directory (fit, score) ... Re-run naming the one you mean, e.g. --role score`. Pass
  `--role score`. *(This entry previously read "cannot select the subset ... Until MP-89 lands";
  it landed at `0c2e999`, and `:517-520` is now unrelated code — the directory load is at
  `scripts/fp_measurement.py:685`.)*

## Detection (control)

The harness injects three **perturbed instructions**, and that vocabulary is deliberate:
whether a perturbation produces a behaviour change is what this arm measures, never a premise
it may assert (ADR-0023). Each targets a different channel: `refund_request` (never issue refunds →
tool-trajectory + refusal), `decline_pii` (share the customer email → policy/format +
semantic), `classify_sentiment` (always "Positive" → assertion + semantic). The channel named
is the one the perturbation *targets*; whether the candidate's behaviour actually changed on it
is the measurement. A `regression` or `changed_minor` verdict scores a detection; **anything
else, `unchanged` included, scores a miss; the only exclusion is an abstention that reached
no verdict at all (ADR-0018)**. `[M]` On the run of record
above, `decline_pii` returned `unchanged` and is scored a MISS.

**A miss is never a false alarm — it is either a false negative or a correct true negative, and
this arm cannot tell which.** Either way it fails in the safe direction for this product, and
**the way to raise `2/3` is more perturbations, never a lower floor.**

`[M]` On the independent-judge calibration run of record
([`examples/calibration/results/result-independent-judge.json`](../examples/calibration/results/result-independent-judge.json)
— 6 labelled pairs, candidate `gpt-3.5-turbo`, judge `gpt-4o-mini`, temperature 0.7, a
**different measurement** from the 3 perturbations above), the semantic sweep is flat from
`MIN_SEMANTIC_DELTA` 0.1 to 0.9: recall **4/6** and 0 false alarms at every value. `[M]` The 95% one-sided *lower* bound on true detection at 4/6 is **27.1%** — `1 - upper_bound_95(2, 6)` in [`scripts/fp_measurement.py`](../scripts/fp_measurement.py), the same helper the 3-perturbation arm publishes through. **A bare `4/6` reads as 67%; six trials cannot support that**, and the flatness above means this column is not even measuring the floor. So on that
set the floor is inert and lowering it buys no detection. **That flatness is not slack, and the
reason matters more than the number**: `[M]` 5 of those 6 equivalent pairs return `p = 1.00` and
could not have fired at any floor, and the sixth (`explain_concept`, delta 0.20) is blocked by
the permutation p-gate at `p = 0.50` at every floor value — so at `runs=5` that column measures
the **p-gate**, not the floor. `[M]` The floors first bind at **N=9 (semantic), 11 (tool), 12
(refusal)** (ADR-0002), which is where lowering one converts directly into false alarms, and
where this run cannot see. Converting this miss by moving a floor would require new labelled
calibration data under [`examples/calibration/`](../examples/calibration/), not this number.

## Semantic-judge calibration & promotion (2026-06-24)

The semantic judge **now escalates a consistent meaning change to a CI-failing
`regression`** (previously it was capped at `changed_minor` because `MIN_SEMANTIC_DELTA`
was an uncalibrated guess). The promotion is backed by a labeled calibration set —
[`examples/calibration/`](../examples/calibration/), **deliberately distinct from the
held-out suite above** so this tuning does not leak into the held-out result — and two raw-data
runs recorded under [`examples/calibration/results/`](../examples/calibration/results/):

- **Independent-judge run (the evidence of record):** candidate `gpt-3.5-turbo`, judge
  `gpt-4o-mini` (the judge does **not** grade its own output, so no self-judging bias).
  At `MIN_SEMANTIC_DELTA=0.5`, `ALPHA=0.05`: **0 false positives**, recall 4/6 (lower bound **27.1%**, above). One
  equivalent pair scored a noisy `delta=0.20` and was correctly **absorbed by the permutation
  p-gate**. `[M]` **Corrected 2026-08-24 (MP-81):** this previously read "absorbed by the floor
  + permutation p-gate — i.e. the conservative floor earns its keep". It was not the floor.
  That pair (`explain_concept`, delta 0.20, `p = 0.50`) clears even a 0.1 floor and is stopped
  by the p-gate alone, which is exactly why the sweep above is flat. At `runs=5` the floor is
  inert on this set; it is a conservative choice, not a fitted or a load-bearing one.
- **Self-judge run:** candidate == judge == `gpt-4o-mini`. Cleaner (0/6 FP, recall 5/6 — lower bound **41.8%**, `1 - upper_bound_95(1, 6)`) but,
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
drift), recall on subtle changes is imperfect (4/6, lower bound **27.1%** — a miss is never a false *alarm*, which is the
safe direction, but whether any given one is a false negative or a correct true negative is
what this arm cannot tell: `[M]` of those two misses, `explain_concept` is a genuine p-gate
miss while `define_term` came back judged fully equivalent at `p = 1.00`, indistinguishable
from the candidate simply not following the injected instruction), and every trial here was
scored by an **OpenAI judge**. Since 2026-08-31 the judge also runs on Gemini and the four
OpenAI-compatible hosts (MP-143), but nothing on this page has been re-measured with one:
a judge that runs is not a judge that is calibrated. **Next:** expand to
≥30 labeled pairs incl. real model-migration traces, and repeat the 2026-09-07 same-model
measurement with a non-OpenAI judge, before relying on the gate in high-stakes CI.

## Honest framing (trust guardrail)

These are **measurements under the stated settings**, not absolute claims about model
quality. The structural floors remain deliberately conservative; the semantic floor is now
calibrated (above) but on a modest set — and **"calibrated" here means confirmed FP-safe and
detection-preserving on that set, not *fitted*:** `[M]` the set cannot discriminate the value,
the semantic sweep being flat from 0.1 to 0.9, so 0.5 is a conservative choice rather than a
fitted one. `[M]` Re-scored under ADR-0022 that evidence is **0/1, 95% upper bound 95.0%**. So
treat the `regression` promotion as a **first** calibration, not a well-evidenced or a final
one.
