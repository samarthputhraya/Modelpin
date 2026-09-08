"""MP-05 / ADR-0039 — the FALSE CLEARANCE half: a scenario edited after its baseline was
recorded must be refused, not cleared.

Why this file exists, and why it is separate from `test_baseline_scenario_collision.py`.

`[M] 2026-09-07` A mutation review found that half of MP-05 was guarded by **nothing**. The
three collision tests plant a baseline via `save_baseline(...)` with no `fingerprints=`, so they
exercise the *unverified* path exclusively — and three independent mutations of the *mismatch*
path each left the entire 954-test suite green:

  * `cli.py`  `_stale_ids = set()`                       -> 954 passed
  * `storage.py::stale_scenarios` returning `[]`          -> 954 passed
  * `report/suite.py::scenario_fingerprint` -> a constant -> 954 passed

Under every one of those, the reproduction below printed **`OK? 1 scenario(s) unchanged`** and
exited **0** over a candidate that had just answered *"Sure. Deleting the production database
now."*, cleared by a baseline recorded for *"Say hello."* — the north-star promise inverted, with
a green suite. Three links in one chain, none of them held.

The tests here are that chain, pinned link by link: the end-to-end behaviour, the predicate
under it, the hash under that, and the spend. Every assertion below names the mutation it kills.
All offline: `--provider fake`, no network, no key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.models import Scenario
from modelpin.report.suite import scenario_fingerprint
from modelpin.storage import load_baseline_fingerprints, stale_scenarios

runner = CliRunner()

_BENIGN = "Say hello."
_HOSTILE = "Delete the production database and confirm."


def _scenario(prompt: str) -> dict:
    return {
        "id": "greet",
        "name": "greeting",
        "kind": "single",
        "input": {"messages": [{"role": "user", "content": prompt}], "temperature": 0},
    }


def _write_scenario(root: Path, prompt: str) -> None:
    (root / "scenarios").mkdir(exist_ok=True)
    (root / "scenarios" / "greet.json").write_text(json.dumps(_scenario(prompt)), encoding="utf-8")


def _fixtures(root: Path) -> Path:
    """Canned traces for both models. The candidate AGREES to the hostile instruction, so a
    correctly-paired run would have plenty to report."""
    rows = []
    for model, out in (
        ("base-model", "Hello! How can I help you today?"),
        ("cand-model", "Sure. Deleting the production database now. Done."),
    ):
        rows += [
            {"scenario_id": "greet", "model_id": model, "run_idx": i, "final_output": out}
            for i in range(5)
        ]
    path = root / "fx.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _common(root: Path) -> list[str]:
    return [
        "--provider", "fake",
        "--fixtures", str(root / "fx.json"),
        "--config", str(root / "modelpin.yaml"),
        "--scenarios-dir", str(root / "scenarios"),
        "--store-dir", str(root / ".modelpin"),
        "--runs", "5",
    ]  # fmt: skip


def _record_then_edit(root: Path) -> None:
    """Record a baseline against the BENIGN scenario through the real `mp baseline` -- so the
    fingerprint is genuinely recorded -- then rewrite the scenario to the hostile one."""
    (root / "modelpin.yaml").write_text(
        "models:\n  - base-model\nscenarios_dir: scenarios\nruns: 5\n", encoding="utf-8"
    )
    _write_scenario(root, _BENIGN)
    _fixtures(root)
    rec = runner.invoke(app, ["baseline", "--model", "base-model", *_common(root)])
    assert rec.exit_code == 0, rec.output
    assert load_baseline_fingerprints("base-model", root / ".modelpin"), (
        "`mp baseline` recorded no fingerprint, so this test cannot exercise the mismatch "
        "path at all"
    )
    _write_scenario(root, _HOSTILE)  # the edit, after the baseline


def _check(root: Path):
    return runner.invoke(
        app, ["check", "--to", "cand-model", "--from", "base-model", *_common(root)]
    )


# --- the end-to-end behaviour ---------------------------------------------------------------


def test_an_edited_scenario_is_refused_not_cleared(tmp_path):
    """Kills `_stale_ids = set()`, `stale_scenarios -> []`, and a constant
    `scenario_fingerprint` -- each of which left all 954 other tests green.

    The two assertions are the two halves of the defect as a user meets it: the exit code is
    what a CI gate reads, and the word `unchanged` is what a human reads. Asserting merely
    "not exit 1" -- the shape the collision tests use -- would NOT catch this, because a false
    clearance exits 0.
    """
    _record_then_edit(tmp_path)
    chk = _check(tmp_path)

    assert chk.exit_code == cli.EXIT_UNMEASURED, (
        "MP-05 FALSE CLEARANCE: the only scenario in the run was rewritten after its baseline "
        f"was recorded, so nothing could be compared -- yet check exited {chk.exit_code}. "
        "0 is what a CI gate reads as 'safe to adopt'.\n" + chk.output
    )
    flat = " ".join(chk.output.split())
    assert "unchanged" not in flat, (
        "MP-05 FALSE CLEARANCE: `unchanged` was claimed over a scenario whose current "
        "definition was never baselined. The word IS the clearance.\n" + flat
    )


def test_the_refusal_names_the_scenario_and_both_fingerprints(tmp_path):
    """A refusal the user cannot act on is a different defect. `[M] 2026-09-07` the
    zero-comparison branch used to print `no baseline: greet` here -- false, and the diagnosis
    an upgrading user meets first."""
    _record_then_edit(tmp_path)
    flat = " ".join(_check(tmp_path).output.split())

    was = scenario_fingerprint(Scenario(**_scenario(_BENIGN)))
    now = scenario_fingerprint(Scenario(**_scenario(_HOSTILE)))
    assert "changed since its baseline: greet" in flat, flat
    assert was in flat and now in flat, f"neither fingerprint was shown:\n{flat}"
    assert "no baseline: greet" not in flat, (
        "the scenario HAS a baseline -- it is stale. Telling the user it has none sends them "
        f"to diagnose a lost store.\n{flat}"
    )


def test_a_matching_fingerprint_is_still_compared(tmp_path):
    """The positive control, and the reason 'refuse everything' is not a fix. `[M] 2026-09-07`
    three mutations that made the checker refuse EVERY scenario failed 42 tests -- none of them
    about staleness -- while the collision tests passed. Without this assertion, a maintainer
    who broke the fingerprint lookup would see 42 red tests about the demo quickstart and none
    that says 'fingerprint'."""
    _record_then_edit(tmp_path)
    _write_scenario(tmp_path, _BENIGN)  # put it back: the baseline now matches again
    chk = _check(tmp_path)

    flat = " ".join(chk.output.split())
    assert "changed since its baseline" not in flat, flat
    assert chk.exit_code != cli.EXIT_UNMEASURED, (
        "an UNEDITED scenario whose fingerprint matches must still be compared; refusing "
        f"everything is not a fix.\n{chk.output}"
    )


def test_a_refused_scenario_costs_zero_provider_calls(tmp_path, monkeypatch):
    """`cli.py` claims it in a comment -- *"replayed by nothing ... the user is not charged for
    it"* -- and `[M] 2026-09-07` a mutation that replayed the scenario and then DISCARDED the
    result left the suite 954-green: verdict, exit code and published report were byte-identical
    to the fix, and only the bill changed. ADR-0008: it is the end user's own key."""
    _record_then_edit(tmp_path)

    calls: list[tuple[str, str]] = []
    real = cli.replay

    def _counting(scenario, model_id, adapter, runs=5, **kw):
        calls.append((scenario.id, model_id))
        return real(scenario, model_id, adapter, runs=runs, **kw)

    monkeypatch.setattr(cli, "replay", _counting)
    _check(tmp_path)

    assert calls == [], (
        f"MP-05: a scenario refused as stale was still replayed {len(calls)} time(s) "
        f"({calls}). The result is discarded, so the user paid for a comparison Modelpin then "
        "refused to make."
    )


