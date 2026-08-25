# The argument-jitter subset (`arg_*.json`)

> **Role: SCORE. Never fit a threshold on these files.** They sit in `examples/calibration/`
> for provenance, but they do **not** carry that directory's tuning role — pricing a
> false-positive rate is a measurement, and a threshold fitted on the set it is measured on
> makes that measurement in-sample and void. Declared in [`../roles.json`](../roles.json),
> enforced by `tests/test_suite_roles.py`, decided in **ADR-0025**.
>
> **What this does and does not block.** These are the only tool-bearing scenarios at
> `temperature > 0` in the repo, so they are both the obvious place to fit an argument
> threshold and the one place it must not be fitted — a floor fitted on the only surface where
> a signal can fire, then scored on that same surface, is unfalsifiable. It does **not** block
> MP-04: `[M]` `MIN_TOOL_ARG_TVD = 1.0` on that branch is documented as a structural rule
> ("no candidate run used a payload any baseline run used"), derived from exhaustive
> relabelings under a constructed null with **no scenario set involved**, and it sits at the
> ceiling of its scale. Nothing was fitted, so nothing needs a fit set. A labelled fit set
> becomes necessary only if a measured run says 1.0 must move.
>
> **Read the bound before spending a key — the likeliest result is no number at all.** `[M]`
> Seven scenarios give a one-sided 95% Clopper-Pearson upper bound of **34.8%** at zero
> observed false positives, but only if all seven *score*. They will not: ADR-0022 excludes
> trials that could not have fired, and across the **three runs of record** in
> `docs/fp-measurement.md` the scored fraction was **0 of 8**, **1 of 6**, and **0 of 6** —
> **1 of 20 attempted, 5.0%**. Seven scenarios at that rate project to **0.35 expected scored
> trials, so the modal outcome is zero**: an abstention (ADR-0018), not a rate. One scored
> trial gives a **95.0%** bound; reaching 5.0% needs n≈59. This subset can *falsify* the
> argument signal — one equivalent-looking change that flags is decisive and costs one trial.
> It cannot establish a low rate, and without MP-89's `--repeats` it most likely establishes
> nothing.
>
> **`[M] 2026-08-25` That 5.0% is not measured on this subset at all.** All three pooled runs
> (`0 of 8`, `1 of 6`, `0 of 6`) ran the temperature-0 `examples/suite` and the six *semantic*
> calibration files, which **declare no tools** — argument payloads had nothing to do with the
> number. It is a prior for the exclusion rate in general, not for `arg_*`. `[M]` The one live
> `arg_*` run scored **7 of 70 (10.0%)**, all on one scenario, and the section below explains
> why the other six were excluded.

These seven scenarios are **not** semantic discriminators like the six single-turn files
described in [`README.md`](README.md). They exist for one job: to price the
**false-positive rate of the tool-call *argument* signal** (MP-04 / MP-54), which no
scenario in this repo could exercise before.

Why nothing here could measure it: `[M] 2026-08-22` every scenario in `examples/suite/`,
`examples/report-suite/` and `examples/drift-suite/` runs at `temperature: 0`, and the six
calibration scenarios run at `0.7` but declare **no tools**. `[M]` A unimodal pool early-exits
at `p = 1.0` (`modelpin/diff/stats.py`), so a false-positive run over temperature-0 scenarios
returns "0 false positives" *whatever the engine does* — that is not evidence about anything.

## The rule that makes these files different

**Every tool declares a real JSON-Schema `parameters` block.** `[M]` A tool declared as a bare
string is normalised by `modelpin/providers/openai.py::_to_tools` into
`{"type": "function", "function": {"name": ..., "parameters": {"type": "object", "properties": {}}}}`
— a function with no arguments to fill — so every recorded payload is `{}` and the argument
signal is measuring an empty set. A scenario without a real schema re-measures nothing.

Two more properties are deliberate, and removing either kills the measurement:

- **No `tool_choice`.** `[M]` The generation params are re-sent on *every* turn of the
  model↔tool loop in `providers/openai.py::run`, so `tool_choice: "required"` would force a
  tool call on all `MAX_TOOL_TURNS` turns and end every run at the turn cap.
- **A pinned one-word final reply.** The judge is skipped when a run's text matches the modal
  baseline text (`diff/semantic.py`), so pinning the closing word keeps the *semantic* channel
  quiet and leaves the argument channel as the only thing that can move the verdict.

## The axes

