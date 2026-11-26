# Security Policy

Modelpin tells developers whether a model migration changed their app's behavior, so a flaw in it
can mislead the people who rely on its verdict. Thank you for helping keep it safe.

## Supported versions

| Version | Supported |
| ------- | --------- |
| The latest release on PyPI | Yes |
| Any earlier release | No |

Only the latest release gets fixes. A fix ships as a new patch release, and the Action's `v1`
tag moves to it. Release tags (`vX.Y.Z`, such as `v0.5.3`) are protected by a repository rule
against moving or deleting them, so a bad release is fixed with a new version. A release tag
pins the Action's own steps; to pin the Modelpin version the Action installs, also set
`modelpin-spec` (for example `modelpin[providers]==0.5.7`).

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
**[private vulnerability reporting](https://github.com/samarthputhraya/modelpin/security/advisories/new)**
(the "Report a vulnerability" button on the repository's *Security and quality* tab). Include:

- what an attacker can do, and what they need to control to do it,
- steps to reproduce (a minimal scenario or config if relevant),
- the Modelpin version (`modelpin version`) and how it ran (CLI or GitHub Action).

If you cannot use that form, open an issue that says only that you have a security report and
need a private channel. Leave every detail out of the issue.

We aim to acknowledge a report within **72 hours** and to ship a fix or a mitigation as fast as
its severity warrants. We credit reporters in the release notes unless you prefer not to be named.

## What Modelpin does with your key and your data

- **Your key goes only to the model provider.** Modelpin reads your key from the environment (on
  Vertex AI, the Google SDK uses your Application Default Credentials instead), and the
  provider's SDK sends it to that provider's endpoint, or to the base URL or proxy your
  environment sets for that SDK. Modelpin has no server of its own and sends no telemetry. It
  does not write the key it reads from the environment to any file.
- **Provider error messages are scrubbed; model output is not.** Before Modelpin prints a
  provider's error message, it replaces key-shaped strings that have a recognisable prefix:
  `sk-` (which covers `sk-proj-` and `sk-ant-`), `gsk_`, Google `AIza`, `ya29.` and `AQ.`, AWS
  `AKIA`/`ASIA` key ids, GitHub `ghp_`-style and `github_pat_` tokens, Azure `AccountKey=`,
  complete PEM private-key blocks, and `Bearer` headers. `modelpin draft` removes the same shapes
  from your file before sending it to the model. A key with no recognisable prefix, such as a
  bare hex key, is not recognised. Model outputs and tool-call arguments are not scrubbed:
  baselines and run files record them verbatim, and the console (so the CI log), reports and the
  pull-request comment quote them, shortened. Keep secrets out of scenario prompts, fixtures and
  tool results. `modelpin baseline` warns when a run's final output, or a tool-call argument
  whose value is plain text, holds a key-shaped string.
- **A committed baseline publishes model outputs.** A baseline (`.modelpin/baseline-*.json`)
  written by Modelpin 0.3.1 or later holds each recorded run's output and tool calls, with their
  arguments, but not the prompts. One written by an earlier version also holds the prompts until
  you re-record it. An output or a tool-call argument can still repeat text from a prompt or a
  tool result. On a public repository, committing a baseline publishes what it holds.
- **Scenarios and config are data.** Config, scenarios, fixtures, baselines and `--registry`
  files are read with `yaml.safe_load` or `json`. Modelpin does not evaluate or execute anything
  in them.
- **The GitHub Action passes inputs as data.** Every caller-controlled input reaches the shell
  steps through an environment variable and is never spliced into the script text.

## Running the Action safely

- **Trigger it with `on: pull_request`,** as our examples do. On a public repository, a pull
  request from a fork then gets no repository secrets and a read-only token. Do not run Modelpin
  under `pull_request_target` or `workflow_run` with the pull request's own code checked out:
  that code supplies the config, scenarios and baseline the check reads, and your secrets would
  be in scope.
- **Review the files the verdict depends on.** A pull request that edits `modelpin.yaml`,
  `scenarios/` or `.modelpin/baseline-*.json` changes what is compared. Review those files as you
  review code. A `CODEOWNERS` entry for them, with "Require review from Code Owners" turned on in
  branch protection, makes that review required.

## Scope

In scope: the CLI, the engine, the GitHub Action, the published `modelpin` package, and any file
Modelpin reads from a pull request. Out of scope: vulnerabilities in upstream model-provider SDKs
or in the providers themselves (please report those to the vendor), and issues that require a
malicious local environment you already control.
