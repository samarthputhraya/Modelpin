# Modelpin

**Dependabot for AI models.** Know before the model breaks you.

[![CI](https://github.com/samarthputhraya/modelpin/actions/workflows/ci.yml/badge.svg)](https://github.com/samarthputhraya/modelpin/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/modelpin.svg)](https://pypi.org/project/modelpin/)
[![Python](https://img.shields.io/pypi/pyversions/modelpin.svg)](https://pypi.org/project/modelpin/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/samarthputhraya/modelpin/blob/main/LICENSE)

Your app runs on a language model. The provider ships a new version, or retires the one you use.
**Modelpin replays your app's real requests on the new model, tells you whether its behavior
actually changed — despite the randomness in every answer — and fails your CI build only when
the change is real.**

```text
$ modelpin check --to gpt-4.1
...
Modelpin: gpt-4o-mini -> gpt-4.1  (8 scenario(s) x5 runs)

REGRESSION refund_flow: tool-call behavior changed: ['lookup_order', 'issue_refund'] ->
['lookup_order', 'lookup_order', 'issue_refund'] (reproduced on a second set of candidate
runs, scored on its own) (confidence 0.99)
   baseline : tools lookup_order -> issue_refund; "Refund issued for order A-1042."
   candidate: tools lookup_order -> lookup_order -> issue_refund; "Refund issued for order A-1042."
MINOR invoice_total: output format drift: violates the scenario's text assertions (confidence 1.00)
   baseline : "Total: $42.00"
   candidate: "Total: 42.00"
OK 6 scenario(s) unchanged

-> Pin to gpt-4o-mini until resolved.
```

- **Built to not cry wolf.** Every scenario runs several times on both models; a difference only
  counts when it is statistically significant, large enough to matter, *and* reproduces on a
  fresh set of runs.
- **Sees what text diffs miss.** Changed tool-call plans, new refusals, and answers that mean
  something different — even when the final text looks identical.
- **Works with what you use.** OpenAI, Google Gemini (AI Studio or Vertex AI), OpenAI-compatible
  hosts (Groq, OpenRouter, Together, Cerebras), and Anthropic (API or Vertex AI; new in 0.4.0 and
  not yet validated by a live run). Cross-vendor comparisons work too.
- **Your key, your data.** Runs locally or in your CI with your own API key. Nothing is sent to
  Modelpin or any third party other than the model providers you configure (and, in CI, your own
  pull request).

Modelpin does not watch provider release or retirement feeds and does not open pull requests on
its own: you choose the candidate model, and a scheduled workflow can re-check it on your clock.

CLI: `modelpin` (alias `mp`). License: Apache-2.0.

---

## Contents

- [Install](#install)
- [Try it in 30 seconds, offline](#try-it-in-30-seconds-offline)
- [Use it on your app](#use-it-on-your-app)
- [How it works](#how-it-works)
- [Reading the results](#reading-the-results)
- [Run it in CI (GitHub Action)](#run-it-in-ci-github-action)
- [Providers and credentials](#providers-and-credentials)
- [Cost](#cost)
- [CLI reference](#cli-reference)
- [Troubleshooting](#troubleshooting)
- [Limits, and the evidence behind the design](#limits-and-the-evidence-behind-the-design)

---

## Install

Python 3.12 or newer.

```bash
pip install "modelpin[providers]"      # or: pipx install "modelpin[providers]"
modelpin version                        # -> modelpin 0.4.1
```

The `providers` extra installs the OpenAI, Anthropic and Google SDKs. Plain `pip install modelpin`
is enough for the offline demo.

> **Windows PowerShell:** type `modelpin`, not `mp`. PowerShell has a built-in `mp` alias
> (`Move-ItemProperty`) that wins over the program. `mp` works in cmd, bash and zsh.

Every `modelpin` command below also works as `python -m modelpin ...`, which needs no Modelpin
entry on your `PATH` — useful in a venv you have not activated, in a container, or when a
security policy blocks the generated `modelpin.exe`. Commands still print their follow-up
suggestions as `modelpin ...`; prefix those the same way.

## Try it in 30 seconds, offline

No API key, no cost. `modelpin init --demo` writes a small sandbox with four scenarios and
recorded model answers, and runs the same diff and statistics as a live check (with the
semantic judge off, since there is no key to call it with):

<!-- mp:smoke -->
```bash
modelpin init --demo
cd modelpin-demo
modelpin baseline --fixtures traces.json
modelpin check --to demo-model-v2 --fixtures traces.json
```

You will see one scenario per verdict:

| scenario | verdict | why |
|---|---|---|
| `greeting` | `unchanged` | same behavior on both models |
| `refund_request` | `regression` | the new model calls `lookup_order` twice — the final text is identical, so a text diff would miss it |
| `angry_customer` | `regression` | the new model refuses an action the old one performed |
| `invoice_parse` | `changed_minor` | `"Total: $5"` became `"Total: 5"`, breaking the scenario's text check |

The command exits **1** because regressions were found — that is what fails a CI build. The full
report is written to `.modelpin/last-report.md`, the same file the GitHub Action posts on a pull
request. Edit `traces.json` and re-run to watch the verdicts move.

## Use it on your app

**1. Set up.** In your repository:

```bash
modelpin init
```

`init` reads your code to find the model you already call (for example `gpt-4o-mini` in
`client.chat.completions.create(model="gpt-4o-mini", ...)`) and writes `modelpin.yaml` with that
model, its provider, and a judge model different from your current model. (If you later want
to check the judge's own model as a candidate, change `judge_model:` first.) It also writes a starter scenario in
`scenarios/`. It never overwrites existing files. `modelpin scan` shows every model id it finds.

**2. Describe what your app does.** Put a few real requests from your app in `scenarios/`, one
JSON file each — the system prompt, the user message, and any tools. For example:

```json
{
  "id": "route_ticket",
  "name": "Route a support ticket to the right queue",
  "input": {
    "messages": [
      {"role": "system", "content": "Route the ticket. Reply with exactly one of: billing, bug, account, other."},
      {"role": "user", "content": "I was charged twice for my March invoice."}
    ],
    "temperature": 0
  },
  "assertions": {"must_contain": ["billing"]}
}
```

Templates for classifiers, JSON extraction, refusal policies, tool-using agents and free-text
answers: **[Writing scenarios](https://github.com/samarthputhraya/modelpin/blob/main/docs/writing-scenarios.md)**.
`modelpin init --agent-example` adds a runnable tool-calling agent scenario.

**Faster: draft them from your code.** `modelpin draft app/support.py` sends that one file to your
configured model (one call, your key, key-shaped strings redacted first) and writes draft
scenarios to `scenarios/.drafts/`, which `baseline` and `check` ignore. The system prompt and
tools are copied only if they appear in the file; the user messages, canned tool results and
suggested assertions are invented and marked as such. Review each draft, then move it into
`scenarios/`.

**3. Record your current model.**

```bash
export OPENAI_API_KEY=sk-...     # your key; see "Providers and credentials" for the others
modelpin baseline
```

Each scenario runs 5 times; the results are saved to `.modelpin/baseline-<model>.json`. Commit
that file if you want CI to compare against it (it holds model outputs, not your API key).

**4. Check a candidate model.**

```bash
modelpin check --to gpt-4.1
```

Before it spends anything, `check` prints how many calls the run will make. Then it replays your
scenarios on the candidate, compares, and prints a verdict per scenario.

A good first sanity check is to compare your model against itself
(`modelpin check --to <your current model>`): it should come back `unchanged`. If a scenario
flags against itself, its prompt leaves the model too much freedom — see
[Writing scenarios](https://github.com/samarthputhraya/modelpin/blob/main/docs/writing-scenarios.md#habits-that-make-scenarios-work).

## How it works

```text
 scenarios/*.json ──► modelpin baseline ──► N runs on your current model (saved)
                                                   │
 modelpin check --to <candidate> ──► N runs on the candidate
                                                   │
      compare the two sets of runs, signal by signal:
        • tool calls (which tools, in what order)     • refusals
        • meaning, judged by a separate LLM            • your must_contain / must_not_contain checks
                                                   │
      a regression must be (1) statistically significant, (2) large enough to matter,
      and (3) reproduce on N fresh candidate runs
                                                   │
      verdict per scenario + exit code + .modelpin/last-report.md
```

Models are random: the same prompt gives different words every time, so comparing single answers
is useless. Modelpin compares **distributions** — "the old model called the refund tool on 5 of 5
runs, the new one on 0 of 5" — with an exact permutation test, a minimum effect size per signal,
and a confirmation replay that stops one unlucky **candidate** sample from turning a build red.
It does not re-record the baseline: if the same scenario flags again and again against the same
model, the recorded baseline itself may be unusual — re-record it with `modelpin baseline`.

The full explanation, in plain language, with the exact rule at the end:
**[How Modelpin works](https://github.com/samarthputhraya/modelpin/blob/main/docs/how-it-works.md)**.

## Reading the results

| Verdict | Meaning | Exit code |
|---|---|---|
| `unchanged` | no significant behavior change | `0` |
| `changed_minor` | something moved — a text check started failing, tool arguments changed, or a regression did not reproduce. Read it; it does not fail the build | `0` |
| `regression` | a real, reproduced change in tool calls, refusals, or meaning | **`1`** |
| `insufficient_evidence` | one side recorded nothing usable (for example empty answers) | `3` (unless another scenario regressed: `1` wins) |

| Exit code | Meaning |
|---|---|
| `0` | no regression |
| `1` | at least one regression — pin your current model until you have looked |
| `3` | the run could not fully answer (a scenario was unmeasurable or rejected by the provider, or nothing could be compared). **Not** a clean result |
| `4` | Modelpin could not run at all: missing key, invalid flag value, unreadable config, no scenarios. Nothing was measured |
| `2` | usage error: an unknown option or a missing `--to` |

Every flagged scenario shows one example run from each model, so you can see what changed.
Each check also writes `.modelpin/runs/check-<from>-to-<to>-<time>.json`: the exit code, every
verdict with its signals, and every candidate run — for scripts, and for reading the full runs
behind a verdict.

"Regression" means *your app's behavior changed from the baseline* — Modelpin does not judge
which model is better. A new model that starts calling a tool your prompt asked for is a change
worth reviewing, even if you like it.

When a run's scenarios could not have detected certain kinds of change (for example, no judge
configured and no tool calls), the output says so under `coverage:` instead of implying a clean
bill of health.

## Run it in CI (GitHub Action)

Commit `modelpin.yaml`, `scenarios/` and `.modelpin/baseline-<model>.json`, then add
`.github/workflows/modelpin.yml`:

```yaml
name: Modelpin

on:
  pull_request:             # did MY change (a prompt, a model id) break it?
  workflow_dispatch:        # run by hand the day a provider ships a new model
  schedule:
    - cron: "0 9 * * 1"     # re-check the same candidate weekly (Mondays 09:00 UTC)

permissions:
  contents: read
  pull-requests: write      # lets the action post its report as a PR comment

jobs:
  model-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: samarthputhraya/modelpin@v1
        with:
          to: gpt-4.1             # the candidate model to vet
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}   # from repository secrets
```

The action installs Modelpin, runs `modelpin check`, posts (and updates in place) a PR comment
with the report, and fails the job on a regression (exit `1`), an unmeasurable run (`3`) or a
setup error (`4`) — with an annotation saying which.

Inputs: `to` (required), `from`, `provider` (defaults to `modelpin.yaml`), `config`,
`scenarios-dir`, `runs`, `match`, `confirm`, `baseline` (record a fresh baseline first),
`comment`, `fail-on-regression`, `github-token`, `modelpin-spec`, `python-version`,
`working-directory`. Outputs: `verdict-exit-code`, `report-path`. More:
[`actions/README.md`](https://github.com/samarthputhraya/modelpin/blob/main/actions/README.md).

Things GitHub does that are worth knowing: scheduled workflows run only on the default branch,
in a public repository GitHub disables them after 60 days without activity, pull requests from
forks get no repository secrets (so the check exits `4` there), and a scheduled run needs your
baseline model to still be available if you record baselines in CI.

## Providers and credentials

Modelpin always uses **your** credentials, read from the environment. It never stores, logs or
ships a key. Error messages are scrubbed of key-shaped strings with a recognisable prefix (`sk-`,
`gsk_`, `AIza`, `ya29.`, AWS, GitHub, PEM); a key with no distinctive prefix, such as a bare hex
key, is not recognised.

| Provider (`providers:` / `--provider`) | Credentials | Notes |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | Chat Completions, multi-turn tool calls. Reasoning models (`o1`/`o3`/`o4`/`gpt-5*`) get `max_completion_tokens` and no temperature |
| `anthropic` | `ANTHROPIC_API_KEY`, **or** Claude on Vertex AI: `ANTHROPIC_VERTEX_PROJECT_ID` + `gcloud auth application-default login` (`CLOUD_ML_REGION` defaults to `global`) | Messages API, multi-turn tool calls. Newer Claude models accept only default sampling, so a scenario's `temperature` is not sent to them. Verified offline against the SDK's request shapes; not yet validated by a live run |
| `google` | `GEMINI_API_KEY` (AI Studio), **or** Vertex AI: `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` + application-default login | Multi-turn tool calls. On Vertex, `GOOGLE_CLOUD_LOCATION` defaults to `global`; in the project's testing `gemini-3.x` ids were not served on `us-central1`, so set it only if you need a specific region |
| `groq`, `openrouter`, `together`, `cerebras` | `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `CEREBRAS_API_KEY` | OpenAI-compatible endpoints. Groq has a free tier |

**The judge** (`judge_model:` in `modelpin.yaml`) decides whether two answers mean the same thing.
It can run on any provider above; set `judge_provider:` when the model id does not name its
vendor (`gpt-*`, `claude-*` and `gemini-*` do; `openai/gpt-oss-120b` on Groq does not). Choose a
model that is neither the one you run today nor the candidate. Remove `judge_model:` to compare
only tool calls, refusals and text checks, with no extra calls.

**Cross-vendor:** the baseline and the candidate can be on different providers — record the
baseline with one `--provider`, check with another (`modelpin check --provider google --to
gemini-3.1-flash-lite`). `check` reads the baseline from disk, so only the candidate's key is needed
(plus the judge's).

**Behind a corporate proxy:** on Windows and macOS Modelpin trusts the operating system's
certificate store, so a TLS-inspecting proxy your machine already trusts works. On Linux, set
`SSL_CERT_FILE` to a CA bundle that includes your proxy's root and the public roots (for Vertex AI
also set `REQUESTS_CA_BUNDLE` to the same file).

## Cost

Everything runs on your key, and `check` prints the size of the run before it spends:

```text
provider=openai from=gpt-4o-mini to=gpt-4.1 runs=5 match=strict | 8 scenario(s) from scenarios -> 40 replays, >=40 paid calls + up to 360 judge calls
a scenario flagged as a regression is replayed again before it can fail the build: +5 replays, up to 45 judge calls per flagged scenario (--no-confirm to skip)
```

- **Replays** = scenarios × runs. A scenario without tools is one model call per replay; a scenario
  with tools can take up to 6 (one per tool-loop turn).
- **Judge calls** are an upper bound; identical answers skip the judge and it stops at the first
  match, so the real number is usually much lower.
- **Confirmation:** only a scenario flagged as a regression is replayed again; the second line says
  how much that adds.

Rough guide: 8 scenarios without tools at 5 runs is 40 candidate calls plus judge calls; multiply
the calls shown by your provider's price. `modelpin baseline` costs the same replays on your
current model, once.

## CLI reference

| Command | What it does |
|---|---|
| `modelpin init [dir]` | Write `modelpin.yaml` and a starter scenario, configured from the models your code calls. `--demo` writes the offline sandbox; `--agent-example` adds a tool-calling agent scenario. Never overwrites. |
| `modelpin scan [path]` | List the model ids a repository (or a single file) uses, and where. |
| `modelpin draft <file>` | Draft scenarios from one file of your app into `scenarios/.drafts/` for review (one model call). `--count`, `--model`, `--provider`. |
| `modelpin baseline` | Run every scenario N times on your current model and save the results. |
| `modelpin check --to <model>` | Replay on a candidate, compare with the baseline, print verdicts, write the report, exit `0`/`1`/`3`/`4`. |
| `modelpin report --to <new> --from <old> --suite-dir <dir>` | Replay a scenario suite on two models and write a reproducible, publishable Markdown report plus a JSON sidecar under `reports/`. Always exits 0. |
| `modelpin version` | Print the version. |

Common flags for `baseline` and `check`: `--provider`, `--runs`, `--config`, `--scenarios-dir`,
`--store-dir`, and `--fixtures` (required with `--provider fake`). `baseline` takes `--model`;
`check` takes `--from`, `--match strict|unordered|subset|superset` (how strictly tool-call
sequences must agree; a scenario's own `"match"` overrides it) and `--no-confirm`. Run any command
with `--help` for details.

## Troubleshooting

**`error: OPENAI_API_KEY is not set` (exit 4).** Export the key for the provider in
`modelpin.yaml` (see [Providers and credentials](#providers-and-credentials)), or change
`providers:` to one you have a key for.

**PowerShell answers `mp` with `Cannot find path ...`, `missing mandatory parameters`, or a
`Supply values for the following parameters` prompt.** That is PowerShell's built-in `mp` alias.
Press Ctrl+C and type `modelpin`.

**`modelpin: command not found`, or Windows blocks `modelpin.exe`.** The console script lives in
your environment's `Scripts/` (or `bin/`) directory, which may not be on `PATH` — and on Windows,
Application Control can refuse a freshly written `.exe`. Run `python -m modelpin ...` instead; it
is the same CLI and needs no `PATH` entry.

**`The Google GenAI SDK is not installed` (or the OpenAI/Anthropic one), exit 4.** You most
likely installed plain `modelpin`. Run `pip install "modelpin[providers]"`. `modelpin init` also
warns about a missing SDK before it suggests `modelpin baseline`, whether it is writing
`modelpin.yaml` or adopting one that already exists.

**`rate limit or quota exceeded`.** Modelpin already retried with backoff. Wait for the quota
window, lower `--runs`, or check billing. On Gemini's AI Studio, *"prepayment credits are
depleted"* means that key bills a separate prepaid wallet; Vertex AI bills Google Cloud credit
instead. Claude on Vertex AI may need a per-model quota granted in the Google Cloud console first.

**`CERTIFICATE_VERIFY_FAILED`.** You are behind a TLS-inspecting proxy on Linux: set
`SSL_CERT_FILE` to your organisation's CA bundle.

**`scenario ... changed since its baseline`.** You edited the scenario after recording it, so
comparing would measure your edit, not the model. Run `modelpin baseline` again. Upgrading from a
version before 0.3.0 needs one fresh `modelpin baseline` for the same reason.

**A scenario flags `regression` against the same model.** The prompt leaves the model real
freedom (for example a tool it may call "when useful"). Tighten the instruction, or add
`"match": "subset"` to that scenario. See
[Writing scenarios](https://github.com/samarthputhraya/modelpin/blob/main/docs/writing-scenarios.md).

**`insufficient_evidence`.** A model returned empty answers — often a `max_tokens` too small for a
reasoning model, or a content filter. The console note says which side was empty. (A wrong model
id shows up differently: the scenario is listed as *could not be replayed*.)

**Exit 3 in CI.** The run could not answer for at least one scenario; the report lists which
and why. It is deliberately not a pass.

**A `warning: ... cannot report a regression` before the run.** `--runs` is too low for the
statistics to ever reach significance. Use 5.

## Limits, and the evidence behind the design

Modelpin is designed so that **if it says it broke, it broke**, and it prefers to stay quiet on a
borderline change rather than raise a false alarm. Know the trade-offs:

- **It can miss subtle changes** — a behavior that shifts on only one run in five, or a meaning
  change the judge considers equivalent. Missing a borderline change is the deliberate direction
  of error.
- **It only measures your scenarios.** Behavior no scenario exercises is invisible. Coverage gaps
  are disclosed in every report.
- **The judge is a model too.** Its sensitivity depends on the model you pick. The project's
  semantic threshold was calibrated with an OpenAI judge; re-scoring the published measurements
  with a Gemini judge changed no alarm decision on 732 paired trials, but other judges are
  unmeasured.
- **It measures change, not quality.** It never says one model is better.

**What happened when it was run at scale, live:** 972 same-model checks across six Gemini
models raised **0** false alarms, and across six real Gemini upgrades it flagged 28 regressions,
every one of which an independent model rated a material behavior change —
[docs/live-validation.md](https://github.com/samarthputhraya/modelpin/blob/main/docs/live-validation.md),
with the bugs that campaign found in Modelpin itself.

The project publishes its own false-positive and detection measurements — how they were run,
the confidence bounds, the same-model false alarms it has observed (four so far: one on tool
calls, two from the semantic judge, one on a text assertion), and what the numbers do *not* show — in **[docs/fp-measurement.md](https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md)**.
Those measurements describe the engine without the confirmation replay, which can only remove
alarms. A worked multi-model example is the
**[Drift Map #1](https://github.com/samarthputhraya/modelpin/blob/main/docs/reports/modelpin-drift-map-1.md)**.

**Not goals:** Modelpin is a migration check. It is not an eval platform, an observability tool,
a prompt manager, a model gateway, or a leaderboard.

## Contributing and development

```bash
git clone https://github.com/samarthputhraya/modelpin
cd modelpin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,providers]"
python -m pytest -q
```

See [CONTRIBUTING.md](https://github.com/samarthputhraya/modelpin/blob/main/CONTRIBUTING.md)
and [SECURITY.md](https://github.com/samarthputhraya/modelpin/blob/main/SECURITY.md).

## License

**Apache-2.0.** See [LICENSE](https://github.com/samarthputhraya/modelpin/blob/main/LICENSE).
The open-source core (CLI, engine, Action) stays open.
