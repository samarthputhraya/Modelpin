# Modelpin GitHub Action

Run Modelpin in CI: replay your scenarios on a **new model**, diff the behavior against a
committed **baseline**, post a PR-style report **as a comment on the pull request**, and
**fail the check** when a real regression is found — so you find out *before* you merge a
model bump that quietly breaks your app.

BYO-key: the replay uses **your** provider API key from repo secrets (cost + provider ToS;
see the project's `docs/`). Modelpin never ships or stores keys.

## Quickstart

1. In your repo, run `mp init`, add a few scenarios, then record a baseline for the model
   you depend on today and **commit it**:
   ```bash
   pip install "modelpin[providers]"
   mp init
   # ...edit modelpin.yaml + scenarios/...
   mp baseline --model gpt-4o-mini --provider openai   # writes .modelpin/baseline-*.json
   git add modelpin.yaml scenarios/ .modelpin/ && git commit -m "modelpin baseline"
   ```
2. Add a workflow that checks a candidate model on every PR (or when a model bumps):

   ```yaml
   # .github/workflows/modelpin.yml
   name: Modelpin
   on:
     pull_request:
   permissions:
     contents: read
     pull-requests: write        # required to post the PR comment
   jobs:
     check:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v7
         - uses: samarthputhraya/modelpin@v1     # this action (pin to a tag)
           with:
             from: gpt-4o-mini             # your committed baseline model
             to: gpt-5.5                    # the new model to test
             provider: openai
           env:
             OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   ```

On each PR you get a **sticky comment** (updated in place, never spammed) with the
behavioral diff, and the job **fails on a regression** so the bump can't merge silently.

## Cross-vendor

Set `provider` to any supported adapter and `to` to that vendor's model id — e.g. compare
your OpenAI baseline against Google or a free Llama host:

```yaml
        with:
          from: gpt-4o-mini
          to: gemini-3.1-flash-lite
          provider: google
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}   # if judge_model is an OpenAI model
```

`provider` accepts `openai | google | anthropic | groq | openrouter | together | cerebras`.
A free Groq/Llama key (`GROQ_API_KEY`) adds a third vendor whose *replays* cost nothing.
The judge is billed separately and is OpenAI-only — that is what the `OPENAI_API_KEY` line
above is for. Omit `judge_model` to keep the run structural and free.

## Inputs

| Input | Default | Description |
|---|---|---|
| `to` | — (required) | New model id to test against the baseline. |
| `from` | config | Baseline model id (else first `models:` in `modelpin.yaml`). |
| `provider` | `openai` | Candidate provider adapter. |
| `config` | `modelpin.yaml` | Path to the config. |
| `scenarios-dir` | config | Scenarios directory. |
| `runs` | config | Replays per scenario (≥5 recommended). |
| `match` | `strict` | Tool-call match: `strict\|unordered\|subset\|superset`. |
| `baseline` | `false` | Record a fresh baseline for `from` first (needs the old model still available; usually you commit the baseline instead). |
| `comment` | `true` | Post/update a sticky PR comment. |
| `fail-on-regression` | `true` | Fail the job on a regression (the migration gate). |
| `github-token` | `${{ github.token }}` | Token used to post the comment. |
| `modelpin-spec` | `modelpin[providers]` | `pip install` spec — pin a version or install from git pre-PyPI. |
| `python-version` | `3.12` | Python to set up. |
| `working-directory` | `.` | Where to run Modelpin. |

## Outputs

| Output | Description |
|---|---|
| `verdict-exit-code` | `0` = no regression, `1` = regression detected. |
| `report-path` | Path to the rendered Markdown report. |

## Notes

- **Keys** are passed via job `env:` from repo **secrets** — never inline. The candidate
  provider needs its key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, …); if
  `judge_model` is set in `modelpin.yaml`, that judge's provider key is needed too.
- **Permissions:** the job needs `pull-requests: write` to comment.
- **Baseline strategy:** committing the baseline (recorded while the old model still worked)
  is the migration-true flow — the new model is diffed against known-good behavior. Use
  `baseline: true` only when the old model is still callable in CI.
- **Version pinning:** `modelpin` is on PyPI, so the default `modelpin-spec: modelpin[providers]`
  works. Pin a release with `modelpin-spec: "modelpin[providers]==0.1.0"`, or install an unreleased
  commit with `"modelpin[providers] @ git+https://github.com/samarthputhraya/modelpin@TAG"`.
