# Modelpin

**Dependabot for AI models.** Know before the model breaks you.

[![CI](https://github.com/samarthputhraya/modelpin/actions/workflows/ci.yml/badge.svg)](https://github.com/samarthputhraya/modelpin/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/modelpin.svg)](https://pypi.org/project/modelpin/)
[![Python](https://img.shields.io/pypi/pyversions/modelpin.svg)](https://pypi.org/project/modelpin/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A provider ships a new model (or retires the one you depend on). Modelpin **replays your app's
real behavior** on the new model, decides whether anything *actually* regressed despite model
randomness, and posts a PR-style report — so you find out in a pull request, not in production.

CLI: `modelpin` (alias `mp`). License: Apache-2.0.

---

## Why this exists (and why you can trust the verdict)

Models are non-deterministic. Run the same prompt twice and the words change. So the naive way to
"test a new model" — diff the text — cries wolf on every run. An alerter that cries wolf is worse
than no alerter: you mute it, then it misses the real break.

Modelpin's entire design optimizes for **one north-star metric: false-positive rate.** The promise
is narrow and falsifiable: *if Modelpin says it broke, it broke.* Everything below is in service of
that promise — and where the evidence is thin, this README says so plainly.

This is meant to be the **independent, no-BS** tool: it measures behavior change **relative to your
app**, it never declares one model globally "better," and the whole harness is open source so you
can reproduce it and disagree.

---

## Quickstart

Install (Python 3.12+):

```bash
pip install "modelpin[providers]"      # or: pipx install "modelpin[providers]"
modelpin version                        # -> modelpin 0.1.2
```

> **Windows PowerShell:** run `modelpin …`, not `mp …`. PowerShell ships a built-in `mp` alias
> (`Move-ItemProperty`) that shadows the CLI. The `mp` alias works everywhere else (cmd, bash,
> zsh) and via `mp.exe`; on PowerShell either use `modelpin`, call `mp.exe`, or run
> `Remove-Item Alias:mp -Force` once per session (add it to your `$PROFILE` to make it permanent).

### Try it offline, no API key (30 seconds)

`modelpin init --demo` writes a self-contained sandbox — four scenarios, canned traces and a
config — into `modelpin-demo/`. It replays those traces through the fake provider, so you see
the whole pipeline (baseline, candidate replay, behavioral diff, report) at zero cost and no key:

<!-- mp:smoke -->
```bash
modelpin init --demo
cd modelpin-demo
modelpin baseline --fixtures traces.json
modelpin check --to demo-model-v2 --fixtures traces.json
```

You'll get a per-scenario verdict (`unchanged` / `changed_minor` / `regression` /
`insufficient_evidence` - the last meaning a side recorded nothing to compare), a confidence
score, a one-line plain-English explanation per scenario, and a Markdown report written to
`.modelpin/last-report.md`:

| scenario | verdict | why |
|---|---|---|
| `greeting` | `unchanged` | identical behavior — Modelpin stays quiet |
| `refund_request` | `regression` | the candidate calls `lookup_order` twice; the final answer is word-for-word identical, so a text diff sees nothing |
| `angry_customer` | `regression` | the candidate refuses an action the baseline performed |
| `invoice_parse` | `changed_minor` | `"Total: $5"` → `"Total: 5"` breaks the scenario's assertion, but nothing refused and no tool moved |

`modelpin check` exits **1** only on a real `regression` — that's the CI gate, and it is why
the demo exits 1. It also exits **3** when a scenario could not be measured at all, which is a
different claim from "it broke". Then edit `traces.json`, re-run, and watch the verdict move:
the answer is computed from the traces, not baked in.

None of this is bundled inside the installed package — the wheel is code only, and the demo is
generated on your machine. That is deliberate: shipping them would mean the quickstart depends
on data that a `pip install` may or may not place where the docs claim — which is exactly how the
previous quickstart broke. Generating it means the commands above cannot rot.

### The real flow, on your own app

```bash
# 1. Scaffold modelpin.yaml + scenarios/ (never overwrites existing files)
modelpin init

# 2. See which models your repo already depends on, and where
modelpin scan

# 3. Add a scenario or two (a JSON file per representative case — see below),
#    then record how your current model behaves, N times
export OPENAI_API_KEY=sk-...        # your key, read from the env — never stored
modelpin baseline                   # uses models[0] + providers[0] from modelpin.yaml

# 4. Replay your scenarios on a candidate model and diff the behavior
modelpin check --to gpt-5.5
```

A scenario is a small JSON file (one per case) under `scenarios/`. The one `mp init` writes:

```json
{
  "id": "greeting",
  "name": "Simple greeting",
  "kind": "single",
  "input": {"messages": [{"role": "user", "content": "Say hello in one short sentence."}]},
  "assertions": {"must_contain": ["hello"]}
}
```

Scenarios can also be agent runs: set `"kind": "agent"`, add `"tools"` (and canned `"tool_results"`)
to `input`, and Modelpin drives a multi-turn model↔tool loop so trajectories like
`lookup_order → issue_refund` actually emerge during replay. Eight worked examples spanning tool
trajectories, semantic equivalence, refusals, and output format live in
[`examples/suite/`](examples/suite/).

---

## See it in your PR (GitHub Action)

The point of Modelpin is that the answer shows up **at review time.** It ships a real composite
GitHub Action: it installs Modelpin, optionally records a baseline, runs `mp check`, posts a
**sticky PR comment** (found-and-updated in place via a hidden marker — no comment spam), and
**fails the job on a regression**. Drop this at `.github/workflows/modelpin.yml`:

```yaml
name: Modelpin

on:
  pull_request:
  workflow_dispatch:        # trigger by hand the day a provider ships a new model

permissions:
  contents: read
  pull-requests: write      # so the action can post/update the PR comment

jobs:
  model-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: samarthputhraya/modelpin@v1
        with:
          from: gpt-4o-mini       # the model you depend on today (committed baseline)
          to: gpt-5.5             # the candidate to vet before adopting
          provider: openai
          runs: "5"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}     # BYO-key from repo secrets — never inline a key
          # If your judge_model lives on another provider, add its key too:
          # GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          # GROQ_API_KEY:   ${{ secrets.GROQ_API_KEY }}
```

Action inputs: `to` (required), `from`, `provider`, `config`, `scenarios-dir`, `runs`, `match`,
`baseline`, `comment`, `fail-on-regression`, `github-token`, `modelpin-spec`, `python-version`,
`working-directory`. Outputs: `verdict-exit-code` (0 clean, 1 a behavioral regression,
3 at least one scenario could not be measured) and `report-path`. The usual pattern is to
**commit your baseline** so CI only replays the candidate; flip `baseline: "true"` to record fresh
(needs the old model still reachable). Copy-paste workflow:
[`examples/github-workflow.yml`](examples/github-workflow.yml).

---

## How the behavioral diff works (the moat)

Modelpin decides "did it really change?" from **multiple signals over multiple runs**, then gates
every regression behind a **distributional significance test plus an effect-size floor**. A single
odd run never trips it; a majority that merely flips between two equally-likely behaviors never
trips it. Here is the whole decision rule, no hand-waving:

**1. Multi-run, not single-shot.** Each scenario runs N times (`runs:` in config; default 5,
minimum 2 — a single run can't form a distribution, so `--runs 1` is rejected outright). Baseline
*and* candidate are both sampled, so the comparison is distribution-vs-distribution.

**2. Structural signals** (per run, no network, deterministic):
- **Tool-call trajectory match** with four modes — `strict | unordered | subset | superset`
  (`--match`) — so you choose how strict "same plan" means for your agent.
- **Output format / assertion validity** — your scenario's `must_contain` / `must_not_contain`
  text assertions, checked as a rate across runs.
- **Refusal detection** — did the model start declining requests it used to answer?
- **Latency / token deltas** — captured and reported, but **informational only**; they never gate
  the verdict (latency is jittery; a token bump isn't a behavior regression).

**3. Semantic signal** (optional LLM-as-judge): a low-temperature judge answers the only question
that matters — *do these answers mean / accomplish the same thing?* This catches the structural
blind spot: two answers that are textually different but identical in meaning ("The total is $5."
vs "5 dollars."). The judge is **injected and optional** — with no `judge_model` set (and always on
the offline `fake` path) the diff stays purely structural and makes zero network calls, so CI can
run for $0. The judge is independent of the two models being compared, so it can arbitrate a
cross-vendor check.

**4. The statistics that kill false alarms.** Every gating signal goes through an **exact
two-sample permutation test** (`modelpin/diff/stats.py` — no SciPy, deterministic, so golden tests
stay reproducible). A signal counts as a regression only when **both**:
- the candidate distribution differs from baseline at **p ≤ 0.05** (`ALPHA`), **and**
- the effect clears a conservative **size floor** — tool-call shift ≥ 0.5 total-variation distance
  (`MIN_TOOL_TVD`), refusal-rate rise ≥ 0.34 (`MIN_REFUSAL_DELTA`), or semantic-divergence rate
  ≥ 0.5 over baseline (`MIN_SEMANTIC_DELTA`).

The size floor is what stops a *statistically* significant but *practically* trivial jitter from
firing once N grows large. These floors are intentionally conservative — biased toward missing a
borderline change rather than inventing one — because a miss is a false *negative* (the safe
direction for a trust product), while a false alarm erodes trust permanently.

**Output:** each scenario gets a verdict, a confidence score, the underlying signals, and a one-line
explanation. A structural tool-call / refusal break or a calibrated semantic divergence is a
**CI-failing `regression`**; format/assertion drift alone is `changed_minor` (reported, doesn't fail
the build).

### The false-positive evidence — and its limits, stated plainly

**Result: the false-positive rate is not yet established.** This section previously read
"**0/8 false positives** on a held-out 8-scenario suite ... all `unchanged` at confidence 1.00".
That claim is **withdrawn** as of 2026-08-23. All 8 of those trials ran at temperature 0, and a
trial in which *every* channel returned `p = 1.00` could not have produced a false alarm at any
threshold — counting it as a passed trial credits the engine for a test it could not fail. That
is broader than "nothing moved": it also drops trials where an effect **was** measured but 5
runs a side could not separate it. Scored
honestly the same run is **0/0**, whose 95% upper bound is *unbounded*. The harness now says so
itself. Full writeup: [`docs/fp-measurement.md`](docs/fp-measurement.md), summarised in the
[changelog](CHANGELOG.md).

What that run **does** still support, and how far: the accounting change touches only the
false-positive arm (the recall arm excludes nothing, deliberately), so detection is unaffected.
**2 of 3** injected perturbations were flagged. The third (`decline_pii`) the model simply
resisted — it still declined, so nothing changed for the engine to see; the harness scores that a
**MISS** and we claim no credit for it either way. These are 3 synthetic, deliberately extreme
system-prompt injections against one model at temperature 0, in a single run, and the interval
treats the three as exchangeable trials, which by construction they are not. `[M]` The 95%
one-sided *lower* bound on true detection is **13.5%** at 2/3 — `1 - upper_bound_95(1, 3)` in
[`scripts/fp_measurement.py`](scripts/fp_measurement.py). A `2/2` reading, which drops the
resisted case from the denominator, is **withdrawn**: the harness cannot tell a resisted
instruction from a dead engine, so it never excludes on that basis. See the correction note in
[`docs/fp-measurement.md`](docs/fp-measurement.md). Detection is demonstrated, not
characterised — and quietness on *equivalent* behavior is not evidenced at all.

The semantic judge's escalation threshold is **calibrated** on a labeled set in
<!-- calibrated = confirmed FP-safe and detection-preserving on a labelled set, NOT fitted -->
[`examples/calibration/`](examples/calibration/) that is **deliberately distinct from the held-out
suite** (so it cannot leak into the held-out result). `[M]` On the independent-candidate run of
record, equivalent pairs land at divergence **0.0–0.20** and real meaning changes at **0.60–1.0**,
leaving a gap around the 0.5 floor. **"Calibrated" here means confirmed FP-safe and detection-preserving on that set — not *fitted*:** `[M]` the set cannot discriminate the value, the semantic sweep being flat from 0.1 to 0.9, so **0.5 is a conservative choice rather than a fitted one**. But `[M]` 5 of those 6 equivalent pairs return `p = 1.00` and
could not have fired at all, so the honest score is **0/1 — 95% upper bound 95.0%**, not 0/6.
(The cleaner "0.0 versus ≥0.8" figures quoted here previously are the *self-judge* run, which an
adversarial audit demoted as circular — and which scores **0 trials** under the same predicate.)
FP-safety was re-checked with an **independent judge** (a different model arbitrating) and
re-validated on the held-out suite after promoting semantic divergence from `changed_minor` to a
CI-failing `regression` — no verdict moved. That re-validation contributed **0 scored trials** under
the corrected accounting, and `[M]` the two calibration runs share their scenarios and their
perturbations and **both record `"judge": "gpt-4o-mini"`**, differing only in the candidate model.
Since this floor gates the judge's own output, the judge is the factor that would have had to vary.
So the floor rests on **one** labeled condition — and that one scores 0/1, not 0/6.

**This is a first calibration. Do not over-trust it.** The honest limitations, documented in
[`docs/fp-measurement.md`](docs/fp-measurement.md):
- the calibration set is **small** (≈6+6 pairs) and the perturbations are **synthetic**, not
  harvested from real migrations;
- recall on subtle changes was 4/6 — `[M]` a 95% one-sided *lower* bound of **27.1%** on
  true detection, `1 - upper_bound_95(2, 6)` in
  [`scripts/fp_measurement.py`](scripts/fp_measurement.py). It can *miss* a subtle real
  change (again, the safe direction);
- the judge is **OpenAI-only** so far;
- the structural floors are **not** FP-validated: `[M]` the held-out run contributed 0 scored
  trials, and at the shipped `runs: 5` the floors are **inert** anyway — the p-value gate is
  strictly stricter, and they first bind at N=9 (semantic), N=11 (tool), N=12 (refusal).

Planned before any high-stakes reliance: ≥30 pairs including real migration traces, and a
non-OpenAI judge. We'd rather you know this than discover it.

### Proof it actually *fires*: the Drift Map

Quietness on equivalent behavior is the half **not** yet evidenced (above); the complement — that it catches
*real* drift — is the **[Modelpin Drift Map #1](docs/reports/modelpin-drift-map-1.md)**. We replayed
an open, deliberately-hard suite across **5 real migration pairs** (including cross-vendor), 5 runs
each, judge on. `[M]` The engine stayed quiet on **50 of 60** comparisons and flagged the other
10 — 9 `regression`, 1 `changed_minor`. **4 of the 5 pairs** carry a `regression` that survives
a read of the raw traces: an agent that went from *asking for a missing date* to *hallucinating
a flight booking*, prompt-injection resistance **flipping across a version bump**, and a
multi-constraint format breaking on an upgrade. The fifth does not — the report says so, and
says why. `[M]` Of the 9 `regression` flags, **6** are solid and **3** are soft; we publish
that split rather than a precision rate, because the 9 land on only 4 distinct scenarios and
are therefore not exchangeable trials. The exact raw traces and per-scenario verdicts are
published in [`docs/reports/data/`](docs/reports/data/) — diff against ours without spending a cent.
It also **discloses a false positive our own refusal detector produced** (a Unicode-apostrophe bug,
since fixed): flagging our own measurement's soft spots is the whole point of being an independent
voice. The same capability is wired behind `mp report` — point it at any model launch.

---

## Cross-vendor (including a free third vendor)

A model migration isn't always within one lab. Modelpin diffs **across vendors** through one engine;
a separate judge model arbitrates meaning-equivalence.

| Provider | Status |
|---|---|
| **OpenAI** | Live (Chat Completions), multi-turn tool loops |
| **Google / Gemini** | Live (`google-genai`), multi-turn tool loops, cross-vendor proven |
| **OpenAI-compatible hosts** — `groq`, `openrouter`, `together`, `cerebras` | Live (the OpenAI adapter pointed at the host's `base_url`) |
| **Anthropic** | **Stub** — raises `NotImplementedError` (deferred until a paid key is in play) |

**What we observed (open suite, our settings):**
- `gpt-4o-mini` vs `gemini-3.1-flash-lite`, 5 runs × 8 scenarios, OpenAI judge on → **8/8
  `unchanged`**: the cross-vendor judge genuinely fired and found the two vendors behaviorally
  equivalent on this suite.
- `gpt-4o-mini` vs `llama-3.3-70b-versatile` on **Groq**, same suite → **8/8 `unchanged`**.

**Free third vendor, zero cost:** [Groq](https://console.groq.com) serves Llama models over the
OpenAI-compatible API and has a free tier, so a free key makes a zero-cost cross-vendor check:

```bash
export GROQ_API_KEY=...     # free at console.groq.com
modelpin check --provider groq --from gpt-4o-mini --to llama-3.3-70b-versatile
```

A caveat worth stating: open-model *hosts* rotate ids but don't retire on a lab's fixed schedule the
way the big providers do, so Groq/OpenRouter/etc. are a genuine cross-vendor bonus and an
architecture proof — not the core migration wedge.

---

## Bring your own key

Modelpin replays with **the end user's own API key**, always read from the environment, **never**
hardcoded, shipped, or stored (cost stays yours; provider ToS stays clean):

- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
- `GROQ_API_KEY` (and the equivalents for other OpenAI-compatible hosts)

In CI, supply these as repo secrets (see the workflow above). Error text is scrubbed of
`sk-` / `Bearer` tokens, so a failed call never leaks your key into a log, traceback, or PR comment.

### Google: billing Vertex AI instead of an API key

Google sells Gemini through two doors, and **an AI Studio API key cannot spend Google Cloud
credit** — that path bills a separate prepaid wallet. If your Gemini budget lives in Cloud
billing, point Modelpin at Vertex instead. It uses Application Default Credentials, so there is
no key at all:

```bash
gcloud auth application-default login          # once
export GOOGLE_GENAI_USE_VERTEXAI=true          # or GOOGLE_GENAI_USE_ENTERPRISE=true
export GOOGLE_CLOUD_PROJECT=your-project-id
modelpin check --to gemini-3.5-flash --provider google
```

The variable names are the Google GenAI SDK's own. The API-key path stays the default and is
unchanged.

**Leave `GOOGLE_CLOUD_LOCATION` unset unless you need data residency.** It defaults to `global`,
which is both the SDK's own default and the only location that serves current models: `[M]` every
`gemini-3.x` id returns **404 on regional endpoints** such as `us-central1`, where only the legacy
2.5 family is available. Setting a region keeps processing in that jurisdiction, at the cost of
the newer models — `global` routes dynamically and makes no residency guarantee.

The two doors do not offer the same catalogue: `[M]` `gemini-2.5-flash` currently returns
*"no longer available to new users"* on AI Studio while still serving on Vertex — which is the
sort of divergence Modelpin exists to notice.

---

## CLI reference

| Command | What it does |
|---|---|
| `mp init [dir]` | Scaffold `modelpin.yaml` + `scenarios/` (never overwrites). |
| `mp scan [path]` | Detect which AI models the repo depends on, and where. |
| `mp baseline` | Record current model behavior for your scenarios (N runs). |
| `mp check --to <model>` | Replay scenarios on a new model, diff vs baseline, write the PR-style report, fail CI on a regression. |
| `mp version` | Print the Modelpin version. |
| `mp report --to <new> --from <incumbent> --suite-dir <dir>` | Replay a scenario suite across two models and draft a reproducible, opinion-framed Modelpin Report (Markdown + a JSON audit sidecar) under `reports/`. Unlike `check`, it **publishes** — exits 0 even on a regression. `--suite-dir` is required: the wheel ships no scenarios, so the **open public suite** lives in the repo at `examples/report-suite/` — clone it, or point this at your own. |

Shared flags on `baseline` / `check`: `--from` / `--model`, `--provider`, `--runs`, `--match`
(`strict\|unordered\|subset\|superset`), `--config`, `--scenarios-dir`, `--store-dir`, and
`--fixtures`, which is **required** with `--provider fake` (on `report` too).

---

## Install

```bash
pip install "modelpin[providers]"     # or: pipx install "modelpin[providers]"   (Python 3.12+)
modelpin version
```

The `providers` extra pulls in the `openai`, `google-genai`, and `anthropic` SDKs. The bare
`pip install modelpin` (no extra) runs the offline `fake` path with no provider SDKs at all.

From source (for development):

```bash
git clone https://github.com/samarthputhraya/modelpin
cd modelpin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,providers]"
```

---

## What this is *not* (non-goals, on purpose)

Modelpin is a **migration tool**, and stays one. It is **not**:

- a general eval / observability platform,
- prompt management,
- a model gateway or host,
- an absolute "which model is best" leaderboard.

It measures **behavior change relative to your app** — not abstract quality. Saying no to that scope
is what keeps the false-positive promise honest and the tool small enough to trust.

### Honest-framing rules (this is a trust product)

- Any public / measurement claim is phrased as *"on our open suite, under these settings, we
  observed…"* — **never** "Model X is worse." The harness and scenarios are open source so anyone
  can rerun and disagree. That's the whole point of being the independent voice.
- We don't overclaim and we don't falsely undersell. The engine is real and cross-vendor proven;
  *and* Anthropic is still a stub and the judge calibration is a documented first pass. All true at
  once.

---

## Status

**Phase 0 (core engine MVP) — detection demonstrated but NOT characterised; the false-positive half is NOT met**
(see [`docs/fp-measurement.md`](docs/fp-measurement.md)); `v0.1.2` live on PyPI. Live-validated cross-vendor
(OpenAI ↔ Google ↔ Groq/Llama); **false-positive rate not established** (the "0 in 8 held-out
trials" claim is withdrawn — those 8 could not have fired, so the honest score is 0/0); multi-turn replay; a real
GitHub Action; the public-report engine (`mp report`) + the open suite (in this repo, not
in the wheel); the
[Drift Map #1](docs/reports/modelpin-drift-map-1.md) published across 5 real migration pairs;
`pip install "modelpin[providers]"`; **411 tests passing**, `ruff` + `black` clean. The Anthropic
adapter is still a stub (deferred until a paid key is in play); not yet listed on the GitHub
Marketplace.

The full false-positive measurement lives in [`docs/fp-measurement.md`](docs/fp-measurement.md),
and the multi-model Drift Map in [`docs/reports/`](docs/reports/). Next up: the first public
**Modelpin Report** on a real model launch (the harness is launch-ready), then the Anthropic adapter.

## License

**Apache-2.0.** See [`LICENSE`](LICENSE). The open-source core (CLI, engine, Action) is and stays
open; any future hosted tier lives in a separate, proprietary package.

Repo: <https://github.com/samarthputhraya/modelpin>
