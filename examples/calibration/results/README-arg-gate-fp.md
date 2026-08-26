# Argument-gate false-positive run — 2026-08-25

`[M]` The first live measurement of MP-04's argument gate. Both transcripts in this directory
are verbatim stdout, unedited.

    scripts/fp_measurement.py --provider google --model gemini-2.5-flash --runs 5 \
        --no-judge --scenarios-dir examples/calibration --role score --repeats 10

Billed to Google Cloud credit via Vertex (MP-104) — no API key. `--no-judge`, because the
argument gate is structural: invoking the semantic judge would have billed a second provider
and added a channel this run is not measuring.

> **`[M] 2026-08-26` — the verdicts recorded below were measured BEFORE the advisory cap.**
> Every `regression` this document attributes to the argument channel alone now reads
> `changed_minor` (ADR-0029). **No rate in this document changes**, because
> `scripts/fp_measurement.py` scores `changed_minor` as a false positive
> (`_FLAGGED = (regression, changed_minor)`). The cap was chosen for what the UNCAPPED gate
> cost the user — a red X on a build that did not break — not for what either shape costs the
> metric, which is nothing. Read every `regression` below as "the argument channel flagged";
> read no `0%` as having improved.

## Headline

```
70 trials attempted
  53  could not have fired at ALPHA   (excluded, ADR-0022)
  10  provider errors                 (a network outage mid-run; never reached a verdict)
   7  SCORED

False-positive rate: 0/7 = 0%
95% upper bound on the true rate: 34.8% (one-sided Clopper-Pearson, n=7)
```

## Read the denominator before the rate

**`[M]` All 7 scored trials are the SAME scenario, `arg_freetext_note`.** Every other `arg_*`
scenario was excluded on every repeat:

| scenario | scored | excluded |
|---|---|---|
| `arg_freetext_note` | **7** | 2 |
| `arg_enum_phrasing` | 0 | 8 |
| `arg_key_order` | 0 | 9 |
| `arg_list_order` | 0 | 9 |
| `arg_multistep_carry` | 0 | 9 |
| `arg_numeric_rounding` | 0 | 8 |
| `arg_optional_fields` | 0 | 8 |

So `0/7` is **seven repeats of one scenario**, not seven scenarios, and the 34.8% bound is not
a scenario-level false-positive rate. More repeats cannot fix this: they add more of the same
scenario. This is the exchangeability caveat MP-82 prints beside the detection bound, in its
sharpest possible form.

**`[M]` It is not a corpus bug — and the reason is not the one first published here.**

> **Corrected 2026-08-25 (MP-105).** This paragraph previously read: *"At temperature 0.7,
> `gemini-2.5-flash` emits **byte-identical tool arguments** for six of seven argument shapes —
> enum choice, key order, list order, numeric rounding and optional fields are all
> schema-constrained, so the model is deterministic regardless of temperature. Only free text
> varies."* **That is refuted by the transcript in this directory.** `[M]` Recounting the
> per-side repertoire columns of
> [`arg-gate-fp-2026-08-25-gemini-2.5-flash.txt`](arg-gate-fp-2026-08-25-gemini-2.5-flash.txt),
> `arg_numeric_rounding` printed `args 1|2` or `2|1` on **5 of its 8** scored-or-excluded
> trials and `arg_optional_fields` on **3 of 8**. The model varied on **three** of seven
> shapes, not one. The claim was inferred from the exclusion table above rather than read off
> the rows underneath it.

All seven scenarios declare `temperature: 0.7` and `providers/google.py:210-211` passes it
through. `[M]` The reason six shapes never scored is **pool overlap, not determinism**: a side
that emitted one payload sits *inside* a side that emitted two, so every channel returns
`p = 1.00` and ADR-0022 excludes the trial as one that could not have fired. **That is the
gate's false-positive defence working exactly as designed.** A scenario needs *disjoint* pools
to score, and low-rate variance produces overlapping ones far more often than disjoint ones.

`[M]` What a different candidate changes is the **rate**, not the kind: on the same seven files
unchanged, `arg_numeric_rounding` emits 1 distinct payload in 16 runs on `gpt-4o-mini` and 7–9
on `gpt-4.1-mini`. See `examples/calibration/README-arguments.md` and the
`arg-repertoire-*.json` artifacts beside this file.

## `[M] 2026-08-25` The gate's added false positives, priced OFFLINE

The live run above could not price condition 6 — it scored 7 trials, all one scenario. But the
dominant term never needed a key. `arg-repertoire-*.json` in this directory commits payload
**frequencies**, and [`scripts/arg_gate_price.py`](../../../scripts/arg_gate_price.py) deals
those actual runs into two disjoint sides and drives the **real** `diff_scenario`, classified
by the repo's own ADR-0022 predicate. Both sides come from one model's own runs, so every
non-`unchanged` verdict is a false positive by construction.

Artifact: [`arg-gate-price.json`](arg-gate-price.json) — 15 repertoire cells, 3,000 replicates
each, `git_sha` and seed recorded.

| | split-half FP rate (of scored) |
|---|---|
| Worst cell: `arg_optional_fields`, `gpt-4.1-mini`, `--match subset`, **N=3** | **4.18%** (66/1580) |
| `arg_freetext_note`, `gpt-4.1-mini`, `strict`, N=6 | 3.58% (76/2122) |
| `arg_numeric_rounding`, `gpt-4.1-mini`, `strict`, N=5 | 3.19% (53/1664) |
| Range across the 26 non-zero cells of 48 | **0.08% – 4.18%** |

