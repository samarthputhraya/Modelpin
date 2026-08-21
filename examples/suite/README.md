# Modelpin evaluation suite

A small, realistic scenario set for a customer-support / data SaaS app, used for **live
two-model runs** and the **false-positive measurement** (spec §11 DoD). Unlike
the generated `mp init --demo` sandbox (kept minimal, and offline), these are meant
to run against real models with your own API key.

The eight scenarios are chosen to exercise every diff signal:

| Scenario | kind | Primarily exercises |
|---|---|---|
| `refund_request` | agent | multi-step tool trajectory (`lookup_order` → `issue_refund`) |
| `order_status` | agent | single-tool trajectory |
| `cancel_subscription` | agent | tool action + confirmation |
| `extract_total` | single | extraction correctness (stable token) |
| `classify_sentiment` | single | classification + semantic equivalence |
| `summarize_ticket` | single | pure semantic equivalence (wording varies) |
| `decline_pii` | single | refusal / policy-decline detection |
| `format_contact_json` | single | output format / schema drift |

Agent scenarios declare canned `tool_results`, so multi-step replay is deterministic and
needs no real tool execution. Assertions are deliberately minimal and meaningful — no
noisy `must_contain:["$"]`-style checks (the live smoke run showed those flag formatting,
not behavior); semantic equivalence is judged by the LLM-judge instead.

Run it (BYO key), e.g.:

```
mp baseline --provider openai --model gpt-3.5-turbo --scenarios-dir examples/suite --runs 5
mp check    --provider openai --to gpt-4o-mini --from gpt-3.5-turbo --scenarios-dir examples/suite --runs 5
```
