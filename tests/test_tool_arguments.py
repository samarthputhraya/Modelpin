"""Tool-call ARGUMENTS are captured but never diffed (MP-04, spec 6A).

`structural.py:27-29` collapses a run to `tuple(tc.name for tc in trace.tool_calls)` --
names only -- while `models.py:72-74` faithfully stores `arguments` on every ToolCall. So a
candidate that calls the RIGHT tool with a catastrophically WRONG argument reads as
`unchanged` at confidence 1.00: `issue_refund(amount=49.99)` -> `issue_refund(amount=4999.00)`
is a 100x financial error the engine cannot see.

The tool signal is the ONLY place arguments could ever matter: the judge reads `final_output`
(`semantic.py:68-69`), the assertion signal reads `final_output`
(`structural.py:122`), and refusal reads `refused`. When only an argument changes the output
text is identical, so `semantic.py:64` short-circuits and the judge is never even called.

The guard tests below fix the sensitivity ceiling BEFORE the fix raises it: arguments arrive
as a parsed dict whose key order is whatever the model emitted (`openai.py:150` json.loads),
so any encoding that is not order-insensitive turns provider jitter into a false positive --
the one thing this engine must never do. They pass today only because arguments are ignored
entirely; they must still pass afterwards.

The gate ships ADVISORY (ADR-0029): `MIN_TOOL_ARG_TVD` has no labelled calibration set, so an
argument change escalates to `changed_minor` and NEVER to a build-failing `regression` on its
own. That is why the assertions below read `changed_minor` -- a weaker claim than the first
draft of this file made, so the cap itself is pinned separately under "the advisory cap".
"""

from __future__ import annotations

from modelpin.config import DEFAULT_RUNS
from modelpin.diff import diff_scenario
from modelpin.models import DiffVerdict, ToolCall, Trace

RUNS = DEFAULT_RUNS  # 5 since MP-03; below N=4 the tool signal cannot fire at all.


def _runs(model: str, args: dict, n: int = RUNS, tool: str = "issue_refund") -> list[Trace]:
    return [
        Trace(
            scenario_id="refund",
            model_id=model,
            run_idx=i,
            tool_calls=[ToolCall(name=tool, arguments=dict(args))],
            final_output="Refund issued.",
        )
        for i in range(n)
    ]


# --- the headline ------------------------------------------------------------------------


def test_a_hundredfold_argument_change_is_not_unchanged():
    """The row's claim: same tool name, 100x the money, reported as `unchanged` @ 1.00."""
    r = diff_scenario(
        "refund", "old", "new", _runs("old", {"amount": 49.99}), _runs("new", {"amount": 4999.00})
    )
    assert r.verdict == DiffVerdict.changed_minor, (
        f"a 100x refund amount reported {r.verdict.value} @ {r.confidence} "
        f"(tool_call_match={r.signals.tool_call_match})"
    )


def test_the_argument_signal_does_not_score_a_changed_argument_as_a_perfect_match():
    """A published `1.0` means "identical distributions" -- a positive claim of sameness, and
    here it is false. MP-74 moved WHICH field carries that claim, not whether it is made:
    the argument distance is published as `tool_arg_match` (the report's `Arg match` column)
    and no longer folded into `tool_call_match`, which is now the tool-NAME signal alone. The
    intent of this test is unchanged; only the field it reads moved."""
    r = diff_scenario(
        "refund", "old", "new", _runs("old", {"amount": 49.99}), _runs("new", {"amount": 4999.00})
    )
    assert r.signals.tool_arg_match is not None and r.signals.tool_arg_match < 1.0


# --- MP-74: each column measures the thing its name says ---------------------------------


