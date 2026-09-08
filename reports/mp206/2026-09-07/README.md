# MP-206 / ADR-0040 — the run of record re-scored under the new semantic rule

Same stored traces, same judge as each surface's original, engine changed. No replay was
re-bought (ADR-0037 --rejudge). COMPLETE as of 2026-09-07: every trial reached a verdict. Earlier cuts were caused by
OpenAI rate limiting, not by any property of the engine. Finishing them is MP-216, and until
they are finished NO bound from this directory may be published.

| artifact | keys | with a verdict | still failing |
|---|---|---|---|
| mp206-s0-suite-gpt-4o-mini-newengine.jsonl | 11 | 11 | 0 |
| mp206-s1-arg-gpt-4.1-mini-newengine.jsonl | 217 | 217 | 0 |
| mp206-s2a-newengine.jsonl | 252 | 252 | 0 |
| mp206-s2b-newengine.jsonl | 252 | 252 | 0 |
| mp206-s3-fp-suite-groq-gpt-oss-20b-newengine.jsonl | 24 | 24 | 0 |

[M] FINAL, all five surfaces complete. Pooled over 710 FP-arm trials: **0 false alarms**
under BOTH engines. Scored 82 -> 39, so the CONDITIONAL bound moves 3.6% -> 7.4%; the
UNCONDITIONAL bound is **0.42% under both** and ADR-0036's revisit trigger, written on the
unconditional bound, does NOT fire. Detection **43/46 -> 45/46** (lower bound 84.0% -> 90.1%):
both measured false negatives are caught, on both surfaces, at confidence 0.996.
