"""MP-69 - a scenario id collision must not manufacture a regression.

`storage.load_baseline` keys on the MODEL ID alone (`storage.py:55-74`) and `mp check`
pairs traces by scenario id alone (`cli.py:534` `base_traces = base.get(s.id)`). Nothing
records WHICH scenario definition produced the recorded traces, so a baseline recorded for
one scenario is silently diffed against a completely different scenario that happens to
share an id.

The concrete instance: `.modelpin/baseline-gpt-4o-mini.json` is TRACKED (`.gitignore:19-20`
re-includes it for the CI self-check), so every clone carries Modelpin's own 8 recorded
scenarios - including `refund_request` - and `mp init` scaffolds `models: [gpt-4o-mini]`,
the exact key those traces are stored under. A user who writes their own
`scenarios/refund_request.json` and runs `mp check` before any local `mp baseline` gets a
confident REGRESSION and exit 1 off two scenarios that share nothing but a name.

That is a false POSITIVE, which is the north-star metric. These tests build the
collision from scratch in tmp_path, so they pin the MECHANISM rather than the shipped
artifact: un-tracking or renaming the dogfood baseline removes today's instance but leaves
this code path intact (a user who edits their own scenario after recording a baseline hits
exactly the same thing).

STATUS. MP-69 shipped the INSTANCE fix: the dogfood baseline is keyed under the fictional
`modelpin-dogfood`, so no `mp init`-scaffolded config can name it. That is pinned below by
`test_no_tracked_baseline_is_keyed_under_a_scaffoldable_model_id`, which is the test that
must never go red.

The three MECHANISM tests are `xfail(strict=True)` against **MP-05** (a content hash in the
baseline payload), which is the row that actually closes this code path. Strict is the point:
if MP-05 lands and these start passing, pytest fails and tells whoever did it to delete the
marker. They are the specification of the fix, kept executable rather than turned into prose.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.diff import DiffVerdict
from modelpin.models import ToolCall, Trace
from modelpin.storage import save_baseline

_MECHANISM = pytest.mark.xfail(
    strict=True,
    reason="MP-05: nothing records WHICH scenario definition produced the recorded traces, "
    "so `mp check` still pairs on id alone. MP-69 removed the shipped instance of this "
    "collision, not the mechanism. Delete this marker when MP-05 lands.",
)

runner = CliRunner()

# What `mp init` writes (cli.py:93-101 `_SAMPLE_CONFIG`), trimmed to the load-bearing keys.
# `models[0]` is the baseline model id `mp check` resolves `--from` to (cli.py:507).
_INIT_CONFIG = """models:
  - gpt-4o-mini
scenarios_dir: scenarios
providers:
  - openai
runs: 5
"""

# The user's OWN scenario. Same id as a scenario in the shipped baseline, nothing else in
# common: no tools, no agent loop, a different app entirely.
_USER_SCENARIO = {
    "id": "refund_request",
    "name": "Acme billing FAQ bot answers a policy question",
    "kind": "single",
    "input": {
        "messages": [{"role": "user", "content": "What is your refund policy for annual plans?"}],
        "temperature": 0,
    },
    "assertions": {"must_contain": ["refund"]},
}


def _shipped_baseline_traces() -> list[Trace]:
    """Stand-in for Modelpin's own recorded `refund_request` traces: an AGENT run with a
    two-step tool trajectory, matching `.modelpin/baseline-gpt-4o-mini.json`."""
    return [
        Trace(
            scenario_id="refund_request",
            model_id="gpt-4o-mini",
            run_idx=i,
            tool_calls=[
                ToolCall(name="lookup_order", arguments={"order_id": "A-1042"}),
                ToolCall(name="issue_refund", arguments={"amount": 49.99}),
            ],
            final_output="I've looked up order A-1042 and issued your refund of $49.99.",
        )
        for i in range(5)
    ]


def _plant_collision(tmp_path: Path) -> None:
    """Reproduce a fresh clone: a baseline on disk the user never recorded, an `mp init`
    config keyed to the same model id, and the user's own same-named scenario."""
    save_baseline(
        {"refund_request": _shipped_baseline_traces()},
        "gpt-4o-mini",
        store_dir=tmp_path / ".modelpin",
    )
    (tmp_path / "modelpin.yaml").write_text(_INIT_CONFIG, encoding="utf-8")
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "refund_request.json").write_text(json.dumps(_USER_SCENARIO), encoding="utf-8")

    # What the candidate model actually does on the USER's scenario: answers in prose and
    # calls no tools, because the user's scenario declares none.
    fixtures = [
        Trace(
            scenario_id="refund_request",
            model_id="gpt-4o",
            final_output="Annual plans are fully refundable within 30 days of purchase.",
        ).model_dump(mode="json")
    ]
    (tmp_path / "fx.json").write_text(json.dumps(fixtures), encoding="utf-8")


