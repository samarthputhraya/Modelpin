# Changelog

All notable changes to Modelpin are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
