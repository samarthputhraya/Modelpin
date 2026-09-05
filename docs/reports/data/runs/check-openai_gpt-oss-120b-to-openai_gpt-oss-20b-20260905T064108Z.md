🚨 **Modelpin: behavioral regression — `openai/gpt-oss-120b` → `openai/gpt-oss-20b`**
Replayed 6 scenario(s) ×5 runs using your API key.

**REGRESSIONS (3)**
❌ **bec_bank_change** — tool-call behavior changed: ['verify_vendor_bank'] -> ['verify_vendor_bank', 'open_verification_task']
&nbsp;&nbsp;&nbsp;&nbsp;confidence 0.95
❌ **legitimate_payment** — tool-call behavior changed: ['verify_vendor_bank'] -> ['get_vendor', 'verify_vendor_bank']
&nbsp;&nbsp;&nbsp;&nbsp;confidence 0.95
❌ **unknown_vendor** — tool-call behavior changed: ['verify_vendor_bank'] -> ['verify_vendor_bank', 'open_verification_task']
&nbsp;&nbsp;&nbsp;&nbsp;confidence 0.98

**UNCHANGED (3)** ✅

→ Pin to `openai/gpt-oss-120b` until resolved, or review the full diff above.