def _freetext(model: str, salt: str, n: int = RUNS) -> list[Trace]:
    """The `arg_freetext_note` shape: one tool, same name every run, a free-text payload that
    is distinct on every single run -- so the argument key pools are maximally high-entropy
    and disjoint between the two sides, while the NAME trajectory is a perfect match."""
    return [
        Trace(
            scenario_id="arg_freetext_note",
            model_id=model,
            run_idx=i,
            tool_calls=[ToolCall(name="add_call_note", arguments={"note": f"{salt}-note-{i}"})],
            final_output="Noted.",
        )
        for i in range(n)
    ]


def test_an_unchanged_verdict_does_not_publish_a_zero_tool_match():
    """`[M]` MP-74, reproduced: `unchanged @ 1.00` published `Tool match 0.00` -- a
    contradiction on the face of the public Report -- because free-text argument jitter was
    folded into the tool-NAME column via `max()`. The names are identical on all 10 runs."""
    r = diff_scenario(
        "arg_freetext_note", "m", "m", _freetext("m", "baseline"), _freetext("m", "candidate")
    )
    assert r.verdict is DiffVerdict.unchanged
    assert r.confidence == 1.0
    assert r.signals.tool_call_match == 1.0, (
        f"published Tool match {r.signals.tool_call_match} beside "
        f"'{r.verdict.value} @ {r.confidence:.2f}' -- the tool NAMES are identical on every "
        f"run, so the name signal is a perfect match"
    )
    # The argument divergence is NOT lost -- it moved to the column that names it.
    assert r.signals.tool_arg_match == 0.0


def test_a_refusal_regression_does_not_blame_the_tool_trajectory():
    """Sharper than MP-74's own row, and the case this must never regress on: a `regression`
    caused ENTIRELY by refusal, with identical tool names throughout, published
    `Tool match 0.00`. A reader concludes the tool trajectory broke. It did not."""
    base = _freetext("old", "baseline")
    cand = [t.model_copy(update={"refused": True}) for t in _freetext("new", "candidate")]
    r = diff_scenario("arg_freetext_note", "old", "new", base, cand)
    assert r.verdict is DiffVerdict.regression
    assert "refusal" in r.explanation and "tool-call" not in r.explanation
    assert r.signals.tool_call_match == 1.0, (
        f"published Tool match {r.signals.tool_call_match} on a regression the tool-name "
        f"signal took no part in: {r.explanation!r}"
    )


def test_a_changed_argument_is_caught_in_every_match_mode():
    """`strict`/`unordered` bucket a run into a key; `subset`/`superset` score it against a
    relation. Arguments are dropped on the way into BOTH paths, so neither can see the change."""
    for mode in ("strict", "unordered", "subset", "superset"):
        r = diff_scenario(
            "refund",
            "old",
            "new",
            _runs("old", {"amount": 49.99}),
            _runs("new", {"amount": 4999.00}),
            mode=mode,
        )
        assert r.verdict == DiffVerdict.changed_minor, f"mode={mode} reported {r.verdict.value}"


def test_a_new_argument_key_is_a_behavior_change():
    """Not just changed values: a candidate that stops passing `dry_run` is executing for real."""
    r = diff_scenario(
        "refund",
        "old",
        "new",
        _runs("old", {"amount": 49.99, "dry_run": True}),
        _runs("new", {"amount": 49.99}),
    )
    assert r.verdict == DiffVerdict.changed_minor


# --- the false-positive ceiling: these must pass BEFORE and AFTER the fix ----------------


def test_the_same_arguments_in_a_different_key_order_are_not_a_regression():
    """`openai.py:150` json.loads preserves the model's emission order, which is jitter, not
    behavior. Passes today only because arguments are ignored."""
    r = diff_scenario(
        "refund",
        "old",
        "new",
        _runs("old", {"amount": 49.99, "order_id": "A1"}),
        _runs("new", {"order_id": "A1", "amount": 49.99}),
    )
    assert r.verdict == DiffVerdict.unchanged


def test_int_and_float_spellings_of_the_same_number_are_not_a_regression():
    """`1` and `1.0` are equal in Python and unequal under json.dumps/repr; the OpenAI adapter
    parses JSON while the Google adapter passes the SDK's dict through, so the two can disagree
    on the same call."""
    r = diff_scenario("refund", "old", "new", _runs("old", {"qty": 1}), _runs("new", {"qty": 1.0}))
    assert r.verdict == DiffVerdict.unchanged


