# The false-positive suite (`examples/fp-suite/`)

> **Role: SCORE. Never fit a threshold here; never edit a file here after a rate has been
> measured on it.** Declared in [`../roles.json`](../roles.json), enforced by
> `tests/test_suite_roles.py`, pre-registered before its first run in **ADR-0036**.
>
> `ADR-nnnn` refers to this project's internal decision records, which are not published. They
> are cited for provenance only; every argument they carry is stated inline here or in
> `docs/fp-measurement.md`.

Modelpin's whole promise is *"if it says it broke, it broke"* — a false-positive rate. For the
first ten weeks of the project that number was measured on scenario sets that could not produce
a false positive at all: `[M]` every set that existed before this one runs at `temperature: 0`
(where two samples of the same model are byte-identical and every channel returns `p = 1.00`),
except the seven `arg_*` files (0.7, tools only) and six semantic files that a threshold was
fitted on. `docs/fp-measurement.md` said so plainly: *the real reading is `0/0`*.

This suite exists to put the engine on the surface where a false alarm can actually happen.

## What is here

Twelve scenarios modelled on the apps this project exists for, every one at **`temperature: 1.0`
— the OpenAI API's default**, i.e. what a zero-config app actually runs at:

| Scenario | Shape | Channels that can move |
|---|---|---|
| `support_order_status` | support bot, one lookup tool, an internal note it must not leak | tool trajectory, assertion, judge |
| `triage_ticket_json` | strict JSON classification | assertion, judge |
| `classify_review_sentiment` | one-word label on a mixed review | judge |
| `extract_invoice_fields` | invoice → JSON | assertion, judge |
| `summarize_standup_notes` | summary that must keep every blocker | judge |
| `rag_answer_with_citation` | grounded answer with a `[n]` citation | assertion, judge |
| `agent_reschedule_two_step` | look up, then reschedule (two tools) | tool trajectory, arguments, assertion |
| `agent_missing_param_ask` | a REQUIRED id was not given: ask, never invent | tool trajectory (call vs no call) |
| `borderline_medication_question` | caveated help, not a refusal | refusal, judge |
| `format_markdown_table` | table only | assertion, judge |
| `rewrite_email_polite` | polite rewrite keeping every fact | judge |
| `sql_from_question` | one SELECT statement | assertion, judge |

Each scenario has a matching **perturbation** in `scripts/fp_measurement.py::PERTURBATIONS`:
a replacement system prompt that keeps the task and changes one policy (a tool no longer
called, a decision forced, a fact inverted, a citation dropped, a refusal introduced). The
detection arm replays that against the unperturbed baseline and expects a flag; whether each
one actually changed behaviour is what the arm measures, never a premise (ADR-0023).

## Design rules, and the trap each one closes

- **Assertions are things a correct answer must ALWAYS contain** (an invoice number copied
  verbatim, a citation the prompt demands), or things it must NEVER contain (an internal note).
  A `must_contain` that a correct answer satisfies only sometimes manufactures same-model false
  alarms, and that would be a suite defect published as an engine defect.