| File | Axis under test | Expected to vary | Pinned (control) |
|---|---|---|---|
| `arg_numeric_rounding.json` | rounding of a computed number | `weight_kg` (7.4 lb → 3.35659 kg) | `parcel_id` |
| `arg_optional_fields.json` | presence of optional fields | `priority` present or absent | `title`, `project`; `notify_channel` should stay absent |
| `arg_enum_phrasing.json` | wording/case in a free string | `queue` ("billing" / "Billing" / …) | `ticket_id`, `channel` (a real `enum`) |
| `arg_list_order.json` | **element order inside an array** | order of `tags` | `article_id`, the tag set itself |
| `arg_multistep_carry.json` | format of a value carried across two calls | `new_time` ("19:30" / "7:30 PM" / …) | both `confirmation_code`s, the trajectory |
| `arg_freetext_note.json` | model-authored prose in an argument | `note` (a fresh sentence each run) | `account_id` |
| `arg_key_order.json` | **quiet anchor** — every value pinned | nothing but JSON key order | all five fields |

`arg_key_order` is the anchor: `[M]` `diff/argkey.py` canonicalises with `sort_keys=True`, so
key-order jitter cannot mint a distinct payload key. If this file ever flags, the protection
regressed — do not "fix" the scenario.

## `[M] 2026-08-25` Why six of seven shapes never scored: the pools OVERLAPPED

MP-105 read the first live run of this subset — `[M]` 7 scored trials, **all of them the same
scenario** (`arg_freetext_note`) — and concluded the other six shapes are schema-constrained and
therefore deterministic **regardless of temperature**, proposing that we either author
free-text-dominant replacements or concede in `docs/` that this method cannot test them at all.

**`[M]` Its own transcript refutes that.** Recounting
`arg-gate-fp-2026-08-25-gemini-2.5-flash.txt` (committed on the MP-04 branch), the per-side
distinct-payload counts printed on every row:

| Scenario | `gemini-2.5-flash` max distinct / side | trials with >1 |
|---|:---:|:---:|
| `arg_enum_phrasing` | 1 | 0 of 8 |
| `arg_freetext_note` | 5 | **9 of 9** |
| `arg_key_order` | 1 | 0 of 9 |
| `arg_list_order` | 1 | 0 of 9 |
| `arg_multistep_carry` | 1 | 0 of 9 |
| **`arg_numeric_rounding`** | **2** | **5 of 8** |
| **`arg_optional_fields`** | **2** | **3 of 8** |

Gemini varied on **three** of seven shapes, not one. Those trials were excluded because base and
candidate **pools overlapped** — a 1-payload side against a 2-payload side that contains it
returns `p = 1.00` on every channel, so ADR-0022 scores it *could not have fired*. That is the
gate's false-positive defence working exactly as designed. It is **not** a statement about the
model, and "the corpus does not vary" was the wrong reading of it.

### What a second candidate does add: rate, not kind

Re-running the same seven files unchanged, at the same `temperature: 0.7`, pooling **16 runs on
one model** (a different instrument from the table above, which counts per 5-run side):

| Scenario | `gpt-4o-mini` (n=16) | `gpt-4.1-mini` (n=16) |
|---|:---:|:---:|
| `arg_enum_phrasing` | 1 | 1 |
| `arg_freetext_note` | **8** | **9** |
| `arg_key_order` | 1 | 1 |
| `arg_list_order` | 1 | 1 |
| `arg_multistep_carry` | 1 | 1 |
| **`arg_numeric_rounding`** | **1** | **9** |
| `arg_optional_fields` | **2** | **2** |

`[M]` Both runs are from `fe121d3`, 16/16 successful replays per cell, **zero provider errors**,
and raw and key-sorted counts agreed in all 14 rows — **no key-order jitter was observed at
all**, so `arg_key_order` is doing its job as the quiet anchor.

**`arg_numeric_rounding` is the one cell that separates the two models.** `[M]` `gpt-4o-mini`
returned a single payload in each of two independent 16-run samples; `gpt-4.1-mini` returned 7,
then 9, and 10 distinct payloads at n=24 (modal 8/24). The values are real computed-number
jitter — `weight_kg` came back as `3.35658`, `3.35662`, `3.35664` and more. `[M]` **Report the
spread, not one figure**: the count itself is a sample statistic and moved 7→9 between two runs
of the same model at the same n.

`[M]` `arg_optional_fields` varies on **both** models (2 payloads each, modal 13/16 and 11/16),
so it does not discriminate between them. On `gpt-4o-mini` the second payload is a re-wording at
the same `priority`; on `gpt-4.1-mini` it is `priority` flipping `"normal"` → `"low"` — a
difference an application would act on. `[M]` It read a single payload in an earlier 16-run
sample, so at this rate a 16-run sample is not reliable evidence of quietness either.

`[A]` **Which axes can move still looks scenario-driven, and three models cannot separate the
two factors.** Four shapes (`enum_phrasing`, `key_order`, `list_order`, `multistep_carry`) held
at one payload on every model and every run so far; the two models that vary on three shapes
(`gemini-2.5-flash`, `gpt-4.1-mini`) vary on the *same* three, across two vendors. Treat the
scenario/model split as unresolved rather than settled in either direction.

