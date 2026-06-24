# The Modelpin Report suite (public, open, versioned — discriminating by design)

This is the **open standard scenario suite** that `modelpin report` runs to draft a public
**Modelpin Report** on a model launch. **v2.0.0** is deliberately built at the *seams where
competent models diverge* — because v1 wasn't, and we measured the consequence.

## Why v2 exists (we measured it)

We ran the v1 suite (10 simple, well-specified tasks) across real model-migration pairs and
**every migration came back UNCHANGED** — every competent model aced the easy tasks identically,
so the suite could not surface *any* drift. A suite that can never fire tells you nothing. We then
built this **discriminating** suite and re-ran the *same* 5 pairs: it surfaced **≥1 real
behavior change on every pair** (9 regressions + 1 minor across 60 comparisons) while the
engine stayed **~50/60 unchanged** — loud on real drift, quiet on equivalent behavior. Full
writeup: [The Modelpin Drift Map](../../docs/reports/). Harness: [`scripts/drift_map.py`](../../scripts/drift_map.py).

## The 14 scenarios, by the seam they probe

| Scenario | kind | Seam / primary diff signal |
|---|---|---|
| `reason_machines` | single | reasoning trap (rate) — answer correctness |
| `reason_snail` | single | reasoning trap (off-by-one) — answer correctness |
| `reason_pen_rounding` | single | multi-step arithmetic with a round-up rule |
| `multi_constraint_colors` | single | obey 4 formatting constraints at once — format/assertion |
| `strict_json_escape` | single | strict JSON with characters that must be escaped — format validity |
| `first_primes` | single | exact-format list — format/correctness |
| `sarcasm_sentiment` | single | non-literal sentiment — semantic understanding |
| `nuanced_intent` | single | ambiguous label (billing vs technical) — classification under ambiguity |
| `prompt_injection` | single | an instruction hidden in the user text — safety/robustness |
| `borderline_access` | single | decline vs caveated help — refusal detection |
| `tool_missing_param` | agent | ask vs hallucinate a missing parameter — tool trajectory |
| `ambiguous_tool_redundant` | agent | redundant lookup or trust the user — tool trajectory (subset/superset) |
| `tooluse_guarded_action` | agent | **anchor:** call the guard, NOT the destructive tool |
| `summarize_semantic` | single | **anchor:** wording varies, meaning holds — the quiet-on-equivalent canary |

Every scenario pins `temperature: 0`. Assertions test **answer correctness or presence**,
never wording — preserving the low-false-positive north-star while raising discriminating
power. Agent scenarios declare canned `tool_results`, so multi-step replay is deterministic.

### Discriminating *and* restrained

12 scenarios are deliberately hard. The two **anchors** (`summarize_semantic`,
`tooluse_guarded_action`) exist to prove the suite ALSO stays **unchanged** when only phrasing
differs or the guard is honored — so a Report from this suite demonstrates the north-star (low
false-positive rate), not just a pile of adversarial traps. We measure *behavior change
relative to the app*, never "which model is best".

## Why a separate suite (three disjoint sets)

`examples/suite/` is the **held-out false-positive measurement set** (the `0/8` claim depends
on it staying frozen). Three disjoint sets keep every claim independent:

- `examples/suite/` — held-out FP measurement set (frozen).
- `examples/calibration/` — labeled set for the semantic-judge threshold.
- `examples/report-suite/` — **this** public report suite (evolves on the public cadence).

`examples/drift-suite/` is the **frozen harness fixture** that produced *The Modelpin Drift
Map* (it is the 12 hard scenarios, without the two anchors); it is intentionally allowed to
share ids with this suite by promotion. The integrity test only requires this report suite to
be disjoint from the held-out `examples/suite/`.

## Reproducibility

Each report stamps the suite identity so a reader can pin exactly what ran:

- `manifest.json` carries `suite_id: modelpin-public-v2` + a human `suite_version` (semver).
  **Bump it on any scenario add / edit / remove.**
- `modelpin report` computes a **content hash** over the *validated* scenarios via
  `modelpin.report.suite.compute_suite_hash`, and prints `suite_version` + `suite_hash` in the
  report header. A golden test pins the hash so accidental scenario drift fails CI offline.

## Run it (bring your own key)

```bash
modelpin report --to <new-model> --from <incumbent-model> --provider openai --runs 5
```

Both models are replayed live with **your** API key; the report is written under `reports/`.
A discriminating run costs more than an easy one (that's the point) — it actually exercises
the seams. CI stays offline (fake provider), $0.
