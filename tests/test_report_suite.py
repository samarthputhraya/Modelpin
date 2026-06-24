"""Tests for the public report-suite helpers (content hash + manifest) and the
integrity of the committed suite. All offline — no providers, no network."""

import re
from pathlib import Path

from modelpin.models import Scenario
from modelpin.report.suite import (
    DEFAULT_SUITE_ID,
    DEFAULT_SUITE_VERSION,
    compute_suite_hash,
    read_manifest,
    slug,
)
from modelpin.scenarios import load_scenarios

REPO = Path(__file__).resolve().parents[1]
REPORT_SUITE = str(REPO / "examples" / "report-suite")
HELD_OUT_SUITE = str(REPO / "examples" / "suite")

#: Pinned content hash of the committed public suite. If a scenario changes, bump
#: examples/report-suite/manifest.json's suite_version AND update this value in the same
#: commit — that is the point: a silent scenario mutation fails CI here.
GOLDEN_SUITE_HASH = "sha256:ffd99774f681"

#: Comparative-quality words a public report must never emit about a model (spec section 9).
_BANNED = re.compile(
    r"(?i)\b(better|worse|best|beats|wins|loses|superior|inferior|upgrade|downgrade)\b"
)


def _scn(sid: str, content: str = "hi") -> Scenario:
    return Scenario(
        id=sid,
        name=sid,
        kind="single",
        input={"messages": [{"role": "user", "content": content}]},
    )


def test_suite_hash_is_stable_for_committed_suite():
    scenarios = load_scenarios(REPORT_SUITE)
    assert len(scenarios) == 14
    assert compute_suite_hash(scenarios) == GOLDEN_SUITE_HASH


def test_compute_suite_hash_is_deterministic_and_order_independent():
    a = [_scn("a"), _scn("b"), _scn("c")]
    b = list(reversed(a))
    assert compute_suite_hash(a) == compute_suite_hash(b)


def test_suite_hash_changes_on_scenario_mutation():
    base = [_scn("a"), _scn("b")]
    mutated = [
        base[0],
        base[1].model_copy(update={"input": {"messages": [{"role": "user", "content": "x"}]}}),
    ]
    assert compute_suite_hash(base) != compute_suite_hash(mutated)


def test_read_manifest_returns_suite_identity():
    assert read_manifest(REPORT_SUITE) == ("modelpin-public-v2", "2.0.0")


def test_read_manifest_falls_back_to_documented_defaults_when_absent(tmp_path):
    assert read_manifest(tmp_path) == (DEFAULT_SUITE_ID, DEFAULT_SUITE_VERSION)


def test_read_manifest_falls_back_on_malformed_json(tmp_path):
    (tmp_path / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    assert read_manifest(tmp_path) == (DEFAULT_SUITE_ID, DEFAULT_SUITE_VERSION)


def test_read_manifest_falls_back_on_non_dict_json(tmp_path):
    (tmp_path / "manifest.json").write_text('["not", "a", "dict"]', encoding="utf-8")
    assert read_manifest(tmp_path) == (DEFAULT_SUITE_ID, DEFAULT_SUITE_VERSION)


def test_read_manifest_caps_oversized_values(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"suite_id": "' + "x" * 5000 + '", "suite_version": "' + "y" * 5000 + '"}',
        encoding="utf-8",
    )
    suite_id, suite_version = read_manifest(tmp_path)
    assert len(suite_id) <= 128 and len(suite_version) <= 64


def test_slug_is_filesystem_safe():
    assert slug("openai/gpt-4.1:turbo") == "openai_gpt-4.1_turbo"


def test_report_suite_ids_disjoint_from_held_out_suite():
    """Integrity guardrail: the public suite must not share scenario files with the
    held-out false-positive set, or the 0/8 FP claim's independence is compromised."""
    report_ids = {s.id for s in load_scenarios(REPORT_SUITE)}
    held_out_ids = {s.id for s in load_scenarios(HELD_OUT_SUITE)}
    assert report_ids and held_out_ids
    assert report_ids.isdisjoint(held_out_ids)


def test_public_suite_carries_no_comparative_quality_words():
    """Framing guardrail (spec section 9): scenario ids, names, and tool names flow into the
    published report (via the diff explanation), so the suite itself must be free of
    comparative-quality words — else a tool named e.g. `upgrade_plan` would smuggle a banned
    word into a report about named commercial models."""
    for s in load_scenarios(REPORT_SUITE):
        tokens = [s.id, s.name, *(s.input.get("tools") or [])]
        for token in tokens:
            hit = _BANNED.search(str(token))
            assert (
                hit is None
            ), f"scenario {s.id!r}: banned word {hit and hit.group(0)!r} in {token!r}"
