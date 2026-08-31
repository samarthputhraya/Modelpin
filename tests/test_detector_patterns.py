"""MP-135 -- the detector reported an emoji shortcode as a model id.

`[M] 2026-08-29`, dogfooding on a real app: `detector/__init__.py` compiled
`re.compile(r"\\bo[0-9][\\w.\\-]*\\b")`, which matched **`o2`** at
`rich/_emoji_codes.py:3381`, whose content is `"o2": "\\U0001f17e"` -- an emoji shortcode.
`o2` is not an OpenAI model at all. The pattern exists to catch `o1`/`o3`/`o4`, but a bare
two-character token matches ordinary text anywhere.

**This is the north-star metric surfacing in the detector**: the project spends 31 ADRs
defending the false-positive rate in `diff/` while the FIRST command a stranger runs
publishes model ids that do not exist.

`[M] 2026-08-31` A SECOND, NON-CONTRIVED FALSE POSITIVE, in this repo, found while
reproducing the first: `scan_repo('.')` reported `o3XPaKcS` from
`.modelpin/drift_cache_drift-suite.json` -- a random cache token.

`[M]` THE ROW'S "0 true positives" CLAIM IS FALSE and the fix is scoped on the corrected
reading: the pattern DOES catch real ids (`o1`, `o3`, `o4-mini`, `o3-mini` appear in
`modelpin/judge.py:97` and in the OpenAI SDK), so narrowing it must not lose them.

`[M]` Measured over 6,228 real third-party files under `site-packages`:

    OLD  \\bo[0-9][\\w.\\-]*\\b        556 matches / 22 distinct
    NEW  \\bo[134](?:-[\\w.\\-]*)?\\b  543 matches / 20 distinct
    dropped: ['o2', 'o239k1gxzz0juy9wqstndhncr85krehehf551hqh']   <- both false positives
    newly matched: []                                             <- no new false positives

Two false positives removed, zero true positives lost, nothing new matched.
"""

from __future__ import annotations

import re

import pytest

from modelpin.detector import MODEL_PATTERNS, _O_SERIES_DIGITS, scan_repo

_O_PATTERN = next(p for p in MODEL_PATTERNS if p.pattern.startswith(r"\bo["))


def _matches(text: str) -> list[str]:
    return _O_PATTERN.findall(text)


# --- the reported false positives ------------------------------------------------------


def test_the_emoji_shortcode_is_not_a_model():
    """`[M]` The exact line from `rich/_emoji_codes.py:3381`."""
    assert _matches('    "o2": "\U0001f17e",') == []


def test_a_random_token_is_not_a_model():
    """`[M]` `o3XPaKcS`, from `.modelpin/drift_cache_drift-suite.json` in this repo. After
    `o3` comes a word character, so there is neither a word boundary for the bare form nor
    a `-` for the suffixed one."""
    assert _matches('{"id": "o3XPaKcS"}') == []
    assert _matches("o239k1gxzz0juy9wqstndhncr85krehehf551hqh") == []


@pytest.mark.parametrize("token", ["o1_internal", "o3_cache", "o1x", "o4z9"])
def test_an_identifier_that_merely_starts_like_one_is_not_a_model(token):
    assert _matches(token) == []


# --- the true positives a narrowing must not lose --------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "o1",
        "o3",
        "o1-mini",
        "o3-mini",
        "o4-mini",
        "o1-preview",
        "o1-pro",
        "o3-pro",
        "o1-2024-12-17",
        "o1-mini-2024-09-12",
        "o3-mini-2025-01-31",
        "o4-mini-2025-04-16",
        "o3-deep-research-2025-06-26",
        "o4-mini-deep-research",
    ],
)
def test_every_real_o_series_id_still_matches_in_full(token):
    """`[M]` All of these appear in the OpenAI SDK under `site-packages`. A first draft of
    this fix used `-[\\w.]+`, which EXCLUDED dashes and silently truncated the dated ids to
    `o1-2024` and `o3-deep` -- a narrowing that corrupts the value instead of rejecting it,
    which is worse than the defect. Hence `fullmatch`, not `search`."""
    assert _matches(f'MODEL = "{token}"') == [token]


def test_the_judge_routing_ids_are_still_detected():
    """`modelpin/judge.py` routes `o1`/`o3`/`o4` to the OpenAI judge, so the detector losing
    them would be a real regression in the product's own code."""
    found = set(_matches('for p in ("o1", "o3", "o4"):'))
    assert found == {"o1", "o3", "o4"}


# --- the boundary the fix deliberately draws -------------------------------------------


def test_o2_is_excluded_by_enumeration_and_that_is_the_point():
    """`o2` is shape-valid but has never been an OpenAI model, so no shape rule can reject
    it -- only the enumeration can. That is the trade this fix makes."""
    assert "2" not in _O_SERIES_DIGITS
    assert _matches("o2") == []


def test_adding_a_future_o_series_number_is_a_one_token_edit():
    """A missed model costs a line in a table the user can add by hand; a fabricated one is
    the north-star failure in the first command a stranger runs. This test documents where
    the edit goes so the next person does not widen the pattern instead."""
    assert set(_O_SERIES_DIGITS) == {"1", "3", "4"}
    widened = re.compile(rf"\bo[{_O_SERIES_DIGITS}5](?:-[\w.\-]*)?\b")
    assert widened.findall('MODEL = "o5-mini"') == ["o5-mini"]


# --- end to end ------------------------------------------------------------------------


def test_a_scan_of_a_vendored_emoji_table_reports_no_models(tmp_path):
    """The dogfood shape, minimised: a dependency's emoji table must contribute nothing."""
    (tmp_path / "rich").mkdir()
    (tmp_path / "rich" / "_emoji_codes.py").write_text(
        'EMOJI = {\n    "o2": "\U0001f17e",\n    "ok": "\U0001f197",\n}\n', encoding="utf-8"
    )
    (tmp_path / "app.py").write_text('MODEL = "o3-mini"\n', encoding="utf-8")
    assert [h["model"] for h in scan_repo(str(tmp_path))] == ["o3-mini"]


def test_this_repo_reports_no_bogus_o_token():
    """`[M]` The regression fixture for the in-repo false positive. Before MP-135,
    `scan_repo('.')` reported `o3XPaKcS` twice."""
    bogus = [
        h
        for h in scan_repo(".")
        if h["model"].startswith("o") and not re.fullmatch(r"o[134](-[\w.\-]*)?", h["model"])
    ]
    assert bogus == [], bogus
