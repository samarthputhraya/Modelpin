"""MP-24: the exact same-model tool-channel rates in `docs/fp-measurement.md` are re-derived.

`[M] 2026-11-27` The worst-case figures (12/252 for one pool, 2.15% -> 0.25% with confirmation)
lived only in code docstrings (`modelpin/diff/confirm.py`, `diff/structural.py`) and a backlog
row; the public page had none of them. The page now carries them, and this test recomputes the
5-run figures from the real engine (`scripts/tool_null_rates.py`, about two seconds) and requires
the page to quote exactly those values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import tool_null_rates as tnr  # noqa: E402

DOC = REPO / "docs" / "fp-measurement.md"


@pytest.fixture(scope="module")
def section() -> str:
    doc = " ".join(DOC.read_text(encoding="utf-8").split())
    start = doc.index("### Exact rates under a constructed null")
    return doc[start : doc.index("## The assertion channel", start)]


@pytest.fixture(scope="module")
def numbers() -> dict:
    return tnr.summary(n=5, b=5, mode="strict")


def test_the_per_check_rates_are_the_engines(section: str, numbers: dict) -> None:
    for k in (2, 3):
        first = f"**{numbers[k]['worst_first'][0]:.2%}**"
        confirmed = f"**{numbers[k]['worst_confirmed'][0]:.2%}**"
        assert first in section, f"{k} trajectories: the page must quote {first}"
        assert confirmed in section, f"{k} trajectories: the page must quote {confirmed}"
    # The three-trajectory maxima sit at different mixes; the page names both.
    assert numbers[3]["worst_first"][1] != numbers[3]["worst_confirmed"][1]
    for key in ("worst_first", "worst_confirmed"):
        mix = " / ".join(f"{p:g}" for p in numbers[3][key][1])
        assert f"({mix})" in section, f"the page must name the mix {mix} for {key}"


def test_the_unlucky_baseline_figures_are_the_engines(section: str, numbers: dict) -> None:
    for k in (2, 3):
        q, base, chance = numbers[k]["even"]["worst_baseline"]
        assert max(base) == 5 and sum(base) == 5, f"the worst baseline is one trajectory: {base}"
        assert f"**{q:.2%}**" in section and f"**{q * q:.2%}**" in section, (k, q)
        # The chance of one NAMED trajectory filling the baseline, and of ANY one doing so.
        assert (
            f"{chance:.2%}" in section
        ), f"the page must give the chance of recording it: {chance}"
        assert (
            f"{k * chance:.2%}" in section
        ), f"the page must give the chance for any one: {k * chance}"


def test_the_pool_ceiling_is_the_engines_and_under_alpha(section: str, numbers: dict) -> None:
    from modelpin.diff import ALPHA

    hits, total = numbers["pool_ceiling"]
    assert f"**{hits} of the {total}**" in section and f"{hits / total:.2%}" in section
    assert hits / total <= ALPHA, "an exact permutation test may not exceed its level on a pool"


def test_confirmation_only_ever_lowers_the_rate(numbers: dict) -> None:
    """ADR-level property of `confirm.py`: confirmation can withhold an alarm, never create one."""
    for k in (2, 3):
        p = numbers[k]["worst_confirmed"][1]
        r = tnr.rates(p, n=5, b=5)
        assert r["confirmed"] <= r["first"]
