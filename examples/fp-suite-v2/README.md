# The false-positive suite, v2 — the tool and assertion channels (`examples/fp-suite-v2/`)

> **Role: SCORE. Never fit a threshold here; never edit a file here after a rate has been
> measured on it.** Must be declared in [`../roles.json`](../roles.json) and pinned in
> `tests/test_suite_roles.py::EXPECTED_MEMBERS` before it can be run — until it is, the
> examples tree fails `test_no_scenario_file_is_undeclared`, which is the intended behaviour:
> a scenario that lands with no declared role is exactly what that test exists to stop.
>
> This is a NEW set, not a replacement. `examples/fp-suite/` has been scored and ADR-0025
> forbids editing it; nothing here touches it.

## Why this set exists

`examples/fp-suite/` put the engine on a surface where a false alarm can happen — prose at
`temperature: 1.0` — and it worked, for the channels it reached. It did not reach two of them.

`[M] 2026-09-07`, re-verified over `reports/fp-runs/2026-09-07/*.jsonl`:

| arm | trials | `tool_call_match < 1.0` | `format_valid == False` |
|---|---|---|---|
| FP (same model vs itself) | 710 | **0** | **0** |
| recall (perturbed candidate) | 46 | 10 | 7 |

Both channels demonstrably *work* — the recall arm moves them. Neither has ever been exposed to
a same-model null. So `MIN_TOOL_TVD` has a measured false-positive exposure of **exactly zero
trials**, and the published false-positive bound says nothing whatsoever about the tool
trajectory or the text assertions. For a tool whose wedge is *migration*, the tool-call
trajectory is the single most load-bearing channel there is.

`[M]` The scenario authored for precisely this, `../fp-suite/agent_missing_param_ask.json`,
produced **zero tool calls on both sides in all of its scored FP trials**. Its system prompt
says *"if the customer has not given it, ask them for it and do not call the tool"* — which is
unambiguous, so the model always asks. A scenario whose correct behaviour is deterministic
cannot vary, and a channel that cannot vary cannot be measured. **That is the failure mode
every file in this directory is built to avoid.**

## The inversion, stated up front

Modelpin's normal scenario rule is *"assert only what a correct answer MUST always contain"* —
a `must_contain` satisfied only sometimes manufactures same-model false alarms. **Four
scenarios here deliberately break that rule, and that is the point.** This set is a NULL: every
trial replays one model against itself, so there is no behaviour change to detect and the only
question being asked is *does the same model vary, and does the engine over-read the variance*.
A literal a correct answer produces only sometimes is the instrument, not a defect.

The shape is realistic rather than contrived: in each case the user's app has a downstream
constraint (a plain-text renderer, a `[n]` citation parser, a `json.loads`, a ledger keyed on
the ISO currency code) that the prompt **forgot to state**. That is the commonest real-world
source of assertion churn there is.

**Do not merge the rate measured here into the `examples/fp-suite` bound.** They are different
denominators over different exposures. This set is deliberately exposure-MAXIMISING on two
channels, so what it produces is a near-worst-case conditional rate — *"given a corpus where
the tool trajectory and the assertions are live, how often does the engine cry wolf"* — not a
headline user-facing number. A reader who averages the two gets a figure that means nothing.

## Pre-registered prediction (write this down before the run)

Under a true null with `runs: 5` a side, a scenario whose model-side probability of taking a
branch is `q` produces two DIFFERING 5-run distributions with probability `1 - Σ B(k;5,q)²`:

| `q` | 0.05 | 0.1 | 0.2 | 0.3 | 0.5 | 0.7 | 0.8 | 0.9 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|
| P(sides differ, i.e. trial is SCORED) | 0.359 | 0.538 | 0.680 | 0.728 | **0.754** | 0.728 | 0.680 | 0.538 | 0.359 |
| P(gate fires, two-key null) | — | 0.0005 | 0.005 | 0.012 | **0.022** | 0.012 | 0.005 | 0.0005 | — |

The second row is `P(|k₁-k₂| ≥ 4)`: the tool gate needs `tool_tvd ≥ MIN_TOOL_TVD` (0.5, i.e.
`|k₁-k₂| ≥ 3`) **and** `p ≤ ALPHA`, and `[M]` a 3-run gap scores `p ≈ 0.16` while a 4-run gap
scores `p = 0.0476` and a 5-run gap `p = 0.0079` — so in practice only a gap of 4 or 5 fires.

