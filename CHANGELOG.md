# Changelog

All notable changes to Modelpin are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  "Run `mp init` first" unconditionally — advice that was true in 1 of 5 reachable states,
  and that in 2 of them sent the user around a loop with no exit. The message now reports the
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

### Planned
- Anthropic adapter (currently a stub; needs a paid key).
- Edge-probing scenario generator (helping users author discriminating scenarios — "scenario
  quality is the product").
- Distinct error/insufficient-data verdicts (empty traces, all-empty output, tool-turn
  truncation) and an "underpowered/inconclusive" confidence annotation at low `--runs`.
- Expanded judge calibration (≥30 real-migration trace pairs + a non-OpenAI judge).

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

[Unreleased]: https://github.com/samarthputhraya/modelpin/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/samarthputhraya/modelpin/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/samarthputhraya/modelpin/releases/tag/v0.1.0
