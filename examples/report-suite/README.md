# The Modelpin Report suite (public, open, versioned)

This is the **open standard scenario suite** that `modelpin report` runs to draft a public
**Modelpin Report** on a model launch. It is deliberately kept **separate** from
[`examples/suite/`](../suite/) — see *Why a separate suite* below.

## What it is

Ten scenarios spanning the behaviors a launch writeup is expected to probe, one (or more)
per diff signal:

| Scenario | kind | Primarily exercises |
|---|---|---|
| `tooluse_multistep_refund` | agent | multi-step tool trajectory (`lookup_order` → `issue_refund`) |
| `tooluse_single_lookup` | agent | single-tool trajectory |
| `tooluse_guarded_action` | agent | guarded action — call the check, **not** the destructive second tool (subset/superset) |
| `extract_structured_field` | single | extraction correctness (stable token) |
| `classify_label` | single | one-label classification |
| `summarize_semantic` | single | pure semantic equivalence (wording varies) — the false-positive-risk case |
| `refusal_safety_decline` | single | refusal / policy-decline detection |
| `format_schema_json` | single | output format / schema drift |
| `reasoning_multistep` | single | small deterministic reasoning (one correct answer) |
| `instruction_following_constraints` | single | multi-constraint instruction adherence |

Every scenario pins `temperature: 0`. Agent scenarios declare canned `tool_results`, so
multi-step replay is deterministic without executing real tools. Assertions are deliberately
minimal (the live smoke run showed noisy `must_contain` checks flag formatting, not behavior);
meaning-equivalence is left to the LLM-judge.

## Why a separate suite (not `examples/suite/`)

`examples/suite/` is the **held-out false-positive measurement set** — the `0/8 = 0%` FP
headline in [`docs/fp-measurement.md`](../../docs/fp-measurement.md) depends on it staying
untouched by threshold tuning. Publishing reports from that same set would conflate
*"the scenarios we validate our own false-positive rate on"* with *"the scenarios we publish
reports from."* Three disjoint sets keep every claim independent:

- `examples/suite/` — held-out FP measurement set (frozen).
- `examples/calibration/` — labeled set for the semantic-judge threshold (distinct, by design).
- `examples/report-suite/` — **this** public report suite (evolves on the public cadence).

## Reproducibility

Each report stamps the suite identity so a reader can pin exactly what ran:

- `manifest.json` carries a human `suite_version` (semver). **Bump it on any scenario
  add / edit / remove** — a changed suite is a new version.
- `modelpin report` computes a **content hash** over the *validated* scenarios (not raw file
  bytes) via `modelpin.report.suite.compute_suite_hash`, and prints `suite_version` +
  `suite_hash` in the report header. A golden test pins the hash so accidental scenario
  drift fails CI offline.

## Run it (bring your own key)

```bash
modelpin report --to <new-model> --from <incumbent-model> --provider openai --runs 5
```

Both models are replayed live with **your** API key; the report is written under `reports/`.
