"""The false-positive numbers `docs/fp-measurement.md` publishes are re-derived, offline, from
the committed run-of-record artifacts - so the document cannot drift from the run.

MP-83 recorded the gap this closes: *"closing it against the run itself needs a committed
artifact of the run, which the repo does not have."* Since MP-205 the run IS in the repo
(`reports/fp-runs/<date>/*.jsonl`, one JSON line per trial with its traces), and the document
embeds the aggregator's rendering of it between two HTML-comment markers. This test regenerates
that rendering from the artifacts through `scripts/fp_aggregate.py` - the same pure functions
the harness prints with - and requires the embedded block to match byte for byte.

Offline by construction: nothing here calls a provider (ADR-0006). The artifacts are pruned from
the sdist (`MANIFEST.in`), so inside an unpacked sdist this file skips rather than fails, exactly
as the `.github/` repo-hygiene tests do.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import fp_aggregate as agg  # noqa: E402
from scripts.fp_measurement import upper_bound_95  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "fp-measurement.md"
RUNS = REPO / "reports" / "fp-runs"

BEGIN = "<!-- fp-run-of-record:begin"
END = "<!-- fp-run-of-record:end -->"


def _artifacts() -> tuple[str, list[Path]]:
    """The run directory the document names, and its artifacts in name order.

    The marker carries either a bare date, resolved under `reports/fp-runs/`, or a
    repo-relative directory. `[M] 2026-09-07` (MP-216) the second form became necessary when
    the run of record was re-scored under ADR-0040: those artifacts are deliberately NOT in
    `reports/fp-runs/`, because a re-score living beside its source is what
    `tests/test_cross_judge_agreement.py` exists to prevent.
    """
    doc = DOC.read_text(encoding="utf-8")
    m = re.search(rf"{re.escape(BEGIN)} (\S+) -->", doc)
    if m is None:
        pytest.fail("docs/fp-measurement.md has no `fp-run-of-record:begin <where>` marker")
    where = m.group(1)
    directory = (REPO / where) if "/" in where else (RUNS / where)
    paths = sorted(directory.glob("*.jsonl"))
    if not paths:
        pytest.skip(f"{where} is not present (pruned from the sdist)")
    return where, paths


def _embedded_block() -> str:
    doc = DOC.read_text(encoding="utf-8")
    start = doc.index(BEGIN)
    start = doc.index("-->", start) + len("-->")
    end = doc.index(END)
    return doc[start:end].strip("\n")


def test_the_document_embeds_exactly_what_the_artifacts_say():
    """The load-bearing assertion. `[M]` The 2026-08 document published `Detection: 2/2` for a
    run the harness scored `2/3`, by hand. A number the reader can re-derive from a file in the
    repo cannot be adjusted by hand without this going red."""
    _, paths = _artifacts()
    regenerated = "\n".join(agg.render(agg.summarise([str(p) for p in paths]))).strip("\n")
    assert _embedded_block() == regenerated, (
        "docs/fp-measurement.md's embedded run-of-record block differs from what "
        "`python scripts/fp_aggregate.py reports/fp-runs/<date>/*.jsonl` prints. Regenerate it; "
        "never edit the numbers by hand."
    )


def test_the_headline_quotes_the_pooled_numbers_the_artifacts_carry():
    """The sentence a skimmer reads must state the same fraction and bound as the block below
    it - the MP-81 failure was a headline that disagreed with its own table."""
    _, paths = _artifacts()
    pooled = agg.summarise([str(p) for p in paths])["pooled"]
    doc = DOC.read_text(encoding="utf-8")
    headline = doc[doc.index("**Headline") : doc.index("\n", doc.index("**Headline"))]
    fp, scored, reached = pooled["false_positives"], pooled["scored"], pooled["reached_verdict"]
    assert f"{fp} false alarm" in headline, headline
    assert f"{scored} scored" in headline, headline
    assert f"{reached} trials" in headline, headline
    for k, n in ((fp, scored), (fp, reached)):
        if n:
            assert (
                f"{upper_bound_95(k, n):.1%}" in headline
            ), f"the headline must carry the exact bound {upper_bound_95(k, n):.1%} for {k}/{n}"


def test_every_artifact_the_run_directory_holds_is_in_the_block():
    """A surface cannot be quietly dropped from the pooled numbers by deleting it from the
    command line: the block must name every artifact in the dated directory."""
    _, paths = _artifacts()
    block = _embedded_block()
    for p in paths:
        assert (
            p.name in block or p.name.replace(".jsonl", "") in block
        ), f"{p.name} is in reports/fp-runs but not in the published block"


def test_every_flagged_trial_is_listed_never_excluded():
    """ADR-0036 rule: a flag is published whatever a human thinks of it. The aggregator lists
    each one; the document must carry the list, including its '(none)' when there are none."""
    _, paths = _artifacts()
    summary = agg.summarise([str(p) for p in paths])
    block = _embedded_block()
    if not summary["flagged"]:
        assert "(none)" in block
    for f in summary["flagged"]:
        assert f"`{f['sid']}`" in block, f"flagged trial {f['sid']} is missing from the document"