### What this means for pricing condition 6

**Read this before concluding the good news is good.** Three qualifications, all measured:

1. `[M]` **There is no argument signal on `main`.** `modelpin/diff/structural.py:29` builds a
   trajectory from `tc.name` only, and nothing under `modelpin/diff/` reads `.arguments`.
   `diff/argkey.py` lives on the unmerged MP-04 branch. Condition 6 cannot be priced from
   `main` at any n, on any model.
2. `[M]` **A high-repertoire candidate cuts both ways.** `ops/LOG.md` already records disjoint
   rounding jitter (`3.36` vs `3.357` kg) scoring `regression` at confidence 0.992 — a false
   positive. `gpt-4.1-mini`'s rounding cell (7 distinct in 16) is precisely the
   disjointness-prone pool that produces it. More scorable trials is not the same as a lower
   rate, and this is the north-star metric.
3. `[M]` **One distinct payload is an abstention, never quietness** (ADR-0018). Sixteen
   identical runs bound the per-run divergence rate only at **17.1%**
   (`upper_bound_95(0, 16)`), and one of the four steady shapes (`arg_key_order`) is the pinned
   control that is *designed* not to move. `--repeats` is not useless here, it is **unpriced**:
   `[M]` `arg_optional_fields` read one payload in one 16-run sample and two in the next, on
   the same model.

Measure the repertoire before spending a key on the full arm:

<!-- arg-repertoire-command -->
```bash
python scripts/arg_repertoire.py --provider openai --model gpt-4.1-mini --runs 16     --scenarios-dir examples/calibration --glob 'arg_*.json'
```

Committed runs of record, each carrying its own git sha, UTC timestamp, per-scenario
temperature and full payload *frequencies* (not just the distinct set, so pool disjointness
stays auditable): [`results/arg-repertoire-gpt-4o-mini.json`](results/arg-repertoire-gpt-4o-mini.json),
[`results/arg-repertoire-gpt-4.1-mini.json`](results/arg-repertoire-gpt-4.1-mini.json), and the
n=24 single-scenario deep run [`results/arg-repertoire-rounding-n24.json`](results/arg-repertoire-rounding-n24.json).

`arg_list_order` is its twin and the reason it is here: `[M]` list element order is
deliberately **not** canonicalised (`_canon` maps a sequence element-wise), so a reordered
array does mint a distinct key. `[A]` A reordered tag list is the shape most likely to produce
a real false alarm, because a human calls it identical behaviour.

## Running it

```
python scripts/fp_measurement.py --provider openai --model gpt-4.1-mini --runs 5     --scenarios-dir examples/calibration --role score --no-judge
```

> **`[M] 2026-08-25` Two corrections to the line above, both measured.**
> **`--role score` is now required.** MP-89 landed the filter this section anticipated, as a
> *refusal* rather than a default: `[M]` the previously documented command now exits with
> `error: 2 roles declared for this directory (fit, score). ... Re-run naming the one you
> mean, e.g. --role score.` The old line no longer runs at all.
> **The model changed for a reason**, not a preference: `[M]` on `gpt-4o-mini` six of the seven
> `arg_*` shapes emit one payload in 16 runs, so they cannot score. See the section above.

> **`[M] 2026-08-24` This command over-collects, and the surplus is not scorable.**
> `scripts/fp_measurement.py:685` takes a directory and calls `load_scenarios` on all of
> it — it has **no role or subset filter** — so the line above runs all **13** files, the seven
> `arg_*` **and** the six semantic scenarios `MIN_SEMANTIC_DELTA` was fitted on. Those six are
> a `fit` set (see [`../roles.json`](../roles.json)); a false-positive rate that includes them
> is in-sample for 6 of its 13 scenarios and cannot be published as an out-of-sample result.
> **`[M] 2026-08-25` The filter landed.** Pass `--role score`; it is now mandatory for this
> directory, not advisory. Do not quote a 13-scenario denominator.

Each side is the **same model against itself**, so any verdict other than `unchanged` is a
false positive by construction. `--runs` must be equal on both sides or the argument gate
declines to compare at all. `--no-judge` isolates the argument channel; a second pass with the
judge on measures the full stack. BYO-key (ADR-0008); nothing here may be run from an agent
seat (ADR-0006).

**Publish the observed repertoire, not just the verdict.** A run that reports "0 false
positives" over pools that turned out to be unimodal has reproduced exactly the non-evidence
this subset was written to remove. The number that makes the result meaningful is, per
scenario and per side, *how many distinct argument payloads appeared across the N runs*.
