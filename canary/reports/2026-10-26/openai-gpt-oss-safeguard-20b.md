⚠️ **Modelpin: minor changes — `openai/gpt-oss-safeguard-20b` → `openai/gpt-oss-safeguard-20b`**
Replayed 8 scenario(s) ×5 runs using your API key.

**MINOR CHANGES (1)**
⚠️ refund_request — tool-call arguments changed: lookup_order(dropped order_id); issue_refund(dropped amount; added refund_amount)
<details><summary>example runs</summary>

- baseline: `tools lookup_order(order_id="A-1042") -> issue_refund(amount=49.99, order_id="A-1042"); "I’ve processed a refund of $49.99 for order **A‑1042**. Your refund will appear on the original payment method within 3–5 business days. If you have any othe..."`
- candidate: `tools lookup_order -> issue_refund(order_id="A-1042", refund_amount=49.99); "I’ve processed a full refund for your order **A‑1042**. • **Refund amount:** $49.99 • **Refund ID:** R‑9001 You’ll see the credit appear on your original pay..."`

</details>

**UNCHANGED (7)** ✅

→ Pin to `openai/gpt-oss-safeguard-20b` until resolved, or review the full diff above. These are minor changes: they do not fail the build, so the decision is yours.

<sub>coverage: inert this run -- semantic judge (no `judge_model` configured); 5 of 8 scenario(s) called no tool, so no CI-failing channel could see a content change in them (`classify_sentiment`, `decline_pii`, `extract_total`, `format_contact_json`, `summarize_ticket`)</sub>