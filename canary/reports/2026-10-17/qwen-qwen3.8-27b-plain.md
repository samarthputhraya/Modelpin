# Migration check: `qwen/qwen3.8-27b` to `qwen/qwen3.8-27b`

**Bottom line: Incomplete: nothing could be compared, so this check clears nothing.**

No scenario could be compared between `qwen/qwen3.8-27b`, the model you use today, and `qwen/qwen3.8-27b`, the model you are moving to. 8 more scenarios were not compared at all; they are listed under what this check did not cover.

A scenario counts as changed in a way that matters only when the difference is statistically significant across the repeated runs, large enough to matter, and repeats on a fresh set of runs. Models answer differently every time, so a single different answer is never enough. A change says nothing about which model is good: it means your app would behave differently, which may be neutral or even welcome.

## What this check did not cover

- Only the scenarios above were tested. Anything your app does that no scenario exercises was not measured.
- 8 scenario(s) were never replayed - the provider rejected them - so `qwen/qwen3.8-27b` is NOT fully cleared: `cancel_subscription`, `classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `order_status`, `refund_request`, `summarize_ticket`. A rejected scenario is not a passing one; re-run, or fix what the provider named.
- Meaning was not compared: no judge ran in this check, so an answer that says something different in the same shape would not show here.

## The run

| | |
|---|---|
| Date | 2026-10-17 09:40 UTC |
| Model in use | `qwen/qwen3.8-27b` |
| Model moving to | `qwen/qwen3.8-27b` |
| Provider | groq |
| Scenarios compared | 0 |
| Runs per scenario | 5 of `qwen/qwen3.8-27b`; recorded runs of `qwen/qwen3.8-27b`: - |
| Recorded runs of `qwen/qwen3.8-27b` compared | 0 |
| Runs of `qwen/qwen3.8-27b` made by this check | 0, including re-runs made to check a flagged change |
| Judge (compares meaning) | none ran |
| Tokens used by `qwen/qwen3.8-27b` | 0 in, 0 out |
| Tool-call comparison | `strict` |
| Modelpin | 0.5.0 |

Each run of a model is one request, plus one more for each turn in which it called a tool, up to 6 requests per run. Recorded runs of the model in use were made earlier and cost nothing here.
