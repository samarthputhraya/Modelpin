# How Modelpin works

This page explains, in plain language, what Modelpin does between the moment you type
`modelpin check` and the moment it prints a verdict. No statistics background needed; the
precise decision rule is at the end for those who want it.

## The problem

You built something on a language model. The provider ships a new version, or retires the one
you use. You need to know: **will my app still behave the same on the new model?**

Two things make that hard:

1. **Models are random.** Ask the same question twice and the wording changes. A plain text
   diff between two models says "everything changed", every time, so it is useless.
2. **The changes that matter are behavioral.** An agent that now calls the refund tool twice,
   a support bot that starts refusing a request it used to handle, a classifier whose answer
   flips from `bug` to `other` — these break production even when the prose looks fine.

Modelpin answers the question the way a careful engineer would: run the same cases many times on
both models, compare *how each model behaves across those runs*, and only raise an alarm when the
difference is too consistent to be chance — and still there when you run it again.

## The four steps

```
 scenarios/*.json ──► modelpin baseline ──► .modelpin/baseline-<model>.json
                            (N runs on the model you use today)
                                              │
 modelpin check --to <candidate>              ▼
   1. replay each scenario N times on the candidate
   2. compare the two sets of runs, signal by signal
   3. a regression must be statistically significant AND large enough
   4. a regression must reproduce on N fresh candidate runs
                                              │
                                              ▼
   verdict per scenario + exit code + .modelpin/last-report.md (the PR comment)
```

### 1. Scenarios: the cases you care about

A **scenario** is one JSON file describing one representative thing your app asks a model to do:
the messages you send, and optionally the tools the model may call, canned tool results, and text
the answer must or must not contain. See [Writing scenarios](writing-scenarios.md).

Scenarios are yours. Modelpin never invents test cases; it measures *your* app's behavior.

### 2. Baseline: record the model you depend on today

`modelpin baseline` runs every scenario **N times** (default 5) on your current model and saves
what happened — the final answer, every tool call and its arguments, whether the model refused,
token counts and latency — to `.modelpin/baseline-<model>.json`. You can commit that file so CI
never has to call the old model again (read it first: it contains model outputs).

### 3. Check: replay on the candidate and compare

`modelpin check --to <candidate>` replays each scenario N times on the candidate model, then
compares the candidate's runs with the baseline's runs. It looks at several **signals**:

| Signal | What it notices | Can fail the build? |
|---|---|---|
| **Tool-call trajectory** | the sequence of tools the model called changed (e.g. `lookup_order → issue_refund` became `lookup_order → lookup_order → issue_refund`, or a tool stopped being called) | yes |
| **Refusal rate** | the model started declining requests it used to handle | yes |
| **Meaning (LLM judge)** | the answers no longer mean the same thing, even if they are worded differently. Needs `judge_model` set | yes |
| **Your text assertions** | answers started violating the scenario's `must_contain` / `must_not_contain` | no — reported as a minor change |
| **Tool-call arguments** | the right tool was called with different arguments | no — reported as a minor change |
| **Latency and tokens** | speed and length moved | no — shown for information only |

Each signal is compared as a **distribution**: "the baseline called the tool on 5 of 5 runs, the
candidate on 0 of 5" rather than "run 1 differs from run 1".

### 4. Decide: significant, large, and reproducible

A difference only counts as a **regression** when all of these hold:

1. **It is statistically significant.** Modelpin uses an exact permutation test: it asks how
   often you would see a difference this large if both sets of runs came from the *same* model.
   If that happens more than 5% of the time, it is treated as noise.
2. **It is large enough to matter.** A statistically significant but tiny shift (say, one refusal
   in fifty runs) is ignored. Each signal has a minimum effect size.
3. **It reproduces.** When a scenario looks like a regression, Modelpin replays the candidate N
   more times and checks those fresh runs on their own. If the regression does not show up again,
   it is reported as *not confirmed* and does **not** fail your build. This is what stops a
   one-off unlucky sample from turning a pull request red. (Turn it off with `--no-confirm`.)

The semantic judge is a separate model that reads a baseline answer and a candidate answer and
says whether they are equivalent. It runs at temperature 0, it is optional, and it should be a
model that is *not* one of the two you are comparing.

