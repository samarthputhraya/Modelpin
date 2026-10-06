⚠️ **Modelpin: minor changes — `openai/gpt-oss-120b` → `openai/gpt-oss-120b`**
Replayed 8 scenario(s) ×5 runs using your API key.

**MINOR CHANGES (1)**
⚠️ refund_request — tool-call arguments changed: issue_refund(dropped amount; added reason)
<details><summary>example runs</summary>

- baseline: `tools lookup_order(order_id="A-1042") -> issue_refund(amount=49.99, order_id="A-1042"); "I’ve processed a refund of **$49.99** for order **A‑1042**. The refund has been successfully issued and should appear on your original payment method within..."`
- candidate: `tools lookup_order(order_id="A-1042") -> issue_refund(order_id="A-1042", reason="Item arrived damaged"); "I’ve processed a refund for order **A‑1042** due to the damage. The refund of **$49.99** has been issued and is now being processed. You should see the amoun..."`

</details>

**UNCHANGED (7)** ✅

→ Pin to `openai/gpt-oss-120b` until resolved, or review the full diff above. These are minor changes: they do not fail the build, so the decision is yours.

<sub>coverage: inert this run -- semantic judge (no `judge_model` configured); 5 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`)</sub>