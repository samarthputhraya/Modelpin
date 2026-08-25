# Calibration scenarios

> **This directory holds two roles, not one.** The six semantic scenarios described below are
> for **tuning**. The seven `arg_*.json` files are **not** — they are a scoring set, and no
> threshold may be fitted on them. See [`README-arguments.md`](README-arguments.md) and
> **ADR-0025**. Roles are declared machine-readably in [`../roles.json`](../roles.json) and
> enforced by `tests/test_suite_roles.py`.

This set is deliberately **separate from the held-out DoD suite** in
[`examples/suite/`](../suite/).

## Why a separate set (do not merge these)

The false-positive claim in [`docs/fp-measurement.md`](../../docs/fp-measurement.md) depends on
the suite being *held out* — i.e. the thresholds were **not** fit on it. If you calibrate on
the suite, a held-out result stops being an honest out-of-sample measurement (classic
train/test leakage). So:

- **`examples/suite/`** — held-out. Measure FP rate / detection here. Never tune on it.
- **`examples/calibration/*.json`** (the six semantic files) — tune thresholds here, then
  *re-validate* on the suite.
- **`examples/calibration/arg_*.json`** — held-out for the **argument** channel. Measure here;
  never tune here. It lives in this directory for provenance, not because it shares this
  directory's role.

**Why the exception exists rather than a tidier directory split:** `[M] 2026-08-24` the `arg_*`
files are the only scenarios in the repo with both tools and `temperature > 0`, so they are the
only surface on which an argument-channel false positive is even possible. That makes them the
scoring set by necessity — and it is exactly why MP-04 may not fit its argument floor on them.

## What these scenarios are

Single-turn prompts where the **meaning of the answer is the discriminator** — each has a
clear correct answer, so a meaning-flipping perturbation (in
[`scripts/calibrate_thresholds.py`](../../scripts/calibrate_thresholds.py)) produces a
structurally-similar but semantically-wrong candidate. That isolates the **semantic
LLM-judge** signal, which is the one gating promotion of a semantic change from
`changed_minor` to a CI-failing `regression`.

They run at `temperature: 0.7` on purpose: wording varies run-to-run, so the judge is
actually exercised on the *equivalent* (same-model) pairs — the real false-positive risk —
instead of being skipped because the text was byte-identical.

## How to use

```
python scripts/calibrate_thresholds.py --model gpt-4o-mini --runs 5
```

Generates equivalent pairs (must NOT fire) and meaning-changed pairs (should fire), extracts
the raw semantic signal per pair, and sweeps `MIN_SEMANTIC_DELTA` to find the smallest
false-positive-safe threshold.

## Results of record

Committed raw per-pair signals live in [`results/`](results/):

- **`result-independent-judge.json`** — candidate `gpt-3.5-turbo`, judge `gpt-4o-mini` (the
  judge does **not** grade its own output, so no self-judging bias). **This is the run that
  justifies promoting the semantic signal to a CI-failing `regression`:** 0 false positives at
  `MIN_SEMANTIC_DELTA=0.5`, with the conservative floor absorbing one noisy equivalent pair.
- **`result-selfjudge.json`** — candidate == judge == `gpt-4o-mini`. Kept only as a cross-check;
  an adversarial audit showed self-judging made the separation artificially clean.

Full write-up + the post-promotion held-out re-validation: [`docs/fp-measurement.md`](../../docs/fp-measurement.md).
