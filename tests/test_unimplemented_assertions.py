"""No assertion field may exist that the engine does not read (MP-142 -> MP-147).

MP-142 established, by DIFFERENTIAL rather than by grep, that `expected_tool_calls` and
`output_schema` were consulted by nothing -- five trace configurations, verdict/confidence/
explanation byte-identical with the field set and with `assertions=None`:

    baseline SATISFIES exp, candidate VIOLATES (drops issue_refund)  regression @0.992  ==
    baseline VIOLATES, candidate SATISFIES                           regression @0.992  ==
    both SATISFY (no change)                                          unchanged @1.0    ==
    both VIOLATE identically                                          unchanged @1.0    ==
    candidate calls a tool the expectation forbids entirely          regression @0.992  ==

The first and last cases are what make that proof mean anything: an earlier reproduction
used only "both violate identically", which yields `unchanged` whether or not the field is
implemented -- Modelpin measures change RELATIVE to baseline -- so it could not discriminate
the defect it was named for.

**MP-147 deleted them.** That was a decision, not a cleanup: `compute_suite_hash` hashes the
VALIDATED pydantic model, so removing a field moves the content hash of both shipped suites
even though no scenario's MEANING changed. `examples/report-suite` (role `public`, ADR-0009)
is therefore versioned **2.0.0 -> 3.0.0**.

`[M] 2026-08-31`, and worth recording because half of MP-147's own prediction did not
reproduce:

| suite | before | MP-147 predicted | actual |
|---|---|---|---|
| `examples/report-suite` | `sha256:ffd99774f681` | `sha256:eed334061b5e` | `sha256:5cba1dc8b691` |
| `examples/suite` | `sha256:44cbde8e3b74` | `sha256:5482ccd734fd` | `sha256:3edf6b1ae19a` |

The prediction was computed with the fields deleted from the MODEL but the dead keys left in
the JSON FILES, where pydantic silently ignores them. `[M]` Reconstructing exactly that
reproduces `eed334061b5e` for `report-suite` to the digit -- and still does NOT reproduce
`5482ccd734fd` for `suite` (it gives `22a03cce1e6c`), so that half of the row was wrong by
some route this session did not chase. The files were cleaned too, which is the end state a
reader deserves, so the actual hashes are the ones pinned below.

**No measurement was invalidated.** Verdicts are byte-identical either way -- that is exactly
what MP-142's differential proved -- so `docs/fp-measurement.md`'s numbers, measured on the
held-out `examples/suite`, still describe the same behaviour. Only the content fingerprint of
the file set moved, and `fp-measurement.md` cites no hash.

Nothing was lost with the fields. `expected_tool_calls` was redundant with a channel that
WORKS: the tool-trajectory diff measures whether a scenario called the tool it should have,
distributionally over N runs, instead of against a static list.
"""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.diff import structural
from modelpin.models import Assertion
from modelpin.report.suite import compute_suite_hash
from modelpin.scenarios import load_scenarios, unrecognised_assertion_keys

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]

#: The names MP-147 removed. Pinned so reintroducing either one is a deliberate act with a
#: failing test attached, not a merge that looks harmless.
DELETED_FIELDS = ("expected_tool_calls", "output_schema")


# --- the invariant the whole row exists to establish -------------------------------------


@pytest.mark.parametrize("name", DELETED_FIELDS)
def test_the_dead_fields_are_gone(name):
    assert name not in Assertion.model_fields


def test_every_assertion_field_is_actually_read_by_the_engine():
    """THE generalisation of MP-142, and the reason this file survives the deletion.

    A field on `Assertion` that nothing reads is a promise the engine does not keep. Rather
    than re-proving that for two specific names, assert the property for EVERY field there
    is, so the next write-only field fails on the commit that adds it instead of surviving
    to be discovered by an audit months later.
    """
    src = inspect.getsource(structural.violates_text_assertions)
    for field in Assertion.model_fields:
        assert field in src, (
            f"`Assertion.{field}` is declared but `violates_text_assertions` never reads it. "
            "A field the engine ignores is the MP-142 defect: it silently does nothing. "
            "Either read it, or do not declare it."
        )


def test_no_shipped_scenario_declares_a_key_this_version_ignores():
    """Our own suites must not model the mistake the advisory warns users about."""
    for suite in sorted(p for p in (REPO / "examples").iterdir() if p.is_dir()):
        found = unrecognised_assertion_keys(suite)
        assert not found, f"{suite.name} declares unread assertion keys: {found}"


# --- the migration hazard the deletion CREATED, and how it is answered -------------------


def _suite(tmp_path, assertions: str) -> tuple[str, str]:
    suite = tmp_path / "scenarios"
    suite.mkdir()
    (suite / "s.json").write_text(
        '{"id":"s","name":"s","kind":"single",'
        '"input":{"messages":[{"role":"user","content":"hi"}]},'
        f'"assertions":{assertions}}}',
        encoding="utf-8",
    )
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    return str(suite), str(tmp_path / "modelpin.yaml")


