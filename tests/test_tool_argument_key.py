"""The canonical argument key itself (MP-04).

``tests/test_tool_arguments.py`` pins the VERDICT behaviour; this file pins the encoder that
produces it. The distinction matters because the encoder is where this fix spends or protects
the north-star metric, and [M] the rest of the suite is blind to it: every existing fixture is
argument-free, so `canonical_arguments` could be replaced with `lambda a: ""` and 252 tests
would still pass.

Each test below corresponds to a way a naive encoding turns provider jitter into a false
positive, or turns two different behaviours into one key (a silent false NEGATIVE).
"""

from __future__ import annotations

import math

import pytest

from modelpin.diff import ALPHA, MIN_TOOL_ARG_TVD, diff_scenario
from modelpin.diff.argkey import canonical_arguments, describe_argument_change
from modelpin.diff.stats import (
    permutation_pvalue_distribution,
    permutation_pvalue_mean,
    total_variation_distance,
)
from modelpin.diff.structural import name_trajectory_is_stable, tool_arg_sequence
from modelpin.models import DiffVerdict, ToolCall, Trace


def _trace(model: str, calls: list[tuple[str, dict]], idx: int = 0) -> Trace:
    return Trace(
        scenario_id="s",
        model_id=model,
        run_idx=idx,
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls],
        final_output="x",
    )


# --- false-positive channels: these MUST collapse to one key ----------------------------


def test_key_order_does_not_change_the_key():
    """providers/openai.py json.loads() preserves the model's emission order, so the same
    payload can arrive keyed differently across runs of the SAME side. That is jitter."""
    assert canonical_arguments({"amount": 49.99, "order_id": "A1"}) == canonical_arguments(
        {"order_id": "A1", "amount": 49.99}
    )


def test_nested_key_order_does_not_change_the_key():
    """sort_keys=True is not enough on its own -- it has to reach every nesting level."""
    assert canonical_arguments({"o": {"a": 1, "b": 2}, "l": [{"x": 1, "y": 2}]}) == (
        canonical_arguments({"l": [{"y": 2, "x": 1}], "o": {"b": 2, "a": 1}})
    )


def test_int_and_float_spellings_collapse():
    """openai.py json.loads() yields int where google.py's SDK dict yields float, so the
    spelling is an artefact of which adapter saw the call, not of the model's behaviour."""
    assert canonical_arguments({"qty": 1}) == canonical_arguments({"qty": 1.0})


def test_a_non_finite_float_matches_its_persisted_baseline():
    """A baseline round-trips through model_dump(mode="json"), which turns nan/inf into None.
    If the live candidate keyed them differently it could never equal its own baseline."""
    assert canonical_arguments({"x": float("nan")}) == canonical_arguments({"x": None})
    assert canonical_arguments({"x": math.inf}) == canonical_arguments({"x": None})


# --- false-NEGATIVE channels: these must stay DISTINCT -----------------------------------


def test_a_bool_never_collides_with_an_int():
    """LOAD-BEARING, and the one a tidy-up pass would break: isinstance(True, int) is True,
    True == 1, and hash(True) == hash(1) -- so if the bool check moved below the int check,
    a FLAG and a QUANTITY would merge inside the Counter in stats.py. Silent false negative."""
    assert canonical_arguments({"f": True}) != canonical_arguments({"f": 1})
    assert canonical_arguments({"f": False}) != canonical_arguments({"f": 0})


def test_large_ints_beyond_float_precision_stay_distinct():
    """Above 2**53 a float cannot represent an int exactly, so int/float unification has to
    stop there or two distinct large arguments would collide into one key."""
    assert canonical_arguments({"n": 2**53 + 1}) != canonical_arguments({"n": 2**53 + 2})


def test_a_dropped_argument_key_changes_the_key():
    """A candidate that stops passing dry_run is executing for real."""
    assert canonical_arguments({"a": 1, "dry_run": True}) != canonical_arguments({"a": 1})