def test_one_odd_argument_in_five_runs_is_noise_not_a_regression():
    """ADR-0001: a single run never decides. Arguments must enter through the same
    distributional test as every other signal, not as a per-run equality check."""
    cand = _runs("new", {"amount": 49.99}, n=RUNS - 1) + _runs("new", {"amount": 4999.00}, n=1)
    r = diff_scenario("refund", "old", "new", _runs("old", {"amount": 49.99}), cand)
    assert r.verdict == DiffVerdict.unchanged


# --- blast radius: nothing else is watching ----------------------------------------------


def test_no_other_signal_sees_an_argument_only_change():
    """With the output text identical, the judge is never even billed -- so the tool signal is
    the sole place an argument change could ever register."""
    billed: list[tuple[str, str]] = []

    class _ParanoidJudge:
        """Says nothing is ever equivalent; if it is ever asked, it flags."""

        def equivalent(self, reference: str, candidate: str, task: str | None = None) -> bool:
            billed.append((reference, candidate))
            return False

    r = diff_scenario(
        "refund",
        "old",
        "new",
        _runs("old", {"amount": 49.99}),
        _runs("new", {"amount": 4999.00}),
        judge=_ParanoidJudge(),
    )
    assert billed == [], "judge saw the outputs differ; the premise of this file is wrong"
    assert r.signals.semantic_score == 1.0
    assert r.signals.refusal_delta == 0.0
    assert r.signals.format_valid is True


# --- the advisory cap: ADR-0029 -----------------------------------------------------------
#
# The three headline assertions above were WEAKENED from `regression` to `changed_minor` when
# the gate was capped. A weakened assertion cannot notice the cap being lifted again by
# accident, so the cap gets its own tripwires -- these are the tests that fail if someone
# promotes the argument signal without the labelled calibration set ADR-0029 requires.


def _jitter(model: str, n: int, *, with_optional: bool) -> list[Trace]:
    """The MP-112 shape: one OPTIONAL field, present on every run or absent on every run.

    This is provider jitter on a small repertoire, not a behavior change -- and it is the
    input that made the uncapped gate fail a build with the SAME model on both sides.
    """
    args = {"q": "shoes", "limit": 10} if with_optional else {"q": "shoes"}
    return _runs(model, args, n=n, tool="search")


def test_the_argument_gate_alone_never_reaches_a_regression():
    """The cap, stated directly: at no N and in no match mode may arguments fail a build.

    `!= regression` alone would also pass with the gate DEAD -- the failure mode a cap most
    easily hides -- so the sweep additionally records that the signal still fires somewhere.
    """
    fired = 0
    for mode in ("strict", "unordered", "subset", "superset"):
        for n in range(2, 9):
            r = diff_scenario(
                "search",
                "old",
                "new",
                _jitter("old", n, with_optional=True),
                _jitter("new", n, with_optional=False),
                mode=mode,
            )
            assert r.verdict != DiffVerdict.regression, (
                f"mode={mode} runs={n} reached {r.verdict.value} @ {r.confidence} on an "
                f"argument-only change -- ADR-0029 caps this signal at changed_minor"
            )
            fired += r.verdict == DiffVerdict.changed_minor
    assert fired, (
        "the argument gate did not fire ANYWHERE in this sweep. A cap that silently became "
        "a deletion would satisfy the assertion above; this one it does not."
    )