- **Five scenarios have legitimately multi-modal correct behaviour**, because real apps do,
  and a same-model null WILL sample more than one mode: `agent_missing_param_ask` (a model at
  temperature 1.0 sometimes asks and sometimes invents the id), `borderline_medication_question`
  (a caveated answer or a soft decline), `classify_review_sentiment` (`mixed` or `negative` are
  both defensible on that review), `sql_from_question` (two correct date idioms are
  syntactically disjoint) and `summarize_standup_notes` (a three-bullet cap under-determines
  which third fact survives). `[M]` At `runs: 5` a 0/5 vs 4/5 split of tool calls fires the
  tool channel at `p = 0.0476` (two-sided, `--match strict`) and a 0/5 vs 5/5 split at
  `p = 0.0079`. That is the engine's real exposure, and it is measured rather than designed
  away. Two more traps are named before the run, not after: `REFUSAL_MARKERS` substring-matches
  the whole output, so a correct clarifying question (*"I can't send it without your account
  id, could you..."*) can be scored `refused`; and the judge compares every run to the MODAL
  baseline output, which for free text is baseline run 0, so the semantic channel is
  structurally asymmetric. A refusal-driven flag here must quote the matched marker from the
  stored trace before anyone calls it drift.
- **No `tool_choice`**, no pinned one-word replies, no `seed`: the point is the model's own
  spread at its default settings, not a quiet pool.
- **Content-disjoint** from `examples/suite`, `examples/calibration`, `examples/report-suite`
  and every other declared set — by id AND by content hash (`tests/test_suite_roles.py`).

## Running it

```
python scripts/fp_measurement.py --provider openai --model gpt-4.1-mini --judge gpt-4o-mini \
    --runs 5 --repeats 20 --scenarios-dir examples/fp-suite \
    --out reports/fp-runs/<date>/s2a-fp-suite-gpt-4.1-mini.jsonl --workers 4
```

Same model on both sides, so any verdict other than `unchanged` is a false positive by
construction. `--out` records every trial with its traces as it completes; `--resume` continues
a cut run; `--rescore` rebuilds the report offline from the artifact with no key. The runs of
record, their transcripts and the pooled tables are under `reports/fp-runs/` and are read in
`docs/fp-measurement.md`.

## What a result here does and does not say

- It measures the shipped default stack — `runs: 5`, `--match strict`, the semantic judge on —
  on **this** model at **its** default temperature. A different model, temperature or `runs`
  is a different measurement; the artifact header records all of them.
- The rate is published two ways, and both are needed: over trials that **could have fired**
  (ADR-0022, the stricter, engine-centric number) and over **every trial that reached a
  verdict** (what a user running the same check sees). Abstentions and provider errors are
  printed beside both and folded into neither.
- Twelve scenarios cannot characterise twelve app shapes; repeats buy resolution on these
  twelve, never coverage of a thirteenth. The number is a floor on what the engine can do, not
  a ceiling on what a user's own suite might do.

## `[M] 2026-09-07` — what the first run did to the predictions above

The predictions are left standing; this section records what happened to them. Artifacts:
`reports/fp-runs/2026-09-07/s2a-fp-suite-gpt-4.1-mini.jsonl`, `s2b-fp-suite-gpt-4o-mini.jsonl`,
`s3-fp-suite-groq-gpt-oss-20b.jsonl` (20 + 20 + 1 repeats).

- **Zero false alarms**: 0 of 23 scored on `gpt-4.1-mini`, 0 of 28 on `gpt-4o-mini`, 0 of 1 on
  `gpt-oss-20b`; 0 of 240 / 240 / 12 trials that reached a verdict. Detection 11/12, 11/12, 12/12.
- **The multi-modality list was wrong in both directions.** `classify_review_sentiment` scored
  **0 of 41** — it never produced a scorable trial on any model. `agent_missing_param_ask` scored
  6 of 20 on `gpt-4o-mini`, but every one had `tool_call_match = 1.0` with **zero tool calls on
  both sides**: the model always asked, and the trials became scorable through the semantic
  channel, not the tool channel. Six scenarios not listed as multi-modal did score
  (`triage_ticket_json` 8, `rewrite_email_polite` 7, `agent_reschedule_two_step` 6, and three
  singletons), and `summarize_standup_notes` scored 19 — the highest in the whole run.
- **The `0/5 vs 4/5 → p = 0.0476` arithmetic stands; the exposure did not appear.** 0 of 710
  trials across the whole run could have fired on the tool channel.
- **The `REFUSAL_MARKERS` trap did appear.** `borderline_medication_question#14` on `gpt-4o-mini`
  scored 1 of 5 candidate runs `refused` (`refusal_delta = 0.2`) on a plainly non-refusing
  caveated answer; the matched marker is `i cannot`, in *"I cannot replace the personalized
  guidance of a healthcare professional"*. `p = 0.5`, so no false alarm — and the marker is
  quoted here from the stored trace, as this file requires.
- **The structural-asymmetry trap did too, as a false negative.** On `triage_ticket_json`
  (`gpt-4o-mini`) and `summarize_standup_notes` (`gpt-4.1-mini`) the perturbed candidate changed
  on 5 of 5 runs and the judge flagged 5 of 5, but the judge also flagged 2 and 4 of the 5
  baseline runs against the modal baseline output, so the verdict stayed `unchanged` at
  `p = 0.083` and `p = 0.500`. Counted as misses; tracked as MP-206.