def _check(tmp_path, assertions: str):
    suite, config = _suite(tmp_path, assertions)
    return runner.invoke(
        app,
        # fmt: off
        ["check", "--to", "m2", "--from", "m1", "--provider", "fake",
         "--scenarios-dir", suite, "--config", config,
         "--store-dir", str(tmp_path / "empty")],
        # fmt: on
    )


def test_a_users_stale_field_is_named_not_silently_dropped(tmp_path):
    """LOAD-BEARING, and the reason the advisory moved instead of being deleted with the
    fields. Pydantic ignores unknown keys, so the deletion ALONE would have taken a user's
    `expected_tool_calls` from documented-but-inert to invisible -- MP-142's defect made
    worse and pushed into a file we do not own."""
    out = " ".join(_check(tmp_path, '{"expected_tool_calls":["lookup_order"]}').output.split())
    assert "does not check" in out, out
    assert "expected_tool_calls" in out, out


def test_a_removed_field_gets_the_remedy_that_is_TRUE_FOR_IT(tmp_path):
    """`[M]` first-run review, 2026-08-31: the first cut answered every unknown key with the
    same sentence -- "this version checks `must_contain` / `must_not_contain`". That is right
    for a typo and WRONG here: there is no text assertion that expresses "expect this tool
    call", so the reader either guesses the field is safe to delete or wastes time trying to
    shoehorn a tool check into a string match. The remedy that is actually true -- the
    tool-trajectory channel already does this work -- existed only in the CHANGELOG and never
    reached the console."""
    out = " ".join(_check(tmp_path, '{"expected_tool_calls":["lookup_order"]}').output.split())
    assert "trajectory" in out.lower(), out
    assert "Delete the field" in out, out
    # ...and it must NOT be answered with the text-assertion line, which is the whole defect.
    assert "this version checks" not in out, out


def test_a_typo_is_caught_by_the_same_advisory(tmp_path):
    """A capability the field-based check never had: reading the RAW keys means a
    `must_containn` typo -- which no version of Modelpin has ever checked -- is now named
    rather than silently ignored. THIS is the case the generic remedy is right for."""
    out = " ".join(_check(tmp_path, '{"must_containn":["hi"]}').output.split())
    assert "must_containn" in out, out
    assert "this version checks" in out, out
    assert "trajectory" not in out.lower(), out  # not a removed field; no removal remedy


def test_both_kinds_in_one_suite_each_get_their_own_remedy(tmp_path):
    """They compose. A file can carry a stale field AND a typo, and answering only one of
    them is the same class of half-disclosure the rest of this codebase keeps closing."""
    out = " ".join(
        _check(tmp_path, '{"expected_tool_calls":["x"],"must_containn":["hi"]}').output.split()
    )
    assert "trajectory" in out.lower(), out
    assert "this version checks" in out, out


def test_a_suite_using_only_live_assertions_gets_no_note(tmp_path):
    """Anti-noise: the disclosure must not fire on a suite that declares nothing dead."""
    assert "does not check" not in _check(tmp_path, '{"must_contain":["hi"]}').output


def test_the_shipped_demo_declares_no_assertion_the_engine_cannot_check():
    """`[M]` `angry_customer` once shipped an `Assertion` whose ONLY field was
    `expected_tool_calls` -- an assertion that asserts nothing, in the suite a brand-new user
    runs first, which MP-141's census then counted as armed coverage."""
    from modelpin.demo import write_demo

    root = Path(tempfile.mkdtemp(prefix="modelpin-mp147-"))
    write_demo(root)
    scenarios = root / "modelpin-demo" / "scenarios"
    assert not unrecognised_assertion_keys(scenarios)
    for s in load_scenarios(str(scenarios)):
        if s.assertions is None:
            continue
        assert s.assertions.must_contain or s.assertions.must_not_contain, f"{s.id} asserts nothing"


# --- the published hashes, pinned at their POST-BUMP values ------------------------------


@pytest.mark.parametrize(
    "path,expected,was",
    [
        ("examples/report-suite", "sha256:5cba1dc8b691", "sha256:ffd99774f681"),
        ("examples/suite", "sha256:3edf6b1ae19a", "sha256:44cbde8e3b74"),
    ],
)
def test_the_published_suite_hashes_are_pinned(path, expected, was):
    """Unchanged in purpose, only in value: a shipped suite's fingerprint may not move as a
    side effect. `report-suite` is `public` under ADR-0009 and its manifest now says 3.0.0;
    `suite` is the held-out `score` set ADR-0025 forbids tuning on. Moving either again means
    editing this test in the same commit, deliberately."""
    assert compute_suite_hash(load_scenarios(str(REPO / path))) != was
    assert compute_suite_hash(load_scenarios(str(REPO / path))) == expected


def test_the_public_suite_version_records_the_bump():
    """A content hash that moved without its version moving is an unannounced change to a
    published artifact (ADR-0009)."""
    manifest = json.loads(
        (REPO / "examples" / "report-suite" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["suite_version"] == "3.0.0"
    assert manifest["suite_id"] == "modelpin-public-v2"
