"""Two ways an error stopped being useful (MP-193, MP-197).

**MP-193 — a silent truncation is worse than a long error.** Provider text was cut with
``str(exc)[:300]`` and the result wrapped in ``[... ].``, so an over-long message ended with
punctuation and read as a complete sentence while the half that says what to do was gone.
Providers put the remedy at the END — *"...set the parameter tool_choice to auto and retry the
request"* — so a head-only cut deletes exactly the part the user needs, and leaves no sign that
it did. `[M] 2026-09-06` a real Groq 400 on this project's own dogfood run carried its
diagnosis (`tool_use_failed`) in the head and its `failed_generation` payload in the tail.

**MP-197 — the atomic write had no failure path.** `save_baseline` wrote a `.tmp` and
`os.replace`d it, with no `except`: an unwritable store produced an unhandled traceback AND
left the stray `.tmp` behind, so the next run met a file the user had no reason to expect and
no message explaining it.

Both are small. They are here because the first error a stranger sees is the one that decides
whether they try again, and this project has already had to fix that class twice
(`test_error_messages_survive_rich.py`, MP-161 + MP-170).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelpin.models import Trace
from modelpin.providers._common import ERROR_DETAIL_LIMIT, elide
from modelpin.storage import BaselineError, save_baseline

# --------------------------------------------------------------------------- MP-193

#: Shaped like a real provider 400: the error CODE at the front, the REMEDY at the back.
_HEAD = "Error code: 400 - tool_use_failed: the model called a tool while tool_choice was none"
_TAIL = "To fix this, set the parameter tool_choice to auto and retry the request."
_LONG = f"{_HEAD} {'x' * 400} {_TAIL}"


def test_a_short_message_is_returned_unchanged() -> None:
    """Control. The overwhelming majority of provider errors fit, and must not be touched."""
    assert elide("Error code: 401 - invalid api key") == "Error code: 401 - invalid api key"


def test_a_message_at_the_limit_is_not_elided() -> None:
    exact = "y" * ERROR_DETAIL_LIMIT
    assert elide(exact) == exact


def test_the_remedy_at_the_end_survives() -> None:
    """The defect in one assertion: the half that tells the user what to do must not vanish."""
    out = elide(_LONG)
    assert _TAIL in out, "the remedy at the end of the provider message was deleted: " + out


def test_the_diagnosis_at_the_start_survives() -> None:
    """Keeping only the tail would trade one lost half for the other."""
    assert "tool_use_failed" in elide(_LONG)


def test_the_elision_is_visible() -> None:
    """A silent cut leaves a sentence that reads as complete. That is the whole defect."""
    out = elide(_LONG)
    assert "elided" in out, "the message was shortened with no marker saying so: " + out
    assert (
        out != _LONG[: len(out)]
    ), "the output is still a plain prefix of the input, i.e. a silent head-only cut"


def test_the_result_stays_bounded() -> None:
    """The marker must not let the output grow without limit."""
    assert len(elide("z" * 5000)) < ERROR_DETAIL_LIMIT + 60


@pytest.mark.parametrize("module", ["openai", "google"])
def test_both_adapters_use_the_shared_helper(module: str) -> None:
    """`[M]` This was two independent `[:300]` slices. One helper, so they cannot drift into
    truncating differently -- the same reasoning `_fail` used for markup escaping."""
    src = Path(__file__).resolve().parents[1] / "modelpin" / "providers" / f"{module}.py"
    text = src.read_text(encoding="utf-8")
    assert "[:300]" not in text, f"{module}.py still slices provider text by hand"
    assert "elide(" in text


# --------------------------------------------------------------------------- MP-197


def _traces() -> dict[str, list[Trace]]:
    return {
        "s": [Trace(scenario_id="s", model_id="m1", run_idx=0, final_output="ok")],
    }


def test_an_unwritable_store_raises_a_typed_error_not_a_traceback(tmp_path: Path) -> None:
    """An `OSError` escaping here reached the user as a raw traceback."""
    store = tmp_path / "store"
    store.mkdir()
    # A directory where the baseline FILE must go: `os.replace` onto it fails on every OS.
    from modelpin.storage import baseline_path

    target = baseline_path("m1", store)
    target.mkdir(parents=True, exist_ok=True)

    with pytest.raises(BaselineError) as exc:
        save_baseline(_traces(), "m1", store)
    assert "baseline" in str(exc.value).lower()
    assert "re-run" in str(exc.value), "the error names no next step: " + str(exc.value)


def test_no_stray_tmp_file_is_left_behind(tmp_path: Path) -> None:
    """The half a user actually trips over: a `.tmp` in the directory they were told to commit."""
    store = tmp_path / "store"
    store.mkdir()
    from modelpin.storage import baseline_path

    target = baseline_path("m1", store)
    target.mkdir(parents=True, exist_ok=True)

    with pytest.raises(BaselineError):
        save_baseline(_traces(), "m1", store)

    strays = [p.name for p in store.rglob("*.tmp")]
    assert strays == [], f"the failed write left {strays} behind"


def test_a_writable_store_still_round_trips(tmp_path: Path) -> None:
    """Control. The failure path must not disturb the path everything else depends on."""
    from modelpin.storage import load_baseline

    path = save_baseline(_traces(), "m1", tmp_path / "store")
    assert path.exists()
    assert list(load_baseline("m1", tmp_path / "store")) == ["s"]
    assert not list((tmp_path / "store").rglob("*.tmp"))
