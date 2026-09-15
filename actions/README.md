# Modelpin GitHub Action

Run Modelpin in CI: replay your scenarios on a **new model**, diff the behavior against a
committed **baseline**, post a PR-style report **as a comment on the pull request**, and
**fail the check** when a real regression is found — so you find out *before* you merge a
model bump that quietly breaks your app.

BYO-key: the replay uses **your** provider API key from repo secrets (cost + provider ToS;
see the project's `docs/`). Modelpin never ships or stores keys.

## Quickstart

1. In your repo, run `modelpin init` (it configures `modelpin.yaml` from the model your code
   already calls), add a few scenarios, then record a baseline for the model you depend on
   today and **commit it**:
   ```bash
   pip install "modelpin[providers]"
   modelpin init
   # ...add real cases to scenarios/, check modelpin.yaml...
   modelpin baseline                 # writes .modelpin/baseline-<model>.json
   git add modelpin.yaml scenarios/ .modelpin/ && git commit -m "modelpin baseline"
   ```

   > **Know what you are committing — especially to a public repository.**
   > `scenarios/` holds your prompts, exactly as you wrote them. The baseline file
   > (`.modelpin/baseline-*.json`) holds, for every run of every scenario, the model's
   > **output text and the arguments of every tool call it made**, verbatim. It does not
   > store your prompts. If your scenarios, or the answers a model gives to them, contain
   > anything you would not put in a public commit — a system prompt you consider
   > proprietary, a real customer record, an internal URL, a credential — keep this in a
   > private repository, or use synthetic data in the scenarios. `mp baseline` warns when it
   > sees a key-shaped token, but it cannot recognise a trade secret or a person's details.
   >
   > Only `baseline-*.json` needs committing. The rest of `.modelpin/` (`last-report.md`,
   > `runs/`) is per-run output, and Modelpin writes `.modelpin/.gitignore` so that only the
   > baselines (and that file) are picked up by `git add`.
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
             to: gpt-4.1-mini               # the new model to test
             # from / provider default to modelpin.yaml
           env:
             OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   ```

On each PR you get a **sticky comment** (updated in place, never spammed) with the
behavioral diff and an example run from each model for every flagged scenario, and the job
**fails on a regression** so the bump can't merge silently. A scenario that looks like a
regression is replayed once more first, and only fails the job if the regression reproduces.

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
Claude needs `ANTHROPIC_API_KEY`. A free Groq key (`GROQ_API_KEY`) adds a vendor whose
*replays* cost nothing. The judge is billed separately on whichever provider `judge_model`
names — that is what the second key above is for. Omit `judge_model` to skip judge calls.

## Inputs

| Input | Default | Description |
|---|---|---|
| `to` | — (required) | New model id to test against the baseline. |
| `from` | config | Baseline model id (else first `models:` in `modelpin.yaml`). |
| `provider` | config | Candidate provider adapter (else first `providers:` in `modelpin.yaml`). |
| `config` | `modelpin.yaml` | Path to the config. |
| `scenarios-dir` | config | Scenarios directory. |
| `runs` | config | Replays per scenario (≥5 recommended). |
| `match` | `strict` | Tool-call match: `strict\|unordered\|subset\|superset`. |
| `confirm` | `true` | Replay a flagged scenario again and fail only if the regression reproduces. `false` fails on the first sample. |
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
| `verdict-exit-code` | `0` = no regression. `1` = a real regression was detected — the CI gate. `3` = the run could not answer: a compared scenario was unmeasurable, the provider rejected one, or nothing could be compared. `4` = the run never happened: a setup, configuration or environment failure (often a missing API-key secret), so no verdict exists and nothing is claimed about the model. A scenario with no recorded baseline is disclosed in the report and costs the run its clearance, but does not by itself change the exit code. |
| `report-path` | Path to the rendered Markdown report. |

## Notes

- **Keys** are passed via job `env:` from repo **secrets** — never inline. The candidate
  provider needs its key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, …); if
  `judge_model` is set in `modelpin.yaml`, that judge's provider key is needed too.
- **Permissions:** the job needs `pull-requests: write` to comment. On a pull request from a
  fork GitHub grants a read-only token, so the comment is skipped (the step does not fail the
  job) and the report is still in the log and at `report-path`.
- **Baseline strategy:** committing the baseline (recorded while the old model still worked)
  is the migration-true flow — the new model is diffed against known-good behavior. Use
  `baseline: true` only when the old model is still callable in CI.
- **Version pinning:** `modelpin` is on PyPI, so the default `modelpin-spec: modelpin[providers]`
  works. Pin a release with `modelpin-spec: "modelpin[providers]==0.4.0"`, or install an unreleased
  commit with `"modelpin[providers] @ git+https://github.com/samarthputhraya/modelpin@TAG"`.