Two consequences worth stating before anyone sees a number:

1. **Any `q` strictly inside (0, 1) works.** The design target is not `q = 0.5`; it is
   `q ∉ {0, 1}`. Even a scenario the model takes only 10% of the time yields a scored trial
   more than half the time. The ONLY fatal outcome is a scenario that is deterministic.
2. **Expect this set to produce false positives, and expect roughly 1–2 per 100 tool-live
   trials.** At the worst case `q = 0.5` the per-trial tool-channel rate is ~2.2%. If the run
   comes back with zero flags AND high scored coverage, that is a real (and pleasant) result
   about the permutation test's discreteness. If it comes back with zero SCORED trials, the
   corpus failed again and the engine is untested — see each scenario's falsifier below.

## What is here

Twelve scenarios, every one at **`temperature: 1.0`** (the API default — what makes prose and
tool choice actually vary), no `seed`, no `tool_choice`, no pinned one-word replies.

### (a) Genuinely 50/50: call the tool, or just answer — 4 scenarios

The prompt offers a tool and **does not resolve** whether to use it, because a competent model
could defensibly do either. Target channel: **tool trajectory** (`[]` vs `[t]`).

| Scenario | Mechanism | Falsifier |
|---|---|---|
| `calc_tool_or_mental_math` | `calculate` offered for `1850 × 1.0837` — awkward enough to tempt the tool, easy enough to do in-head | `calculate` is called in 5/5 runs on both sides, or 0/5 on both, in every trial |
| `verify_or_trust_pasted_status` | The customer already quoted the tracking status, so `lookup_shipment` re-verifies rather than informs. The prompt deliberately omits fp-suite's *"before answering, look it up"* | `lookup_shipment` is called in 5/5 (or 0/5) runs on both sides in every trial |
| `docsearch_or_general_knowledge` | A p50-vs-p95 question the model plainly knows, asked of a bot with a `search_docs` tool, referencing "your dashboard" | `search_docs` is called in 5/5 (or 0/5) runs on both sides in every trial |
| `grammar_tool_or_direct_fix` | Four obvious typos, and a `check_grammar` tool that exists for exactly them | `check_grammar` is called in 0/5 runs on both sides in every trial — the likeliest of the four to collapse |

### (b) A redundant or optional second tool — 3 scenarios

The first call is effectively forced; the SECOND is genuinely optional given what the user or
the first tool result already supplied. Target channel: **tool trajectory via LENGTH**
(`[t1]` vs `[t1, t2]`), with no behaviour change either way.

| Scenario | Mechanism | Falsifier |
|---|---|---|
| `optional_availability_before_booking` | `check_availability` is offered as *"if you want to confirm a room is free first"*; the guest supplied every field `create_booking` needs | The trajectory is the same length in 5/5 runs on both sides in every trial |
| `optional_notify_after_status_update` | `notify_customer` is *"use it when it would be useful"* — a judgement call, on an operator-facing request that never asked for it | As above |
| `optional_part_stock_second_lookup` | Optionality comes from the DATA, not the prompt: `get_repair_ticket` already returns `part_eta` and `promised_by` (so one call answers it) while naming an `awaiting_part` that `get_part_stock` covers | As above. If it fires, check whether the model looped — a `> 2`-call trajectory is a different finding |

### (c) A strict format a correct answer meets only some of the time — 4 scenarios

Assertions are only `must_contain` / `must_not_contain` substrings (`expected_tool_calls` and
`output_schema` were deleted in MP-147 and do not exist). Each keys on a literal the model
produces **variably** while the answer stays correct either way. Target channel: **assertion
violation rate**. `[M]` The `[2]` and `EUR` literals appear in neither the prompt nor any canned
tool result, so the model must generate them rather than copy them.

