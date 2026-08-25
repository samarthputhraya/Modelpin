# Changelog

All notable changes to Modelpin are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (breaking)
- **`--fixtures` is now required with `--provider fake`.** The fake provider no longer
  fabricates a trace when it has no canned one: it raises `MissingCannedTrace` and the CLI
  exits 1 with an error naming the flag. Previously a bare `mp baseline --provider fake`
  produced placeholder traces that were structurally indistinguishable from real ones, so a
  run could report `unchanged` — "safe to adopt" — about behaviour that was never measured.
  Any scripted offline invocation that omitted `--fixtures` will now fail. Pass
  `--fixtures <file>`; `modelpin init --demo` writes one.
  **If you ever ran a fixture-less `baseline` on 0.1.2, delete the stored baseline and
  re-record.** That run wrote placeholder traces to `.modelpin/baseline-*.json`, and this
  release does not detect them there — the guard is on the replay path, not the store. A
  fully correct `check --fixtures` against such a baseline reports regressions at
  confidence 1.00 that did not happen. See ADR-0015.

### Changed
- **The false-positive rate now excludes trials that could not have produced a false alarm —
  and the previously published `0/8` result is WITHDRAWN.** `scripts/fp_measurement.py` counted
  a scenario as a passed trial even when the engine measured no effect on any channel. `[M]`
  `diff/stats.py:128-129` early-exits at `p=1.0` when two sides are distributionally identical,
  so such a trial scores `unchanged` *regardless of any change to the engine* — counting it as
  clean credits the engine for a test it could not fail. The temperature-0 held-out suite
  therefore published `0/8 = 0%`; re-scored (**not** re-run — no API call was made) the same
  recorded verdicts give **`0/0 = n/a`**, whose 95% upper bound is unbounded. A trial is now
  excluded iff the engine reports `unchanged` at confidence exactly 1.0 — under ADR-0001 that
  confidence is `min(p)`, so it holds precisely when nothing could have fired at ALPHA. This is
  **sound by construction**: a flagged verdict never carries `unchanged`-confidence, so the
  exclusion can never remove a false positive. Excluded counts are printed beside the rate,
  never folded into it.
  `[M]` The same correction applies to the golden-test row: the 4 "noisy-but-equivalent" pairs
  in `tests/test_diff.py` all return `unchanged` at confidence 1.0000, so `0/4` is also `0/0`.
  Two further rows in that table have not been re-scored and must not be quoted as an FP rate.
  The engine did not change — `git diff` over `modelpin/` for this work is comment-only, and no
  threshold moved. Only the accounting, and the claims resting on it, changed.
- **The published confidence interval was wrong for every non-zero result.** The harness printed
  `1 - alpha**(1/n)`, which is the Clopper-Pearson upper bound *only* at k=0. `[M]` at 1/8 it
  understated 47.1% as 31.2%; at 4/8 it printed 31.2% — an upper bound **below** the 50%
  observed rate. Replaced with an exact bisection bound.
- **`MIN_SEMANTIC_DELTA`'s evidence is restated from three independent conditions to one, and
  that one is weaker than first published.** The held-out re-validation contributed 0 scored
  trials, and `[M]` the two calibration runs share their scenarios and perturbations and both
  record `"judge": "gpt-4o-mini"`, differing only in the candidate model — so the judge, which
  is what this floor gates, provably did not vary. `[M]` Of the surviving run's 6 equivalent
  pairs, **5 return `p = 1.00`** and could not have fired, so the evidence is **0/1 (95% upper
  bound 95.0%)**, not 0/6 at 39.3% — that figure was itself the pre-MP-75 accounting, corrected
  here. The threshold value is unchanged; only the claimed evidence for it.

