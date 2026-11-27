"""README's registry-freshness sentence is re-derived from `data/models.json`.

README states how many registry entries carry the latest re-read date. `[M] 2026-11-27` that
sentence ("45 of its 100 entries") was hand-kept and would have gone stale the moment the
registry was re-read, which is exactly when it is quoted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_readme_counts_the_entries_that_carry_the_latest_read_date() -> None:
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    m = re.search(r"on (\d{1,2} \w{3} \d{4}): (\d+) of its (\d+) entries carry that date", readme)
    assert m, "README no longer states how many registry entries carry the latest read date"
    day = datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
    models = json.loads((REPO / "data" / "models.json").read_text(encoding="utf-8"))["models"]
    dates = Counter(e["fetched_at"] for e in models)
    assert day == max(dates), f"README names {day}; the newest fetched_at is {max(dates)}"
    assert (int(m.group(2)), int(m.group(3))) == (
        dates[day],
        len(models),
    ), f"README says {m.group(2)} of {m.group(3)}; the registry has {dates[day]} of {len(models)}"