def test_colliding_nested_keys_keep_both_entries():
    """Two distinct keys can collide under str(). A dict comprehension would silently DROP
    one of them; the pair-list fallback keeps both."""
    key = canonical_arguments({"k": {1: "a", "1": "b"}})
    assert "a" in key and "b" in key


def test_a_list_and_its_reordering_stay_distinct():
    """Argument ORDER inside a list is the model's choice and can be behavioural (a sequence
    of steps). Only DICT key order is jitter."""
    assert canonical_arguments({"steps": [1, 2]}) != canonical_arguments({"steps": [2, 1]})


# --- totality: the contract is "never raises" -------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"b": b"\x00\xff"},
        {"s": {1, 2, 3}},
        {"t": (1, 2)},
        {"deep": None},
        {"cjk": "漢字"},
        {"none": None},
        {"nested": [[[{"a": [1, {"b": 2}]}]]]},
    ],
    ids=["bytes", "set", "tuple", "none-value", "unicode", "none", "deeply-nested"],
)
def test_never_raises_on_awkward_payloads(payload):
    key = canonical_arguments(payload)
    assert isinstance(key, str)
    hash(key)


def test_never_raises_on_a_self_referential_payload():
    """Depth-bounded rather than recursion-bounded, so it terminates instead of raising."""
    d: dict = {}
    d["self"] = d
    assert isinstance(canonical_arguments(d), str)


def test_never_raises_on_an_object_whose_repr_raises():
    class Hostile:
        def __repr__(self):
            raise RuntimeError("nope")

        def __str__(self):
            raise RuntimeError("nope")

    assert isinstance(canonical_arguments({"x": Hostile()}), str)


def test_an_unknown_type_does_not_mint_a_fresh_key_every_run():
    """A default repr embeds a memory address. If one leaked into the key, an unrecognised
    argument type would become a PERMANENT false positive -- every run a distinct key."""

    class Opaque:
        pass

    assert canonical_arguments({"x": Opaque()}) == canonical_arguments({"x": Opaque()})
    assert "0x" not in canonical_arguments({"x": Opaque()})


def test_none_and_empty_arguments_encode_alike():
    assert canonical_arguments(None) == canonical_arguments({})


# --- the explanation is a PR-comment surface --------------------------------------------


def test_the_explanation_never_prints_a_string_argument_value():
    """Argument values are model-authored and land in a PR comment -- exactly where a customer
    email, an order id or a key-shaped token lives. Numbers and booleans are diagnostic and
    safe; strings are not."""
    base = (("send", canonical_arguments({"to": "alice@example.com", "amount": 5})),)
    cand = (("send", canonical_arguments({"to": "sk-live-000111222", "amount": 500})),)
    text = describe_argument_change(base, cand)
    assert "@" not in text
    assert "sk-" not in text
    assert "amount 5->500" in text  # the useful half survives


def test_the_explanation_cannot_inject_rich_markup():
    """report/__init__.py interpolates the explanation into Rich markup. A model-authored
    "[/]" once crashed `mp check` while it was rendering its own regression."""
    base = (("search", canonical_arguments({"[/]": 1})),)
    cand = (("search", canonical_arguments({"[/]": 2})),)
    assert "[" not in describe_argument_change(base, cand)


# --- the gate's two structural properties ------------------------------------------------


def test_the_argument_gate_is_skipped_when_the_name_trajectory_is_unstable():
    """The precondition that keeps the argument gate off a pool the NAME gate already owns.

    Narrowed 2026-08-25: this docstring previously said "the precondition that keeps this fix
    false-positive-neutral". [M] It is not — the gate raises verdicts on its own and its
    measured cost peaks near 3.5% at the shipped `runs: 5`. What the precondition buys is that
    two tests never fire on one pool.
    """
    base = [_trace("o", [("web_search", {"q": f"q{i}"})], i) for i in range(5)]
    cand = [_trace("n", [("sql_query", {"q": f"s{i}"})], i) for i in range(5)]
    assert not name_trajectory_is_stable(base, cand)
    result = diff_scenario("s", "o", "n", base, cand)
    # The NAME signal must still fire at full strength -- folding arguments into one key
    # instead of gating them separately is what silences this case.
    assert result.verdict == DiffVerdict.regression
    assert result.signals.tool_arg_match is None, "the gate ran when it should have been skipped"