- **`mp baseline`, `mp check` and `mp report` state how many replays a run will make,
  before making them.** The pre-spend line was `provider=… model=… runs=…`; it now also
  carries `N scenario(s) from <dir> -> M replays, >=M paid calls`, and
  `+ up to 2M judge calls` when a `judge_model` is configured. The `>=` is deliberate: one
  replay is one adapter call, but a `kind: agent` scenario's replay drives a tool loop of up
  to `MAX_TOOL_TURNS` completions, so replays are a **floor** on the paid calls of a run that
  completes, never a ceiling. (A run cut short — a provider error, a skipped scenario — bills
  less.) `mp check` counts only scenarios that have a baseline, since the rest are skipped
  rather than replayed. `--provider fake` claims no cost at all — that path replays canned
  traces and bills nothing. See ADR-0019.
- **`mp report` discloses a two-sided run as two-sided.** It replays `--from` *and* `--to`,
  so its line reads `N scenario(s) from <dir> x 2 models -> M replays` and its replay and
  paid-call figures are **twice** what the same suite costs under `mp check` — exactly twice
  when every scenario has a baseline, since `check` counts only those. `[M]` the 14-scenario
  `examples/report-suite` queues **140** replays
  (`modelpin report --to B --from A --suite-dir examples/report-suite`, 14 × `runs: 5` × 2
  models). Before this release that line named the suite directory but not its size, so the
  user saw `runs=5` with no hint that 140 replays were queued, and learned the scenario count
  only from the results table — after the calls had been billed. The judge figure is
  deliberately **not** doubled: for `report` the semantic judge scores every run on both
  sides whether or not both were replayed live, so it is bounded by `2 × scenarios × runs`,
  the same figure `mp check` discloses over the same suite. The header also drops its
  now-redundant `suite=<dir>` token, which the disclosure line names.
- **`mp init` names the scenario set it adopted.** When a `modelpin.yaml` already exists,
  `init` honours its `scenarios_dir` (0.1.2 behaviour, unchanged) but now also prints how
  many scenarios that directory holds and where the config is, so an adopted config is
  visible before `mp baseline` acts on it.

### Added
- **`insufficient_evidence` verdict, and exit code 3.** A scenario where **at least half**
  of one side's runs recorded no behaviour — no output, no tool call, no refusal — now
  abstains instead of reporting `unchanged`. Below that threshold (e.g. 2 of 5) the run
  still reports `unchanged`; the counts are recorded on `DiffSignals` but gate nothing. Previously two identically-degenerate sides scored `unchanged` at
  **confidence 1.00**, because every p-value was 1.0 and unchanged-confidence is `min(p)`:
  the engine's least-evidence case scored as its most confident. `mp check` exits 3 when a
  scenario abstains **and nothing regressed** — a regression still outranks it and exits 1
  — and the report omits "safe to adopt". See ADR-0018.

### Fixed
- **A fresh clone no longer fabricates a regression against Modelpin's own baseline.** `[M]`
  Reproduced verbatim: clone the repo, run `mp init`, write your own
  `scenarios/refund_request.json`, run `mp check` — and get
  `REGRESSION refund_request: tool-call behavior changed: ['lookup_order', 'issue_refund'] -> []
  (confidence 0.99)`, **exit 1**, plus a `last-report.md` the GitHub Action posts as a PR
  comment. You never ran `mp baseline`; the traces on the left were Modelpin's own, recorded
  2026-06-24 for an unrelated scenario that merely shares a filename. Three innocent facts
  combined: the dogfood baseline is tracked on purpose (`.gitignore:19-20`) so it ships in every
  clone, `mp init` scaffolds `models: [gpt-4o-mini]` — the exact key it was stored under — and
  `mp check` pairs a baseline to a scenario by **id alone**. `refund_request` is not an unlucky
  guess: it is the name in the README's worked-example table and the file `mp init --demo`
  writes to disk. The dogfood baseline is now keyed under the fictional `modelpin-dogfood`, so
  no scaffolded config can name it; the traces inside are unchanged, genuine gpt-4o-mini
  recordings. See ADR-0021.
  **This fixes the instance, not the mechanism.** `mp check` still pairs on scenario id with no
  provenance recorded, so editing your own scenario after recording your own baseline hits the
  same path. That needs a content hash in the baseline payload — tracked, and specified as three
  `xfail(strict=True)` tests rather than prose.
