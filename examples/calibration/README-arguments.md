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

`arg_list_order` is its twin and the reason it is here: `[M]` list element order is
deliberately **not** canonicalised (`_canon` maps a sequence element-wise), so a reordered
array does mint a distinct key. `[A]` A reordered tag list is the shape most likely to produce
a real false alarm, because a human calls it identical behaviour.

## Running it

```
python scripts/fp_measurement.py --provider openai --model gpt-4o-mini --runs 5 --scenarios-dir examples/calibration --no-judge
```

> **`[M] 2026-08-24` This command over-collects, and the surplus is not scorable.**
> `scripts/fp_measurement.py:517-520` takes a directory and calls `load_scenarios` on all of
> it — it has **no role or subset filter** — so the line above runs all **13** files, the seven
> `arg_*` **and** the six semantic scenarios `MIN_SEMANTIC_DELTA` was fitted on. Those six are
> a `fit` set (see [`../roles.json`](../roles.json)); a false-positive rate that includes them
> is in-sample for 6 of its 13 scenarios and cannot be published as an out-of-sample result.
> **Until the filter lands (MP-89), score the `arg_*` rows only and say so when you report the
> number** — do not quote a 13-scenario denominator.

Each side is the **same model against itself**, so any verdict other than `unchanged` is a
false positive by construction. `--runs` must be equal on both sides or the argument gate
declines to compare at all. `--no-judge` isolates the argument channel; a second pass with the
judge on measures the full stack. BYO-key (ADR-0008); nothing here may be run from an agent
seat (ADR-0006).

**Publish the observed repertoire, not just the verdict.** A run that reports "0 false
positives" over pools that turned out to be unimodal has reproduced exactly the non-evidence
this subset was written to remove. The number that makes the result meaningful is, per
scenario and per side, *how many distinct argument payloads appeared across the N runs*.
