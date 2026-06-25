# Security Policy

Modelpin is a trust product — it tells developers whether to ship a model migration — so we
treat its own security seriously. Thanks for helping keep it safe.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

We support the latest released `0.1.x`. Fixes ship in a new patch release.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **[Private Vulnerability Reporting](https://github.com/samarthputhraya/modelpin/security/advisories/new)**
(the "Report a vulnerability" button on the repository's *Security* tab). Include:

- a description and impact,
- steps to reproduce (a minimal scenario/config if relevant),
- the Modelpin version (`modelpin version`) and how it was invoked (CLI or GitHub Action).

We aim to acknowledge within **72 hours** and to ship a fix or mitigation as fast as the
severity warrants. We'll credit you in the release notes unless you'd prefer to stay anonymous.

## Design choices that limit blast radius

Modelpin is built BYO-key and tries hard to never become a way to leak a secret:

- **Your API keys never leave your machine/CI.** Replays call the provider directly with the
  **end user's** key, read from the environment. Modelpin never transmits a key to us or to any
  third party, and never persists one.
- **Secrets are scrubbed from all output.** Key-shaped tokens (`sk-`, `sk-proj-`, `sk-ant-`,
  `gsk_`, Google `AIza…`/`ya29.`/`AQ.`, and `Bearer …` headers) are redacted before anything
  is printed, logged, or written to a report (`scrub_secrets`).
- **The GitHub Action is script-injection safe.** Every caller-controlled input flows through
  the step environment as data and is never interpolated into shell text; the PR-comment body
  is markdown-escaped before posting.
- **No code execution from scenarios.** Scenarios are plain data (messages/tools/assertions);
  Modelpin does not `eval` or execute scenario content.

## Scope

In scope: the CLI, the engine, the GitHub Action, and the published `modelpin` package.
Out of scope: vulnerabilities in upstream model-provider SDKs or the providers themselves
(please report those to the respective vendor), and issues that require a malicious local
environment you already control.
