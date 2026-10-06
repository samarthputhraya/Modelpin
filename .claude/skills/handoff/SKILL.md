---
name: handoff
description: Write HANDOFF.md capturing the exact current state so a fresh Claude session or a teammate taking the next shift can continue without re-reading the conversation. Use when the session is long, answers get worse, before /clear, or when a teammate swaps in.
---

# Handoff

Long sessions get worse: every message re-reads the whole history, so quality drops and tokens burn. The fix is to write the state down and start fresh.

## When
- The conversation is long (roughly 40+ turns, or Claude starts repeating itself or forgetting decisions).
- A teammate is taking over (sleep shift, switching laptops).
- Before `/clear` or `/compact`.

## Steps
1. Run `git status` and `git log --oneline -10` to see what actually changed. Trust the repo over memory.
2. Overwrite `HANDOFF.md` at the repo root (gitignored) with exactly these sections, filled from the repo and this session:

```markdown
# Handoff - <date time>, written by <name or "Claude session">

## Goal (one line)
## Demo path (the exact clicks a judge will see)
## Works now (verified, with how it was verified)
## Broken / in progress (file:line, error message, what was tried)
## Next 3 steps (concrete, in order)
## Decisions made (and why, one line each)
## Gotchas (env vars, ports, flaky things, commands that must be run first)
## How to run
```

3. Keep it under ~60 lines. Facts only. Mark anything unverified as "unverified".
4. Tell the user: "Handoff written. Run /clear, then start with: read HANDOFF.md and continue with step 1."

## Rules
- Never write "everything works" without the command or click that proved it.
- Don't paste large logs. Quote the one error line that matters.
