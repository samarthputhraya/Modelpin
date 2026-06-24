# Modelpin — Phase 0 status & next steps

> Durable handoff so any session can resume from the repo (not from chat memory).
> Read this with `CLAUDE.md` and `docs/Modelpin-Engineering-Context-Pack.md` (the spec).

## ▶ Resume here (latest — 2026-06-24)

**🧪 THESIS VALIDATED + v2 DISCRIMINATING SUITE (latest — PR #3, commit `596825c`).** Ran a
multi-model **drift map** to answer the make-or-break question (*do model migrations break
app-relevant behavior?*). Key finding sequence: the easy public suite returned **all-unchanged
across 5 real migrations** (false comfort — easy tasks every model aces identically), so it
could not test the thesis. A **hard, discriminating suite** (seams where models diverge) then
surfaced **≥1 real change on every pair** (9 regressions + 1 minor / 60) while the engine stayed
**~50/60 unchanged** — loud on real drift, quiet on equivalent behavior. Real verified catches:
an agent that **hallucinated a flight booking** vs one that asked (gpt-3.5→4o-mini, tool
trajectory); **prompt-injection behavior flipping across a version bump** (gpt-4o refused, gpt-4.1
engaged — "PWNED"); a **reverse-alphabetical constraint breaking** on 4o→4.1. **Thesis holds: the
wedge is real.** Landed via a **parallel multi-agent "org" operation** (4 departments: diff-eng,
product/suite, editorial, GTM) + a **QA panel** (code-review + guardrail/FP audit + data
fact-check); all QA findings integrated. Shipped:
- **fix(diff):** refusal detector now folds apostrophe glyphs (U+2019 curly "can't" dodged the
  ASCII markers → spurious refusal-rate "regression" on identical declines); dropped the truncated
  `"i'm sorry, but i can"` marker (misfired on "I'm sorry, but I can help"). `providers/_common.py` + 5 tests.
- **feat(suite):** `examples/report-suite` promoted to **v2** (`modelpin-public-v2` 2.0.0, hash
  `sha256:ffd99774f681`, **14 scenarios**) — 12 discriminating + 2 quiet anchors; dropped the 8 easy
  v1 scenarios. Golden-hash/count/manifest tests updated. The easy v1 suite gave false comfort.
- **feat(harness):** `scripts/drift_map.py` (multi-model migration replay, cached/rate-paced/
  cross-vendor + judge diff). `examples/drift-suite/` = the frozen 12-scenario Report-#1 fixture.
- **docs:** **The Modelpin Drift Map #1** (`docs/reports/modelpin-drift-map-1.md`) + published raw
  data (`docs/reports/data/`) + launch assets (`docs/launch/`). Measurement/opinion framed; honestly
  discloses the refusal-detector bug we caught in our OWN measurement. Old gpt-4.1-vs-gpt-4o report
  marked superseded (v1 easy suite). **167 tests pass; ruff+black clean.** PR #3 (open).
- **Product insight (founder-level):** *scenario quality IS the product* — an easy suite reassures
  you for the wrong reason; the tool's value is bounded by whether the user's scenarios hit their
  app's seams. Onboarding (help users author discriminating scenarios) is the wedge.

**🆕 `mp report` (the public Modelpin Report capability) is BUILT** — the last unbuilt box in
the CLAUDE.md build order is done (uncommitted, on `main`; branch + commit next). `modelpin
report --to <new> --from <incumbent>` runs an **open public suite** across both models (live,
BYO-key) and writes a **reproducible, opinion-framed Markdown report + a JSON audit sidecar**
under `reports/`. Built this pass (research→plan→TDD→4-reviewer adversarial pass→fixes):
- **New public suite** `examples/report-suite/` (10 scenarios + `manifest.json` + README),
  **strictly disjoint** from the held-out FP set `examples/suite/` (a CI test enforces id
  disjointness, so the 0/8 FP claim stays independent). Versioned (`suite_version 1.0.0`) +
  content-hashed (`sha256:1c25c111a296`, pinned by a golden test).
- **`modelpin/report/__init__.py`**: `render_report_md(results, meta)` + `to_report_sidecar`
  (both PURE — date/hash/version/thresholds injected via a frozen `ReportMeta`, golden-testable
  offline) + `modelpin/report/suite.py` (`compute_suite_hash`/`slug`/`read_manifest`).
- **`report()` wired in `cli.py`** reusing all of `check()`'s plumbing; **exits 0 even on a
  regression** (it publishes, it doesn't gate CI); per-scenario `ProviderError` → skip + disclose.
- **Guardrails enforced + tested:** measurement/opinion framing ("on our open suite … we
  observed…"), a **banned-words** test over rendered output AND over the suite ids/names/tools,
  reproducibility block (suite hash, models, runs, judge, thresholds, version, date, reproduce
  cmd), mandatory limitations + disclaimer, skipped-scenario disclosure. **No new deps** (stdlib
  hashlib/json + the existing renderer pattern; agentevals/jinja2/canonical-json all evaluated → skip).
- **28 new offline tests (163 total pass; ruff + black clean).** Zero live API calls in tests.
- **Still gated:** *publishing* Report #1 needs a real provider model launch — the capability is
  now launch-ready ("published within hours of each major launch"). Prior-art for the format:
  Artificial-Analysis methodology disclosure, lm-eval-harness task-versioning, HELM raw-data
  release (see the research synthesis in this session's workflow).

**🚀 PHASE 0 COMPLETE + LAUNCHED.** `modelpin 0.1.1` is **LIVE on PyPI**
(`pip install "modelpin[providers]"`); PR #1 + PR #2 merged to `main`; tags `v0.1.1` **and**
`v1` both point to the 0.1.1 commit (`40cb86f`, which has the root `action.yml`), so
`uses: samarthputhraya/modelpin@v1` resolves. Built + shipped: OpenAI adapter (live) +
OpenAI-compatible hosts (Groq/Llama, live), Google/Gemini (live cross-vendor), Anthropic stub;
semantic LLM-judge (calibrated → CI-failing regression); multi-turn replay; 8-scenario suite;
FP + calibration harnesses; **real GitHub Action — dogfood-validated LIVE on PR #2** (green check
+ sticky PR comment). **135 tests pass; ruff + black clean.**

- **What was necessary is DONE.** Sequence completed this session: cross-vendor (gemini-3.1 +
  Groq/Llama) → semantic calibration + judge promotion → OpenAI-compatible providers → real
  GitHub Action + PyPI packaging → published 0.1.0 → dogfood validated → fixed header + Node-24
  action deps + gitignore → published **0.1.1** → tagged (fixed a stale `v1` that pointed at a
  pre-`action.yml`-move commit; re-pointed `v1` → 0.1.1). **Remaining is all OPTIONAL** — see
  "Remaining (optional)" at the end of this block.

- **Action validated live (PR #2):** the dogfood workflow ran on GitHub infra → installed the
  pkg, ran `mp check`, posted the **sticky PR comment**, set a **green** status. The dogfood
  surfaced + fixed two real issues: (a) the PR-comment header was always 🚨 "model change
  detected" even when unchanged → now ✅/⚠️/🚨 by actual outcome (`report/__init__.py`, +3 tests);
  (b) `.modelpin/` was fully gitignored so the documented "commit your baseline" flow was broken
  → now `.modelpin/*` + `!baseline-*.json`. Also bumped Action deps off Node-20
  (`checkout@v7`, `setup-python@v6`, `github-script@v9`). The dogfood workflow is **manual**
  (`workflow_dispatch`) so it doesn't spend API on every PR; the per-PR pattern for real apps is
  in `examples/github-workflow.yml`. **These all live on branch `dogfood/modelpin-self-check`
  (PR #2) — merge it to land them on main.**

- **DoD met:** held-out FP rate = **0/8 = 0%** (judged), regressions detected. See
  [`fp-measurement.md`](fp-measurement.md).
- **Full 8-scenario cross-vendor DONE:** `mp check` gpt-4o-mini → **`gemini-3.1-flash-lite`**,
  5 runs × 8 scenarios, OpenAI judge on → **8/8 unchanged @ confidence 1.00**
  (tool-call match 1.00, semantic equivalence 1.00, refusal delta 0.00 on every scenario).
  The cross-vendor judge genuinely fired and found the two vendors behaviorally equivalent
  on the held-out suite. Paced at ≤14 Gemini calls/min (under the 15 RPM cap); one transient
  429 was absorbed by backoff. (Earlier smoke: gpt-4o-mini → gemini-2.5-flash-lite, 2 scn.)
- **Two live-surfaced bugs found + fixed (both Gemini-3.x tool-loop):**
  1. *Tool loop aborted on a `400`* — Gemini 3.x requires the opaque `thought_signature`
     (and function-call `id`) to be **echoed back** when a call is fed into the next turn;
     the adapter rebuilt the model turn from scratch and dropped them. Fixed in
     `providers/google.py` `_model_turn_content` (preserve both; omit when absent so the
     2.5 path is unaffected). 2.5 never required this — pure cross-version drift.
  2. *`mp baseline --provider google` crashed on persist* — the (now-preserved) signature
     is raw, non-UTF-8 **bytes** living in `Trace.messages`, which `save_baseline`'s
     `model_dump(mode="json")` couldn't serialize (`UnicodeDecodeError`). Fixed at the model
     boundary (`models.py`): a `field_serializer` base64-encodes any bytes in `messages` on
     dump (round-trips losslessly); in-memory stays raw bytes for the SDK feed-back.
  Both are regression-tested (mutation-verified to have teeth) + live-proven end-to-end.
  An adversarial review workflow also hardened the OpenAI adapter's `tool_call_id` echo test.
- **Git:** all work merged to **`main`** (PR #1 + PR #2). Tags `v0.1.1` + `v1` pushed (both →
  `40cb86f`). Repo: github.com/samarthputhraya/modelpin. (Note: the "Where we are (main)" section
  far below is the *original kickoff* state — superseded by this block.)

**Done this phase (all ✅):** cross-vendor on gemini-3.1-flash-lite + free Groq/Llama 3rd vendor →
semantic-threshold calibration + judge promoted to CI-failing `regression` → OpenAI-compatible
providers → real **GitHub Action** (sticky PR comment + fail-on-regression) + PyPI-ready packaging →
**published `0.1.0` then `0.1.1`** → Action **dogfood-validated live** on PR #2 (green + sticky
comment) → fixed the alarmist PR-header (now ✅/⚠️/🚨 by outcome) + bumped action deps to Node-24
majors + fixed the `.modelpin/` gitignore (baselines are now committable) → **tagged `v0.1.1` + `v1`**
(re-pointed a stale `v1`). The DoD (held-out FP 0/8) was met earlier this phase.

**▶ NEXT STEP: commit the `mp report` work, then publish Modelpin Report #1 on the next launch.**
The *build* half of the rule-defined next step is now ✅ DONE (see the 🆕 block at the top): the
public Reporter — the last unbuilt box in the `CLAUDE.md` build order — is implemented behind
`mp report`, with the open suite + reproducible generator + trust guardrails, all TDD'd offline.
What remains:
1. **Commit** the uncommitted `mp report` work (branch off `main`, conventional commit, PR like #1/#2).
2. **Publish Report #1** — *gated on a real provider model launch* (spec §11 DoD 2nd half / the
   go/no-go signal: *"Publish Modelpin Report #1 on the next model release"*). When a major model
   drops, run `modelpin report --to <new> --from <incumbent>` on the open suite (BYO-key), commit
   the generated `reports/*.md`, and post it (HN / r/LocalLLaMA / X). The harness is launch-ready now.
3. Optional report polish (do anytime): a `docs/reports/` archive + a static site later; a
   `--baseline-from` flag to halve replay cost; richer per-signal rollups; expand the suite (bump
   `suite_version` + the pinned hash together). Honor the trust guardrails throughout (reproducible;
   measurement/opinion framing — "on our open suite we observed…", never "Model X is worse").

**Optional polish (NOT in the roadmap — do anytime, none blocking):**
1. **GitHub Marketplace** listing — publish from the `v1` release page (root `action.yml` ✓).
2. **CHANGELOG.md** + a `v0.1.1`/`v1` GitHub release note.
3. **License-metadata cleanup** (silence setuptools deprecation): `license = "Apache-2.0"` SPDX
   string, drop the `License ::` classifier, `setuptools>=77`.
4. **Anthropic adapter** (needs a paid key — DoD doesn't require it; you already have 3 vendors);
   retire stale `regression_threshold`; subset/superset + cost/latency into the verdict; expand the
   semantic calibration set (≥30 pairs, real migration traces, a non-OpenAI judge).

**Windows gotcha (durable):** `mp` is a built-in **PowerShell** alias for `Move-ItemProperty`, so
`mp <cmd>` runs the alias, not the CLI. Use `modelpin <cmd>` (or `mp.exe`, or `Remove-Item Alias:mp`).
The `mp` alias is fine in cmd/bash/zsh. Documented in the README quickstart.

**Semantic-judge calibration & promotion (DONE 2026-06-24).** Built a labeled calibration set
[`examples/calibration/`](../examples/calibration/) — **distinct from the held-out suite** so it
doesn't leak into the 0/8 claim — + harness `scripts/calibrate_thresholds.py`. Result: at
`MIN_SEMANTIC_DELTA=0.5`/`ALPHA=0.05` the semantic signal cleanly separates equivalent (delta 0.0)
from real meaning changes (delta ≥0.8) with **0 false positives**, so a consistent semantic
divergence is now a **CI-failing `regression`** (was capped at `changed_minor`). FP-safety
confirmed across **3 independent conditions**: self-judge calibration (0/6), **independent-judge**
calibration (gpt-3.5-turbo candidate ⊥ gpt-4o-mini judge → 0/6 at the floor; the run of record),
and the **post-promotion held-out re-validation** (`fp_measurement.py`, semantic→regression live →
**still 0/8**, detection improved). An adversarial audit (`backtest-honesty-checker`) flagged
self-judging circularity (→ defused by the independent-judge run), small N, and synthetic
perturbations; its KEEP verdict was conditioned on the evidence being committed — it is
(`examples/calibration/results/`). **Limitations (documented, not oversold):** N=6+6, synthetic
perturbations, recall 4/6 on subtle changes (the safe direction — a miss is a false *negative*),
OpenAI-only judge. **Future:** ≥30 pairs incl. real migration traces + a non-OpenAI judge before
high-stakes reliance. Committed (`feat:`) — see git log.

**How to run live in this environment** (corporate proxy + BYO-key):
- Keys are in `.env.local` (gitignored): line 1 = raw OpenAI key (`sk-…`), a
  `GEMINI_API_KEY=…` line for Gemini. Load both before a run, e.g. parse each line —
  `KEY=VALUE` → that var; a bare `sk-…` → `OPENAI_API_KEY`.
- `pip install` needs `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- The SDKs need the OS trust store, so invoke the CLI through a truststore shim:
  `python -c "import truststore; truststore.inject_into_ssl(); from modelpin.cli import main; main()" <args>`.
- Gemini free-tier **RPM caps** (AI Studio → Rate Limit): 2.0-flash = 0 (unavailable),
  2.5-flash = 5, **2.5-flash-lite = 10, 3.1-flash-lite = 15**. Stay under them (small/paced
  runs); 429 = our rate, 503 = transient Google capacity (retry).

**Free 3rd vendor via an OpenAI-compatible host (no paid key — Anthropic deferred).** The
OpenAI adapter now takes a `base_url`, so free Llama hosts that speak the OpenAI Chat
Completions API are first-class providers: `groq | openrouter | together | cerebras`
(`providers/openai.py` `OPENAI_COMPATIBLE_PROVIDERS`). **Recommended: Groq** (free tier, fast,
serves Llama 3.x/4, tool calls). To use: get a free key at console.groq.com → add
`GROQ_API_KEY=…` to `.env.local`, then a 3-vendor check is e.g.
`mp check --provider groq --from gpt-4o-mini --to llama-3.3-70b-versatile --config <cfg>`
(OpenAI judge arbitrates, same as the Gemini cross-vendor path). Caveat: open-model *hosts*
don't retire on a lab's schedule like the big-3 do (they do rotate hosted ids), so this is a
real bonus cross-vendor target + architecture proof, not the core migration wedge.
**✅ LIVE-VALIDATED (2026-06-24):** full 8-scenario `gpt-4o-mini → llama-3.3-70b-versatile`
(Groq) ×5 runs, OpenAI judge → **8/8 unchanged** (judge found the two vendors equivalent on
the held-out suite). 56 Groq calls paced ≤24/min, 1 transient absorbed. **Three vendors now
proven through one engine: OpenAI + Google + Groq/Llama.**

---

## Where we are (branch `main`, pushed to GitHub, private)

- **`33d10d0`** — Phase-0 skeleton + **statistically-hardened behavioral diff** (the moat):
  exact two-sample permutation test (`diff/stats.py`, no SciPy, deterministic);
  verdict gated on `p ≤ 0.05` **and** an effect-size floor (`diff/__init__.py`);
  `strict/unordered/subset/superset` match modes; calibrated confidence.
  Cleanups: Python 3.12, full Apache-2.0 LICENSE, current Anthropic model ids.
- **`f24bd90`** — **CLI wired end-to-end** (`scan/init/baseline/check`), **spec §7 reporter**,
  offline fake-provider demo (`examples/traces/demo_traces.json`), `storage.py`
  baseline persistence, fix for a Windows `UnicodeEncodeError` crash.
- A 13-agent adversarial audit of the diff engine found + fixed **1 CRITICAL**
  (sampling fallback returned `p=0.0`); two proposed "fixes" were verified wrong and rejected.
- **31 tests passing; ruff + black clean.**

Run the offline demo:
```
mp baseline --provider fake --fixtures examples/traces/demo_traces.json \
  --model claude-opus-4-6 --scenarios-dir examples/scenarios --config examples/modelpin.yaml
mp check --to claude-opus-4-7 --from claude-opus-4-6 --provider fake \
  --fixtures examples/traces/demo_traces.json --scenarios-dir examples/scenarios --config examples/modelpin.yaml
```

## Milestone (Phase 0 Definition of Done — spec §11)

`mp check` detects a genuine regression **between two REAL models** and prints the
PR-style report, **with a measured low false-positive rate on a held-out set**.

## The one gap that blocks the milestone

`modelpin/providers/openai.py` is **implemented** (Chat Completions API, BYO-key from
`OPENAI_API_KEY`, SDK import kept lazy, dependency-injectable client). `anthropic.py`
is still a `NotImplementedError` stub.

**Phase A — live-path hardening: DONE.** A two-workflow adversarial sweep (40-agent
discovery + 22-agent review) found 32 confirmed ungraceful-failure modes on the real
`mp baseline`/`mp check` path; the 13 must-fix-before-live-run items are fixed and the
fixes adversarially re-verified (APPROVE). Highlights: every live `openai.*`/adapter
error now becomes a friendly, **key-safe** `ProviderError` (auth errors never echo the
key; all error text is scrubbed of `sk-`/`Bearer` tokens) instead of a raw traceback;
zero-config no longer routes to the Anthropic stub; `--runs <2` is rejected (a single
run can't form a distribution → false confidence); malformed scenario/YAML/baseline
files fail with named, friendly errors; baseline writes are atomic; the CI exit code is
computed before the (now-guarded) report write; skipped no-baseline scenarios are
surfaced, not hidden. **78 tests pass; ruff + black clean.** The 7 deferred items
(stale `regression_threshold`/`judge_model` knobs, mid-run resume, `--fail-on` knob,
baseline model/provider validation, latency/token deltas in CLI output) are Phase B/C.

**Phase A — live smoke run: DONE (first real two-model run).** Ran the full pipeline
live: `mp baseline` on **gpt-3.5-turbo** then `mp check --to gpt-4o-mini` over the 3
bundled scenarios ×5 runs each (BYO-key from a gitignored `.env.local`, ~45 short
completions, pennies). Verdict: 3/3 **unchanged**, PR-style report written. Real traces
for both models are persisted under `.modelpin/` (calibration data). Findings:
- **No false positives on real data (north-star holds).** gpt-4o-mini fired a 2nd tool
  call (`issue_refund`) in 1/5 `refund_request` runs — a real difference — and the engine
  correctly treated the 1-in-5 blip as noise (TVD 0.2 < the 0.5 floor), not a regression.
- **The semantic blind spot is now empirically proven.** On `invoice_parse` both models
  were semantically identical but textually different ("The total amount is $5." vs "5
  dollars."); the structural diff can't see equivalence → **the LLM-judge is the #1 next
  build**, not a nice-to-have. (The bundled `must_contain:["$"]` assertion is noise — "$"
  appeared in 1/5 of gpt-3.5's outputs and 0/5 of gpt-4o-mini's, yet both are correct.)
- **Single-turn replay can't realize multi-step agent trajectories.** `refund_request`
  expects `[lookup_order, issue_refund]` but replay stops after turn 1 (no tool-result
  loop), so the 2nd call rarely appears. Either make agent scenarios single-step or add a
  (mocked) tool-execution loop to replay.
- **Env/ops:** corporate-proxy TLS — the openai SDK (httpx+certifi) fails with
  `CERTIFICATE_VERIFY_FAILED`; fixed safely with `truststore.inject_into_ssl()` (uses the
  OS trust store; verification stays ON). Worth a built-in opt-in for corporate users.

The DoD is **not fully closed**: still need a held-out FP measurement (known-equivalent
pairs → false-alarm rate; a known-regression pair → confirmed detection) and the judge.

## Next steps (priority order — re-ordered after the live smoke run)

1. **Semantic LLM-judge — ✅ DONE (built + adversarially reviewed: APPROVE_WITH_NITS).**
   `diff/semantic.py` (Judge protocol + per-run divergence flags vs the modal baseline;
   identical text skips the judge), `judge.py` (`OpenAIJudge`, temp-0, BYO-key, key-safe
   `ProviderError`, FP-safe parse default), wired into the verdict in `diff/__init__.py`
   through the *same* permutation test + an effect-size floor (`MIN_SEMANTIC_DELTA=0.5`).
   It is **optional/injected**: `diff_scenario(judge=None)` stays purely structural and
   offline (CI is $0); the CLI builds a judge only when `judge_model` is set and the
   provider isn't `fake`. An uncalibrated judge escalates only to `changed_minor`, never
   a CI-failing `regression`. `judge_model` is wired in config + sample/example YAML.
   **Before promoting semantic → `regression`:** calibrate the judge on a labeled set
   (a systematically-miscalibrated judge can otherwise emit `changed_minor` false alarms
   at high error rates — bounded, but trust-eroding). Still TODO: retire the stale
   `regression_threshold` config field (deferred item 14).
2. **Replay depth — ✅ DONE.** `OpenAIAdapter.run()` (and the Gemini adapter) now drive a
   model↔tool loop up to `MAX_TOOL_TURNS`, feeding back canned `scenario.input.tool_results`
   so multi-step trajectories (`lookup_order` → `issue_refund`) actually emerge. Deterministic
   + offline-testable.
3. **6–8 real scenarios — ✅ DONE.** [`examples/suite/`](fp-measurement.md) has 8 scenarios
   spanning tool trajectories / semantic / refusal / format, with canned `tool_results` and
   no noisy assertions. `examples/scenarios/` stays as the minimal offline demo.
4. **Held-out FP measurement — ✅ DONE (DoD met).** Live judged run
   (`scripts/fp_measurement.py --model gpt-4o-mini --runs 5`, judge on): **false-positive
   rate = 0/8 = 0%** on the held-out suite (gpt-4o-mini vs itself, all `unchanged` @ conf
   1.00). Detection: both perturbations that actually changed behavior were caught
   (`refund_request`→regression, `classify_sentiment`→changed_minor); the `decline_pii`
   injection was resisted by the model (still declined) so `unchanged` was correct — not a
   false negative. Corroborated by 0/4 (golden), 0/6 (real same-model split-half), 0/3
   (real cross-model). Full writeup in [`docs/fp-measurement.md`](fp-measurement.md).
5. **Calibrate the diff thresholds — ✅ DONE for the SEMANTIC floor (judge promoted).**
   `MIN_SEMANTIC_DELTA=0.5` is now calibrated on [`examples/calibration/`](../examples/calibration/)
   (labeled set, distinct from the held-out suite) + harness `scripts/calibrate_thresholds.py`,
   and a consistent semantic divergence now escalates to a **CI-failing `regression`** (was
   `changed_minor`). Validated FP-safe across self-judge, independent-judge, and post-promotion
   held-out (0/8) runs; adversarially audited. See "Semantic-judge calibration" in ▶ Resume here
   + [`docs/fp-measurement.md`](fp-measurement.md). **Still TODO:** the structural floors
   (`MIN_TOOL_TVD`, `MIN_REFUSAL_DELTA`, `ALPHA`) are FP-validated by the held-out suite but not
   yet swept on a labeled set; and expand the semantic calibration set (≥30 pairs, real migration
   traces, a non-OpenAI judge) before high-stakes reliance.
6. **Google/Gemini adapter — ✅ DONE + LIVE-VALIDATED (cross-vendor unlocked).**
   `providers/google.py` via `google-genai` (SDK shapes verified live), BYO-key from
   `GEMINI_API_KEY`/`GOOGLE_API_KEY`, same multi-turn architecture, key-safe errors,
   mock-tested. **Live-validated on `gemini-2.5-flash`:** the text path (correct
   `summarize_ticket` output + token capture) AND the multi-step tool loop —
   `refund_request` produced the full `lookup_order → issue_refund` trajectory and the
   final answer consumed the canned tool data, confirming the `function_response`
   (role="user") feedback is correct. Cross-vendor checks use an OpenAI judge (provider
   inferred from `judge_model`). **✅ FULL 8-scenario cross-vendor run completed:**
   `mp check --provider google --from gpt-4o-mini --to gemini-3.1-flash-lite` over all 8
   suite scenarios ×5 runs, judge on → **8/8 unchanged @ conf 1.00** (tool-call match 1.00,
   semantic equivalence 1.00, refusal delta 0.00 on every scenario; token deltas small/jittery
   and correctly non-gating). The OpenAI judge found gpt-4o-mini and gemini-3.1-flash-lite
   behaviorally equivalent on the held-out suite — "safe to adopt". (Earlier: a 2-scenario
   smoke vs gemini-2.5-flash-lite.) Run paced at ≤14 Gemini calls/min (under the 15 RPM cap);
   60 Gemini calls total, one transient 429 absorbed by backoff. **Two Gemini-3.x tool-loop
   bugs were surfaced + fixed during this run** — (a) `thought_signature`/`id` must be echoed
   back in the loop (else a `400`); (b) those raw signature bytes then crashed
   `mp baseline`'s JSON persist — see the "Two live-surfaced bugs" note in ▶ Resume here.
   The full run is otherwise limited only by Gemini **free-tier RPM** (per AI Studio:
   2.0-flash = 0 RPM/unavailable, 2.5-flash = 5 RPM, **2.5-flash-lite = 10 RPM,
   3.1-flash-lite = 15 RPM**) — use a higher-RPM model + small/paced runs, or enable billing;
   503s are transient Google capacity (clear on retry). **Anthropic** stays a $0 stub until an
   `ANTHROPIC_API_KEY` appears.
7. **Corporate-proxy TLS:** the FP harness opts into `truststore.inject_into_ssl()`; still
   worth a built-in opt-in at CLI startup so devs behind an intercepting proxy don't hit a
   `CERTIFICATE_VERIFY_FAILED` on first run. Verification stays ON (OS trust store).
8. **Diff deepening (offline-doable now):** cost (token) regression as `changed_minor`
   (gate via the permutation test; latency is jittery — handle carefully); an
   "inconclusive / underpowered → re-run" surface (spec §6C); wire `subset/superset`
   + `expected_tool_calls`/`output_schema` assertions into the verdict.
9. **GitHub Action — ✅ DONE (PR comment via API; published).** Root `action.yml` (moved from
   `actions/` so it's Marketplace-eligible; ref `samarthputhraya/modelpin@v1`) is a real composite
   action: install → (optional baseline) → `mp check` → **sticky PR comment** (find-or-update
   via `actions/github-script`, marker `<!-- modelpin-report -->`, no spam) → **fail on
   regression**. Inputs cover `to/from/provider/config/runs/match/comment/fail-on-regression/
   modelpin-spec`; cross-vendor works (set `provider` + the right secret). Docs +
   copy-pasteable workflow: `actions/README.md`, `examples/github-workflow.yml`. **Published:**
   `modelpin 0.1.0` LIVE on PyPI (`pip install "modelpin[providers]"`). Remaining: tag the release,
   the Marketplace publish (root `action.yml` ✓), and a green dogfood CI run.
10. **Smaller audit items:** detector registry-validation (cut regex false positives);
   retire the now-stale `regression_threshold` config field; add the missing per-module
   tests for watcher/report. (Scenario-JSON error handling + config/cli tests: ✅ done.)

## Design guardrails (do NOT undo — see also `~/.claude` project memory)

- Diff confidence for an `unchanged` verdict = `min(p)` is **intentional** (identical
  traces → 1.0; near-α miss → low). Do **not** invert it to `1 − p`.
- The conservative effect-size floors are deliberate for the **false-positive north-star**;
  tune them only with real-trace calibration data, never by guessing.
- BYO-key everywhere; no live paid API calls in tests.
- Public reports are framed as measurement/opinion, never "Model X is worse." Apache-2.0 core.

## Completion & roadmap (estimate — part-time solo dev + AI pair; full-time ~2-3x faster)

- **MVP (Phase 0): ~55-60% done.** Built: full offline pipeline + the hardened, audited
  diff engine + CLI + §7 reporter. Remaining ~40% is the *proof-of-value*: real
  OpenAI/Anthropic adapters, the LLM-judge, a **real two-model run with a measured low
  false-positive rate on a held-out set** (the DoD), 6-8 real scenarios, and Report #1.
- **Public OSS launch (early Phase 1): ~30% done.** pipx/PyPI, a real GitHub Action,
  Watcher automation, the public report site + cadence, and hosted Pro are barely started.

| Milestone | Remaining | Calendar (from now) |
|---|---|---|
| MVP (Phase 0 DoD) | OpenAI adapter -> Anthropic adapter -> LLM-judge -> real 2-model run + FP measurement + threshold calibration -> 6-8 scenarios | ~2-4 weeks |
| Modelpin Report #1 | run open suite on a real model launch, write up, post | overlaps; gated by an actual model launch |
| OSS public launch | pipx/PyPI, real GitHub Action (PR comment via API), docs/DX | ~6-8 weeks |
| First dollars (full Phase 1) | Watcher automation, report site + cadence, hosted Pro | ~3-6 months (~$1.5k MRR) |
| Phase 2 (GitHub App + teams) | FastAPI+Postgres, auto-PR App, dashboard, Stripe | ~6-14 months (~$10k MRR) |
| Phase 3 (business + scale) | managed auto-fix PRs, SSO, more providers | ~14-30 months ($1M ARR) |

Caveats: scales with hours/week; Report #1 is gated by a real provider model launch;
hosted tiers are the heavy lifts. Mirrors the spec's phasing (§11).
