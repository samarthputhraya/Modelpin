# Engine Review Brief — the false-positive foundation

> **Purpose.** This brief focuses a deep review (cloud `/code-review ultra`, a local
> multi-agent pass, or a human reviewer) on the ~520 lines that carry Modelpin's entire
> trust claim. Read it before reviewing `modelpin/diff/`. It states *what a finding is here*,
> *where the load-bearing seams are*, and — critically — *which choices are intentional and
> must not be "fixed."* A reviewer who "corrects" the deliberate items below will quietly
> break the false-positive guarantee.

## The claim under review

Modelpin's one falsifiable promise is: **"if Modelpin says it broke, it broke."** The
north-star metric is the **false-positive rate**. Everything in `diff/` exists to flag a real
behavior change while staying quiet on model nondeterminism.

So in this code, **a "finding" is anything that could:**
1. **Manufacture a regression** that isn't real (false positive) — the cardinal sin.
2. **Hide a real regression** (false negative) — the safe-direction failure, but still a finding.
3. **Misrepresent the verdict** — confidence, explanation, or signals that don't match what
   the math actually concluded.
4. **Break determinism / reproducibility** — public reports must regenerate identically.

Ordinary style nits matter far less here than any of the above. Rank by *effect on the claim*.

## Files in scope (and nothing else)

| File | Role | Lines |
|---|---|---|
| `modelpin/diff/stats.py` | Exact two-sample permutation test (no SciPy). **The heart.** | ~133 |
| `modelpin/diff/__init__.py` | Verdict orchestration: gating, direction, confidence, signal combination. | ~209 |
| `modelpin/diff/structural.py` | Per-run signal extraction: tool trajectory, refusal, assertions. | ~111 |
| `modelpin/diff/semantic.py` | LLM-as-judge equivalence signal (optional, injected). | ~72 |

Refusal *text* detection lives in `modelpin/providers/_common.py` (sets `Trace.refused`); it is
adjacent but secondary here — the engine consumes the boolean.

## Threat model, file by file — attack these

### `stats.py` — the permutation test
- **p can never be 0.** `_splits` deliberately includes the observed labeling so `hits >= 1`
  and `p = hits/total >= 1/total`. Confirm this holds on **both** the exact path (`n <= 16`,
  `combinations`) **and** the sampled path (`n > 16`, seeded `random`). A `p=0.0` here is a
  historical CRITICAL bug — verify it cannot recur (e.g., empty pool, `total==0`, float paths).
- **Small N.** Default 3–5 runs/side. Walk N=2 per side. Are p-values sane at the smallest
  pools the CLI allows (`--runs` min is 2)?
- **Ties / all-identical samples.** Both `permutation_pvalue_*` short-circuit to `1.0` when the
  observed effect is `<= 0` (mean) or `<= 0` TVD (distribution). Confirm identical baseline≡
  candidate ⇒ `p = 1.0`, never a spurious flag. Check the `_EPS` comparisons at the boundary.
- **One-sided vs two-sided.** `permutation_pvalue_mean` is **one-sided** (only a *rise* in a bad
  rate counts) by design; `permutation_pvalue_distribution` is **two-sided** (any categorical
  shift). Verify the statistic and the `>= observed - _EPS` comparison match those intents.
- **Sampled path correctness & cost.** `target = min(SAMPLED_PERMUTATIONS, comb(n, k))`, dedup via
  a `set`. Is the loop guaranteed to terminate, and does it avoid a coupon-collector stall when
  `target` approaches `comb(n, k)`? Is the seed (`_SAMPLING_SEED=0`) enough for determinism?
- **Exchangeability.** The permutation null assumes baseline and candidate runs are exchangeable
  under H0. Is anything fed in that violates that (e.g., ordered/correlated runs)?

### `__init__.py` — verdict gating
- **The gate is `p <= ALPHA` AND an effect-size floor**, per signal. Confirm *both* conditions are
  required everywhere a verdict fires (`tool_regressed`, `refusal_regressed`, `fmt_drift`,
  `semantic_diverged`) — a missing `and` would be a false-positive hole.
- **Direction.** Refusal fires only on a *rise* (`refusal_delta >= MIN_REFUSAL_DELTA`); tool-call
  TVD is **direction-agnostic** (any shift). Is that the intended asymmetry? Should a refusal-rate
  *drop*, or a beneficial tool change, ever be a `regression`? (Currently: no / it can.)
