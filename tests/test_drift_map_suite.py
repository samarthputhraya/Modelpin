"""MP-108: the Drift Map's suite line is re-derived, not hand-kept.

ADR-0009 asks every public report for a suite hash. `docs/reports/modelpin-drift-map-1.md` had
none, because the run (2026-06-24) recorded none. `[M] 2026-11-27` the suite's files are
unchanged since the commit that published the run, so the page now gives the hash today's
`compute_suite_hash` computes for them. These tests keep that line true: if the suite or the
hash function moves, the page must be updated in the same change.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from modelpin.report.suite import compute_suite_hash
from modelpin.scenarios import load_scenarios

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "reports" / "modelpin-drift-map-1.md"
SUITE = REPO / "examples" / "drift-suite"


def test_the_page_quotes_the_hash_of_the_suite_it_names() -> None:
    page = PAGE.read_text(encoding="utf-8")
    m = re.search(r"`compute_suite_hash` as it stands today gives `(sha256:[0-9a-f]+)`", page)
    assert m, "the Drift Map no longer states its suite hash"
    scenarios = load_scenarios(SUITE)
    assert len(scenarios) == 12 and "12 scenarios" in page
    assert compute_suite_hash(scenarios) == m.group(
        1
    ), "the drift suite or compute_suite_hash changed; the Drift Map's suite line must say so"


def test_the_suite_is_unchanged_since_the_run_commit() -> None:
    try:
        log = subprocess.run(
            ["git", "-C", str(REPO), "log", "--format=%h", "--", "examples/drift-suite"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env-specific
        pytest.skip(f"git unavailable: {exc}")
    if log.returncode != 0 or not log.stdout.strip():  # pragma: no cover - sdist / shallow
        pytest.skip("no git history for examples/drift-suite here")
    commits = log.stdout.split()
    if len(commits) == 1 and not commits[0].startswith("935f260"):  # pragma: no cover
        pytest.skip("shallow clone: the only visible commit is not the run commit")
    assert len(commits) == 1 and commits[0].startswith("935f260"), (
        f"examples/drift-suite changed after the run commit ({commits}); the Drift Map says it "
        "is unchanged since this run"
    )
