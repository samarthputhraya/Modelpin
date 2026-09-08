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
**0.4210% under both engines** — 0 alarms in 710 trials either way. (`0.4283%` is a different
number: what the unconditional bound becomes if the 12-trial Groq arm is removed. ADR-0040
Amendment 2 briefly reported 0.42% → 0.43% as an engine effect; that was an artifact of the 11
trials still missing at the time, corrected in its Amendment 4.) **ADR-0036's revisit trigger,
which is written on the unconditional bound, does NOT fire.** Detection **43/46 → 45/46** (lower
bound 84.0% → 90.1%), and **19/22 → 21/22** over distinct perturbations: both measured false
negatives are caught, on both surfaces, at confidence 0.996.

Read three caveats with the number:

1. `[M]` **30 of the 39 scored trials could only have fired on the advisory argument gate**,
   which by ADR-0029 can never fail a build on its own. Only 9 of the 39 — 8 semantic, 1
   refusal — sat on a channel that can. The tool trajectory and the assertion channel
   contributed zero.
2. `[M]` The two recovered detections are **the same two trials ADR-0040 was designed and
   accepted against** (its D4), so they are in-sample and the 90.1% lower bound is optimistic by
   an unquantified amount.
3. `[M]` "0 new false alarms" is a real result but a weak one on the affected channel: the
   semantic channel's scored exposure fell 51 → 8, so zero there bounds it only at 31.2%. And
   ADR-0040's own exact enumeration of a modelled null makes the new rule **1.63×–9.59× more
   prone to a false alarm at the shipped `runs: 5`**, with its measured safety conditional on
   judge leniency (its falsifier #4 is open). `docs/fp-measurement.md` carries all three.

Regenerate everything this directory supports, offline and with no key:

```
python scripts/fp_aggregate.py reports/fp-runs-adr0040/2026-09-07/*.jsonl
```