## Verdicts

| Verdict | Meaning | `check` exit code |
|---|---|---|
| `unchanged` | no significant behavior change | 0 |
| `changed_minor` | something moved — assertion drift, argument change, or a regression that did not reproduce. Worth reading, not worth blocking | 0 |
| `regression` | a real, reproduced behavior change on a signal that can fail the build | **1** |
| `insufficient_evidence` | one side recorded nothing usable (empty answers), so nothing could be compared | 3 |

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | no regression | nothing — but read any `changed_minor` lines |
| `1` | at least one reproduced regression | read the report; pin your current model until you have looked |
| `3` | the run could not fully answer: a scenario could not be measured, the provider rejected one, or nothing could be compared | read the notes in the output; it is **not** a clearance |
| `4` | Modelpin never ran: missing key, bad flag, unreadable config, no scenarios | fix the setup error printed above |

## Why N runs, and why 5

A single run cannot tell a change from randomness, so Modelpin refuses `--runs 1`. Small N also
limits what the permutation test can ever conclude: with 2 runs per side no signal can reach
significance at all, and with 3 the tool-call signal cannot. From 4 runs every signal can fire;
**5 is the default** and the setting the project's own measurements use. More runs give more
sensitivity and cost proportionally more. Modelpin prints a warning before spending when your run
count is too low to detect anything.

## What a run costs

Before it spends anything, `check` prints the size of the run, for example:

```
provider=openai from=gpt-4o-mini to=gpt-4.1-mini runs=5 match=strict | 8 scenario(s) from scenarios -> 40 replays, >=40 paid calls + up to 360 judge calls
```

- **Replays:** scenarios × runs. A `single` scenario is one model call per replay; an `agent`
  scenario is up to 6 (one per tool-loop turn).
- **Confirmation:** only a scenario that looks like a regression is replayed again (another
  `runs` replays for that scenario).
- **Judge calls:** an upper bound. Identical answers skip the judge, and it stops at the first
  equivalent baseline answer, so the real number is usually far lower.

All calls use **your** API key, read from the environment. Modelpin never stores or ships a key.

## Where the limits are

Modelpin is built so that *if it says it broke, it broke* — and it prefers to stay quiet on a
borderline change rather than cry wolf. That has two consequences worth knowing:

- **It can miss subtle changes.** A change that only shows up on one run in five, or a meaning
  shift the judge considers equivalent, may be reported as `unchanged` or `changed_minor`.
- **It only knows your scenarios.** A behavior no scenario exercises cannot be measured. When a
  run's scenarios could not have detected a content change at all (no judge, no tool calls),
  the report says so instead of claiming a clean result.

The project's measured false-positive and detection numbers, with their confidence bounds and
caveats, are published in [docs/fp-measurement.md](fp-measurement.md).

## The precise rule

For readers who want the exact definition (source: `modelpin/diff/__init__.py`,
`modelpin/diff/stats.py`, `modelpin/diff/confirm.py`):

- Significance: exact two-sample permutation test, `p <= ALPHA = 0.05`.
- Effect-size floors: tool-trajectory total-variation distance `>= 0.5` (`MIN_TOOL_TVD`); refusal
  rate rise `>= 0.34` (`MIN_REFUSAL_DELTA`); semantic-divergence rate rise `>= 0.5`
  (`MIN_SEMANTIC_DELTA`); tool arguments fully disjoint (`MIN_TOOL_ARG_TVD = 1.0`, advisory only).
- Tool-trajectory match modes (`--match`, or `"match"` in a scenario): `strict` (same calls, same
  order), `unordered` (same calls, any order), `subset` (candidate may drop calls, may not add),
  `superset` (candidate may add calls, may not drop).
- Semantic flags: each candidate answer is divergent when the judge finds it equivalent to *no*
  baseline answer; each baseline answer is scored against the other baseline answers, so the
  baseline's own natural variety is part of the comparison.
- Confirmation: a `regression` on the first candidate sample triggers N fresh candidate runs,
  diffed alone against the same baseline. The verdict stays `regression` only if that diff is also
  a `regression`; otherwise it becomes `changed_minor`. Confirmation can remove a regression but
  never create one.