`[M]` **Against 0/0 on `main`**, where the harness prints its *** THIS RUN MEASURED NOTHING ***
banner because nothing under `modelpin/diff/` reads `.arguments`. The right comparison is an
absolute **addition** of false-positive mass, not a ratio.

### Three things this changes about how the gate should be described

**1. Disjointness is not the firing condition.** `[M]` It is necessary, not sufficient. At N=5,
`a,a,a,a,a` vs `b,b,b,b,b` fires at p=0.0079 and `a,a,a,b,b` vs `c,c,c,d,d` at p=0.0159 — but
`a,a,a,b,c` vs `d,d,e,f,g` is *fully disjoint* and does **not** fire (p=0.0873), and ten pooled
distinct payloads give p=1.0 and are excluded outright. `permutation_pvalue_distribution` is
relabeling-invariant, so at maximum jitter the gate goes **silent**. The risk lives in the
middle: sides concentrated enough to look decisive, different enough across sides to be disjoint.

**2. The match modes invert, so a number quoted for one mode is a false zero for the other.**
`[M]` At N=3 the equivalence modes cannot fire at all (0.00%) while `subset` reaches 4.18%; by
N=5 that reverses and `subset` falls to 0.00% while `strict` runs at ~3%.

**3. Where the N-curve peaks is an artifact of the estimator.** `[M]` Without replacement
(headline) `arg_freetext_note` peaks at N=6; with replacement the same 16 runs peak at N=5. An
earlier draft of this analysis published "`runs: 5` is the FP peak" as a finding — it is a
property of the resampling scheme, not of the gate. Both estimators are reported side by side
for that reason.

### What the number is not

- `[A]` **Not a rate for `mp check` as shipped.** The synthetic traces carry no assertions, no
  refusal and no judge, so this is the argument channel in isolation. The shipped verdict is an
  OR across five channels, so the gate's *marginal* contribution there is smaller.
- `[M]` **One false-positive mode only** — sampling noise against a same-model null. It
  structurally cannot see the mode recorded above, two *different* models emitting
  different-but-equivalent payloads (`3.36` vs `3.357` kg). That mode remains unpriced.
- `[M]` **Three scenarios, not seven.** Four shapes emit one payload in 16 runs on every model
  measured and contribute 0 to both numerator and denominator. MP-105's exchangeability caveat
  carries forward: replicates buy resolution, never scenario coverage.
- `[M]` **Weighting is a choice.** At `strict`, N=5 the same data reads 2.11% replicate-pooled,
  1.69% scenario-mean over the varying shapes, and 0.68% scenario-mean over all seven. The
  artifact publishes all three rather than picking one.
- `[M]` **No interval over replicates.** They are draws from an assumed population; a
  Clopper-Pearson bound over them shrinks with compute (3.82% at 1k replicates, 3.27% at 4k at
  a fixed 2.8% point estimate) and `upper_bound_95` raises `OverflowError` past n≈3,000 by
  design. `[M]` The binding uncertainty is the repertoire estimate itself: Good-Turing puts
  **31.2% / 37.5% / 43.8%** of the payload mass on values the 16 runs never saw, and truncating
  that tail moves the rate in **both** directions — it raises within-side concentration while
  lowering cross-side disjointness. Nothing here may be called conservative.
- `[A]` **Exchangeability is untestable for these artifacts.** They store counts. `arg_repertoire.py`
  now also records `payload_sequence`, so runs measured from here on can be checked for drift.

### What it means operationally

`[A]` A user whose suite has J scenarios carrying a jittering argument sees a per-check false
alarm with probability `1-(1-r)^J` on the **unconditional** rate (2.20% at the worst cell), not
the conditional one: **8.5% at J=4, 16.3% at J=8**. Both J and the independence are assumptions.
`[M]` An earlier version of this estimate quoted ~13% by compounding a conditional rate; that is
the wrong denominator.

## What this DOES support

- `[M]` **Zero false positives in 7 scored trials**, on the one shape that varies. The gate saw
  genuine run-to-run variance (`args 5|3`, `4|5`, `3|4`) and stayed `unchanged` every time: the
  disjointness requirement held against real model output, not just synthetic pools.
- `[M]` **Schema-constrained arguments rarely reach disjoint pools on this model**, so they
  seldom fire the gate — a narrower blast radius than the synthetic analysis feared. Stated as
  a rate, not an absolute: each of those six scenarios contributed only 8–9 trials here, and
  `[M]` two of them *did* emit a second payload. "Never fires" is not what n≈8 can support.

## What this does NOT support, and must not be quoted as

- A false-positive rate for the argument gate. `n=7` from one scenario is not that.
- Anything about a model other than `gemini-2.5-flash`, or a temperature other than 0.7.
- #38's condition 6. It remains **open** — but `[M]` **not for the reason first published
  here.** The corpus does not need replacing: it varies on three shapes, and MP-54 delivered
  what it was asked for. What blocks the number is that a scenario must reach *disjoint* pools
  to score, which at these variance rates is rare. Pricing condition 6 needs a candidate
  measured to vary more (`gpt-4.1-mini`, per MP-105) and enough repeats to reach disjointness —
  and it can only be run **after this PR merges**, since `main` has no argument signal at all.
- Any conclusion that a higher-variance candidate is straightforwardly better. `[M]` This
  repository already records disjoint rounding jitter (`3.36` vs `3.357` kg) scoring
  `regression` at confidence 0.992 — a **false positive**. More scorable trials is not a lower
  rate, and the rate is the north-star metric.
