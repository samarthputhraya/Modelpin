# `examples/refusal-suite/` — the suite built to make the refusal channel fire

**Role: `fit`** (declared in [`examples/roles.json`](../roles.json), enforced by
`tests/test_suite_roles.py`, governed by ADR-0025).

## Why this suite exists

Modelpin has five verdict-bearing channels. `[M] 2026-08-31`, after two live dogfood runs,
four of them had been observed working on real material and one had not:

| channel | first observed on real material |
|---|---|
| text assertions | dogfood #1 (a real JSON-classification app) |
| tool trajectory | dogfood #2 (a real tool-calling app) — flagged 3 of 6 at 0.95–0.99 |
| tool arguments | dogfood #2 — exercised, same-model only |
| semantic judge | dogfood #2 — scored 0.80–1.00 and varied |
| **refusal** | **never** — `[M]` 0 of 60 traces on dogfood #1, 0 of 30 on #2 |

A channel that has never fired is an assumption wearing a measurement's clothes. Neither
dogfood suite asked for anything a model would decline, so the zero says nothing about
whether `looks_like_refusal` works on real output — only that it was never given the chance.

## What the scenarios do, and why these ones

Every `refuse_*` scenario asks for something a competent assistant declines for a
**capability** or **privacy** reason — live market data, fetching a URL, reading a file on
the user's disk, sending mail with no tool to send it, the personal details of a private
individual.

Capability boundaries are a deliberate choice over the obvious alternative (unsafe-content
prompts):

- they are **harmless to ship** in a public repository;
- they decline **consistently across vendors**, which is what "reliably declines on both
  sides" requires;
- and a model that *gains* the capability **stops declining**, which is the migration this
  suite is about.

**`[M]` The refusal channel cannot currently see that drop, and this suite was authored on the
assumption that it could.** The channel is **one-sided by design** (ADR-0002):
`diff/__init__.py` gates on `refusal_delta >= MIN_REFUSAL_DELTA`, and
`stats.permutation_pvalue_mean` returns `p = 1.0` for any non-positive change. `[M]`
Reproduced on the engine: baseline 5/5 refused → candidate 0/5 refused, no tools, no
assertions, no judge — exactly this suite's live configuration — returns **`unchanged`,
confidence 1.00, `refusal_delta = -1.0`**.

So a model that *starts* refusing is a regression; a model that *stops* is not. On these
scenarios the drop is visible only to a configured **semantic judge**. That one-sidedness is a
deliberate false-positive protection and is not being "fixed" on this evidence — the honest
consequence is that this suite measures the **detector**, and the direction it was designed
around needs a judge on the run.

`answer_plain_question` is the **quiet anchor**: an ordinary question that must never read as
a refusal. Without it the suite could only show that the detector *fires*, not that it
*discriminates* — and the north-star metric is the false-positive rate.

## Why `fit` and not `score`

If a live run shows real declines that `REFUSAL_MARKERS` does not match, the fix is to widen
those markers — a **threshold change**. ADR-0025 forbids scoring a rate on the set a
threshold was fitted on, so this suite may be calibrated against and **no false-positive rate
may ever be claimed from it**. That measurement belongs on the held-out `examples/suite/`,
which already carries `decline_pii`.

## Running it

Offline first — the suite is ordinary scenarios, so `--provider fake` works with fixtures.
Live, on a free Groq key:

```bash
modelpin baseline --provider groq --model <model-a> --scenarios-dir examples/refusal-suite --runs 5
modelpin check    --provider groq --from <model-a> --to <model-b> \
                  --scenarios-dir examples/refusal-suite --runs 5
```

6 scenarios × 5 runs × 2 sides = **60 completions**. Pace at ≤24 calls/min.

## Status — `[M] 2026-08-31`, live on Groq

> **These numbers are not reproducible from this repository.** The run's raw traces and reports
> were not published; the working notes are maintainer-local. Treat every `[M]` below as a
> maintainer assertion, not as evidence you can recount — unlike the Drift Map, whose raw data
> is in `docs/reports/data/`. Publishing the traces is open work.

**The detector fires, and it discriminates.** `[M]` **50 of 50** `refuse_*` traces on
`qwen/qwen3.6-27b` and `qwen/qwen3.8-27b` recorded `refused=True`, counted directly off the
stored baselines; **0 of 15** on `answer_plain_question`. Before this run the count was 0 of 90
across two dogfoods.

`[A]` **A third model, `allam-2-7b`, contributed 25 more `refuse_*` traces that are NOT
counted.** Its check returned `unchanged`, and an earlier draft of this file read that as proof
it had also refused — *"a 5/5 → 0/5 flip could not have produced `unchanged` at N=5"*. That
inference is **backwards**: per the one-sided note above, a 5/5 → 0/5 flip produces exactly
`unchanged`, at confidence 1.00. So `unchanged` cannot distinguish "it also refused" from "it
stopped refusing", and those 25 traces are unmeasured. The honest count is **50 of 50 measured,
25 unmeasured**.

`[M]` One decline came back with a **curly apostrophe** — `I don't have access … so I can't
provide` — and was still detected, which is the first time `_normalize_for_refusal`'s
glyph-folding has been exercised by a real model rather than by a string this project wrote.

**Still `[A]`: the channel has never moved a verdict on real material.** Every comparison came
back `unchanged`. `[M]` The natural experiment (`groq/compound`, which has web search and
should *not* decline) is unusable: `413 Request Entity Too Large` on 2 of 6 scenarios and over
the 8000 TPM ceiling on the rest.

And note what the one-sidedness costs here: closing that half needs a model that answers where
another declines **and a judge configured on the run**. A model that stops refusing does not
move the refusal channel in either direction, so on a judge-less run there is nothing to see.
