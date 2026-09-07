# MP-206 / ADR-0040 — the run of record re-scored under the new semantic rule

Same stored traces, same judge as each surface's original, engine changed. No replay was
re-bought (ADR-0037 --rejudge). INCOMPLETE: the trials below marked failing were cut by
OpenAI rate limiting, not by any property of the engine. Finishing them is MP-216, and until
they are finished NO bound from this directory may be published.

| artifact | keys | with a verdict | still failing |
|---|---|---|---|
| mp206-s0-suite-gpt-4o-mini-newengine.jsonl | 11 | 7 | 4 |
| mp206-s1-arg-gpt-4.1-mini-newengine.jsonl | 217 | 210 | 7 |
| mp206-s2a-newengine.jsonl | 252 | 246 | 6 |
| mp206-s2b-newengine.jsonl | 252 | 252 | 0 |
| mp206-s3-fp-suite-groq-gpt-oss-20b-newengine.jsonl | 24 | 3 | 21 |

[M] Pooled over the 699 FP-arm trials that reached a verdict: **0 false alarms** under
both engines. Scored trials fall 82 -> 39, so the CONDITIONAL bound moves 3.6% -> 7.4% while
the UNCONDITIONAL bound is 0.42% -> 0.43%. ADR-0036's revisit trigger is written on the
unconditional bound and does NOT fire. Detection on s2b: 12/12, where the old engine read
11/12 — the two measured false negatives are caught.
