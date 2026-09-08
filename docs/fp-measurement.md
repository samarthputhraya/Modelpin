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
  attempted, 5.0% pooled**, which was the only planning figure this document supported before
  2026-09-07 and the one the `arg_*` projection below used. `[M]` The run of record then scored
  **39 of 710 (5.5%)** — see Results; the projection is preserved unedited with its correction
  attached. *An earlier version of this sentence counted two runs
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
- **Pre-registered, for the surfaces it covers.** The `fp-suite` surfaces' scenarios, models,
  repeat counts, exclusions and the publish-every-flag rule were committed in
  `examples/fp-suite/README.md` at `b504730` (10:35:35Z), before their first trial (10:36:08Z).
  The two earlier surfaces (`examples/suite` continuity and `arg_*`, both started 10:15Z at
  `5fa1d48`) predate that document and are pre-registered only by the configuration their
  artifact headers record. No repeat was added or dropped after a number was seen.

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
python scripts/fp_aggregate.py reports/fp-runs-adr0040/2026-09-07/*.jsonl   # the tables below, offline
```

The third command reads the **re-scored** artifacts, which is what the tables below publish. To
re-score a recorded run under the current engine without buying its replays again:

```
python scripts/fp_measurement.py --rejudge reports/fp-runs/2026-09-07/s2a-fp-suite-gpt-4.1-mini.jsonl \
    --judge gpt-4o-mini --arm both --workers 1 \
    --out reports/fp-runs-adr0040/2026-09-07/s2a-fp-suite-gpt-4.1-mini-adr0040.jsonl
```

## Results

**Headline (run of record 2026-09-07, live, judged, same model vs itself, five surfaces, re-scored under the current engine, ADR-0040) — read the severities first (MP-223). On the CI-FAILING channels, the ones that can turn your build red: 0 false alarms in 9 exposed trials, one-sided 95% upper bound 28.3%. On the ADVISORY channels, which annotate and can never fail a build alone: 0 in 30, upper bound 9.5%. Pooled across both: 0 false alarms in 39 scored trials, 710 trials reached a verdict — upper bound 7.4% of scored trials, 0.4% of trials that reached a verdict.**

`[M]` **Two caveats that come before any of those numbers, both required by ADR-0042.**

1. **Every one of those rates is over TRIALS, and the trials are repeats of very few shapes.** Hard `0/9` is **6 distinct scenarios**, so over shapes it is `0/6` — bound **39.3%**, not 28.3%. Advisory `0/30` is **3** distinct scenarios, two of which supply 29 of the 30, so over shapes it is `0/3` — bound **63.2%**, not 9.5%. A rate over repeats of three scenarios is a statement about three scenarios.
2. **"Hard" is a severity, not a channel, and this hard bound is not a tool-channel bound.** `[M]` Its 9 trials are **8 semantic + 1 refusal + 0 tool + 0 assertion**. The tool-call trajectory — the channel a migration tool exists for — contributed **zero exposure**, so the headline constrains it not at all. That channel's own measurement is under *Channel exposure* below, and it is a separate, worse number.

`[M]` **The pooled 7.4% is the weaker claim about the product's promise, not the stronger one.** 30 of its 39 trials could only ever have fired on the advisory argument gate, which by ADR-0029 escalates to `changed_minor` and cannot exit 1 — so 77% of that denominator is a channel a user's CI never sees. *"If Modelpin says it broke, it broke"* is a promise about the build-failing channels, and the number that constrains it is **28.3% over 9 trials**, not 7.4% over 39. Both are published, per surface, in the block below; the denominators overlap and are never summed. **The classifier that produces them is unchanged**: a `changed_minor` still counts against the north-star metric exactly like a `regression`, because a channel allowed to ship as "advisory" and thereby leave the metric would leave it permanently.

`[M]` 710 same-model comparisons at the shipped defaults (`runs: 5`, `--match strict`, semantic judge on), 5 artifacts, 3 candidate models across two vendors, 2,071,848/447,798 replay tokens. Every trial, with the traces its verdict was computed over, is in [`reports/fp-runs-adr0040/2026-09-07/`](../reports/fp-runs-adr0040/2026-09-07/) and the tables below are regenerated from those files by a test, so this page cannot drift from the run. Those five artifacts are the ORIGINAL replays of [`reports/fp-runs/2026-09-07/`](../reports/fp-runs/2026-09-07/) re-scored under the current engine, by the same judge; no replay was bought twice, and a re-score replaces its source's numbers rather than adding to them. Two of the five surfaces are where a false positive is actually possible, and a third is a
cross-vendor sanity arm:

- **`examples/fp-suite/`** — twelve scenarios modelled on long-tail apps, every one at **temperature 1.0**, the API's default (`gpt-4.1-mini` (judge `gpt-4o-mini`): 0/3 (upper bound 63.2%) of scored, 0/240 (upper bound 1.2%) of reached; `gpt-4o-mini` (judge `gpt-4.1-mini`): 0/6 (upper bound 39.3%) of scored, 0/240 (upper bound 1.2%) of reached). These twelve supply 9 of the 39 scored trials. Under the previous engine they supplied 52 of 82 and carried the promise; they no longer do, and what replaced them is weaker evidence — see *What it does not say*.
- **`examples/calibration/arg_*`** — the seven tool-argument scenarios at 0.7, 30 repeats (`gpt-4.1-mini`: 0/30 (upper bound 9.5%) of scored, 0/210 (upper bound 1.4%) of reached). These now supply **30 of the 39** scored trials. The closest any `arg_*` null came to firing was `p = 0.087` (`arg_freetext_note#5`), which is also the closest run-wide, against `ALPHA = 0.05`.
- **Groq sanity arm** — the same twelve scenarios, one repeat, on `openai/gpt-oss-20b` (judge `gpt-4o-mini`): 12 trials, **0 scored**, 0 false alarms, and so no conditional bound of its own at all. It exists to show the harness runs against a second vendor; it constrains nothing. `[M]` Removing it leaves the pooled conditional bound at 7.4% (39 scored either way), moves the unconditional from 0.4210% to 0.4283%, and moves the detection bound from 90.1% to 86.8% (33/34).
- **`examples/suite/`** — the original held-out eight at temperature 0, re-run for continuity: 0 of 8 could fire, exactly as in 2026-08. It still measures nothing about false positives, and is kept so the previous run of record stays comparable.

**How to read the bound.** Zero flags in 39 scored trials means the true conditional rate is below 7.4% with 95% confidence, and zero in 710 that reached a verdict means the rate a user sees on these shapes is below 0.4%; neither is a zero, and every point figure on this page sits beside its bound. The conditional denominator is small by design: a trial in which every channel returned `p = 1.00` could not have fired and is excluded (above), and `[M]` on prose scenarios that is most of them — 671 of 710 here. So the 0.4% is reported for completeness and is not the number to quote: it is what a user running these same checks would have seen, and 671 of its 710 trials could not have failed. The conditional 7.4% is the number that constrains the engine. That the four schema-constrained `arg_*` shapes would not score was predicted before the run (`examples/calibration/results/README-arg-gate-fp.md`: "the model varied on three of seven shapes"), and they did not.

**What it does not say.** Twelve plus seven plus eight scenarios are twenty-seven scenarios, and `[M]` **9 of them carried the bound**: 18 contributed no scored trial (four `arg_*` shapes, `classify_review_sentiment`, `format_markdown_table`, all eight of the held-out suite, and — new under this engine — `rewrite_email_polite`, `agent_reschedule_two_step`, `support_order_status` and `rag_answer_with_citation`), and two shapes — `arg_numeric_rounding` (16) and `arg_freetext_note` (13) — supply **29 of the 39**. Over distinct shapes the POOLED bound is **28.3%** (`upper_bound_95(0, 9)` — nine *shapes*). `[!]` **Do not confuse this 28.3% with the hard-severity 28.3% in the headline**: that one is `upper_bound_95(0, 9)` over nine *trials*, and its own distinct-shape discount is a different number again (`0/6` over six shapes, **39.3%**). Two different quantities collide on one numeral here purely by arithmetic accident; neither may ever be printed without saying which it is (ADR-0042 D3). `[M]` Of the 39 scored trials, **30 could only have fired on the advisory argument gate, 8 on the semantic channel and 1 on refusal — 0 on the tool-call trajectory and 0 on format/assertion**: the structural floors saw no exposure here.

**Read that last sentence before quoting the 7.4%.** Under the previous engine the bound was carried by the semantic channel (51 of 82 scored trials), which is a hard, build-failing signal. Under this one it is carried by the **argument gate — 30 of 39 — and that gate is advisory**: by ADR-0029 it escalates only to `changed_minor` and can never fail a build on its own. So most of what the conditional bound now measures is a channel that cannot produce a red build. Only **9 of the 39** scored trials sat on a channel that can independently fail one — 8 on the semantic channel, 1 on refusal — and the tool trajectory contributed **zero**. The assertion channel contributed zero too, and it could not have failed a build either: like the argument gate it escalates only to `changed_minor` (ADR-0032). The honest reading is that this number constrains the engine less than the same number did a week ago, on a smaller denominator, and the page says so rather than reporting an improved-looking 0/39. The tool channel has since been given a corpus and a denominator of its own — see *Channel exposure* below; the assertion channel still has neither. Repeats buy resolution on these shapes, never coverage of a twenty-eighth. Two OpenAI candidate models are two models; the Groq arm is a sanity arm. **Every judge that produced a number in the bound above is an OpenAI model** — 24 of these trials have since been re-scored by a non-OpenAI judge, whose separate figures appear under *Cross-judge agreement* below; it agreed on every verdict but *not* on how many trials were scorable at all. The bound itself is unchanged and remains OpenAI-judged. A user's own suite at their own temperature is a different measurement — and now one they can run with one command.

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
reported as a fraction with its bound beside it, never as a bare rate; the generated tables
below print `0/n = 0.0%` only where the bound sits in the same cell.

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

**Detection: `[M]` 45 of 46 injected perturbations were flagged — 46 perturbed replays of 22 distinct perturbations, which is the smaller number to read.**

One perturbation per scenario that has one — a replacement system prompt that keeps the task and
changes one policy — replayed as the candidate against the unperturbed baseline, once per surface:
the 12 `fp-suite` prompts on all three models, the 7 `arg_*` on one, and 3 of the 8 held-out
scenarios on one (3 + 7 + 12 + 12 + 12 = 46). `[M]` The 95% one-sided *lower* bound the harness
prints at 45/46 is **90.1%**: `1 - upper_bound_95(1, 46)`, its own exact Clopper-Pearson helper in
[`scripts/fp_measurement.py`](../scripts/fp_measurement.py). **That interval counts one perturbation
up to three times**, the defect `tests/test_report_claims.py` names for the Drift Map, so it is not
the number to quote. Over **distinct perturbations** the honest reading is `[M]` **21/22 → 80.2%**.
Under the previous engine the two readings differed — 19/22 counting a miss on any surface against
the perturbation, 21/22 counting a detection on at least one — and they now coincide, because the
only remaining miss misses on the single surface it was replayed on. These are measurements of the
engine on a scenario-model pair, never a statement about a model: a miss on `gpt-4o-mini` is a miss
by Modelpin, and the per-model rows in the block below must not be read as a ranking.
The 1 that reads `unchanged` (`decline_pii` on `gpt-4o-mini`) is scored MISSED and never excluded: a
miss means either the engine failed to see a real change or the candidate ignored the injected
instruction, and this arm cannot tell those apart. `[M]` On `decline_pii` the model
still declined on all 5 runs — an instruction the candidate did not follow, which this arm cannot tell
from a blind engine, so it counts as a miss.

`[M]` **Two of the three misses under the previous engine were the engine's own false negatives
rather than a candidate that ignored its instruction — this arm cannot tell those apart in
general, but on these two the stored traces settle it — and ADR-0040 fixed
them.** On `triage_ticket_json` (`gpt-4o-mini`) and
`summarize_standup_notes` (`gpt-4.1-mini`) the judge returned `semantic_score = 0.0` — it *did*
separate the sides (5 of 5 candidate runs `other`/`low` against 5 of 5 baseline `bug`/`high`; 5 of 5
summaries with the blockers dropped) — yet the verdict was `unchanged` at `p = 0.083` and `p = 0.500`,
because the judge also scored 2 and 4 of the 5 **baseline** runs non-equivalent to the modal baseline
output, which for free text is an arbitrary run. That is the structural asymmetry
`examples/fp-suite/README.md` pre-registered as a trap, realised as a **false negative**: a noisy
baseline side raised the permutation p and hid a real change. ADR-0040 compares each candidate run
to **every** baseline run rather than to one arbitrary modal run; on these same stored traces both
are now flagged at confidence 0.996, and `[M]` the same change produced **0** new false alarms across
all 710 FP-arm trials on both surfaces. Detection over replays moved 43/46 → 45/46 and over distinct
perturbations 19/22 → 21/22. The remaining miss is `decline_pii`, described above.

**Read that increase with its cost — three things work against it.** `[M]` The two recovered
replays are **the same two trials the engine change was designed and accepted against** (ADR-0040's
own acceptance criterion D4), so they are in-sample and the 90.1% lower bound is optimistic by an
unquantified amount. `[M]` The check that the fix bought no false alarms is real but weak on the
channel it changed: the semantic channel's scored exposure fell from 51 trials to 8, so zero alarms
there bounds that channel only at **31.2%**. `[M]` And on an exact enumeration of a modelled null at
the shipped `runs: 5`, the new rule is **1.63×–9.59× more prone to a false alarm** than the old one
(0.275% → 2.635% at ten semantic classes); its measured safety on this run is conditional on the
judge being lenient enough to call five differently-worded outputs equivalent, and under a
maximally strict judge ADR-0040's own headline trial reverts to a miss. That is a property of the
judge model, which is user configuration, and it is **not yet priced** — ADR-0040's fourth
falsifier is open. One flag needs the opposite label:
on `arg_numeric_rounding` the candidate resisted the pounds instruction (every stored payload is still
kilograms) and the advisory argument gate fired on 4- versus 5-decimal rounding jitter at `p = 0.048`
— the same mode the `arg_*` section below prices as a false positive. These are synthetic, deliberately extreme system-prompt replacements, one run each, and the
interval treats them as exchangeable trials, which by construction they are not. Detection is
demonstrated on twenty-two distinct perturbations across five surfaces, **not characterised**.

That is what the harness prints, verbatim and unadjusted:

<!-- fp-run-of-record:begin reports/fp-runs-adr0040/2026-09-07 -->
> **These surfaces are RE-SCORES of stored replays, not new samples.** Each one
> REPLACES its source's numbers; it never adds to them.

> - `s0-suite-gpt-4o-mini-adr0040.jsonl` re-scores `s0-suite-gpt-4o-mini.jsonl` under a changed engine (judge `gpt-4o-mini` -> `gpt-4o-mini`, engine `5fa1d48` -> `b4ca88a`)
> - `s1-arg-gpt-4.1-mini-adr0040.jsonl` re-scores `s1-arg-gpt-4.1-mini.jsonl` under a changed engine (judge `gpt-4o-mini` -> `gpt-4o-mini`, engine `5fa1d48` -> `b4ca88a`)
> - `s2a-fp-suite-gpt-4.1-mini-adr0040.jsonl` re-scores `s2a-fp-suite-gpt-4.1-mini.jsonl` under a changed engine (judge `gpt-4o-mini` -> `gpt-4o-mini`, engine `b504730` -> `b4ca88a`)
> - `s2b-fp-suite-gpt-4o-mini-adr0040.jsonl` re-scores `s2b-fp-suite-gpt-4o-mini.jsonl` under a changed engine (judge `gpt-4.1-mini` -> `gpt-4.1-mini`, engine `b504730` -> `b4ca88a`)
> - `s3-fp-suite-groq-gpt-oss-20b-adr0040.jsonl` re-scores `s3-fp-suite-groq-gpt-oss-20b.jsonl` under a changed engine (judge `gpt-4o-mini` -> `gpt-4o-mini`, engine `b504730` -> `b4ca88a`)

### False-positive rate by severity (MP-223)

A `changed_minor` counts against the north-star metric exactly like a `regression`
and must keep doing so -- otherwise a channel could escape the metric permanently by
shipping as advisory. But the two have incompatible consequences: only `regression`
exits 1 and fails a build. Each severity therefore gets its OWN denominator -- the
trials in which a channel of that severity could have fired at all (ADR-0022's
predicate, applied one severity at a time; ADR-0038 D3's shape).

Hard (CI-failing) channels: `tool`, `refusal`, `semantic`. Advisory: `argument`, `assertion`.
**The two denominators overlap and do not sum to SCORED** -- a trial on which both
severities were live is in both.

| surface | HARD (fails your build) | over distinct shapes | advisory (annotates only) | over distinct shapes | undetermined |
|---|---|---|---|---|---|
| suite | **n/a (0 trials)** | n/a (0 trials) | n/a (0 trials) | n/a (0 trials) | 0 |
| calibration (score) | **n/a (0 trials)** | n/a (0 trials) | 0/30 = 0.0%, 95% ub 9.5% | 0/3 = 0.0%, 95% ub 63.2% | 0 |
| fp-suite | **0/3 = 0.0%, 95% ub 63.2%** | 0/2 = 0.0%, 95% ub 77.6% | n/a (0 trials) | n/a (0 trials) | 0 |
| fp-suite | **0/6 = 0.0%, 95% ub 39.3%** | 0/5 = 0.0%, 95% ub 45.1% | n/a (0 trials) | n/a (0 trials) | 0 |
| fp-suite | **n/a (0 trials)** | n/a (0 trials) | n/a (0 trials) | n/a (0 trials) | 0 |
| **POOLED** | **0/9 = 0.0%, 95% ub 28.3%** | 0/6 = 0.0%, 95% ub 39.3% | 0/30 = 0.0%, 95% ub 9.5% | 0/3 = 0.0%, 95% ub 63.2% | 0 |

**A severity is not a channel, and the pooled hard bound above is not a tool-channel bound.** Trials in which each channel could have fired at all, pooled:

| severity | channel | exposed trials |
|---|---|---|
| HARD | `tool` | 0  <-- ZERO exposure: this channel contributes NO measurement |
| HARD | `refusal` | 1 |
| HARD | `semantic` | 8 |
| advisory | `argument` | 30 |
| advisory | `assertion` | 0  <-- ZERO exposure: this channel contributes NO measurement |

`[!]` **Read the distinct-shape columns, not the trial columns.** A rate over repeats of three scenarios is a statement about three scenarios; ADR-0041's detection arm carries the same discount and `examples/roles.json` already prices this set by scenario count. ADR-0042 D3/D4.

### Surfaces

| surface | artifact | candidate (judge) | temp | runs x repeats | attempted | reached verdict | SCORED | could not fire | abstained | errors | false alarms | conditional (of scored) | unconditional (of reached) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| suite | s0-suite-gpt-4o-mini-adr0040.jsonl | `gpt-4o-mini` (gpt-4o-mini) | 0 | 5 x 1 | 8 | 8 | **0** | 8 | 0 | 0 | **0** | n/a (0 trials) | 0/8 = 0.0%, 95% ub 31.2% |
| calibration (score) | s1-arg-gpt-4.1-mini-adr0040.jsonl | `gpt-4.1-mini` (gpt-4o-mini) | 0.7 | 5 x 30 | 210 | 210 | **30** | 180 | 0 | 0 | **0** | 0/30 = 0.0%, 95% ub 9.5% | 0/210 = 0.0%, 95% ub 1.4% |
| fp-suite | s2a-fp-suite-gpt-4.1-mini-adr0040.jsonl | `gpt-4.1-mini` (gpt-4o-mini) | 1.0 | 5 x 20 | 240 | 240 | **3** | 237 | 0 | 0 | **0** | 0/3 = 0.0%, 95% ub 63.2% | 0/240 = 0.0%, 95% ub 1.2% |
| fp-suite | s2b-fp-suite-gpt-4o-mini-adr0040.jsonl | `gpt-4o-mini` (gpt-4.1-mini) | 1.0 | 5 x 20 | 240 | 240 | **6** | 234 | 0 | 0 | **0** | 0/6 = 0.0%, 95% ub 39.3% | 0/240 = 0.0%, 95% ub 1.2% |
| fp-suite | s3-fp-suite-groq-gpt-oss-20b-adr0040.jsonl | `openai/gpt-oss-20b` (gpt-4o-mini) | 1.0 | 5 x 1 | 12 | 12 | **0** | 12 | 0 | 0 | **0** | n/a (0 trials) | 0/12 = 0.0%, 95% ub 22.1% |
| **POOLED** |  |  |  |  | 710 | 710 | **39** | 671 | 0 | 0 | **0** | 0/39 = 0.0%, 95% ub 7.4% | 0/710 = 0.0%, 95% ub 0.4% |

### Per scenario (FP arm)

| surface | scenario | attempted | scored | false alarms | could not fire | abstained | errors |
|---|---|---|---|---|---|---|---|
| calibration (score) | `arg_numeric_rounding` | 30 | **16** | 0 | 14 | 0 | 0 |
| calibration (score) | `arg_freetext_note` | 30 | **13** | 0 | 17 | 0 | 0 |
| fp-suite | `sql_from_question` | 41 | **2** | 0 | 39 | 0 | 0 |
| fp-suite | `summarize_standup_notes` | 41 | **2** | 0 | 39 | 0 | 0 |
| fp-suite | `triage_ticket_json` | 41 | **2** | 0 | 39 | 0 | 0 |
| calibration (score) | `arg_optional_fields` | 30 | **1** | 0 | 29 | 0 | 0 |
| fp-suite | `agent_missing_param_ask` | 41 | **1** | 0 | 40 | 0 | 0 |
| fp-suite | `borderline_medication_question` | 41 | **1** | 0 | 40 | 0 | 0 |
| fp-suite | `extract_invoice_fields` | 41 | **1** | 0 | 40 | 0 | 0 |
| calibration (score) | `arg_enum_phrasing` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_key_order` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_list_order` | 30 | **0** | 0 | 30 | 0 | 0 |
| calibration (score) | `arg_multistep_carry` | 30 | **0** | 0 | 30 | 0 | 0 |
| fp-suite | `agent_reschedule_two_step` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `classify_review_sentiment` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `format_markdown_table` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `rag_answer_with_citation` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `rewrite_email_polite` | 41 | **0** | 0 | 41 | 0 | 0 |
| fp-suite | `support_order_status` | 41 | **0** | 0 | 41 | 0 | 0 |
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
| `agent_reschedule_two_step` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['get_appointment', 'reschedule_appointment'] -> ['get_appointment']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `borderline_medication_question` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - refusal rate 0% -> 100%; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `classify_review_sentiment` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `extract_invoice_fields` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `format_markdown_table` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `rag_answer_with_citation` on `gpt-4.1-mini` | changed_minor (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions |
| `rewrite_email_polite` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `sql_from_question` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `summarize_standup_notes` on `gpt-4.1-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
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
| `triage_ticket_json` on `gpt-4o-mini` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_missing_param_ask` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: [] -> ['send_statement']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `agent_reschedule_two_step` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['get_appointment', 'reschedule_appointment'] -> ['get_appointment']; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `borderline_medication_question` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - refusal rate 0% -> 100%; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `classify_review_sentiment` on `openai/gpt-oss-20b` | regression (conf 0.976) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `extract_invoice_fields` on `openai/gpt-oss-20b` | regression (conf 0.976) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `format_markdown_table` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `rag_answer_with_citation` on `openai/gpt-oss-20b` | changed_minor (conf 0.996) | **detected** - output format drift: violates the scenario's text assertions |
| `rewrite_email_polite` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `sql_from_question` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `summarize_standup_notes` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `support_order_status` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - tool-call behavior changed: ['lookup_order'] -> []; semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |
| `triage_ticket_json` on `openai/gpt-oss-20b` | regression (conf 0.996) | **detected** - semantic drift: candidate answers diverge in meaning from baseline (equivalence 0%) |

```
  Detection: 45/46 injected perturbations caught
  95% lower bound on the true rate: 90.1% (one-sided Clopper-Pearson, n=46)
  That rate is over perturbations APPLIED, not over behaviour changes; and the
  interval treats them as exchangeable trials, which by construction they are
  not - each targets a different signal.
  Unmeasured (excluded): 0
  NOTE: 1 perturbation(s) MISSED - counted against detection, never
  excluded. A miss means EITHER the engine failed to see a real change OR the
  candidate ignored the injected instruction and its behaviour did not change.
  This arm cannot tell those apart, and one that could would let a dead engine
  post 0/0 - so it always counts the miss. Read the per-scenario explanation and
  repertoire above before treating one as an engine defect (ADR-0022).
```

### Cost

Replay tokens in/out 2,071,848/447,798 across 5 artifact(s); 16353 judge calls implied by the traces (judge tokens not metered).
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
| **Run of record 2026-09-07** (five surfaces above; artifacts in `reports/fp-runs-adr0040/2026-09-07/`) | 710 trials over 27 shapes, 9 of which scored | **39** | **0/39**, upper bound 7.4%; 0/710 of trials reached, upper bound 0.4% |
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

**Phase-0 DoD: detection demonstrated (45 of 46 replays, 22 distinct perturbations, lower bound
80.2% over distinct) but not characterised; the false-positive rate is not established — it is now
*bounded*: 0 of 39 scored trials, upper bound 7.4%, on the 2026-09-07 run of record.** A zero-event
run bounds a rate; it does not establish one. The bound covers 9 scenario shapes and two OpenAI
models under the shipped defaults, on the argument, semantic and refusal channels only (30 / 8 / 1) — and 30 of its 39
trials are on the argument gate, which is advisory and cannot fail a build; the previous claim
of *"a measured 0% false-positive rate"* stays withdrawn (2026-08-23) because that is not what any
run can show. Bounding it took a live run over `examples/fp-suite/` and
`examples/calibration/arg_*.json` — surfaces at temperature > 0, where false positives are actually
possible. Establishing a rate still needs what the limitations section names: ≥30 labelled pairs
including real migration traces, and tool-channel exposure this run did not produce. The
non-OpenAI judge that list also named is now **partly** discharged — `[M]` 24 of the 480 trials
have been re-scored by `openai/gpt-oss-120b` on Groq, with 100% verdict agreement and a
denominator that moved (see *Cross-judge agreement*); the remaining 456 are budget, not design.
Those sets exist; so, now, does the run.

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

The harness injects **22 perturbed instructions** (three on the held-out suite, seven on `arg_*`,
twelve on `fp-suite`), and that vocabulary is deliberate:
whether a perturbation produces a behaviour change is what this arm measures, never a premise
it may assert (ADR-0023). Each targets a different channel: `refund_request` (never issue refunds →
tool-trajectory + refusal), `decline_pii` (share the customer email → policy/format +
semantic), `classify_sentiment` (always "Positive" → assertion + semantic). The channel named
is the one the perturbation *targets*; whether the candidate's behaviour actually changed on it
is the measurement. A `regression` or `changed_minor` verdict scores a detection; **anything
else, `unchanged` included, scores a miss; the only exclusion is an abstention that reached
no verdict at all (ADR-0018)**. `[M]` On the 2026-09-07 run of
record, 1 of 46 perturbed replays returned `unchanged` and is scored a MISS (`decline_pii` on
`gpt-4o-mini`). Two further replays were missed under the previous engine and are caught under
this one - see the Results section.

**A miss is never a false alarm — it is either a false negative or a correct true negative, and
this arm cannot tell which.** Either way it fails in the safe direction for this product, and
**the way to raise 45/46 is more distinct perturbations, never a lower floor.**

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
OpenAI-compatible hosts (MP-143); as of 2026-09-07 **24 trials of the run of record have been
re-scored by one** (below), but the calibration set above has not been, and a judge that runs is
still not a judge that is calibrated. **Next:** expand to ≥30 labeled pairs incl. real
model-migration traces, and extend the cross-judge comparison beyond the 24 trials measured so
far — `[M]` 686 of the run of record's 710 FP-arm trials remain unjudged by a second judge, of
which the 456 in the S2a+S2b `fp-suite` arms are `[M]` **13,582** stored judge calls under the
current engine (3,097 under the previous one — ADR-0040 raised judge calls per trial about
**4.4×**, from 3,711 to 16,353 across the whole run). `[A]` At the
~535 tokens/call assumed in ADR-0037 (the harness does not meter judge tokens) that is ~36 days
of Groq's free tier (`[S] 2026-09-07` 200,000 tokens/day); the assumption falls if a metered run
shows a different figure. No money, only calendar time. Then rely on the gate in high-stakes CI.

## Channel exposure (2026-09-07) — the tool trajectory finally gets a denominator

`[M]` Across the run of record's **710** same-model-null trials, **0** had `tool_call_match < 1.0`
and **0** had `format_valid == False` — while its 46 deliberately-perturbed trials produced 10
and 7. The channels work; they had never been shown a null. So the 7.4% bound above was carried
entirely by the argument, semantic and refusal channels (30 / 8 / 1), and `MIN_TOOL_TVD` — the floor protecting the
signal a *migration* tool exists for — had a false-positive exposure of **exactly zero trials**.
`0/0` is not a low rate.

`[M]` The scenario written for precisely this, `examples/fp-suite/agent_missing_param_ask`,
produced **zero tool calls on both sides in its 1 scored FP-arm trial** (6 under the previous engine; of 41 that
reached a verdict; its perturbed recall arm did move the channel): its prompt says *"ask
them for it and do not call the tool"*, which is unambiguous, so nothing varied. An unambiguous
prompt cannot produce trajectory variance however often it is run.

[`examples/fp-suite-v2/`](../examples/fp-suite-v2/) is a new `score` set built so a *same* model
varies: four scenarios where calling the tool is a genuine judgement call, three with an optional
second tool (the trajectory varies in *length*, not outcome), four asserting on a literal the
model produces variably, and one **negative control** with a mandatory tool call and a fully
specified output line.

The design, the twelve scenarios, the negative control and a falsifier for each were fixed in
[`examples/fp-suite-v2/README.md`](../examples/fp-suite-v2/README.md) `[M]` before any run of
this corpus, free or paid. **Read the numbers below as sequentially extended, not as
pre-registered**, for two reasons stated here rather than left to be inferred: ADR-0038 was
written after a free 12-trial Groq pilot
([`pilot-groq-gpt-oss-20b.jsonl`](../reports/channel-exposure/2026-09-07/), `openai/gpt-oss-20b`,
judge off) and quotes it, so its numeric prediction is calibrated on that pilot rather than blind
to it; and `[M]` it registered **240 trials on two surfaces**, while the run below is **480 on
four** — the second pair was started after the first pair had completed. `[M]` At the registered
N the tool bound is **1/12, ub 33.9%**.

`[M]` **480 same-model trials** on four surfaces: `gpt-4o-mini` vs itself judged by
`gpt-4.1-mini`, and `gpt-4.1-mini` vs itself judged by `gpt-4o-mini`, each replicated once
(`runs: 5 × repeats: 10`, `ALPHA 0.05`, `MIN_TOOL_TVD 0.5`, Modelpin `202274b`). No model graded
its own output, but **both judges are OpenAI models**, exactly as in the bound above.

`[M]` 1,755,400 in / 331,997 out replay tokens. `[S] 2026-09-07` at OpenAI list prices
(`gpt-4o-mini` $0.15/$0.60, `gpt-4.1-mini` $0.40/$1.60 per 1M in/out) that is `[A]` ≈ **USD 0.83**
— no invoice is recorded, and the harness does not meter judge tokens, so the 3,610 judge calls
are excluded and the true figure is higher.

Artifacts: [`reports/channel-exposure/2026-09-07/`](../reports/channel-exposure/2026-09-07/).
Reproduce, offline and without a key:

```
python scripts/channel_exposure.py reports/channel-exposure/2026-09-07/v2*.jsonl
```

| channel | exposed (the channel moved) | of which **scored** | false alarms | 95% upper bound |
|---|---|---|---|---|
| **tool-call trajectory** | **37** (`tool_call_match < 1.0`) | **26** | **1** | **17.0%** (1/26 = 3.8%) |
| **format / assertion** | **0**, of 160 in scope | 0 | — | **none — 0/0 is not a rate** |
| all channels pooled | — | 101, of 480 reached | 3 | 7.5% |

The bound is over **scored** trials, never over exposed ones: 11 of the 37 moved the trajectory
by too little to reach any ALPHA, and padding a denominator with trials that could not have
produced a false positive is precisely what ADR-0022 exists to prevent.

- `[M]` **The negative control held**: 40 trials, `tool_call_match = 1.0` and `format_valid = True`
  on every one. The variance is the design's, not the harness's.
- `[M]` **The tool channel's false alarm is real and is the shape a user would meet**: same model,
  same prompt, `['update_order_status', 'notify_customer'] → ['update_order_status']` — the model
  simply chose to send the courtesy email in one sample and not the other — reported as
  `regression` at **confidence 0.95**. This is the failure mode `MIN_TOOL_TVD` exists to prevent,
  and on a corpus where the channel is live it happens.
- `[M]` **The assertion channel returned zero exposure even on a corpus built for it.** Its four
  scenarios pinned at a violation rate of 0/5 or 5/5 on *both* sides. That was a stated limit, not
  a bound. **It has since been lifted — see *The assertion channel* below**, which measures it on
  a third corpus (`examples/fp-suite-v3`) built against a diagnosis of why this one failed.
- `[M]` **Eight of the eleven non-anchor scenarios produced no exposure on their target channel**,
  each tripping the falsifier its own file pre-registered: four of the seven tool-live scenarios
  (`calc_tool_or_mental_math`, `grammar_tool_or_direct_fix`, `verify_or_trust_pasted_status`,
  `optional_availability_before_booking`) took the same branch in 5/5 runs on both sides in all 40
  of their trials, and all four assertion scenarios pinned — three of them violating in **5/5 runs
  on both sides**, the `[2]`, `EUR` and unfenced-query variants never being produced at all.
  Building a corpus that varies is harder than the design predicted; the bound rests on what did.
- `[M]` **The other two false alarms were semantic, and they are not the same kind of error.** In
  one (`plaintext_answer_bold_optional`) the model genuinely contradicted itself between samples —
  a debit note "request a price reduction" in one, "a price increase" in the other — so the judge
  was arguably right and the *model*, not the engine, was inconsistent. In the other
  (`sql_answer_fence_unspecified`) two correct SQL queries differing only in a column alias and a
  redundant `IS NOT NULL` were scored non-equivalent. Both are false alarms by this arm's
  definition — same model, no change, an alarm — and which of them is the judge's fault is exactly
  the question *Cross-judge agreement* below exists to start pricing.

**What this does not say.** This corpus is exposure-maximising by construction, so its rate is a
near-worst-case *conditional* figure — "given an app where the tool trajectory is genuinely
live" — and it is **never pooled with the 7.4% bound above**; `scripts/channel_exposure.py`
refuses to read an artifact from the run-of-record directory, and the two live in separate trees.
`[M]` The tool exposure is concentrated, and the scored subset carrying the bound more so: of the
37 exposed trials **30 come from one scenario** (`optional_notify_after_status_update`) and three
supply all of them — but of the **26 scored** trials **24 come from that one scenario, and only
two scenarios contribute any scored trial at all**: `optional_part_stock_second_lookup` moved the
trajectory 5 times and reached scoring 0 times. So **17.0% is a bound over one dominant shape**,
not over tool use in general. Two models from one vendor are two models. The pooled row's 480 is
every trial that reached a verdict, including the control's 40, which the per-channel rows
exclude.

## The assertion channel (2026-09-08) — measured for the first time, in either direction

`[M]` **1 false alarm in 30 scored assertion-exposed trials = 3.3%, one-sided 95% upper bound
14.9%.** Over **7 distinct scenarios** that is `1/7`, upper bound **52.1%** — and per ADR-0042 D3
the shape figure is the one that constrains, not the trial figure. Both anchors held at zero
exposure, so the run is readable. Corpus `examples/fp-suite-v3` (role `score`), artifacts under
[`reports/channel-exposure/2026-09-08/`](../reports/channel-exposure/2026-09-08/).

Until this run the format/assertion channel had **never been measured for false positives in
either direction.** `[M]` Across the 710-trial run of record: **0** trials with
`format_valid == False`. Across `examples/fp-suite-v2`, a corpus built specifically to move it:
**0 exposed of 160 in scope.** The channel was not dead — the recall arm moved it seven times —
so what was missing was a corpus, not an engine.

**The premise the third attempt was filed on turned out to be false, and that is why it worked.**
`[M]` Recomputed offline from v2's own stored traces, 400 recorded runs per scenario: the models
were *not* deterministic. `citation_style_underspecified` never emitted the asserted `[2]`
(**0/400**) while emitting `Passage 2` **400/400**, splitting `(Passage 2)` 42.5% against
`Passage 2:` 32.0%. Replaying the identical stored runs against a different asserted literal —
changing nothing but the string — turns *"the sides never differ"* into *"the sides differ most
of the time"* (12–16 of 20 trials on three of the four scenarios). v2 did not fail because the
models are deterministic. It failed because the author had to **guess** which string the model
would sometimes emit, and guessed one with probability exactly zero.

So v3 removes the guess: 6 of its 8 live scenarios assert only on literals copied **verbatim out
of their own prompt**, each a 3–5 element conjunction, with a brevity constraint as the source of
variance. A conjunction is dead only if *every* element is pinned, which is a far smaller target
than one literal being pinned.

| | trials in scope | assertion-exposed | scored | flagged | bound |
|---|---|---|---|---|---|
| run of record (710-trial null) | 710 | 0 | 0 | 0 | none |
| `fp-suite-v2` (built to move it) | 160 | 0 | 0 | 0 | none |
| **`fp-suite-v3` (this run)** | **312** | **56** | **30** | **1** | **3.3%, ub 14.9%** |

**Read these five things before quoting 14.9%.**

1. `[M]` **Over distinct shapes it is `1/7` and the bound is 52.1%.** Two scenarios supply 22 of
   the 30 scored trials (`dispatch_line_keeps_the_ids` 15, `regex_anchors_unspecified` 7).
   Repeats buy resolution on these shapes, never coverage of an eighth.
2. `[M]` **The one flagged trial is published as a false positive whatever anyone thinks of it**
   (ADR-0036 rule 4): `standup_digest_keeps_the_ids#6`, `changed_minor @ 0.996`, channel
   `assertion`. It is advisory by ADR-0032 and could not have failed a build — which is a fact
   about severity (ADR-0042), not a reason to discount it.
3. `[M]` **The run is 312 trials, not the 400 pre-registered.** Three of four surfaces completed
   clean (100 trials each, 0 provider errors). The fourth walled on the provider's rate/quota
   limit and contributed 12 of its 100; a `--resume` at one worker re-attempted and failed again.
   Provider errors never enter a rate (ADR-0036), so the bound is unaffected in kind — but the
   denominator is a quarter smaller than bought, and that is a shortfall, not a design choice.
4. `[M]` **Both models are OpenAI and both judges are OpenAI.** No cross-vendor arm ran on this
   corpus at all. The free Groq pilot that gated the spend is not a measurement of these models:
   on v2's prompts `openai/gpt-oss-20b` cites as `【Passage 2】` where both OpenAI models use
   `(Passage 2)`. A rate measured on the pilot model transfers to neither.
5. `[M]` **The tool channel returned zero exposure here, by design.** No file in this corpus
   declares `tools` — one seam per scenario — so nothing in this section says anything about the
   tool trajectory. That channel's own number is in the section above, and it is worse.

**The outcomes were pre-registered before the run** (`examples/fp-suite-v3/README.md`,
§"What failure looks like"): SUCCESS at ≥ 20 scored trials with both anchors quiet, PARTIAL at
1–19, FAILURE at 0 — with FAILURE committing the page to a permanent stated limit and *not*
authorising a fourth attempt. 30 scored clears SUCCESS. The prediction that came with it was
"expect 0 or 1 false alarms, and do not read 0 as a failure"; the observed 1 is inside that.

## Cross-judge agreement (2026-09-07) — one replay, two judges

`[M]` Until 2026-09-07 the harness could not use a non-OpenAI judge at all: it called
`build_judge(args.judge)` with no host, and `openai/gpt-oss-120b` — a Groq-served model whose
vendor prefix names its origin, not its host — died at preflight. It now takes
`--judge-provider`, and `--rejudge` re-scores an artifact's **stored traces** under a second
judge, so the same recorded model behaviour is scored twice and **no replay is bought again**.
That matters for attribution as much as for cost: a fresh replay would confound judge
disagreement with model nondeterminism.

`[M]` **24 paired trials** — the 12 `examples/fp-suite` scenarios, rounds 1–2 of the
`gpt-4o-mini` surface, FP arm — re-scored by **`openai/gpt-oss-120b` on Groq**. 163 judge calls,
9.0 min wall, zero replay calls; `[S]` USD 0.00 on Groq's free tier (no invoice is recorded).
Artifacts: [`reports/judge-agreement/2026-09-07/`](../reports/judge-agreement/2026-09-07/).

`[M]` every figure in this table and the three bullets below is regenerated from those
artifacts by `tests/test_cross_judge_agreement.py`:

| | `gpt-4.1-mini` @ openai (unrecorded; inferred) | `openai/gpt-oss-120b` @ groq |
|---|---|---|
| false alarms / scored | **0 / 5** (ub 45.1%) | **0 / 1** (ub 95.0%) |
| false alarms / reached | 0 / 24 (ub 11.7%) | 0 / 24 (ub 11.7%) |
| could not have fired | 19 | 23 |

- **Verdict agreement: 24 / 24 = 100%**; one-sided 95% upper bound on the judge disagreement
  rate **11.7%**.
- **Identical semantic score: 19 / 24 = 79.2%.** The judges differ on the score in five trials
  without moving a verdict.
- **They do not agree on what was measured: 5 scored trials vs 1, over identical traces.** A
  judge that finds more outputs equivalent pushes the semantic channel to `p = 1.00`, which the
  ADR-0022 exclusion then removes from the denominator. **The choice of judge moves the bound's
  denominator, not only its numerator** — the finding a verdict-agreement rate alone would hide.

**What this does not say.** These 24 trials are rounds 1–2 of the S2b surface alone
(`gpt-4o-mini` vs itself, FP arm) — 10% of that surface's 240 trials, and none of the 240 on the
S2a `gpt-4.1-mini` surface. The prefix rule was fixed before the rejudge run but **after** the
source artifact existed, so it is pre-specified, not pre-registered; rounds are exchangeable
replicates, which is the argument that a prefix is unbiased, and `[M]` this one is
scored-trial-rich against its parent arm (5/24 = 20.8% vs 28/240 = 11.7%) **under the engine both
were scored on**. `[M]` These 24 trials were scored under the pre-ADR-0040 engine and have not been
re-scored, so their denominators are not comparable with the Results tables above, where S2b scores
6 of 240 rather than 28. One non-OpenAI judge
is one judge; and a rejudged artifact is the same trials scored twice, **never a second sample**
— `fp_aggregate.py` refuses to pool it, and these numbers are not folded into the headline
bound. Groq substitutes `1e-8` for the requested `temperature: 0` (`[S] 2026-09-07`
console.groq.com/docs/openai). ADR-0037.

## Honest framing (trust guardrail)

These are **measurements under the stated settings**, not absolute claims about model
quality. The structural floors remain deliberately conservative; the semantic floor is now
calibrated (above) but on a modest set — and **"calibrated" here means confirmed FP-safe and
detection-preserving on that set, not *fitted*:** `[M]` the set cannot discriminate the value,
the semantic sweep being flat from 0.1 to 0.9, so 0.5 is a conservative choice rather than a
fitted one. `[M]` Re-scored under ADR-0022 that evidence is **0/1, 95% upper bound 95.0%**. So
treat the `regression` promotion as a **first** calibration, not a well-evidenced or a final
one.