def test_min_tool_arg_tvd_means_disjoint_in_the_equivalence_modes():
    """MIN_TOOL_ARG_TVD = 1.0 is a structural rule, not a fitted dial, and its meaning must
    not drift with N. The rejected alternative -- halving ALPHA -- is [M] DEAD at N=4: a
    fully disjoint change scores p = 0.028571, which clears ALPHA but not ALPHA/2.
    """
    for n in range(4, 9):
        base, cand = ["A"] * n, ["B"] * n
        tvd = total_variation_distance(base, cand)
        p = permutation_pvalue_distribution(base, cand)
        assert tvd >= MIN_TOOL_ARG_TVD and p <= ALPHA, f"disjoint change does not fire at N={n}"
    # ... and the N=4 trap that ruled the alternative out.
    p4 = permutation_pvalue_distribution(["A"] * 4, ["B"] * 4)
    assert p4 <= ALPHA and p4 > ALPHA / 2


def test_min_tool_arg_tvd_is_pinned_at_the_value_that_means_disjoint():
    """`[M]` FP review 2026-08-25: the test above cannot see this constant move.

    It asserts `tvd >= MIN_TOOL_ARG_TVD` where `tvd` is literally `1.0`, so **every** value
    at or below 1.0 satisfies it. `[M]` Mutating the constant `1.0 -> 0.8` in an isolated
    checkout left the whole suite green. A calibrated constant on the north-star metric that
    CI cannot defend is not calibrated.

    So pin the VALUE, and pin what the value buys. `MIN_TOOL_ARG_TVD = 1.0` encodes the
    structural rule *"no candidate run used a payload any baseline run used"*. `[M]` The
    first step below 1.0 is not a small loosening — at N=5 a candidate pool sharing exactly
    ONE payload with the baseline scores `tvd = 0.80, p = 0.04762`, which clears `ALPHA`.
    Any threshold at or under 0.8 therefore admits an **overlapping** pool, and overlap is
    the whole thing the rule excludes.

    Not fitted, and it must not become fitted: `examples/calibration/arg_*` is role `score`
    (ADR-0025), so the only surface where this could be tuned is the one surface where
    tuning voids the measurement. Moving it needs new labelled data, not a green suite.
    """
    assert MIN_TOOL_ARG_TVD == 1.0, (
        f"MIN_TOOL_ARG_TVD is {MIN_TOOL_ARG_TVD}, not 1.0. It is a structural rule ('no "
        "candidate run used a payload any baseline run used'), not a dial. Below 1.0 the "
        "gate fires on pools that overlap - see the table this test asserts."
    )

    # What the ceiling buys, stated as behaviour rather than as a number.
    shared_one = total_variation_distance(["A"] * 5, ["A"] + ["B"] * 4)
    assert math.isclose(shared_one, 0.8), f"expected tvd 0.8 for a 1-of-5 overlap, got {shared_one}"
    assert permutation_pvalue_distribution(["A"] * 5, ["A"] + ["B"] * 4) <= ALPHA, (
        "a 1-of-5 overlapping pool clears ALPHA on the p-gate, so the TVD ceiling is the "
        "ONLY thing keeping it quiet."
    )
    assert shared_one < MIN_TOOL_ARG_TVD, (
        "an overlapping pool now reaches the argument threshold. That is a false positive by "
        "construction: the two sides used a payload in common."
    )


