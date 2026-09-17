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
   > private repository, or use synthetic data in the scenarios. `modelpin baseline` warns when it
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
| `to` | — (required in check mode) | New model id to test against the baseline. Ignored in watch mode. |
| `mode` | `check` | `check` (default) replays `to` against the baseline and gates the pull request. `watch` reads the model-lifecycle registry, and for every model in `modelpin.yaml` inside its retirement notice window replays the vendor-named successor and opens one pull request carrying the verdict. Opt-in: a workflow that never sets this runs exactly what it ran before. |
| `registry-url` | (the Action's copy) | watch mode: URL of a `data/models.json` newer than the one shipped with the Action (fetched with `curl`, 20 s timeout). Empty uses the copy in this Action's checkout, so no network is needed. |
| `watch-scan` | `false` | watch mode: also read model ids from the repository's source (`mp watch --scan`). |
| `watch-max-prs` | `3` | watch mode: at most this many pull requests opened per run. |
| `watch-recheck-days` | `7` | watch mode: an open Modelpin pull request updated inside this many days is left alone and nothing is spent. |
| `fail-on-affected` | `true` | watch mode: fail the job while a declared model is inside its notice window, already retired, or unknown to the registry, so a scheduled run goes red until you migrate. |
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
| `store-dir` | `.modelpin` | Where baselines live, relative to `working-directory`. Both modes read it. |

## Outputs

| Output | Description |
|---|---|
| `verdict-exit-code` | `0` = no regression. `1` = a real regression was detected — the CI gate. `3` = the run could not answer: a compared scenario was unmeasurable, the provider rejected one, or nothing could be compared. `4` = the run never happened: a setup, configuration or environment failure (often a missing API-key secret), so no verdict exists and nothing is claimed about the model. A scenario with no recorded baseline is disclosed in the report and costs the run its clearance, but does not by itself change the exit code. |
| `report-path` | Path to the rendered Markdown report. |
| `watch-exit-code` | watch mode: `0` every declared model is known and active; `1` a declared model is inside its notice window or already retired; `3` a declared model is unknown to the registry (not a clearance); `4` nothing declared or the registry could not be read. |
| `watch-json` | watch mode: path to the `mp watch --json` document. |
| `pull-requests` | watch mode: comma-separated numbers of the pull requests opened or updated this run. |

## Watch mode: the pull request Modelpin opens

```yaml
on:
  schedule:
    - cron: "17 6 * * *"        # daily; the registry decides whether anything is due
  workflow_dispatch:
permissions:
  contents: write               # push the modelpin/migrate-* branch
  pull-requests: write          # open or update the pull request
jobs:
  model-watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: samarthputhraya/modelpin@v1
        with:
          mode: watch
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

- It opens exactly **one pull request per (retiring model, successor)**, on the branch
  `modelpin/migrate-<model>-to-<successor>`, and updates it on later runs rather than opening
  another. The pull request's only diff is `.modelpin/migrations/<model>-to-<successor>.md`:
  the report, the vendor page and fetch date behind the retirement, and the exact command to
  reproduce. Your own files are never edited.
- It opens **nothing** for a model with no recorded baseline (a verdict cannot be invented;
  the job log names the `mp baseline` to run), nothing when the check could not measure
  (exit 3) or could not run (exit 4), and nothing for a model the registry does not know.
  Those cases show in the job summary and, with `fail-on-affected`, in a red job.
- **Pull requests opened with the default `GITHUB_TOKEN` do not trigger your other workflows.**
  That is GitHub's rule against recursive runs. To run CI on a Modelpin pull request, pass a
  personal-access token or a GitHub App token as `github-token` and check out with the same
  token, or push an empty commit to the branch.
- **It fetches nothing unless you ask.** The registry it reads is the copy inside the Action's
  checkout, pinned by the ref you chose; every date in it carries the vendor page it came from.
  `registry-url` is the only network call, and it is yours.

## Notes

- **Keys** are passed via job `env:` from repo **secrets** — never inline. The candidate
  provider needs its key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, …); if
  `judge_model` is set in `modelpin.yaml`, that judge's provider key is needed too.
- **Permissions:** the job needs `pull-requests: write` to comment. On a pull request from a
  fork, GitHub gives no repository secrets and a read-only token: the check itself fails with
  exit 4 (missing key), and a comment that cannot be posted does not add a failure of its own.
- **Baseline strategy:** committing the baseline (recorded while the old model still worked)
  is the migration-true flow — the new model is diffed against known-good behavior. Use
  `baseline: true` only when the old model is still callable in CI.
- **Version pinning:** `modelpin` is on PyPI, so the default `modelpin-spec: modelpin[providers]`
  works. Pin a release with `modelpin-spec: "modelpin[providers]==0.4.0"`, or install an unreleased
  commit with `"modelpin[providers] @ git+https://github.com/samarthputhraya/modelpin@TAG"`.
