"""MP-319: a platform date README quotes names the page it came from.

`[M] 2026-11-26` claims review: README quoted Azure's o3 retirement date and Bedrock's Claude
Sonnet 4.5 end-of-life date with no source in the repo, ops notes or CHANGELOG. Both were
re-read on 2026-11-27 (`[S]` the two pages below) and are now cited where they are stated.
The vendor dates come from the registry, whose entries carry their own `source_url`.
"""

from __future__ import annotations

from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"

_SOURCES = {
    "on Azure": "learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule",
    "on Bedrock": "docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle",
}


def test_every_platform_date_in_the_readme_names_its_source() -> None:
    text = " ".join(README.read_text(encoding="utf-8").split())
    quoted = [platform for platform in _SOURCES if platform in text]
    assert quoted, "README no longer quotes a platform date; delete this test deliberately"
    for platform in quoted:
        assert _SOURCES[platform] in text, (
            f"README quotes a date {platform} without linking the page it came from "
            f"({_SOURCES[platform]}); cite it with the date it was read, or drop the date"
        )
