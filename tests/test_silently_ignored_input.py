"""Input the user wrote and the tool silently ignored (MP-192, MP-198, MP-199).

Three defects, one family. In each, the user states something, Modelpin discards it without
a word, and the run continues to a confident verdict computed from less than the user
supplied. `[M] 2026-09-06` all three reproduced end to end through the real CLI.

They are grouped because the fix is one principle -- **if we cannot use what you wrote, say
so before spending anything** -- and because keeping them apart invites exactly the mistake
this repo has made before: closing one and assuming the class went with it.

**MP-199 is the worst of the three and the reason the file is P0.** A scenario in a
subdirectory is silently skipped, so a suite that holds a real refusal regression reports
``OK 1 scenario(s) unchanged``, exit 0. That is a FALSE CLEARANCE -- the north-star failure
-- produced by nothing more exotic than putting scenarios in folders.

**MP-198** is the one that costs money. `ModelpinConfig` is a plain pydantic model, so
pydantic v2's default ``extra="ignore"`` silently drops every misspelled key. ``provider:``
for ``providers:`` is the most natural slip available, and the default it falls back to is
``openai`` at ``runs=5`` -- so a config written to get a free offline run bills the user's own
key, five replays per scenario. Stated fairly, the pre-spend line does print
``provider=openai``; the point is that it is the ONLY thing between the typo and the bill.

**MP-192** spends the money and then throws the result away: two files sharing an ``id``
means one recording is overwritten, while every printed count still claims both were measured
and the PR comment publishes a regression count that is simply wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelpin.config import DEFAULT_PROVIDER, DEFAULT_RUNS, ConfigError, load_config
from modelpin.scenarios import ScenarioError, load_scenarios


def _scenario(sid: str, content: str = "hi") -> str:
    return json.dumps(
        {
            "id": sid,
            "name": sid,
            "kind": "single",
            "input": {"messages": [{"role": "user", "content": content}]},
        }
    )


# --------------------------------------------------------------- MP-199: subdirectories


def test_a_scenario_in_a_subdirectory_is_loaded(tmp_path) -> None:
    """The defect in one assertion: a nested scenario must not vanish.

    `[M] 2026-09-06` before the fix this returned 1 -- and the CLI went on to print
    `OK 1 scenario(s) unchanged`, exit 0, over a nested scenario whose candidate refused
    every run.
    """
    d = tmp_path / "scenarios"
    (d / "auth").mkdir(parents=True)
    (d / "top.json").write_text(_scenario("top"), encoding="utf-8")
    (d / "auth" / "nested.json").write_text(_scenario("nested"), encoding="utf-8")

    ids = sorted(s.id for s in load_scenarios(d))
    assert ids == ["nested", "top"], (
        "A scenario in a subdirectory was dropped. Grouping scenarios into folders is the "
        "first thing anyone does past a handful of them, and dropping one silently is a "
        "false clearance: the suite reports `unchanged` over a scenario it never ran."
    )


def test_reserved_manifest_files_are_still_skipped_at_any_depth(tmp_path) -> None:
    """Control. Recursing must not start loading the suite manifest as a scenario."""
    d = tmp_path / "scenarios"
    (d / "sub").mkdir(parents=True)
    (d / "top.json").write_text(_scenario("top"), encoding="utf-8")
    (d / "sub" / "manifest.json").write_text('{"suite_version": "3.0.0"}', encoding="utf-8")
    (d / "sub" / "roles.json").write_text('{"role": "fit"}', encoding="utf-8")

    assert [s.id for s in load_scenarios(d)] == ["top"]


def test_an_output_directory_beside_the_scenarios_is_not_loaded(tmp_path) -> None:
    """Recursing must not start reading RUN OUTPUT as scenarios.

    `[M] 2026-09-06` this is a regression the `rglob` change actually caused before it was
    caught: `examples/calibration/results/*.json` -- six measurement outputs with no `id`,
    `name` or `input` -- were pulled into the loader and failed three tests. Keeping results
    beside the scenarios that produced them is an obvious user layout too.
    """
    d = tmp_path / "scenarios"
    (d / "results").mkdir(parents=True)
    (d / "s.json").write_text(_scenario("s"), encoding="utf-8")
    (d / "results" / "run-1.json").write_text(
        '{"generated_utc": "2026-09-06", "rows": []}', "utf-8"
    )

    assert [s.id for s in load_scenarios(d)] == ["s"]


def test_a_dot_directory_is_not_loaded(tmp_path) -> None:
    """`scenarios_dir` is user-supplied and pointing it at a repo root is a realistic slip;
    `.git` and `.modelpin` are full of JSON that is emphatically not a scenario."""
    d = tmp_path / "scenarios"
    (d / ".modelpin").mkdir(parents=True)
    (d / "s.json").write_text(_scenario("s"), encoding="utf-8")
    (d / ".modelpin" / "baseline-m1.json").write_text('{"model_id": "m1"}', encoding="utf-8")

    assert [s.id for s in load_scenarios(d)] == ["s"]


def test_the_repos_own_example_suites_still_load(tmp_path) -> None:
    """The shipped suites are the closest thing to a real user's tree that is always present.

    `examples/calibration` is the one that carries a `results/` subdirectory, so it is the
    case that would break first if the reserved-directory rule were dropped.
    """
    root = Path(__file__).resolve().parents[1]
    calibration = root / "examples" / "calibration"
    if not calibration.exists():  # pragma: no cover - sdist without examples
        pytest.skip("examples/ not present in this checkout")
    ids = [s.id for s in load_scenarios(calibration)]
    assert ids, "examples/calibration loaded no scenarios at all"
    assert len(ids) == len(set(ids)), f"duplicate ids in the shipped suite: {ids}"


def test_a_nested_malformed_scenario_still_raises_rather_than_being_skipped(tmp_path) -> None:
    """Recursing must not turn a nested syntax error into a silent skip -- that would swap
    one silent-ignore defect for another, one directory deeper."""
    d = tmp_path / "scenarios"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_scenarios(d)


# ------------------------------------------------------------------ MP-192: duplicate id


def test_two_files_sharing_an_id_are_rejected_naming_both(tmp_path) -> None:
    """Rejected BEFORE anything is replayed or spent, and the message must name both files.

    Naming both is the whole usability of this error: the user copied a file and forgot to
    change one field, and "duplicate id 'refund_request'" without paths sends them hunting.
    """
    d = tmp_path / "scenarios"
    d.mkdir()
    (d / "a.json").write_text(_scenario("refund_request", "refund my order"), encoding="utf-8")
    (d / "b.json").write_text(_scenario("refund_request", "refund my EU order"), encoding="utf-8")

    with pytest.raises(ScenarioError) as exc:
        load_scenarios(d)
    message = str(exc.value)
    assert "refund_request" in message
    assert "a.json" in message and "b.json" in message, (
        "The duplicate-id error must name BOTH files: " + message
    )


def test_the_same_id_in_different_directories_is_still_a_duplicate(tmp_path) -> None:
    """MP-192 and MP-199 interact: recursing creates new ways to collide, and the store is
    keyed on `id` alone, so a nested duplicate loses a recording exactly like a flat one."""
    d = tmp_path / "scenarios"
    (d / "sub").mkdir(parents=True)
    (d / "dup.json").write_text(_scenario("dup"), encoding="utf-8")
    (d / "sub" / "dup.json").write_text(_scenario("dup"), encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_scenarios(d)


def test_distinct_ids_are_untouched(tmp_path) -> None:
    """Control. The overwhelmingly common case must not acquire a new failure mode."""
    d = tmp_path / "scenarios"
    d.mkdir()
    (d / "a.json").write_text(_scenario("alpha"), encoding="utf-8")
    (d / "b.json").write_text(_scenario("beta"), encoding="utf-8")
    assert sorted(s.id for s in load_scenarios(d)) == ["alpha", "beta"]


# ------------------------------------------------------------- MP-198: unknown config key


def test_a_misspelled_config_key_is_rejected_not_silently_dropped(tmp_path) -> None:
    """`provider:` for `providers:` must fail loudly, because the fallback SPENDS MONEY.

    `[M] 2026-09-06` before the fix this config yielded `providers=['openai']`, `runs=5` --
    a user who wrote `fake` and `1` got five paid OpenAI replays per scenario.
    """
    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text(
        "models:\n  - m1\nprovider:\n  - fake\nscenario_dir: scenarios\nrun: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfg)
    message = str(exc.value)
    assert "provider" in message, "The error must name the offending key: " + message


def test_the_error_points_at_the_key_the_user_probably_meant(tmp_path) -> None:
    """The failure mode is singular-vs-plural, so a bare "unknown key" wastes the round trip."""
    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text("models:\n  - m1\nprovider:\n  - fake\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(cfg)
    assert "providers" in str(exc.value), "A near-miss key should suggest the real one: " + str(
        exc.value
    )


def test_a_correct_config_still_loads(tmp_path) -> None:
    """Control, and the one that would catch an over-strict fix."""
    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text(
        "models:\n  - m1\nproviders:\n  - fake\nscenarios_dir: scenarios\nruns: 3\n"
        "judge_model: gpt-4o-mini\njudge_provider: openai\nregression_threshold: 0.2\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    assert loaded.providers == ["fake"]
    assert loaded.runs == 3
    assert loaded.judge_model == "gpt-4o-mini"


def test_the_scaffolded_config_this_tool_itself_writes_still_loads(tmp_path) -> None:
    """The fix must not reject `mp init`'s own output -- that would break the first run.

    Asserted against the real scaffold constant rather than a copy, so the guard cannot pass
    while the shipped scaffold drifts into carrying a key the loader now forbids.
    """
    import modelpin.cli as cli

    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text(cli._SAMPLE_CONFIG, encoding="utf-8")
    loaded = load_config(cfg)
    assert loaded.runs == DEFAULT_RUNS or loaded.runs >= 1
    assert loaded.providers  # whatever it scaffolds, it must be loadable


def test_the_default_that_makes_this_dangerous_is_still_what_the_row_says() -> None:
    """Pins the two constants MP-198's severity rests on. If either moves, the row is stale.

    Not decoration: the row argues P0 *because* the silent fallback is a PAID provider at 5
    runs. Were the default `fake`, the same silence would be a papercut.
    """
    assert DEFAULT_PROVIDER == "openai"
    assert DEFAULT_RUNS == 5
