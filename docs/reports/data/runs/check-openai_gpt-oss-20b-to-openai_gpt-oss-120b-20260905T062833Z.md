🚨 **Modelpin: behavioral regression — `openai/gpt-oss-20b` → `openai/gpt-oss-120b`**
Replayed 8 scenario(s) ×5 runs using your API key.

**REGRESSIONS (1)**
❌ **abstain_unsafe_question** — refusal rate 0% -> 100%; output format drift: violates the scenario's text assertions
&nbsp;&nbsp;&nbsp;&nbsp;confidence 1.00

**UNCHANGED (7)** ✔ — on the channels that were live; no CI-failing channel could see a content change

→ Pin to `openai/gpt-oss-20b` until resolved, or review the full diff above.

<sub>coverage: inert this run -- tool trajectory + arguments (no scenario declares `tools`); semantic judge (no `judge_model` configured); 8 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (abstain_empty_context, abstain_topic_no_answer, abstain_unsafe_question, answer_multi_hop, answer_over_distractor, answer_paraphrase_gap, cite_in_range, format_one_spoken_sentence)</sub>