✅ **Modelpin: no behavioral change — `openai/gpt-oss-120b` → `openai/gpt-oss-120b`**
Replayed 8 scenario(s) ×5 runs using your API key.

**UNCHANGED (8)** ✅

→ No behavioral regressions found in the 3 scenario(s) where a CI-failing channel could see a change in content; in 5 no run called a tool, so none was live (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`) and `openai/gpt-oss-120b` is only partially cleared. A wrong-but-confident answer in those would have passed.

<sub>coverage: inert this run -- semantic judge (no `judge_model` configured); 5 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`)</sub>