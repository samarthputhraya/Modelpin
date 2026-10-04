# Migration check: `openai/gpt-oss-20b` to `openai/gpt-oss-20b`

**Bottom line: No harmful change was found, but this run could not see every kind of change; read what it did not cover before switching.**

We ran 8 scenarios from your app 5 times each on `openai/gpt-oss-20b`, the model you are moving to, and compared them with recorded runs of `openai/gpt-oss-20b`, the model you use today. 8 showed no difference this check could detect.

A scenario counts as changed in a way that matters only when the difference is statistically significant across the repeated runs, large enough to matter, and repeats on a fresh set of runs. Models answer differently every time, so a single different answer is never enough. A change says nothing about which model is good: it means your app would behave differently, which may be neutral or even welcome.

## No difference detected

This check detected no difference in: `cancel_subscription`, `classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `order_status`, `refund_request`, `summarize_ticket`.

## What this check did not cover

- Only the scenarios above were tested. Anything your app does that no scenario exercises was not measured.
- Each scenario ran 5 times on `openai/gpt-oss-20b`. A change that shows up in only some of those runs can go unnoticed. More runs can see smaller changes: re-record with `modelpin baseline --runs 10`, then run `modelpin check --runs 10`; model calls grow in proportion to the runs, and judge calls grow faster.
- Coverage: inert this run -- semantic judge (no `judge_model` configured); 5 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`).
- Meaning was not compared: no judge ran in this check, so an answer that says something different in the same shape would not show here.

## The run

| | |
|---|---|
| Date | 2026-10-04 13:42 UTC |
| Model in use | `openai/gpt-oss-20b` |
| Model moving to | `openai/gpt-oss-20b` |
| Provider | groq |
| Scenarios compared | 8 |
| Runs per scenario | 5 of `openai/gpt-oss-20b`; recorded runs of `openai/gpt-oss-20b`: 5 |
| Recorded runs of `openai/gpt-oss-20b` compared | 40 |
| Runs of `openai/gpt-oss-20b` made by this check | 40, including re-runs made to check a flagged change |
| Judge (compares meaning) | none ran |
| Tokens used by `openai/gpt-oss-20b` | 10,474 in, 4,274 out |
| Tool-call comparison | `strict` |
| Modelpin | 0.5.0 |

Each run of a model is one request, plus one more for each turn in which it called a tool, up to 6 requests per run. Recorded runs of the model in use were made earlier and cost nothing here.
