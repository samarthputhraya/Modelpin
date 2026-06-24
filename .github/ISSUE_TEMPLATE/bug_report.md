---
name: Bug report
about: Something behaves incorrectly — wrong verdict, crash, bad output
labels: bug
---

## What happened

<!-- One sentence. -->

## Command you ran

```bash
# paste the full command, with --provider, --from, --to, --runs, etc.
# Remove any real API keys before posting.
```

## Models and provider

- **From model:** <!-- e.g. gpt-4o-mini -->
- **To model:** <!-- e.g. gpt-4.1 -->
- **Provider:** <!-- openai / google / groq / fake -->
- **Runs (`--runs`):** <!-- default is 5 -->

## Expected verdict vs actual verdict

| Scenario | Expected | Actual | Confidence |
|----------|----------|--------|------------|
| | | | |

## Is this a suspected false positive?

<!-- A false positive = Modelpin said "regression" but the model behavior was actually equivalent. -->
<!-- This is our north-star metric. Please flag it clearly if so. -->

- [ ] Yes, I believe this is a false positive
- [ ] No, it's a different kind of bug (crash, wrong output format, etc.)

## Modelpin version

```
modelpin version
```

## Additional context

<!-- Paste the relevant section of .modelpin/last-report.md, or attach the baseline JSON if you can share it. -->
<!-- Stack trace if it's a crash. -->
