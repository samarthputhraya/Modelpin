# The false-positive suite, v3 — the assertion channel, attempt #2 (`examples/fp-suite-v3/`)

> **Role: SCORE. Never fit a threshold here; never edit a file here after a rate has been
> measured on it.** Declared in [`../roles.json`](../roles.json) and pinned in
> `tests/test_suite_roles.py::EXPECTED_MEMBERS`; until both are true the examples tree fails
> `test_every_scenario_file_declares_a_role`, which is the intended behaviour.
>
> This is a NEW set. `examples/fp-suite/` and `examples/fp-suite-v2/` have both been scored and
> ADR-0025 forbids editing either; nothing here touches them. **Its rate is never pooled with
> the `fp-suite` bound** (ADR-0038 D2), and its artifacts belong under
> `reports/channel-exposure/`, never `reports/fp-runs/`.

## Why this set exists

The format/assertion channel has never been measured for false positives in either direction, so
we cannot tell a customer what it does.

`[M] 2026-09-07`, `reports/fp-runs/2026-09-07/*.jsonl` and
`reports/channel-exposure/2026-09-07/`:

| corpus | trials in scope | assertion-exposed | bound |
|---|---|---|---|
| `fp-suite` (710-trial null) | 710 | **0** (`format_valid == False`) | none |
| `fp-suite-v2` (built to move it) | **160** | **0** | none — no denominator |
| recall arm (perturbed candidate) | 46 | 7 | n/a |

The channel is not dead: the recall arm moved it seven times. **What is missing is a corpus, not
an engine.** MP-207's falsifier fired for this channel and the failure was published honestly.
This is attempt #2, and §"What failure looks like" below is written before the run.

---

## The diagnosis: why v2's four assertion scenarios failed

`[M]` Recomputed offline from the stored traces in
`reports/channel-exposure/2026-09-07/v2{a,b,c,d}-*.jsonl` — 400 recorded runs per scenario (20
trials × 2 sides × 5 runs, across gpt-4o-mini and gpt-4.1-mini). No API calls.

