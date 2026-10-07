# Migration check: `qwen/qwen3.8-27b` to `qwen/qwen3.8-27b`

**Bottom line: Incomplete: no harmful change was found, but part of the app was not measured, so `qwen/qwen3.8-27b` is not cleared yet.**

We ran 2 scenarios from your app 5 times each on `qwen/qwen3.8-27b`, the model you are moving to, and compared them with recorded runs of `qwen/qwen3.8-27b`, the model you use today. 2 showed no difference this check could detect. 6 more scenarios were not compared at all; they are listed under what this check did not cover.

A scenario counts as changed in a way that matters only when the difference is statistically significant across the repeated runs, large enough to matter, and repeats on a fresh set of runs. Models answer differently every time, so a single different answer is never enough. A change says nothing about which model is good: it means your app would behave differently, which may be neutral or even welcome.

## No difference detected

This check detected no difference in: `extract_total`, `summarize_ticket`.

## What this check did not cover

- Only the scenarios above were tested. Anything your app does that no scenario exercises was not measured.
- Each scenario ran 5 times on `qwen/qwen3.8-27b`. A change that shows up in only some of those runs can go unnoticed. More runs can see smaller changes: re-record with `modelpin baseline --runs 10`, then run `modelpin check --runs 10`; model calls grow in proportion to the runs, and judge calls grow faster.
- 6 scenario(s) were never replayed - the provider rejected them - so `qwen/qwen3.8-27b` is NOT fully cleared: `cancel_subscription`, `classify_sentiment`, `decline_pii`, `format_contact_json`, `order_status`, `refund_request`. A rejected scenario is not a passing one; re-run, or fix what the provider named.
- Coverage: inert this run -- tool trajectory + arguments (no scenario declares `tools`); semantic judge (no `judge_model` configured); 2 of 2 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`extract_total`, `summarize_ticket`).
- Meaning was not compared: no judge ran in this check, so an answer that says something different in the same shape would not show here.

## The run

| | |
|---|---|
| Date | 2026-10-07 12:13 UTC |
| Model in use | `qwen/qwen3.8-27b` |
| Model moving to | `qwen/qwen3.8-27b` |
| Provider | groq |
| Scenarios compared | 2 |
| Runs per scenario | 5 of `qwen/qwen3.8-27b`; recorded runs of `qwen/qwen3.8-27b`: 5 |
| Recorded runs of `qwen/qwen3.8-27b` compared | 10 |
| Runs of `qwen/qwen3.8-27b` made by this check | 10, including re-runs made to check a flagged change |
| Judge (compares meaning) | none ran |
| Tokens used by `qwen/qwen3.8-27b` | 505 in, 135 out |
| Tool-call comparison | `strict` |
| Modelpin | 0.5.0 |

Each run of a model is one request, plus one more for each turn in which it called a tool, up to 6 requests per run. Recorded runs of the model in use were made earlier and cost nothing here.