def test_the_argument_gate_declines_when_the_two_sides_have_unequal_runs():
    """`[M]` FP review 2026-08-25: deleting the equal-runs clause left the suite green.

    `modelpin/diff/__init__.py` gates `args_compared` on
    `len(baseline_traces) == len(candidate_traces)` first. Nothing covered it — `grep` for
    an unequal-runs case across `tests/` found none — so a reviewer could delete it as a
    redundant guard and CI would agree.

    It is not redundant, and the branch's own comment says why: `[M]` the unconditional
    true-null rate is **0.1953% at 5v5 but 1.9204% at 5v2 and 2.2968% at 8v2** — an order of
    magnitude, against a status quo of 0.0000% because arguments were not diffed at all. The
    regime is reachable by ordinary use, not by contrivance: `cli.py` replays the candidate
    at the current `runs` against a PERSISTED baseline, so `mp baseline --runs 5` followed by
    `mp check --runs 2` produces exactly 5 vs 2.

    Declining is the correct behaviour, and `tool_arg_match is None` is how "declined" is
    distinguished from "compared and matched" (`1.0`).
    """
    base = [_trace("o", [("log_weight", {"kg": 3.35658})], i) for i in range(5)]
    cand = [_trace("n", [("log_weight", {"kg": 3.35671})], i) for i in range(2)]

    result = diff_scenario("s", "o", "n", base, cand)

    assert result.signals.tool_arg_match is None, (
        "the argument gate compared 5 runs against 2. The equal-runs precondition is what "
        "separates a 0.1953% true-null rate from 1.9204%; it is not redundant."
    )
    assert (
        result.verdict != DiffVerdict.regression
    ), "an unequal-runs comparison produced a regression from the argument channel alone."

    # The same pools at EQUAL runs are exactly what the gate is for, so this is not a
    # blanket 'never fire' assertion that a future edit could satisfy by disabling the gate.
    equal = diff_scenario(
        "s", "o", "n", base, [_trace("n", [("log_weight", {"kg": 3.35671})], i) for i in range(5)]
    )
    assert equal.signals.tool_arg_match is not None, (
        "the gate declined at 5v5 too, so the assertion above proves nothing about the "
        "equal-runs precondition specifically."
    )


def test_the_directional_modes_have_a_different_dead_zone():
    """The equivalence modes go through permutation_pvalue_distribution (floor 2/C(2N,N));
    the DIRECTIONAL modes go through permutation_pvalue_mean (floor 1/C(2N,N)). They are not
    interchangeable, and a comment claiming one dead zone for both is wrong: `--match subset
    --runs 3` is legal and fires at exactly p = ALPHA."""
    assert permutation_pvalue_mean([0] * 3, [1] * 3) <= ALPHA
    assert permutation_pvalue_distribution(["A"] * 3, ["B"] * 3) > ALPHA
    # N=2 is dead in BOTH: neither statistic can reach ALPHA.
    assert permutation_pvalue_mean([0] * 2, [1] * 2) > ALPHA
    assert permutation_pvalue_distribution(["A"] * 2, ["B"] * 2) > ALPHA


def test_an_empty_payload_is_never_compared_against_a_populated_one():
    """`{}` is "not measured", not "measured empty" -- providers mint it on malformed args --
    and it is disjoint from any real payload by construction. [M] Treating it as comparable
    flipped 136 corpus verdicts, 136/136 cross-vendor."""
    base = [_trace("o", [("f", {})], i) for i in range(5)]
    cand = [_trace("n", [("f", {"order_id": "E-5500"})], i) for i in range(5)]
    result = diff_scenario("s", "o", "n", base, cand)
    assert result.signals.tool_arg_match is None, "the argument gate compared {} to a payload"
    assert result.verdict == DiffVerdict.unchanged


def test_arguments_are_anchored_to_their_tool_name():
    """f(x=1), g(y=2) must never compare equal to f(y=2), g(x=1)."""
    a = tool_arg_sequence(_trace("m", [("f", {"x": 1}), ("g", {"y": 2})]))
    b = tool_arg_sequence(_trace("m", [("f", {"y": 2}), ("g", {"x": 1})]))
    assert a != b
