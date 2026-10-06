# modelpin: notes for Claude Code

Dependabot for AI models: replays scenarios on the current and candidate model and flags behaviour regressions with statistical tests. The promise is "if modelpin says it broke, it broke", so false alarms are worse than misses.

## Commands (the gate; all must pass before any commit)
```bash
pip install -e ".[dev,providers]"   # [providers] is needed for the full test suite (httpx etc.)
pytest                              # offline, no API keys, ~30 s
ruff check .
black --check .                     # line length 100
```
Offline smoke test: `modelpin init --demo && cd modelpin-demo && modelpin baseline --fixtures traces.json && modelpin check --to demo-model-v2 --fixtures traces.json` (exits 1 on purpose).

## Map
- `modelpin/cli.py`: typer CLI (`modelpin` / `mp`)
- `modelpin/config.py`: modelpin.yaml loading; `extra="forbid"` on purpose
- `modelpin/diff/`: the core statistics. Read CONTRIBUTING.md before touching it.
- `modelpin/providers/`: OpenAI, Gemini, Anthropic/Vertex adapters; `fake.py` replays fixtures
- `modelpin/report/`: Markdown report rendering
- `action.yml`, `actions/`: the GitHub Action
- `docs/`: ADRs and measurements; `scripts/`: calibration and FP-measurement tools

## Rules
- Never call a live provider in tests. Use `--provider fake` and fixtures.
- Do not change effect-size floors, ALPHA or DEFAULT_RUNS without calibration data (see CONTRIBUTING.md and the comments in config.py).
- Every behaviour change gets a test, and a CHANGELOG entry under the unreleased section.
- Comments explain WHY, citing the MP-/issue number, matching the existing style.
- Read files with `encoding="utf-8"` (or `utf-8-sig` for user-supplied files). Windows users exist.

## Long sessions
This repo is large (~59k lines). Context fills fast. When a session gets long or before `/clear`, write `HANDOFF.md` (goal, what's verified, what's broken with file:line, next 3 steps, decisions) and start a fresh session from it. `HANDOFF.md` is gitignored.