def test_the_same_model_on_both_sides_does_not_fail_a_build_on_argument_jitter():
    """[M] MP-112, reproduced 2026-08-26 on `4ab2665`: this returned `regression` @ 0.992 at
    the shipped `runs: 5`, where `main` @ `13a715c` returned `unchanged` @ 1.0. Same model,
    identical tool names, identical output text, no refusal -- only one optional field."""
    r = diff_scenario(
        "search",
        "gpt-4.1-mini",
        "gpt-4.1-mini",
        _jitter("gpt-4.1-mini", RUNS, with_optional=True),
        _jitter("gpt-4.1-mini", RUNS, with_optional=False),
    )
    assert r.verdict == DiffVerdict.changed_minor
    assert r.verdict != DiffVerdict.regression, "a same-model comparison fails a stranger's CI"


def test_an_argument_flag_takes_its_confidence_from_the_minor_pool():
    """The cap is not cosmetic: `arg_p` must move out of `hard_pvalues` into `minor_pvalues`,
    or a later hard signal would inherit the argument p-value as its confidence."""
    r = diff_scenario(
        "refund",
        "old",
        "new",
        _runs("old", {"amount": 49.99}),
        _runs("new", {"amount": 4999.00}),
    )
    assert r.verdict == DiffVerdict.changed_minor
    # 5v5 fully disjoint -> p = 2/C(10,5) = 2/252; confidence is 1 - p.
    assert r.confidence == round(1.0 - 2 / 252, 3)


def test_arguments_never_downgrade_a_real_regression():
    """`if verdict != regression` -- a hard signal that already fired must survive the cap."""
    base = [
        Trace(
            scenario_id="refund",
            model_id="old",
            run_idx=i,
            tool_calls=[ToolCall(name="issue_refund", arguments={"amount": 49.99})],
            final_output="Refund issued.",
        )
        for i in range(RUNS)
    ]
    cand = [
        Trace(
            scenario_id="refund",
            model_id="new",
            run_idx=i,
            tool_calls=[ToolCall(name="issue_refund", arguments={"amount": 4999.00})],
            final_output="I cannot help with that.",
            refused=True,
        )
        for i in range(RUNS)
    ]
    r = diff_scenario("refund", "old", "new", base, cand)
    assert r.verdict == DiffVerdict.regression
    assert "arguments changed" in r.explanation, "the argument reason is still disclosed"


def test_the_cap_does_not_flatter_the_published_false_positive_number():
    """ADR-0029 decision 3. `changed_minor` is already scored `fp`, so capping the gate moves
    no published figure. If someone ever drops `changed_minor` from `_FLAGGED`, this cap
    silently becomes a way to make the north-star metric look better -- fail then."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from fp_measurement import _FLAGGED, classify  # type: ignore[import-not-found]

    assert DiffVerdict.changed_minor in _FLAGGED
    assert classify(DiffVerdict.changed_minor) == "fp"


def test_an_argument_near_miss_lowers_the_unchanged_confidence():
    """ADR-0001: an `unchanged` verdict's confidence is `min()` over the signals' p-values, so
    it reads "how close was the nearest miss", not "how sure are we nothing changed".

    `[M] 2026-08-26` Adding the argument channel to that `min()` is user-visible and was
    shipped with no test: the same input reads `unchanged @ 1.0` on `main` @ `13a715c` and
    `unchanged @ 0.100` here, because the equivalence modes' p-floor at N=3 is 0.100 -- the
    gate could not have fired, and 0.100 is exactly that statement. A silent return to 1.0
    would be a claim of a clean bill of health over a comparison that was never possible.
    """
    for mode in ("strict", "unordered"):
        r = diff_scenario(
            "search",
            "old",
            "new",
            _jitter("old", 3, with_optional=True),
            _jitter("new", 3, with_optional=False),
            mode=mode,
        )
        assert r.verdict == DiffVerdict.unchanged, f"{mode}: {r.verdict.value}"
        assert r.confidence == 0.1, (
            f"mode={mode} reports unchanged @ {r.confidence}; the argument p-value must reach "
            f"the ADR-0001 min(). 1.0 here would assert a clean result over a comparison the "
            f"equivalence modes structurally could not make at N=3."
        )
        assert r.signals.tool_arg_match == 0.0, "the payloads really are disjoint"