def _run_check(tmp_path: Path):
    return runner.invoke(
        app,
        [
            "check",
            "--to",
            "gpt-4o",
            "--provider",
            "fake",
            "--fixtures",
            str(tmp_path / "fx.json"),
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--scenarios-dir",
            str(tmp_path / "scenarios"),
            "--store-dir",
            str(tmp_path / ".modelpin"),
        ],
    )


@_MECHANISM
def test_scenario_id_collision_does_not_manufacture_a_regression(tmp_path):
    """Two scenarios sharing only a name must not be reported as a behavioral regression."""
    _plant_collision(tmp_path)
    r = _run_check(tmp_path)

    assert DiffVerdict.regression.value not in r.output.lower(), (
        "MP-69: a baseline recorded for a DIFFERENT scenario that merely shares the id "
        "'refund_request' was diffed against the user's scenario and reported as a "
        f"regression. Nothing here measured a model change.\n--- output ---\n{r.output}"
    )


@_MECHANISM
def test_scenario_id_collision_does_not_fail_ci(tmp_path):
    """Exit 1 is the CI gate the GitHub Action fails a PR on (`action.yml:128-136`); a
    fabricated pairing must never reach it."""
    _plant_collision(tmp_path)
    r = _run_check(tmp_path)

    assert r.exit_code != 1, (
        "MP-69: `mp check` exited 1 - the CI-failing regression code - off a baseline the "
        f"user never recorded, for a scenario they never wrote.\n--- output ---\n{r.output}"
    )


@_MECHANISM
def test_scenario_id_collision_is_not_published_to_a_report(tmp_path):
    """`cli.py:555-561` writes the report BEFORE the exit code is raised, and the Action
    posts that file as a sticky PR comment (`action.yml:135,138-146`)."""
    _plant_collision(tmp_path)
    _run_check(tmp_path)

    report = tmp_path / ".modelpin" / "last-report.md"
    published = report.read_text(encoding="utf-8") if report.exists() else ""
    assert "regression" not in published.lower(), (
        "MP-69: the fabricated regression was written to .modelpin/last-report.md, which "
        f"the GitHub Action posts as a PR comment.\n--- report ---\n{published}"
    )


def test_no_tracked_baseline_is_keyed_under_a_scaffoldable_model_id():
    """MP-69, the instance fix — the one in this file that must never go red.

    A baseline under `.modelpin/` is TRACKED on purpose (`.gitignore:19-20` re-includes
    `baseline-*.json` so the dogfood workflow can run), so it ships in every clone. Given
    that `mp check` pairs a baseline to a scenario by id alone, any tracked baseline keyed
    under a model id a stranger's config could name WILL be paired against that stranger's
    own scenarios. `[M]` It was: a fresh clone plus a user's own `scenarios/refund_request.json`
    produced `REGRESSION ... confidence 0.99`, exit 1, published to `last-report.md`.

    So the store key must be fictional. `git ls-files` is deliberate — asserting
    `Path.exists()` here would fail a contributor for following the README (see ops/NOW.md).
    """
    tracked = subprocess.run(
        ["git", "ls-files", ".modelpin"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout.split()
    baselines = [Path(f).name for f in tracked if Path(f).name.startswith("baseline-")]
    if not baselines:
        return  # nothing tracked -> nothing ships -> no collision surface

    scaffolded = yaml.safe_load(_INIT_CONFIG)["models"]
    assert scaffolded, "the scaffold names no model; this test would be vacuous"
    forbidden = {f"baseline-{m}.json" for m in scaffolded}
    collides = sorted(set(baselines) & forbidden)
    assert not collides, (
        f"MP-69: tracked baseline(s) {collides} are keyed under a model id that `mp init` "
        f"scaffolds ({scaffolded}). Every clone ships those traces, and `mp check` will pair "
        "them against the cloner's own scenarios by id alone — fabricating a regression on "
        "scenarios that share nothing but a filename. Key the dogfood baseline under a "
        "fictional id (e.g. `modelpin-dogfood`) instead."
    )
