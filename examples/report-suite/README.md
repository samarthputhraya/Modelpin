# The Modelpin Report suite (public, open, versioned — discriminating by design)

This is the **open standard scenario suite** that `modelpin report` runs to draft a public
**Modelpin Report** on a model launch. **v2.0.0** is deliberately built at the *seams where
competent models diverge* — because v1 wasn't, and we measured the consequence.

## Why v2 exists (we measured it)

We ran the v1 suite (10 simple, well-specified tasks) across real model-migration pairs and
**every migration came back UNCHANGED** — every competent model aced the easy tasks identically,
so the suite could not surface *any* drift. A suite that can never fire tells you nothing. We then
built this **discriminating** suite and re-ran the *same* 5 pairs: it returned 9 `regression`
and 1 `changed_minor` verdicts across 60 comparisons and stayed **unchanged on the other 50**
— loud on real drift, quiet on equivalent behavior. The report's own accounting marks **3 of
those 9 as soft**, two of them artifacts of a since-fixed bug in our refusal detector, and
names the one pair whose flags do not survive a read of the raw traces. Full
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

## Why a separate suite (four declared roles)

A threshold tuned on the same scenarios a result is scored on makes that result in-sample, so
every set in `examples/` declares a **role** in [`../roles.json`](../roles.json) — machine-
readable, and enforced by `tests/test_suite_roles.py` (ADR-0025):

- `examples/suite/` — **`score`**: the held-out FP measurement set (frozen). Never tuned on.
- `examples/calibration/` — **two roles, not one**. The six semantic files are **`fit`** (where
  `MIN_SEMANTIC_DELTA` was tuned); the seven `arg_*.json` files are **`score`** — see
  [`../calibration/README-arguments.md`](../calibration/README-arguments.md). No threshold may
  ever be fitted on the `arg_*` set.
- `examples/report-suite/` — **`public`**: **this** suite (evolves on the public cadence).
- `examples/drift-suite/` — **`fixture`**: the frozen harness copy that produced *The Modelpin
  Drift Map* (the 12 hard scenarios, without the two anchors). It is intentionally allowed to
  duplicate this suite; duplicating a `fit` or `score` set is forbidden.

**What is actually enforced** (`tests/test_suite_roles.py`), since this section previously
understated it: this suite must be disjoint from **both** `fit` and `score` — which is all 13
calibration ids as well as the 8 held-out ones — every scenario file in `examples/` must
declare a role at any depth, no two roles may share scenario *content* (not just ids) except
the `public`/`fixture` pair above, each scenario's role is pinned individually, and
`manifest.json`'s scenario list must agree with `roles.json`.

## Reproducibility

Each report stamps the suite identity so a reader can pin exactly what ran:

- `manifest.json` carries `suite_id: modelpin-public-v2` + a human `suite_version` (semver).
  **Bump it on any scenario add / edit / remove.**
- `modelpin report` computes a **content hash** over the *validated* scenarios via
  `modelpin.report.suite.compute_suite_hash`, and prints `suite_version` + `suite_hash` in the
  report header. A golden test pins the hash so accidental scenario drift fails CI offline.

## Run it (bring your own key)

```bash
modelpin report --to <new-model> --from <incumbent-model> --provider openai --runs 5 \
  --suite-dir examples/report-suite
```

Both models are replayed live with **your** API key; the report is written under `reports/`.
A discriminating run costs more than an easy one (that's the point) — it actually exercises
the seams. CI stays offline (fake provider), $0.