- **Match-mode seam (scrutinize hard).** `diff_scenario` buckets tool sequences with
  `canonical_sequence(seq, mode)`. For `subset`/`superset`, `canonical_sequence` **falls back to
  the strict ordered key** and `trajectory_match` (which implements real subset/superset
  semantics) is **never called in the verdict path.** Net effect to verify: do `--match subset`
  and `--match superset` actually loosen the verdict, or do they behave like `strict`? If a
  candidate legitimately *drops* a tool call under `subset`, does the distributional test still
  fire a regression it was meant to suppress? This is an FP-relevant gap if so.
- **Confidence semantics.** `regression`/`changed_minor` ⇒ `1 - min(firing p-values)`;
  `unchanged` ⇒ `min(p across all signals)`. Confirm the explanation/signals reported match the
  signal that actually fired.
- **Signal combination.** A `regression` from any hard signal must not be downgraded by a later
  `changed_minor` path; confirm precedence (`fmt_drift` only sets `changed_minor` when not already
  a regression).

### `structural.py` — signal extraction
- `trajectory_match` for the four modes (`strict`/`unordered`/`subset`/`superset`) — Counter math
  for `subset`/`superset` (`not (cand - base)` / `not (base - cand)`). Off-by-one on multiplicity?
- `violates_text_assertions` is exact, case-sensitive substring containment — intended as a basic
  format stand-in. Note brittleness, but it is the scenario's explicit contract.
- `modal_sequence` / tie-breaking when two sequences are equally common (Counter order).

### `semantic.py` — the judge signal
- Every output is compared to the **modal baseline output**; text identical after
  whitespace/case normalization **skips the judge** and counts equivalent. Confirm the skip can't
  mask a meaning change, and that normalization (`_WHITESPACE`, `strip().lower()`) is safe.
- Baseline runs are scored against their *own* mode, so the candidate is judged relative to the
  baseline's natural spread (delta), not an absolute bar. Verify this is what `__init__.py`
  consumes (`semantic_delta = mean(cand) - mean(base)`).
- Single judge, one-directional `equivalent(reference, output, task)` call. Known limitation.

## Intentional choices — DO NOT "fix" these

These look like bugs to a fresh reviewer and are deliberate. "Correcting" any of them breaks the
FP claim. Flag them only if you can show a *concrete* failing case, not on principle.

- **`confidence = min(p)` for an `unchanged` verdict** — identical traces ⇒ `1.0`; a borderline
  near-miss ⇒ low. **Do NOT invert to `1 - p`.**
- **Conservative effect-size floors** (`MIN_TOOL_TVD=0.5`, `MIN_REFUSAL_DELTA=0.34`,
  `MIN_SEMANTIC_DELTA=0.5`, `ALPHA=0.05`). Biased toward *missing* a borderline change rather than
  inventing one. **Tune only with real calibration data — never by guessing a "better" number.**
- **Latency/token deltas are informational** and never gate the verdict (latency is jittery; a
  token bump is not a behavior regression).
- **One-sided refusal test** (only a *rise* is a regression) — deliberate.
- **No live API calls in tests** — the judge is injected; the offline `fake` path makes zero
  network calls. Don't propose tests that hit a real provider.
- **`MIN_SEMANTIC_DELTA` is calibrated, not arbitrary** — see `examples/calibration/` and
  `docs/fp-measurement.md`. Its limitations (small N, synthetic perturbations, OpenAI-only judge)
  are already documented; re-flagging them as "untrusted" adds nothing — proposing the *specific
  next calibration* does.

## Severity rubric (tuned to this product)

- **CRITICAL** — can manufacture a regression (false positive) or return `p=0.0`/break the gate;
  any path that makes the engine cry wolf.
- **HIGH** — can hide a real regression (false negative), misreport the verdict/confidence, or
  break determinism/reproducibility.
- **MEDIUM** — match-mode/signal behavior that diverges from documented intent (e.g. the
  `subset`/`superset` seam) without a clean FP/FN, or a perf cliff.
- **LOW** — naming, comments, micro-structure with no effect on the claim.

## Out of scope

CLI plumbing, providers/adapters (except the `Trace.refused` contract), the report renderer, the
scenario suites, and packaging. Keep the lens on `diff/`.