- **This repo's own dogfood config no longer hijacks a cloned checkout.** `modelpin.yaml`
  sat at the repo root, which is the path the CLI loads by default, so anyone who cloned
  Modelpin and followed README "The real flow, on your own app" inherited it: `mp init`
  honoured its `scenarios_dir`, found the 8 scenarios already in `examples/suite`,
  scaffolded nothing, and still exited 0 reporting "Already initialised" — then
  `mp baseline` replayed Modelpin's own held-out public suite on the user's key: **40
  replays** (8 scenarios × the config's `runs: 5`), with no warning and no scenario of the
  user's own involved. `[M]` In the one recording we have — the traces committed at
  `.modelpin/baseline-modelpin-dogfood.json`, gpt-4o-mini, 2026-06-24 — those 40 replays cost
  **60 chat-completion calls**. Treat that as an observation, not a constant: three of the
  eight are `kind: agent` scenarios whose replays drive a tool loop of 1–6 turns
  (`MAX_TOOL_TURNS`), and the loop length is the model's decision, so the same 40 replays
  are bounded at 40–115 calls. The config now lives at `.github/modelpin.yaml` and the
  workflow passes it explicitly. **This affects people who cloned the repo, not
  `pip install modelpin` users** — no published artifact ever contained that file: it is
  absent from the 0.1.1 and 0.1.2 wheels and the 0.1.2 sdist on PyPI, and `MANIFEST.in`
  never listed it. A `tests/` guard now fails if a `modelpin.yaml` becomes tracked at the
  root again. Side effect: README's "`mp init` scaffolds `modelpin.yaml` + `scenarios/`" is
  now true in a fresh clone, where it previously was not.

### Planned
- Anthropic adapter (currently a stub; needs a paid key).
- Edge-probing scenario generator (helping users author discriminating scenarios — "scenario
  quality is the product").
- An "underpowered" annotation at low `--runs`. `insufficient_evidence` does **not** cover
  this: at `--runs 2` neither signal can reach `p ≤ ALPHA`, and at `--runs 3` the tool
  signal cannot, yet both still report `unchanged` and exit 0. See ADR-0018 non-goal 2.
- A verdict that reads tool-turn truncation. `Trace.incomplete_reason` now records it, but
  nothing gates on it — an exhausted tool loop implies a tool call, so the abstention
  predicate is provably unable to fire on it. See ADR-0018 non-goal 1.
- Expanded judge calibration (≥30 real-migration trace pairs + a non-OpenAI judge).

## [0.1.2] - 2026-08-22

### Changed (breaking)
- **`mp report --suite-dir` is now required.** There is no built-in default suite directory.
  The previous default (`examples/report-suite`) named a path that cannot exist after
  `pip install`, so a bare `mp report` told users "no scenarios found" for a directory they
  never chose. Bare `mp report` now exits 2 with an error naming the flag. The one documented
  invocation that omitted it (`examples/report-suite/README.md`) has been updated; every
  other documented invocation, including the published report's reproduce command, already
  passed the flag.

### Fixed
- **`pip install modelpin` could not run the README's own quickstart** ([#28]). The wheel
  shipped no `examples/` and no `data/models.json`, so the documented zero-key quickstart
  exited 1, `watcher.load_registry()` raised `FileNotFoundError`, and `mp report` failed on
  its own default. Fixed by making the *program* self-sufficient rather than the archive
  bigger: the wheel stays code-only, `mp init --demo` generates the demo, and the model
  registry is embedded.
- **The error message's remedy no longer loops.** `no scenarios in ...` used to say
  "Run `mp init` first" unconditionally. That was correct only when the missing directory
  happened to be the one `mp init` creates; otherwise it sent the user around a loop with no
  exit (— `mp init` succeeds, the identical command fails identically). The message now reports the
  absolute path it searched, which setting chose it, what it actually found there, and a
  remedy that is true in that state. `mp init` is named only when `mp init` would create
  exactly that path.
- **`mp init` honours `scenarios_dir` from an existing `modelpin.yaml`** instead of always
  scaffolding `scenarios/`, so following the error's advice now terminates.
- **`load_registry()` no longer reads `./data/models.json` from the working directory.** An
  unrelated file in a user's cwd could redefine which models Modelpin believed existed.
- The offline report no longer claims "using your API key" on a keyless `--provider fake` run
  — it says "from canned offline traces (no API key)". That line is printed inside the first
  artifact a new user reads, under a heading that says no key is needed.
- An unmanifested suite directory no longer publishes a report header claiming to be
  `modelpin-public-suite`; the fallback id is now `local-suite`.
- The documented PowerShell escape hatch is `Remove-Item Alias:mp -Force` — the alias is
  `ReadOnly, AllScope`, so the command as previously written failed.

### Added
- **`mp init --demo`** — generates a self-contained offline sandbox (`modelpin-demo/`: four
  scenarios, canned traces, config, README) that demonstrates every verdict the engine can
  reach, including `unchanged`. No key, no cost, and it exits 1 on a real regression because
  that non-zero exit is the CI gate.
- **`wheel-smoke` CI job** — builds the wheel, installs it into a fresh venv *outside* the
  checkout, extracts the quickstart *from `README.md`* (via `tests/docs_extract.py`, which
  fails closed), and runs it in an empty directory, on Linux and Windows. The previous CI
  installed `-e` and ran from the checkout, which removed both preconditions of #28 and made
  that entire bug class invisible to it.
- `MANIFEST.in` — the sdist now carries `tests/`, `examples/`, `docs/` and
  `data/models.json`, so a contributor can build and test from a source distribution. The
  wheel is unaffected and stays code-only.

[#28]: https://github.com/samarthputhraya/modelpin/issues/28

## [0.1.1] - 2026-06-24

### Security
- **GitHub Action hardened against script injection** — all caller-controlled inputs now flow
  through the step `env` as data and are never interpolated into `run:` shell text.
- **PR-comment markdown injection fixed** — report content is escaped before it is posted.
- **Secret scrubber widened** — added Groq `gsk_` and ensured `model_id` is scrubbed from
  error paths.

### Fixed
- `--match subset` / `--match superset` no longer silently collapse to `strict` (which could
  manufacture a false-positive regression on a change the mode is meant to permit). Directional
  modes now route through `trajectory_match` with a one-sided violation-rate test, and `--match`
  is validated at the CLI boundary.
- Refusal detector folds apostrophe-like glyphs to ASCII, so two identical polite declines that
  differ only by a curly vs. straight apostrophe are no longer scored as a refusal-rate change.

### Added
- `CONTRIBUTING.md`, issue templates, and a pull-request template.
- Discriminating public report suite (v2) and **Modelpin Drift Map #1** (an open suite run
  across five real migration pairs, fully reproducible).

## [0.1.0] - 2026-06-24

### Added
- **Behavioral-diff engine** — an exact two-sample permutation test (deterministic, no SciPy)
  over N runs, combined with structural signals (tool-call trajectory match in
  strict/unordered/subset/superset modes, refusal rate, output-format/assertion validity) and
  an optional calibrated low-temperature LLM-as-judge for semantic equivalence. A regression is
  flagged only when the distribution shifts (`p ≤ ALPHA`) **and** clears an effect-size floor —
  optimizing the north-star: a low false-positive rate.
- **CLI** (`modelpin`, alias `mp`): `init`, `scan`, `baseline`, `check`, `report`, `version`.
- **GitHub Action** — replays scenarios on a candidate model, posts a sticky PR comment, and
  fails the check on a real regression.
- **Providers** — OpenAI, Google/Gemini, and Groq/Llama (via the OpenAI-compatible `base_url`)
  through one engine. Anthropic adapter stubbed.
- **`mp report`** — runs an open public suite across two models into a reproducible,
  opinion-framed Markdown + JSON report.
- BYO-key throughout, with key-shaped-secret scrubbing on all output.

[Unreleased]: https://github.com/samarthputhraya/modelpin/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/samarthputhraya/modelpin/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/samarthputhraya/modelpin/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/samarthputhraya/modelpin/releases/tag/v0.1.0