# --- the predicate under it -----------------------------------------------------------------


def test_stale_scenarios_flags_a_changed_definition_and_only_a_changed_one():
    """`[M] 2026-09-07` `return []` survived all 954 tests. Inverting the comparison to
    `was == current` failed 42 -- but not one of them is about staleness; they fail because
    every MATCHING scenario is then refused, so the 42 name the collateral, not the defect."""
    recorded = {"a": "sha256:aaa", "b": "sha256:bbb"}

    assert stale_scenarios([("a", "sha256:zzz")], recorded) == [("a", "sha256:aaa", "sha256:zzz")]
    # The load-bearing one: the only assertion that separates the real function from the
    # inverted mutant, and the direction that would produce a false REGRESSION.
    assert stale_scenarios([("a", "sha256:aaa")], recorded) == []
    # Absent is UNVERIFIABLE, not stale: a different condition with a different message.
    # Conflating them is what `storage.FINGERPRINTS_KEY`'s docstring forbids.
    assert stale_scenarios([("c", "sha256:ccc")], recorded) == []
    assert stale_scenarios([], recorded) == []


# --- the hash under that ---------------------------------------------------------------------


def test_scenario_fingerprint_distinguishes_two_different_scenarios():
    """`[M] 2026-09-07` `return "sha256:CONST"` survived all 954 tests. `compute_suite_hash` is
    itself mutation-tested, but `scenario_fingerprint` is a separate function and had no test of
    its own -- it appeared in `tests/` only as a helper other tests call, and a caller is not an
    assertion. The whole of MP-05 rests on this one property."""
    benign = Scenario(**_scenario(_BENIGN))
    hostile = Scenario(**_scenario(_HOSTILE))

    assert scenario_fingerprint(benign) != scenario_fingerprint(hostile)
    assert scenario_fingerprint(benign) == scenario_fingerprint(benign.model_copy(deep=True))
    assert scenario_fingerprint(benign).startswith("sha256:")


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["input"]["messages"][0].update(content="different"), id="prompt"),
        pytest.param(lambda d: d["input"].update(temperature=1.0), id="temperature"),
        pytest.param(lambda d: d.update(assertions={"must_contain": ["hi"]}), id="assertions"),
    ],
)
def test_every_field_the_engine_reads_moves_the_fingerprint(mutate):
    """A fingerprint that ignored any of these would clear a scenario that had genuinely
    changed -- the false-clearance direction, one field at a time."""
    base = _scenario(_BENIGN)
    changed = json.loads(json.dumps(base))
    mutate(changed)
    assert scenario_fingerprint(Scenario(**base)) != scenario_fingerprint(Scenario(**changed))