| v2 scenario | asserted literal | present in | pinned at | failure class |
|---|---|---|---|---|
| `plaintext_answer_bold_optional` | `must_not_contain: **` | **0/400** | 0 violations, both sides, all 40 sides | prompt suppressed the variance |
| `citation_style_underspecified` | `must_contain: [2]` | **0/400** | 5/5 violations, both sides, all 40 sides | literal outside the support |
| `sql_answer_fence_unspecified` | `` must_not_contain: ``` `` | **400/400** | 5/5 violations, both sides, all 40 sides | prior too strong to be a choice |
| `total_currency_code_or_symbol` | `must_contain: EUR` | **0/400** | 5/5 violations, both sides, all 40 sides | literal outside the support |

**These are three different failures wearing one label, and only one of them is the failure the
backlog assumed.**

### 1. The models were never deterministic. The instrument was pointed away from the variance.

`[M]` What the models actually produced, same 400 runs:

| v2 scenario | asserted form | the forms actually used |
|---|---|---|
| `citation_style_underspecified` | `[2]` — **0/400**; *any* `[` — **0/400** | `Passage 2` — **400/400**. gpt-4o-mini splits `(Passage 2)` **42.5%** / `Passage 2:` **32.0%**; gpt-4.1-mini `(Passage 2)` **99.5%** |
| `total_currency_code_or_symbol` | `EUR` — **0/400** | gpt-4o-mini splits `€` **47.0%** / `euros` **53.0%**; gpt-4.1-mini `euros` **98.5%**, and its thousands separator splits `1,997.75` **28.5%** / `1997.75` **71.5%** |

The decisive test. `[M]` Replaying the **identical stored 5-run sides** against a different
asserted literal — changing nothing but the string — turns *"the sides never differ"* into
*"the sides differ most of the time"*:

| scenario | literal | trials where base *k* ≠ cand *k* |
|---|---|---|
| `citation_style_underspecified` (gpt-4o-mini) | `[2]` **as shipped** | **0/20** |
| | `must_contain "(Passage 2)"` | **12/20** |
| | `must_not_contain "Passage 2:"` | **16/20** |
| `total_currency_code_or_symbol` (gpt-4o-mini) | `EUR` **as shipped** | **0/20** |
| | `must_contain "€"` | **13/20** |
| | `must_contain "euros"` | **13/20** |
| `total_currency_code_or_symbol` (gpt-4.1-mini) | `must_contain "1,997.75"` | **13/20** |
| `sql_answer_fence_unspecified` (gpt-4.1-mini) | `` ``` `` **as shipped** | **0/20** |
| | `must_contain " AS "` | **16/20** |
| | `must_not_contain "IS NOT NULL"` | **15/20** |
| `plaintext_answer_bold_optional` (gpt-4.1-mini) | `**` **as shipped** | **0/20** |
| | `must_contain "seller"` | **13/20** |

**`[M]` This retracts the premise MP-222 was filed on.** The backlog row states *"gpt-4o-mini
and gpt-4.1-mini were deterministic on all four attempts even at `temperature: 1.0`."* Its own
artifacts refute it: on three of the four scenarios the same model, at the same temperature,
varied on an adjacent literal in 12–16 of 20 trials. **The corpus did not fail because the
models are deterministic. It failed because the author had to GUESS which string the model
would sometimes emit, and guessed a string the model never emits at all.** That is a
solvable design problem, and it is the one this set solves.

### 2. `plaintext_answer_bold_optional` is a different failure: the prompt suppressed it.

`[M]` Across all 400 runs the output contained **zero** `**`, **zero** `*` of any kind, and
**zero** newlines. The instruction *"Answer the user's question in two or three short
sentences"* is an implicit anti-Markdown instruction: a three-sentence single paragraph has
nowhere to put a bold span. No choice of literal rescues this one — there was no Markdown to
assert on. Sample, verbatim:

> A debit note is issued by a buyer to notify the seller of a deduction in the amount owed,
> often due to returns or adjustments. In contrast, a credit note is issued by a seller to the
> buyer, reducing the amount they owe, typically for refunds or billing errors.

**Lesson: a brevity instruction and a formatting assertion fight each other.** v3 uses brevity
as the *source* of variance (what gets dropped), never as the thing being asserted on.

### 3. `sql_answer_fence_unspecified` is the one genuine "prior too strong" case.

`[M]` `` ``` `` appeared in **400/400** runs, and `` ```sql `` specifically in **400/400**, on
*both* OpenAI models — and `[M]` **5/5** on Groq's `openai/gpt-oss-20b` in the pilot. Three
model families, one behaviour. Fencing generated SQL is not a free choice for a chat-tuned
model; it is a fixed habit. Not every underspecified thing is underspecified *to the model*.

### 4. The pilot had the answer and the readout hid it.

`[M]` The MP-207 Groq pilot produced `**` in **2 of 5** baseline runs of
`plaintext_answer_bold_optional` — the literal was **alive** on the pilot model. The pilot log
records that scenario as `unchanged conf=1.00 <-- p=1.00 everywhere; could not have fired`,
because the candidate side happened to land at 0 and the engine's one-sided test returns
`p = 1.0` for a *decrease*. The pilot read the **engine's verdict** where it needed to read the
**per-run literal rate**, and so reported "dead" about a live literal.

**The v3 pilot below reads violation counts straight out of the traces and never consults a
verdict.**

---

## What changed in the design, and why (b) is structurally safer than (a)

MP-222 offers two strategies: **(a)** a literal with two equally idiomatic forms the prompt does
not choose between, and **(b)** a longer output where a required element is easy to omit under
length pressure. This set is **6 × (b), 2 × (a), 2 anchors** — and the reason is exactly the
diagnosis above.

Strategy (a) requires the author to **bet on the shape of a distribution they cannot see**. Three
of v2's four bets lost, and lost the same way: the asserted string had probability **exactly
zero**. There is no run count that recovers from that.

Strategy (b) removes the bet. **The asserted literal is copied verbatim out of the scenario's own
prompt**, so it is *guaranteed* to be inside the model's support — the model has already been
shown the string. The only open question is the **omission rate under brevity pressure**, and
omission cannot be *outside* the support the way `EUR` was. `[M]` 5 of the 8 live scenarios here
assert only on literals verbatim present in their own prompt (mechanically checked; see the
falsifier table).

**Second change: every live assertion is a CONJUNCTION.** `structural.py::violates_text_assertions`
requires *all* of `must_contain`, so with *m* elements each independently present with
probability *p*, the per-run violation rate is `q = 1 − pᵐ`. A conjunction is dead only if
**every** element is pinned at exactly 1.0 — a far smaller target than one literal being pinned:

| per-element *p* | *m* = 1 | *m* = 3 | *m* = 5 |
|---|---|---|---|
| 0.99 → *q* | 0.010 | 0.030 | 0.049 |
| 0.95 → *q* | 0.050 | 0.143 | 0.226 |
| 0.90 → *q* | 0.100 | 0.271 | 0.410 |

**Third change: vary the FORM, pin the CONTENT.** Every scenario's *facts* are fully determined
by its prompt, so the semantic judge should score equivalence and stay quiet while the rendering
moves. `[M]` Two of v2's three false alarms were **semantic**, not assertion
(`plaintext_answer_bold_optional#2`, `sql_answer_fence_unspecified#6`), both on free-content
prose tasks. This set deliberately gives the model nothing to be creative *about*.

**Two strategy-(a) survivors, chosen against the diagnosis.** `regex_anchors_unspecified` (`^`
`$`) and `json_null_or_omitted_key` (`null`) are kept because — unlike `[2]` and `EUR` — both
literals are *universally attested* in their idiom. A regex either anchors or does not; a JSON
field for a missing value is either `null` or absent. Neither can be a string the model simply
never writes. They are the honest test of whether (a) can work at all when the literal is
chosen from a two-element support instead of guessed.

### Governance note, stated plainly

`examples/fp-suite-v2/` is role `score`, and this design was informed by reading its stored
traces. **Two things were taken from them and nothing else**: (i) the general fact that these
models vary richly on rendering, and (ii) the general fact that single-literal assertions are
fragile. **No v2 prompt, domain, or literal is reused here** — no SQL, no citation, no currency,
no Markdown-bold scenario appears in v3, and content-disjointness is machine-checked against all
85 other declared scenario files. The measured `q` values above are *not* carried forward as
predictions; §"Pre-registered prediction" derives its numbers from the engine's arithmetic, not
from v2's observations.

---

## Pre-registered prediction — the arithmetic, before the run

`[M]` The assertion gate is `diff/__init__.py`:

```python
fmt_delta = _mean(cand_v) - _mean(base_v)
fmt_p     = permutation_pvalue_mean(base_v, cand_v)
fmt_drift = fmt_p <= ALPHA and fmt_delta > 0
```

**It is the only verdict signal with no effect-size floor** (ADR-0032 finding 1) and it is
**one-sided**. `[M]` Enumerated over the engine's own `permutation_pvalue_mean` at `runs: 5`,
the gate fires on exactly **three of the 36 cells**:

| base *k* → cand *k* | 0 → 4 | 0 → 5 | 1 → 5 |
|---|---|---|---|
| `fmt_p` | 0.0238 | 0.0040 | 0.0238 |

**The candidate side must violate in at least 4 of 5 runs against a baseline violating at most
one.** So *exposure* and *firing* are very different events, and MP-222's acceptance is about
exposure:

| per-run violation rate *q* | 0.005 | 0.01 | 0.02 | 0.05 | 0.1 | 0.2 | 0.3 | 0.5 |
|---|---|---|---|---|---|---|---|---|
| P(sides differ → **exposed**) | 0.048 | 0.093 | 0.174 | 0.359 | 0.538 | 0.680 | 0.728 | **0.754** |
| P(right direction → **scored**) | 0.024 | 0.047 | 0.087 | 0.180 | 0.269 | 0.340 | 0.364 | **0.377** |
| P(gate **fires**) | 0.0000 | 0.0000 | 0.0000 | 0.00002 | 0.0003 | 0.0023 | 0.0061 | **0.0107** |

(The "scored" row is a lower bound: an exposed trial in the *right* direction has `fmt_p < 1.0`
and is therefore always `scored` under ADR-0022. A wrong-direction one needs another channel to
be live.)

Three consequences, written down before anyone sees a number:

1. **The design target is `q ∉ {0, 1}`, not `q ≈ 0.5`.** Even `q = 0.02` — two violating runs in
   a hundred — yields an exposed trial 17% of the time. **Only exactly 0 and exactly 1 are
   fatal.** This is why the conjunction is the whole design: it converts a small per-element
   omission rate into a usable *q*, and it is pinned only if every element is pinned.
2. **Acceptance arithmetic.** 8 live scenarios × 4 surfaces × `--repeats 10` = **320 trials in
   scope**. `[M]` Solving `320 × P(scored | q) ≥ 20` gives a required corpus-average of
   **`q ≥ 0.0137`** — a mean per-run assertion violation rate of **1.4%**. With margin: if
   **5 of the 8 die outright**, the surviving 3 need `q ≥ 0.045`; if only 2 survive, `q ≥ 0.086`.
3. **Expect 0 or 1 false alarms, and do not read 0 as a failure.** `[M]` Even at the pathological
   worst case — all eight scenarios at `q = 0.5` — the expectation over 320 trials is **3.4**.
   At the realistic `q ≈ 0.05–0.2` band it is **0.006–0.75**. A run returning **≥ 20 scored and
   0 flagged** is the *expected* result and is a publishable bound
   (`upper_bound_95(0, 20) = 13.9%`), not a null result.

---

## What is here

Ten scenarios. Every one at **`temperature: 1.0`** (the API default), **no `seed`**, and
**no `tools` on any file** — the assertion channel is the only channel this set is built to
move, so per "one seam per scenario" nothing here can produce a tool-trajectory flag at all.

### (b) Omission under brevity pressure — 6 scenarios

The prompt supplies a rich context and a **brevity constraint**, and the assertion keys on
elements a **downstream parser** needs that the prompt **never asks for**. Both branches are
correct: a two-sentence handover that drops a muted pager policy is a good handover; it just
breaks the bot that links it. `[M]` Every literal is verbatim in its own prompt, so
*"the model never produced the literal"* — the failure that killed 3 of v2's 4 — is
structurally impossible here.

| Scenario | *m* | Brevity dial | Falsifier (written before the run) |
|---|---|---|---|
| `handover_note_keeps_the_ids` | 5 | "two or three sentences" (loosest) | All five present in 5/5 runs on both sides in every trial |
| `standup_digest_keeps_the_ids` | 5 | "a single sentence", across 3 speakers | As above; or all-violating 5/5 both sides |
| `dispatch_line_keeps_the_ids` | 4 | "one line… truncates at roughly 90 characters" (tightest) | Pinned at 5/5 violations on both sides in every trial — the likeliest of the six to collapse *upward* |
| `action_items_keep_the_owners` | 4 | "under about forty words", 4 named owners | The four names present in 5/5 on both sides in every trial |
| `metrics_line_keeps_the_numbers` | 4 | "one line, keep it short", 4 numerals | All four rendered byte-identically in 5/5 on both sides — i.e. the model never rounds, never drops a thousands separator, never appends a unit |
| `slug_conjunction_stopwords` | 3 | no brevity dial; `&` → `and` or dropped | `-and-` present in 5/5 (or absent in 5/5) on both sides in every trial |

`handover_note` → `standup_digest` → `dispatch_line` is a deliberate **tightness ladder** on one
mechanism. Pre-registered ordering: **q(dispatch) > q(standup) > q(handover)**. A violation of
that ordering is itself informative and must be reported, not smoothed over.

### (a) A two-form literal, chosen from a two-element support — 2 scenarios

| Scenario | Literal | Both branches correct because | Falsifier |
|---|---|---|---|
| `regex_anchors_unspecified` | `must_contain: ^`, `$` | `^[A-Z]{3}-\d{6}$` and `[A-Z]{3}-\d{6}` both describe the pattern; whether you anchor depends on full-match vs search, which the prompt never says | Anchored in 5/5 (or 0/5) on both sides in every trial. Note the two literals are near-perfectly correlated, so this is effectively one binary choice, not a conjunction |
| `json_null_or_omitted_key` | `must_not_contain: null` | `"phone": null` and omitting the key are both idiomatic JSON for an absent value; the prompt names the field but never says which. A CRM that rejects nulls is the constraint the prompt forgot | `null` emitted in 5/5 (or 0/5) on both sides in every trial |

### Quiet anchors — 2 scenarios

`[M]` Every asserted literal in both is copyable verbatim from its own prompt, and both outputs
are fully specified — the exact inverse of the eight above.

| Scenario | Role | Falsifier |
|---|---|---|
| `anchor_echo_the_reference_line` | One line, `ACK <reference> <status>`, all three literals given | It moves. Assertion-exposed on any trial |
| `anchor_closed_set_label` | One label from a closed set spelled out in the prompt, on an unambiguous billing message | It moves |

**If either anchor moves, no other number in this set is readable until that is explained** — the
variance would be in the harness, the adapter, or basic instruction-following rather than in the
deliberate optionality of the other eight. Two anchors rather than v2's one: v2's single anchor
covered the tool channel too, and its own README named "a second, tool-free anchor on the
assertion channel alone" as the follow-up. This is it, twice.

> **Reporting note.** `scripts/channel_exposure.py` hard-codes
> `ANCHOR = "anchor_mandatory_lookup_fixed_format"`, so it will not recognise these two by name
> and will count them in `in_scope`. **This does not distort the bound**: an anchor that holds
> contributes 0 to `exposed`, 0 to `scored` and 0 to `flagged`, and the published rate is
> `flagged / scored`. Read the anchor rows off the per-scenario table the script already prints
> (`EXPOSURE BY SCENARIO`) and confirm both read `0 / 0 / n`. Generalising `ANCHOR` to a set is a
> separate backlog row; **do not edit that script as part of this run.**

## Design rules

- **One seam per scenario.** No file declares `tools`. No file mixes a `must_contain` conjunction
  with an unrelated `must_not_contain`.
- **No key outside the consumed allowlist** (`messages` plus the adapters' generation keys). A
  key nothing reads cannot reach a provider call but can give a copied scenario a false content
  identity — `tests/test_suite_roles.py`.
- **Content-disjoint by id AND by content hash** from all 85 other declared scenario files
  (`[M]` verified with the test's own `_content_key`).
- **The content is pinned; only the rendering is free.** This keeps the semantic judge quiet so
  an assertion flag is attributable.
- **`[M]` Byte-exact and case-sensitive** — `structural.py` is `s in out`, no normalisation
  (ADR-0032 finding 3). Every literal here was chosen knowing that; the case and separator
  choices *are* the instrument.

## Known traps this set inherits

1. **`REFUSAL_MARKERS` is a 9-marker English keyword list** substring-matched over the whole
   output. The tightest rung (`dispatch_line_keeps_the_ids`) is the one place a model might
   write *"I can't fit all of that in 90 characters"* and be scored `refused`. A refusal-driven
   flag from this set is **soft evidence at best**: quote the matched marker out of the stored
   trace before anyone calls it drift.
2. **Accidental substring satisfaction.** `must_contain "412"` is satisfied by `412ms`, and
   `"Colm"` by any word containing it. This biases toward *fewer* violations, i.e. toward
   under-exposure — the conservative direction for a false-positive measurement, but it means a
   low exposure number must not be read as "the model always complied".
3. **`json_null_or_omitted_key` has a third branch**, `"phone": ""`, which also passes. That is
   intended (the CRM accepts it), but it lowers `q` relative to a clean binary.

## What failure looks like — pre-registered

**This set is not designed toward a foregone success.** MP-222's acceptance explicitly allows a
second documented failure. The three outcomes, and what gets published for each:

| Outcome | Criterion | What we publish |
|---|---|---|
| **SUCCESS** | ≥ 20 scored assertion-exposed trials, both anchors quiet | A per-channel false-positive bound for the assertion channel with its own denominator, `k/n` flagged with a one-sided 95% upper bound. **0 flagged of 20 scored → 13.9%**, and that is a real result, not a null |
| **PARTIAL** | 1–19 scored trials, both anchors quiet | The exposure count and the flagged count with **no rate**. ADR-0022 forbids a bound over a denominator that small; the honest sentence is *"the channel has now been exposed n times under a same-model null and flagged k, which is too few to bound"* |
| **FAILURE** | **0 scored trials**, i.e. exposure pinned again | `docs/fp-measurement.md` gains a **permanent** stated limit: *the format/assertion channel has resisted two independently designed null corpora and cannot currently be null-tested; the published bound says nothing about it.* Attempt #3 is **not** authorised by this row |

The **discriminating** evidence between PARTIAL and FAILURE is the per-scenario `q̂` — the
observed per-run violation rate over all 40 sides — which `ops/fp-run/v3_pilot_readout.py`
already computes and which must be reported for all ten scenarios whatever the outcome. A
scenario at `q̂ = 0` and a scenario at `q̂ = 1` are both dead, but they are *different* dead
and the docs must say which.

**One outcome that would invalidate the run entirely:** either anchor moving. That is not a
FAILURE result, it is an unreadable run.

## Before running

1. Declared in `../roles.json` (role `score`) and pinned in
   `tests/test_suite_roles.py::EXPECTED_MEMBERS`. `[M]` Both done; `pytest
   tests/test_suite_roles.py` is 13 passed.
2. **Run the free Groq pilot first.** MP-207's pilot is what caught the tool channel early, and
   §"the readout hid it" above is why this one reads literal counts rather than verdicts.
3. The recall arm is **empty for this set**: `scripts/fp_measurement.py` gates on
   `if scn.id in PERTURBATIONS`, and `[M]` the intersection with these ten ids is empty, so they
   run in the FP arm only.
4. **Never** write this set's artifacts under `reports/fp-runs/` — `scripts/channel_exposure.py`
   refuses, and `tests/test_fp_run_of_record.py` globs and pools that directory.

### The free Groq pilot

`[M]` The Groq key in `.env.local` is the live one and the shell copy 401s; `ops/fp-run/run_live.py`
is the only thing that loads the file, so every command goes through it. `--no-judge` keeps the
pilot free of judge tokens — the assertion channel does not need a judge.

```
# P1  smoke, 10 trials = 100 runs (10 per scenario), ~10 min at 1 worker
.venv/Scripts/python.exe ops/fp-run/run_live.py \
  --provider groq --model openai/gpt-oss-20b \
  --scenarios-dir examples/fp-suite-v3 --role score \
  --runs 5 --repeats 1 --arm fp --no-judge --workers 1 \
  --out reports/channel-exposure/2026-09-08/v3-pilot-p1.jsonl

.venv/Scripts/python.exe ops/fp-run/v3_pilot_readout.py \
  reports/channel-exposure/2026-09-08/v3-pilot-p1.jsonl
```

```
# P2  falsification, 50 trials = 500 runs (50 per scenario), ~50 min. --resume reuses P1.
.venv/Scripts/python.exe ops/fp-run/run_live.py \
  --provider groq --model openai/gpt-oss-20b \
  --scenarios-dir examples/fp-suite-v3 --role score \
  --runs 5 --repeats 5 --arm fp --no-judge --workers 1 --resume \
  --out reports/channel-exposure/2026-09-08/v3-pilot-p2.jsonl

.venv/Scripts/python.exe ops/fp-run/v3_pilot_readout.py \
  reports/channel-exposure/2026-09-08/v3-pilot-p2.jsonl
```

**Pass/fail is per scenario and it is asymmetric — the pilot may KILL a scenario, never bless
one.** `[M]` The pilot model is not the paid model and does not track it on rendering. On the
same v2 prompts, `openai/gpt-oss-20b` cites as `【Passage 2】` (U+3010) where both OpenAI models
use `(Passage 2)` or `Passage 2:`; and a trailing *"This query…"* sentence after the generated
SQL appears in **67.5%** of gpt-4o-mini runs, **0%** of gpt-4.1-mini runs and **0%** of Groq
runs. A rate measured on the pilot model transfers to neither paid model. What DOES transfer is
a **structural pin**: `` ``` `` around generated SQL was 5/5 on Groq and 400/400 on both OpenAI
models. So:

| readout | meaning | action |
|---|---|---|
| **GREEN** (`0 < q̂ < 1`) | the literal demonstrably varies on this model | keep; likely but not certain to vary on the paid model |
| **AMBER** (pinned, `n < 218`) | not yet ruled out | keep, or extend with `--only` |
| **RED** (pinned, `n ≥ 218`) | `[M]` all-identical at n = 218 bounds the true rate at ≤ 0.0137, the acceptance threshold | the scenario cannot contribute |
| **ANCHOR-BROKEN** | an anchor moved | **stop**; the run is unreadable |

`[M]` **218 is the minimum number of runs that actually settles whether a literal varies** at
the rate this row needs: an all-identical result over *n* runs bounds `q ≤ 1 − 0.05^(1/n)`, and
`1 − 0.05^(1/n) ≤ 0.0137` first holds at `n = 218`. Ten runs bounds it only at `q ≤ 0.259` and
fifty at `q ≤ 0.058` — which is why P1 alone may not kill anything, and why AMBER is a real
verdict rather than a hedge.

**Gate to the paid run: ≥ 3 GREEN of the 8 live scenarios, and both anchors at `q̂ = 0.000`.**
`[M]` Three surviving scenarios × 4 surfaces × 10 repeats = 120 trials in scope, which clears
the 20-scored acceptance at a mean `q ≥ 0.045`. Fewer than 3 GREEN → extend the pilot on the
AMBER ids with `--only` before spending anything; 0 GREEN at n ≥ 218 → the pre-registered
FAILURE outcome above.

`[S]` Groq free tier for `openai/gpt-oss-20b`, from the response headers recorded in
`ops/fp-run/NOTES.md` on 2026-09-07: 8,000 TPM and 1,000 requests/day. P1 + P2 is 600 requests,
so both fit inside one day with room for one `--only` extension.

### The paid run, only after the pilot gate

Four surfaces, mirroring MP-207's shape, so the 320-trial denominator in the acceptance
arithmetic above is the one actually bought. Judge **on**, so the artifact is comparable with
the v2 run and the overall rate stays honest; the assertion bound is read from the per-channel
block, not from the headline.

```
# v3a / v3c : gpt-4o-mini vs itself, judged by gpt-4.1-mini   (v3c is the repeat)
# v3b / v3d : gpt-4.1-mini vs itself, judged by gpt-4o-mini   (v3d is the repeat)
.venv/Scripts/python.exe ops/fp-run/run_live.py \
  --provider openai --model gpt-4o-mini --judge gpt-4.1-mini \
  --scenarios-dir examples/fp-suite-v3 --role score \
  --runs 5 --repeats 10 --arm fp --workers 3 \
  --out reports/channel-exposure/2026-09-08/v3a-fp-suite-v3-gpt-4o-mini.jsonl

.venv/Scripts/python.exe scripts/channel_exposure.py \
  reports/channel-exposure/2026-09-08/v3{a,b,c,d}-*.jsonl \
  --json reports/channel-exposure/2026-09-08/channel-exposure-v3.json
```

Read, in this order: (1) both anchor rows in `EXPOSURE BY SCENARIO` are `0 / 0 / 40` — if not,
stop; (2) `FORMAT / ASSERTION` → `exposed` and `scored`; (3) only then the flagged count. A
semantic flag on this set is a **separate, already-known finding** and must not be reported as
an assertion result — `[M]` two of v2's three false alarms were semantic, on assertion
scenarios, and this set pins its content precisely to keep that channel quiet.
