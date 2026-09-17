"""The registry's contract: every lifecycle claim carries its source, and the mirror is exact.

MP-276. `data/models.json` is the file contributors edit; `modelpin/watcher/registry.py` is the
copy that ships (ADR-0011: the wheel carries code only). `scripts/registry_check.py` validates
the JSON and regenerates the Python literal, and these tests pin what it enforces -- on the
type itself where the rule must bind a `--registry` file too, and on the script where the rule
needs the whole file (duplicates, dangling replacements, unknown keys).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from modelpin.models import Model
from scripts.registry_check import JSON_PATH, mirror_problems, validate

SRC = {"source_url": "https://example.test/deprecations", "fetched_at": "2026-09-17"}


def _raw(*models: dict) -> dict:
    return {"schema": 2, "models": list(models)}


def test_a_dated_entry_without_a_source_is_refused_by_the_type_itself() -> None:
    with pytest.raises(ValidationError, match="unsourced date is refused"):
        Model(id="m", provider="p", retired_at="2026-10-23")


def test_a_non_active_status_without_a_source_is_refused() -> None:
    with pytest.raises(ValidationError, match="unsourced date is refused"):
        Model(id="m", provider="p", status="deprecated")


def test_an_http_source_is_not_a_source() -> None:
    with pytest.raises(ValidationError):
        Model(
            id="m",
            provider="p",
            retired_at="2026-10-23",
            source_url="http://example.test",
            fetched_at="2026-09-17",
        )


def test_a_sourced_date_is_accepted_and_parsed_as_a_date() -> None:
    m = Model(id="m", provider="p", retired_at="2026-10-23", **SRC)
    assert m.retired_at == date(2026, 10, 23)
    assert m.fetched_at == date(2026, 9, 17)


def test_an_active_undated_entry_needs_no_source() -> None:
    assert Model(id="m", provider="p").status.value == "active"


def test_retired_at_never_precedes_deprecated_at() -> None:
    with pytest.raises(ValidationError, match="precedes"):
        Model(id="m", provider="p", deprecated_at="2026-10-23", retired_at="2026-10-01", **SRC)


def test_the_checker_refuses_a_duplicate_id() -> None:
    problems = validate(_raw({"id": "a", "provider": "p"}, {"id": "a", "provider": "p"}))
    assert any("duplicate id" in p for p in problems)


def test_the_checker_refuses_an_alias_that_is_another_entrys_name() -> None:
    problems = validate(
        _raw({"id": "a", "provider": "p"}, {"id": "b", "provider": "p", "aliases": ["a"]})
    )
    assert any("already the id or an alias" in p for p in problems)


def test_every_replacement_id_must_resolve_to_an_id_or_an_alias() -> None:
    dangling = validate(
        _raw({"id": "old", "provider": "p", "status": "deprecated", "replacement_id": "new", **SRC})
    )
    assert any("names no entry" in p for p in dangling)
    via_alias = validate(
        _raw(
            {"id": "old", "provider": "p", "status": "deprecated", "replacement_id": "new", **SRC},
            {"id": "new-2026", "provider": "p", "aliases": ["new"]},
        )
    )
    assert not via_alias


def test_a_fetched_at_in_the_future_is_refused() -> None:
    problems = validate(
        _raw(
            {
                "id": "m",
                "provider": "p",
                "retired_at": "2030-01-01",
                "source_url": SRC["source_url"],
                "fetched_at": "2030-01-01",
            }
        ),
        today=date(2026, 9, 17),
    )
    assert any("in the future" in p for p in problems)


def test_an_unknown_key_is_a_problem_not_a_silent_drop() -> None:
    problems = validate(_raw({"id": "m", "provider": "p", "retired_on": "2026-10-23"}))
    assert any("unknown key" in p and "retired_on" in p for p in problems)


def test_the_schema_number_is_checked() -> None:
    problems = validate({"schema": 1, "models": []})
    assert any("`schema` must be 2" in p for p in problems)


def test_the_repo_registry_passes_the_contributor_check() -> None:
    raw = json.loads(Path(JSON_PATH).read_text(encoding="utf-8"))
    assert validate(raw) == []
    assert mirror_problems(raw["models"]) == []


def test_the_repo_registry_sources_every_date_from_a_vendor_domain() -> None:
    """Not just https: the page must be the vendor's own. A blog post is not a source."""
    raw = json.loads(Path(JSON_PATH).read_text(encoding="utf-8"))
    vendor_hosts = {
        "openai": ("developers.openai.com", "platform.openai.com"),
        "anthropic": ("platform.claude.com", "docs.anthropic.com"),
        "google": ("ai.google.dev", "docs.cloud.google.com", "cloud.google.com"),
    }
    for entry in raw["models"]:
        url = entry.get("source_url")
        if url is None:
            continue
        host = url.split("/")[2]
        assert host in vendor_hosts[entry["provider"]], (entry["id"], url)


def test_the_repo_registry_has_no_unverified_placeholder_left() -> None:
    """The old seed carried self-declared placeholders; a registry that names its sources
    must not carry a row that admits it has none."""
    text = Path(JSON_PATH).read_text(encoding="utf-8")
    assert "UNVERIFIED placeholder" not in text
    ids = {m["id"] for m in json.loads(text)["models"]}
    assert "gpt-5.2" not in ids and "gpt-5.5" not in ids