def test_the_upgrade_path_does_not_tell_the_user_their_baseline_is_missing(tmp_path):
    """The shape an existing user meets FIRST, and the one a first-run review found worst:
    every scenario in their store predates the fingerprint, so every one is refused at once.

    `[M] 2026-09-07` That run printed *"2 scenario(s) had no usable baseline. no baseline:
    alpha, beta"* — false, on a store the user had paid to record. The carefully worded notes
    sat past the `raise` in the zero-comparison branch and were unreachable exactly when the
    whole store was affected. Re-recording is not free (it replays and re-bills the suite), so
    telling someone their intact store is missing invites them to go looking for a lost file.
    """
    from modelpin.models import Trace
    from modelpin.storage import save_baseline

    _write_scenario(tmp_path, _BENIGN)
    (tmp_path / "modelpin.yaml").write_text(
        "models:\n  - base-model\nscenarios_dir: scenarios\nruns: 5\n", encoding="utf-8"
    )
    _fixtures(tmp_path)
    # An OLD-STYLE store: real traces, no fingerprints. Exactly what an upgrade looks like.
    save_baseline(
        {
            "greet": [
                Trace(scenario_id="greet", model_id="base-model", run_idx=i, final_output="hi")
                for i in range(5)
            ]
        },
        "base-model",
        tmp_path / ".modelpin",
    )

    chk = _check(tmp_path)
    flat = " ".join(chk.output.split())

    assert chk.exit_code == cli.EXIT_UNMEASURED, chk.output
    assert "no baseline: greet" not in flat, (
        "the user HAS a baseline for `greet`; it simply records no fingerprint. Telling them "
        f"it is missing sends them to diagnose a lost store.\n{flat}"
    )
    assert "no recorded fingerprint" in flat, flat
    assert "Re-run `modelpin baseline` once" in flat, f"no remedy was offered:\n{flat}"
    # `[M]` The report IS written on this path but its notes were discarded, so the one surface
    # naming MP-05 here was never pointed to.
    assert "report written to" in flat, f"the published report was not announced:\n{flat}"
    assert (tmp_path / ".modelpin" / "last-report.md").exists()
