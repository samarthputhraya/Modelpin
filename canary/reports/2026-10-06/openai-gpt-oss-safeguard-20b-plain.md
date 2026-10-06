# Migration check: `openai/gpt-oss-safeguard-20b` to `openai/gpt-oss-safeguard-20b`

**Bottom line: No harmful change was found, but this run could not see every kind of change; read what it did not cover before switching.**

We ran 8 scenarios from your app 5 times each on `openai/gpt-oss-safeguard-20b`, the model you are moving to, and compared them with recorded runs of `openai/gpt-oss-safeguard-20b`, the model you use today. 1 changed in a smaller way (a smaller change, or one that did not repeat) that would not fail a build but is worth a look: `refund_request`. 7 showed no difference this check could detect.

A scenario counts as changed in a way that matters only when the difference is statistically significant across the repeated runs, large enough to matter, and repeats on a fresh set of runs. Models answer differently every time, so a single different answer is never enough. A change says nothing about which model is good: it means your app would behave differently, which may be neutral or even welcome.

## What changed

### refund_request: minor change

- It calls the same tools in your app with different inputs.
- **Before** (`openai/gpt-oss-safeguard-20b`, a typical run): `tools lookup_order(order_id="A-1042") -> issue_refund(amount=49.99, order_id="A-1042"); "I’ve processed a refund of $49.99 for order **A‑1042**. Your refund will appear on the original payment method within 3–5 business days. If you have any othe..."`
- **After** (`openai/gpt-oss-safeguard-20b`, a run showing the change): `tools lookup_order -> issue_refund(amount=49.99, order_id="A-1042"); "I’ve looked up your order A‑1042 and confirmed it qualifies for a refund. Your refund of $49.99 has been processed successfully. Your refund reference is **R..."`
- What the check measured: tool-call arguments changed: lookup_order(dropped order_id)

## No difference detected

This check detected no difference in: `cancel_subscription`, `classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `order_status`, `summarize_ticket`.

## What this check did not cover

- Only the scenarios above were tested. Anything your app does that no scenario exercises was not measured.
- Each scenario ran 5 times on `openai/gpt-oss-safeguard-20b`. A change that shows up in only some of those runs can go unnoticed. More runs can see smaller changes: re-record with `modelpin baseline --runs 10`, then run `modelpin check --runs 10`; model calls grow in proportion to the runs, and judge calls grow faster.
- Coverage: inert this run -- semantic judge (no `judge_model` configured); 5 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`).
- Meaning was not compared: no judge ran in this check, so an answer that says something different in the same shape would not show here.

## The run

| | |
|---|---|
| Date | 2026-10-06 12:28 UTC |
| Model in use | `openai/gpt-oss-safeguard-20b` |
| Model moving to | `openai/gpt-oss-safeguard-20b` |
| Provider | groq |
| Scenarios compared | 8 |
| Runs per scenario | 5 of `openai/gpt-oss-safeguard-20b`; recorded runs of `openai/gpt-oss-safeguard-20b`: 5 |
| Recorded runs of `openai/gpt-oss-safeguard-20b` compared | 40 |
| Runs of `openai/gpt-oss-safeguard-20b` made by this check | 40, including re-runs made to check a flagged change |
| Judge (compares meaning) | none ran |
| Tokens used by `openai/gpt-oss-safeguard-20b` | 13,209 in, 6,079 out |
| Tool-call comparison | `strict` |
| Modelpin | 0.5.0 |

Each run of a model is one request, plus one more for each turn in which it called a tool, up to 6 requests per run. Recorded runs of the model in use were made earlier and cost nothing here.
