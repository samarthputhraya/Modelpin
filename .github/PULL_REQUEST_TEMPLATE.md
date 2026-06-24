## What and why

<!-- One paragraph. What does this PR do, and why does it belong in Modelpin? -->

## Changes

<!-- Bullet list of the meaningful changes. -->

## Tests

- [ ] `pytest` passes (all offline — no live API calls in new tests)
- [ ] `ruff check .` clean
- [ ] `black .` clean
- [ ] New behavior has a test (golden/offline preferred for diff engine changes)

## If this touches the diff engine or a trust claim

<!-- Does it change a verdict, a threshold, or the FP measurement? -->
<!-- If yes: what evidence supports the change? Labeled pairs, calibration results, etc. -->
<!-- The intentional choices (confidence=min(p), conservative floors) should not change without calibration data. -->
