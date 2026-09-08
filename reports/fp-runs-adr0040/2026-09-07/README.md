# The run of record, re-scored under ADR-0040

These five artifacts are the **published** false-positive and detection numbers. They are the
stored replays of [`../../fp-runs/2026-09-07/`](../../fp-runs/2026-09-07/) re-diffed under the
current engine, by **the same judge each surface originally used**. No replay was bought a
second time (`--rejudge`, ADR-0037), so this is one sample read twice, not two samples: it
**replaces** its source's numbers and is never pooled with them. `scripts/fp_aggregate.py`
refuses any set that contains both.

`[M]` COMPLETE as of 2026-09-07 — every trial reached a verdict. Earlier cuts were caused by
OpenAI rate limiting, not by any property of the engine, and were finished with `--resume` at
`--workers 1`.

| artifact | keys | with a verdict | still failing |
|---|---|---|---|
| s0-suite-gpt-4o-mini-adr0040.jsonl | 11 | 11 | 0 |
| s1-arg-gpt-4.1-mini-adr0040.jsonl | 217 | 217 | 0 |
| s2a-fp-suite-gpt-4.1-mini-adr0040.jsonl | 252 | 252 | 0 |
| s2b-fp-suite-gpt-4o-mini-adr0040.jsonl | 252 | 252 | 0 |
| s3-fp-suite-groq-gpt-oss-20b-adr0040.jsonl | 24 | 24 | 0 |

`[M]` Pooled over 710 FP-arm trials: **0 false alarms under BOTH engines.** Scored falls
82 → 39, because the quieter semantic channel drives more trials to `p = 1.00` and ADR-0022
excludes them, so the CONDITIONAL bound moves **3.6% → 7.4%** while the UNCONDITIONAL bound is
**0.42% under both** (0.4210% → 0.4283%). **ADR-0036's revisit trigger, which is written on the
unconditional bound, does NOT fire.** Detection **43/46 → 45/46** (lower bound 84.0% → 90.1%),
and **19/22 → 21/22** over distinct perturbations: both measured false negatives are caught, on
both surfaces, at confidence 0.996.

Read the caveat with the number: `[M]` **30 of the 39 scored trials could only have fired on the
advisory argument gate**, which by ADR-0029 can never fail a build on its own, and the two
build-failing structural channels contributed zero. `docs/fp-measurement.md` says so beside the
bound.

Regenerate everything this directory supports, offline and with no key:

```
python scripts/fp_aggregate.py reports/fp-runs-adr0040/2026-09-07/*.jsonl
```
