# Live validation on Gemini (2026-09-15/16)

This page records what happened when the shipped CLI was run against real models, at scale, and
what it found. It is the complement to [docs/fp-measurement.md](fp-measurement.md): that page
measures the diff engine through a harness, this one measures **`modelpin check` as a user runs
it** — the same command, the same defaults, confirmation on.

Everything below is re-derivable from
[`reports/live-validation/2026-09-15/summary.json`](../reports/live-validation/2026-09-15/summary.json)
(every job, its exit code and its verdicts) and is re-checked by
`tests/test_live_validation.py`.

**Setup.** Google Gemini on Vertex AI, ten models reachable on the project. Eight scenario suites
from this repo (`examples/`: `suite`, `drift-suite`, `report-suite`, `fp-suite`, `fp-suite-v2`,
`fp-suite-v3`, `refusal-suite`, `voicerag-suite`) — 82 scenarios in total. Every job is an
isolated working directory running the real CLI: `modelpin baseline`, then `modelpin check`, at
`runs: 5`, `--match strict`, with a Gemini judge that is neither model under comparison.

## 1. Same model against itself — any regression is a false alarm

| baseline | candidate | scenario-checks | `regression` | `changed_minor` |
|---|---|---|---|---|
| `gemini-2.5-flash` | `gemini-2.5-flash` | 164 | 0 | 0 |
| `gemini-2.5-flash-lite` | `gemini-2.5-flash-lite` | 164 | 0 | 0 |
| `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` | 164 | 0 | 0 |
| `gemini-3.5-flash` | `gemini-3.5-flash` | 152 | 0 | 0 |
| `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` | 164 | 0 | 0 |
| `gemini-3.8-flash` | `gemini-3.8-flash` | 164 | 0 | 0 |
| **total** | | **972** | **0** | **0** |

`[M]` **0 false alarms in 972 same-model scenario-checks** (96 jobs; one job's 12 checks were
lost when the campaign was restarted, which is why `gemini-3.5-flash` shows 152).

Read it with its limits. These are *scenario-checks*, not independent trials: 82 scenarios are
re-used across models and repeats, and many of them are so deterministic at these settings that
no channel could have fired at all — the harness measurement on the same models puts that at
230 of 240 trials ([fp-measurement.md](fp-measurement.md), "Gemini as judge"). This number says
the product does not cry wolf in ordinary use; it is not a per-trial false-positive rate, and
the bounds on that live on the other page.

## 2. Real upgrades — what it flags, and whether the flags are worth a reviewer's time

Six real Gemini upgrade pairs, same eight suites:

| baseline | candidate | scenario-checks | `regression` | `changed_minor` |
|---|---|---|---|---|
| `gemini-2.5-flash-lite` | `gemini-3.1-flash-lite` | 82 | 11 | 1 |
| `gemini-3.1-flash-lite` | `gemini-3.5-flash-lite` | 82 | 6 | 3 |
| `gemini-2.5-flash` | `gemini-3.5-flash` | 82 | 6 | 1 |
| `gemini-3-flash-preview` | `gemini-3.5-flash` | 82 | 2 | 2 |
| `gemini-3.5-flash` | `gemini-3.8-flash` | 77 | 1 | 2 |
| `gemini-2.5-pro` | `gemini-3.1-pro-preview` | 82 | 2 | 4 |
| **total** | | **487** | **28** | **13** |

`[M]` **4 further scenarios were flagged on their first sample and withheld** because the
regression did not reproduce on the confirmation runs — they are in the `changed_minor` column,
labelled *not confirmed*. Without that step they would have been 4 more red builds.

**Were the 28 regressions worth acting on?** Each flag was shown to a *different*, stronger
Gemini model (`gemini-3.1-pro-preview`, or `gemini-3.8-flash` where the pro model was under
test) with the scenario, five baseline runs and the candidate runs, and asked whether a
developer of that app would consider the difference material — a changed decision, tool plan,
refusal, fact, or a format a parser depends on — as opposed to wording or noise.

`[M]` **28 of 28 regressions were rated material**, and 8 of 13 `changed_minor` flags. Examples
it found: a model that stopped calling `search_docs` and answered from memory; one that started
calling `check_availability` before booking; a prompt-injection defence that flipped direction
across a version; two arithmetic answers that changed; a dispatch line that dropped the driver
id; an agent that began hallucinating a refund instead of calling the tool.

**That reviewer is itself a language model**, and its judgement is not ground truth: it is a
second opinion, prompted to look for materiality, on the same traces. Treat "28 of 28" as
"nothing in this set looked like noise to an independent reader", not as a precision rate.

## 3. What the campaign found in Modelpin itself

The run was not only a measurement; it was a bug hunt, and it caught six things a user would
have met first:

1. **A refusal flagged on phrasing alone.** `gemini-3.1-flash-lite` declined with "I do not have
   the ability to browse live URLs" and `gemini-3.5-flash-lite` with "I cannot access external
   URLs" — the same behavior, but only the second matched a refusal marker, so `check` published
   `refusal rate 0% -> 100%` and failed the build, twice. The markers now cover both phrasings
   (priced on 3,524 stored pairs first: 0 gate results changed), and re-running those two jobs on
   the fixed build drops exactly those two flags and keeps the material ones.
2. **A blocked prompt read as a crash.** `gemini-3.8-flash` returns no candidate at all for an
   unsafe prompt (`block_reason: SAFETY`); that killed a whole `baseline` (exit 4). It is a
   refusal now, and the suite that triggered it runs clean.
3. **One rejected scenario lost a whole recording.** `modelpin baseline` now keeps every scenario
   it could record, names the ones it could not, and exits 3.
4. **A transient `429` ended runs.** The Google SDK retries nothing by default; it now retries
   with backoff, and the message says the retries were spent.
5. **Archived reports vanished on Windows in deep folders** (the name carries both model ids and
   crossed the 260-character path limit), taking the run record with them.
6. **Runs were strictly sequential**, so a 12-scenario suite at `runs: 5` on a slow model spent
   over twenty minutes on one side. Runs of a scenario are now sent together, and separate runs
   judged together: `[M]` 170 s → 43 s for a baseline, 208 s → 62 s for a check.

## 4. Cost and reproducibility

Roughly 30,000 model calls over the campaign, on Gemini flash-class models plus two pro models,
billed to the maintainer's own Google Cloud credit. Every job directory kept its console log and
its run record; the committed `summary.json` holds every verdict and explanation (traces
excluded, to keep the artifact small). The harness that drove it is not part of the package: it
is 120 lines that shell out to the CLI, one job per directory.
