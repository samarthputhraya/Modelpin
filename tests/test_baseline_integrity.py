"""The baseline file is trusted absolutely, and nothing checks what goes into or out of it.

`[M] 2026-09-06` Three separately-filed P1 rows are one gap, reproduced end to end through
the real CLI:

* **MP-43** a baseline of FABRICATED traces (written by the pre-ADR-0015 fake provider, which
  emitted `"(fake) no canned trace for this scenario/model"`) loads without complaint and
  yields `REGRESSION angry_customer: refusal rate 0% -> 100% (confidence 1.00)` against a
  genuine, well-behaved candidate. That is the north-star promise -- "if Modelpin says it
  broke, it broke" -- inverted at maximum confidence. ADR-0015 fixed the *generator* and says
  in terms: *"It does nothing for baselines already on disk - see MP-43."*
* **MP-57** `baseline` exits **0** while storing 5/5 degenerate traces; the later `check`
  prints *"no tool call and no refusal - re-record with `modelpin baseline`"*, and re-recording
  succeeds silently under the same conditions. The remedy is the loop.
* **MP-08/09** a key-shaped token in a model's output is persisted verbatim into the baseline
  -- the one file `.gitignore` deliberately un-ignores and `actions/README.md` tells the user
  to `git add`. `scrub_secrets` has six call sites and every one of them is inside a provider
  ERROR message, so nothing on the write path ever sees it.

These tests fail against the engine as shipped. They do not touch `modelpin/diff/`, any
threshold, or the on-disk baseline SHAPE -- ADR-0015 rejected tainting `Trace` with a new
pydantic field, so detection happens at the storage boundary instead.
"""

import json

import pytest

from modelpin.models import Trace
from modelpin.storage import (
    BaselineError,
    load_baseline,
    save_baseline,
)

FABRICATED = "(fake) no canned trace for this scenario/model"
KEY_SHAPED = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def _trace(out, *, run_idx=0, refused=False, tool_calls=None):
    return Trace(
        scenario_id="s1",
        model_id="m-old",
        run_idx=run_idx,
        messages=[],
        tool_calls=tool_calls or [],
        final_output=out,
        refused=refused,
    )


# --------------------------------------------------------------------------- MP-43
def test_a_baseline_of_fabricated_traces_is_refused_at_load(tmp_path):
    """The north-star inversion: fabricated evidence must never reach the diff engine.

    Without the guard this loads happily and `check` reports a confident regression
    against a candidate that did nothing wrong.
    """
    save_baseline(
        {"s1": [_trace(FABRICATED, run_idx=i) for i in range(5)]},
        "m-old",
        store_dir=tmp_path,
    )
    with pytest.raises(BaselineError) as exc:
        load_baseline("m-old", store_dir=tmp_path)
    msg = str(exc.value)
    assert "fabricat" in msg.lower() or "fake" in msg.lower(), msg
    # The remedy must not be the thing that produced it.
    assert "delete" in msg.lower() or "re-record" in msg.lower(), msg


def test_one_fabricated_trace_among_real_ones_is_still_refused(tmp_path):
    """A partial poisoning is the harder case and the likelier one."""
    traces = [_trace("A real answer.", run_idx=i) for i in range(4)]
    traces.append(_trace(FABRICATED, run_idx=4))
    save_baseline({"s1": traces}, "m-old", store_dir=tmp_path)
    with pytest.raises(BaselineError):
        load_baseline("m-old", store_dir=tmp_path)


def test_a_genuine_baseline_that_merely_mentions_the_word_fake_still_loads(tmp_path):
    """The guard keys on the sentinel, not on the word -- or it becomes a false positive."""
    save_baseline(
        {"s1": [_trace("The fake news problem is hard to solve.", run_idx=i) for i in range(3)]},
        "m-old",
        store_dir=tmp_path,
    )
    assert len(load_baseline("m-old", store_dir=tmp_path)["s1"]) == 3


# --------------------------------------------------------------------------- MP-57
def test_recording_an_all_degenerate_baseline_does_not_pass_silently(tmp_path):
    """`baseline` must not exit 0 having stored a side that can never be compared.

    `diff/structural.py` already knows what degenerate means (no tool call, no refusal,
    no text). The recording path has never asked.
    """
    from modelpin.storage import degenerate_scenarios

    degenerate = {"s1": [_trace("", run_idx=i) for i in range(5)]}
    assert degenerate_scenarios(degenerate) == {"s1": 5}

    healthy = {"s1": [_trace("A real answer.", run_idx=i) for i in range(5)]}
    assert degenerate_scenarios(healthy) == {}

    mixed = {"s1": [_trace("", run_idx=0), _trace("real", run_idx=1)]}
    assert degenerate_scenarios(mixed) == {}, "only an ALL-degenerate side is unusable"


# --------------------------------------------------------------------------- MP-08/09
def test_a_key_shaped_token_in_model_output_is_flagged_before_it_is_written(tmp_path):
    """SECURITY.md promises redaction 'before anything is printed, logged, or written'.

    `[M]` The baseline is none of those three by the letter of the sentence, and is the one
    file the docs tell users to commit. We do NOT silently rewrite recorded evidence --
    we refuse to let it pass unnoticed.
    """
    from modelpin.storage import secret_bearing_scenarios

    leaky = {
        "s1": [_trace(f"Your config uses OPENAI_API_KEY={KEY_SHAPED}", run_idx=i) for i in range(3)]
    }
    found = secret_bearing_scenarios(leaky)
    assert "s1" in found, found

    clean = {"s1": [_trace("Your config looks fine.", run_idx=i) for i in range(3)]}
    assert secret_bearing_scenarios(clean) == {}


def test_the_secret_scan_covers_prompts_not_only_outputs(tmp_path):
    """A key pasted into a SCENARIO is the likelier leak -- the user wrote it themselves."""
    from modelpin.storage import secret_bearing_scenarios

    t = Trace(
        scenario_id="s1",
        model_id="m-old",
        messages=[{"role": "user", "content": f"debug this: gsk_{'x' * 48}"}],
        tool_calls=[],
        final_output="Sure.",
        refused=False,
    )
    assert "s1" in secret_bearing_scenarios({"s1": [t]})


# --------------------------------------------------------------------------- regression floor
def test_a_normal_baseline_round_trips_unchanged(tmp_path):
    """None of the guards above may change what a healthy baseline stores or returns."""
    original = {
        "s1": [_trace("Answer one.", run_idx=0), _trace("Answer two.", run_idx=1)],
        "s2": [_trace("Tool time.", run_idx=0, refused=True)],
    }
    path = save_baseline(original, "m-old", store_dir=tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw["scenarios"]) == {"s1", "s2"}
    back = load_baseline("m-old", store_dir=tmp_path)
    assert [t.final_output for t in back["s1"]] == ["Answer one.", "Answer two."]
    assert back["s2"][0].refused is True