| Scenario | Literal | Both branches correct because | Falsifier |
|---|---|---|---|
| `plaintext_answer_bold_optional` | `must_not_contain: **` | A two-sentence explanation is correct bolded or not; the prompt never forbids Markdown, and the app renders plain text | `**` appears in 0/5 (or 5/5) runs on both sides in every trial |
| `citation_style_underspecified` | `must_contain: [2]` | *"Cite the passage you used"* is satisfied by `[2]`, `(Passage 2)`, `Passage 2` or `[Passage 2]` alike; passages are labelled `Passage N:` so bracket style is not primed | `[2]` present in 5/5 on both sides (style was never actually free), or absent in 5/5 on both (nobody ever brackets) |
| `sql_answer_fence_unspecified` | `must_not_contain: ` ``` | The query is correct fenced or bare; the prompt never forbids fences (fp-suite's `sql_from_question` does, which is why it never moved) | Fences in 0/5 or 5/5 on both sides in every trial |
| `total_currency_code_or_symbol` | `must_contain: EUR` | `EUR 1,997.75`, `€1,997.75` and `1997.75 euros` are all correct; a ledger keyed on the ISO code accepts only the first | `EUR` present in 5/5 (or absent in 5/5) on both sides in every trial |

### Quiet anchor — 1 scenario

| Scenario | Role | Falsifier |
|---|---|---|
| `anchor_mandatory_lookup_fixed_format` | One REQUIRED tool call, called at most once, and one fully specified output line. It must read `tool_call_match == 1.0` and `format_valid == True` on **every** trial | It fires. If the anchor moves, the variance is in the harness, the adapter or the model's basic instruction-following — NOT in the deliberate optionality of the other eleven, and no result from this set can be read until that is explained |

`[M]` Its two `must_contain` literals (`HR-9042`, `CONFIRMED`) are both copyable verbatim from
the prompt or the canned tool result, which is what makes the anchor quiet by construction —
the exact inverse of the (c) scenarios above.

One anchor rather than the usual two: this set sits at the top of the 8–12 range, and this
single file anchors BOTH target channels at once (trajectory and assertion). A second,
tool-free anchor on the assertion channel alone would be a reasonable follow-up.

## Design rules

- **One seam per scenario.** The (a) and (b) scenarios carry **no assertions at all** — adding
  one would put a second channel on the same file and make a flag ambiguous about which
  channel produced it. The (c) scenarios declare **no tools**.
- **Every tool scenario carries a `tool_results` stub for every tool name it declares**, or the
  multi-turn loop cannot realise a trajectory and the tool signal is dead.
- **No key outside the consumed allowlist** (`messages`, `tools`, `tool_results`, and the
  adapters' generation keys). A key nothing reads cannot reach a provider call but can give a
  copied scenario a false content identity — `tests/test_suite_roles.py`.
- **Content-disjoint by id AND by content hash** from every other declared set. Where a
  scenario deliberately mirrors an fp-suite shape, it inverts the property that made the
  original deterministic (fp-suite's `sql_from_question` forbids fences; this one is silent.
  fp-suite's `support_order_status` mandates the lookup; `verify_or_trust_pasted_status` does
  not. fp-suite's `rag_answer_with_citation` dictates `[n]`; `citation_style_underspecified`
  does not).

## Known trap this set inherits

`REFUSAL_MARKERS` substring-matches the whole output on a 9-marker English keyword list, so an
ordinary hedge (*"I can't confirm the part will arrive before Saturday"*) can be scored
`refused`. Nothing here is designed to move the refusal channel, and **a refusal-driven flag
from this set is soft evidence at best**: quote the matched marker out of the stored trace
before anyone calls it drift. The verdict-bearing signals for this set are the tool trajectory
and the assertion rate, in that order.

## Before running

1. Declare the set in `../roles.json` (role `score`) and pin it in
   `tests/test_suite_roles.py::EXPECTED_MEMBERS` and `EXPECTED_ROLES`.
2. The recall arm is **optional and empty for this set**: `scripts/fp_measurement.py` gates on
   `if scn.id in PERTURBATIONS`, so with no entries these twelve run in the FP arm only — which
   is what the row is about. Add perturbations only if a detection number is also wanted.
3. Acceptance for the row is **≥ 20 scored trials whose `tool_call_match < 1.0` or whose
   assertion violation rate differs**. Seven tool-live scenarios × `repeats` × ~0.68 (table
   above) reaches 20 at `--repeats 5`, and clears it with margin at `--repeats 10` even if two
   of the seven collapse to a deterministic branch.
