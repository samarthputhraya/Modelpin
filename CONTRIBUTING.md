# Contributing to Modelpin

Modelpin is a solo / small-team project. Contributions are welcome — just keep the bar honest. If Modelpin says it broke, it broke: that promise is the whole product, so we guard it carefully.

---

## Dev setup

```bash
git clone https://github.com/samarthputhraya/modelpin
cd modelpin
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,providers]"
```

Verify everything works offline (no API key needed):

```bash
modelpin baseline \
  --provider fake \
  --fixtures examples/traces/demo_traces.json \
  --model claude-opus-4-6 \
  --scenarios-dir examples/scenarios \
  --config examples/modelpin.yaml

modelpin check \
  --to claude-opus-4-7 --from claude-opus-4-6 \
  --provider fake \
  --fixtures examples/traces/demo_traces.json \
  --scenarios-dir examples/scenarios \
  --config examples/modelpin.yaml
```

---

## The gate (must stay green)

```bash
pytest            # the full offline suite — no live API calls, no key required
ruff check .      # linter
black .           # formatter (line length 100)
```

Run these before every PR. CI enforces all three.

---

## Testing ethos — golden tests, no live calls

The diff engine (`modelpin/diff/`) is the core of the product. Its tests use **recorded traces and canned fixtures** — never real provider API calls. This is deliberate:

- CI stays cheap and deterministic (anyone can run it, no key required).
- A test that passes only when OpenAI is up isn't a test — it's a prayer.
- The fake provider (`--provider fake`, `providers/fake.py`) replays any fixture you give it, so you can write a regression test from a single captured trace.

If you're adding to or modifying `diff/`, add a golden test that exercises the change offline. The existing tests in `tests/test_diff*.py` show the pattern.

**Do not "fix" the intentional design choices in `diff/`** without calibration evidence:
- `confidence = min(p)` for an `unchanged` verdict is correct — it reads "the weakest signal we have."
- The conservative effect-size floors (`MIN_TOOL_TVD`, `MIN_REFUSAL_DELTA`, `MIN_SEMANTIC_DELTA`) are biased toward false negatives on purpose. A false alarm erodes trust permanently. If you want to tighten them, bring a labeled dataset.

---

## What we're happy to receive

- **New scenarios** for `examples/suite/` or `examples/report-suite/` — high-value cases are ones that hit real migration seams (tool trajectory changes, refusal flips, format breaks). Discriminating scenarios that most models handle identically aren't interesting; ones where model behavior actually diverges across a version bump are. Include a short note in the PR explaining which migration pair surfaced the pattern.
- **New provider adapters** — implement `ProviderAdapter` from `modelpin/providers/base.py`. Must be key-safe (scrub `sk-`/`Bearer` from all error text), mock-tested offline, and multi-turn (drive the tool loop to `MAX_TOOL_TURNS`). See `providers/openai.py` and `providers/google.py`.
- **Diff engine improvements** — only with calibration evidence. Bring labeled pairs (real migration traces preferred over synthetic) and show the FP/FN tradeoff before and after.
- **Bug fixes** — open an issue first if the fix touches the diff engine or a trust-related claim.
- **Docs / examples** — always welcome.

---

## Commit style

[Conventional commits](https://www.conventionalcommits.org/):

```
feat: add Anthropic adapter
fix(diff): fold Unicode apostrophe glyphs in refusal detector
docs: clarify cross-vendor judge setup in README
test: add golden test for tool-call subset match
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

Keep the subject line under 72 characters. Body is optional but useful for anything non-obvious (design choice, calibration result, edge case discovered).

---

## Pull request checklist

- [ ] `pytest` passes (all tests, offline)
- [ ] `ruff check .` clean
- [ ] `black .` clean (no diff)
- [ ] New code has tests — including a golden/offline test for any diff engine change
- [ ] No hardcoded API keys or credentials anywhere
- [ ] If you changed a trust claim (FP rate, thresholds, verdict logic), the PR body explains the evidence

---

## Reporting bugs

Open a GitHub issue. For behavioral diff bugs (false positives / false negatives), include:

- The two models (`--from` / `--to`) and provider
- The command you ran
- Expected verdict vs actual verdict
- Whether you think it's a **false positive** (Modelpin said "regression" but behavior was equivalent) — this is the north-star metric and we treat FP reports as high-priority

If you can share the raw baseline JSON (`--store-dir`), that's the most useful artifact.

---

## Using Claude Code

This project includes a `CLAUDE.md` with full context for the engine's design decisions. If you're pairing with Claude Code on a contribution, point it there first — especially the "design guardrails" section, which documents the intentional choices that look like bugs.

```bash
claude    # reads CLAUDE.md automatically
```
