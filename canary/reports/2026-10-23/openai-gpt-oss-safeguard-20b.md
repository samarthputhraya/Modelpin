❔ **Modelpin: partially measured — `openai/gpt-oss-safeguard-20b` → `openai/gpt-oss-safeguard-20b`**
Replayed 6 scenario(s) ×5 runs using your API key; 2 could not be replayed at all.

**COULD NOT REPLAY (2)** - excluded from every number below
❗ `cancel_subscription` — Groq call for model 'openai/gpt-oss-safeguard-20b' failed: the API call failed \[APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model \`openai/gpt-oss-safeguard-20b\` in organization \`org_01kt9yam9 \[... 157 chars elided ...\] tokens? Upgrade to Dev Tier today at https\://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}\].
❗ `classify_sentiment` — Groq call for model 'openai/gpt-oss-safeguard-20b' failed: the API call failed \[APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model \`openai/gpt-oss-safeguard-20b\` in organization \`org_01kt9yam9 \[... 157 chars elided ...\] tokens? Upgrade to Dev Tier today at https\://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}\].

**UNCHANGED (6)** ✅

→ No behavioral regressions found in the 2 scenario(s) where a CI-failing channel could see a change in content; in 4 no run called a tool, so none was live (`decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`) and `openai/gpt-oss-safeguard-20b` is only partially cleared. A wrong-but-confident answer in those would have passed.
→ 2 scenario(s) were never replayed - the provider rejected them - so `openai/gpt-oss-safeguard-20b` is NOT fully cleared: `cancel_subscription`, `classify_sentiment`. A rejected scenario is not a passing one; re-run, or fix what the provider named.

<sub>coverage: inert this run -- semantic judge (no `judge_model` configured); 4 of 6 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`)</sub>