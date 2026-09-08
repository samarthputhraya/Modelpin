"""MP-224 - the scorer that will choose a tool-trajectory rule, and the rules it prices.

`scripts/tool_gate_price.py` exists because of two blocks that are measurements, not opinions:

  * `[M] 2026-09-08` Choosing a rule because it takes `examples/fp-suite-v2`'s alarm count from
    1 to 0 is fitting on a corpus ADR-0038 declares role `score` forever -- ADR-0025's exact
    prohibition with one word changed from "threshold" to "structural rule".
  * `[M]` ADR-0002's revisit bar asks for >= 30 labelled pairs. What exists is **4 distinct**
    tool-channel detection scenarios replayed 20 times, so `n_eff = 4` and the one-sided 95%
    upper bound on a detection-loss rate is **52.7%**, not the 25.9% that "0 in 10" suggests.

So this file tests the MECHANISM against known inputs. It does NOT choose a rule, and the one
place it touches the scored corpus (`test_the_mechanism_reproduces_the_mp220_trial`) is a
validation that the suppression logic behaves as described on a trial whose numbers are already
published -- never a selection. The choice happens on `examples/calibration/tool/`, once that
set exists and has been run.

Offline throughout (ADR-0006): synthetic traces plus one committed artifact. No key, no spend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelpin.models import DiffResult, DiffVerdict, ToolCall, Trace
from scripts.tool_gate_price import (
    RULES,
    check_set,
    disjoint_holds,
    novelty_holds,
    verdict_under,
)

ROOT = Path(__file__).resolve().parent.parent
MP220_ARTIFACT = (
    ROOT / "reports" / "channel-exposure" / "2026-09-07" / "v2a-fp-suite-v2-gpt-4o-mini.jsonl"
)
MP220_KEY = "fp:optional_notify_after_status_update#6"


def tr(seq, run=0):
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=run,
        tool_calls=[ToolCall(name=n) for n in seq],
        final_output="ok",
    )


def side(seqs):
    return [tr(s, i) for i, s in enumerate(seqs)]


def result(verdict, explanation):
    return DiffResult(
        scenario_id="s",
        from_model="m",
        to_model="m",
        verdict=verdict,
        confidence=0.95,
        explanation=explanation,
    )


TOOL_ALARM = "tool-call behavior changed: ['a', 'b'] -> ['a']"


# --------------------------------------------------------------------------------------
# The two candidate preconditions, on inputs whose answer is not a matter of opinion.
# --------------------------------------------------------------------------------------


def test_novelty_asks_whether_the_candidate_did_something_the_baseline_never_did() -> None:
    base = side([["a", "b"]] * 5)
    assert novelty_holds(base, side([["a", "c"]] * 5), "strict"), "a new trajectory is novel"
    assert not novelty_holds(base, side([["a", "b"]] * 5), "strict"), "identical is not novel"
    # The MP-220 shape: the candidate DROPPED a call. It did nothing new, so novelty is False
    # -- which is the whole reason this rule is the surviving candidate.
    assert not novelty_holds(side([["a", "b"], ["a"]] * 2), side([["a"]] * 5), "strict")


def test_disjointness_asks_whether_the_two_sides_share_any_trajectory_at_all() -> None:
    assert disjoint_holds(side([["a"]] * 5), side([["b"]] * 5), "strict")
    assert not disjoint_holds(side([["a", "b"], ["a"]] * 2), side([["a"]] * 5), "strict")
    # Sharing a SINGLE run is enough to break disjointness. `[M]` That is exactly why 0 of 37
    # tool-exposed FP trials satisfied it, and why it makes the gate structurally incapable of
    # a hard alarm on 100% of the trials where it has ever met a null (ADR-0038 D3 -> 0/0).
    assert not disjoint_holds(side([["a"]] * 5), side([["a"]] + [["b"]] * 4), "strict")


# --------------------------------------------------------------------------------------
# A precondition may only SUPPRESS. It must never manufacture an alarm.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule", sorted(RULES))
def test_no_rule_can_create_an_alarm_the_engine_did_not_raise(rule) -> None:
    """The direction that matters. A rule that could ADD a hard alarm would be a new gate, and
    a new gate needs its own calibration -- it is not a precondition at all."""
    base, cand = side([["a"]] * 5), side([["b"]] * 5)
    quiet = result(DiffVerdict.unchanged, "no statistically significant behavior change")
    assert verdict_under(quiet, base, cand, "strict", rule) == ("unchanged", False, False)


def test_a_verdict_carried_by_another_channel_is_untouched_and_reported_as_masked() -> None:
    """`[M] 2026-09-08` The single most important line in the scorer's output.

    On the corpus that exists, all 10 scored tool detections ALSO name `semantic drift`, and
    `semantic_diverged` appends to `hard_pvalues` independently of the tool channel. So
    suppressing the tool alarm leaves their verdict at `regression` and they price NOTHING --
    a rule that looks free on that corpus is not being measured, it is being missed. That has
    to be visible in the output, not absorbed into a flattering "0 detections lost".
    """
    base = side([["a", "b"], ["a"], ["a", "b"], ["a", "b"], ["a", "b"]])
    cand = side([["a"]] * 5)
    both = result(DiffVerdict.regression, TOOL_ALARM + "; semantic drift: candidate answers")
    verdict, survived, masked = verdict_under(both, base, cand, "strict", "novelty")
    assert verdict == "regression", "the semantic channel still carries it"
    assert not survived and masked, "and the scorer must SAY the tool alarm was masked"


def test_suppressing_a_tool_only_alarm_moves_the_verdict_all_the_way_to_unchanged() -> None:
    base = side([["a", "b"], ["a"], ["a", "b"], ["a", "b"], ["a", "b"]])
    cand = side([["a"]] * 5)
    tool_only = result(DiffVerdict.regression, TOOL_ALARM)
    assert verdict_under(tool_only, base, cand, "strict", "status_quo") == (
        "regression",
        True,
        False,
    )
    assert verdict_under(tool_only, base, cand, "strict", "novelty")[0] == "unchanged"
    assert verdict_under(tool_only, base, cand, "strict", "disjoint")[0] == "unchanged"


def test_an_advisory_channel_left_standing_demotes_rather_than_clears() -> None:
    """Dropping the hard tool alarm must not also drop an advisory finding that was real."""
    base = side([["a", "b"], ["a"], ["a", "b"], ["a", "b"], ["a", "b"]])
    cand = side([["a"]] * 5)
    mixed = result(DiffVerdict.regression, TOOL_ALARM + "; tool-call arguments changed: x")
    assert verdict_under(mixed, base, cand, "strict", "novelty")[0] == "changed_minor"


def test_the_status_quo_rule_is_the_identity() -> None:
    """It has to be, or every number the scorer prints is measured against a straw man."""
    base = side([["a", "b"], ["a"], ["a", "b"], ["a", "b"], ["a", "b"]])
    cand = side([["a"]] * 5)
    for verdict, explanation in (
        (DiffVerdict.regression, TOOL_ALARM),
        (DiffVerdict.changed_minor, "tool-call arguments changed: x"),
        (DiffVerdict.unchanged, "no statistically significant behavior change"),
    ):
        r = result(verdict, explanation)
        assert verdict_under(r, base, cand, "strict", "status_quo")[0] == verdict.value


# --------------------------------------------------------------------------------------
# The one contact with the scored corpus: a validation of the mechanism, not a choice.
# --------------------------------------------------------------------------------------


def test_the_mechanism_reproduces_the_mp220_trial() -> None:
    """`[M]` The published trial: `regression` @ 0.952 over a same-model, same-prompt null.

    Both candidate rules suppress it and the status quo keeps it. **This is not how the rule
    gets chosen** -- `examples/fp-suite-v2` is role `score` and selecting on it is ADR-0025's
    prohibition. It is here so that a scorer which silently stopped suppressing anything, or
    started suppressing everything, fails against a case whose numbers are already published.
    """
    if not MP220_ARTIFACT.is_file():
        pytest.skip("the MP-220 run of record is pruned from the sdist")
    for line in MP220_ARTIFACT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "trial" and record.get("key") == MP220_KEY:
            break
    else:
        raise AssertionError(f"{MP220_KEY} is not in {MP220_ARTIFACT.name}")

    r = DiffResult(**record["result"])
    base = [Trace.model_validate(t) for t in record["base_traces"]]
    cand = [Trace.model_validate(t) for t in record["cand_traces"]]
    assert r.verdict is DiffVerdict.regression and round(r.confidence, 3) == 0.952

    assert verdict_under(r, base, cand, "strict", "status_quo") == ("regression", True, False)
    for rule in ("novelty", "disjoint"):
        verdict, survived, masked = verdict_under(r, base, cand, "strict", rule)
        assert (verdict, survived, masked) == ("unchanged", False, False), (
            f"{rule} on the MP-220 trial: the tool channel is the ONLY one that fired "
            f"(explanation {r.explanation!r}), so suppressing it must clear the verdict"
        )


# --------------------------------------------------------------------------------------
# The offline validator, which is what stops a defect being found after the money is gone.
# --------------------------------------------------------------------------------------


def _write_set(d: Path, scenarios: list[dict], labels: dict) -> None:
    for s in scenarios:
        (d / f"{s['id']}.json").write_text(json.dumps(s), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps(labels), encoding="utf-8")


def _scn(sid, tools=True, assertions=None):
    body = {
        "id": sid,
        "name": sid,
        "kind": "agent",
        "input": {
            "messages": [{"role": "user", "content": "do the thing"}],
            "temperature": 1.0,
        },
    }
    if tools:
        body["input"]["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "look it up",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                },
            }
        ]
    if assertions:
        body["assertions"] = assertions
    return body


def test_the_validator_refuses_a_set_that_would_waste_a_paid_run(tmp_path, capsys) -> None:
    """Every check here is one that would otherwise be discovered after the spend."""
    _write_set(
        tmp_path,
        [
            _scn("no_label"),
            _scn("no_tools", tools=False),
            _scn("has_assertions", assertions={"must_contain": ["x"]}),
            _scn("changed_without_perturbation"),
        ],
        {
            "no_tools": {"label": "equivalent", "perturbation": None, "why": "w"},
            "has_assertions": {"label": "equivalent", "perturbation": None, "why": "w"},
            "changed_without_perturbation": {"label": "changed", "perturbation": "", "why": "w"},
            "ghost": {"label": "equivalent", "perturbation": None, "why": "w"},
        },
    )
    assert check_set(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "no_label" in out, "an unlabelled scenario must be named"
    assert "ghost" in out, "a label naming no scenario must be named"
    assert "no_tools" in out and "cannot exercise the tool channel" in out
    assert "has_assertions" in out, "this set is one seam only"
    assert "no perturbation to apply" in out
    assert "ADR-0002's bar is >= 30" in out, "the size bar is part of the acceptance"


def test_the_validator_refuses_a_missing_or_malformed_label_file(tmp_path) -> None:
    with pytest.raises(SystemExit, match="will not guess a ground truth"):
        check_set(str(tmp_path))
    (tmp_path / "labels.json").write_text(
        json.dumps({"s": {"label": "probably fine", "why": "w"}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="labels outside"):
        check_set(str(tmp_path))


def test_a_labelled_sets_ground_truth_file_is_not_loaded_as_a_scenario(tmp_path) -> None:
    """`labels.json` sits beside the scenarios it labels, so the loader must skip it.

    `[M] 2026-09-08` Found by this file, not by review: without the reservation
    `load_scenarios` raises `ScenarioError: labels.json is not a valid scenario` and the whole
    calibration set fails to load -- which would have been discovered after a paid run.
    """
    from modelpin.scenarios import _RESERVED_FILES, load_scenarios

    assert "labels.json" in _RESERVED_FILES
    _write_set(
        tmp_path,
        [_scn("only_scenario")],
        {"only_scenario": {"label": "equivalent", "perturbation": None, "why": "w"}},
    )
    loaded = load_scenarios(tmp_path)
    assert [s.id for s in loaded] == ["only_scenario"]


def test_the_mode_count_axis_is_the_one_a_pooled_rate_would_hide() -> None:
    """`[M] 2026-09-08` NOVELTY's precondition is `set(cand) - set(base) != {}`, and the chance
    of meeting it rises steeply with how many trajectories a scenario visits: exact enumeration
    at `runs: 5` gives **6.1%** at 2 modes and **96.3%** at 8.

    So NOVELTY removes 46% of the status quo's false alarms at 2 modes and **0.4%** at 8 -- its
    entire benefit lives on low-mode scenarios, which is the mirror of ADR-0041 D2's rejection
    of DISJOINT on multimodal baselines. A single pooled `k/n` would average the two regimes and
    report a benefit that does not exist where MP-220's own false positive lives.
    """
    from scripts.tool_gate_price import MODE_BUCKETS, mode_bucket, mode_count

    unimodal = (side([["a"]] * 5), side([["a"]] * 5))
    two_mode = (side([["a", "b"], ["a"], ["a", "b"], ["a", "b"], ["a", "b"]]), side([["a"]] * 5))
    many = (side([[f"t{i}"] for i in range(5)]), side([[f"t{i}"] for i in range(5, 10)]))

    assert mode_count(*unimodal, "strict") == 1
    assert mode_count(*two_mode, "strict") == 2
    assert mode_count(*many, "strict") == 10

    assert mode_bucket(1) == "1 (pinned)"
    assert mode_bucket(2) == "2"
    assert mode_bucket(4) == "3-4"
    assert mode_bucket(10) == "5+"
    assert set(mode_bucket(n) for n in range(1, 12)) == set(MODE_BUCKETS)

    # The mechanism itself, on the two ends of the axis: at 2 modes NOVELTY suppresses the
    # MP-220 shape; at 10 its precondition is trivially met and it suppresses nothing.
    assert not novelty_holds(*two_mode, "strict")
    assert novelty_holds(*many, "strict")
