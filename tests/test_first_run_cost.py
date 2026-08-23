"""MP-32 - a cloned repo's own config must never become a stranger's config.

The repo root used to hold `modelpin.yaml`, the dogfood config that
`.github/workflows/modelpin.yml` runs Modelpin against itself with. The CLI reads
`modelpin.yaml` from the working directory by default, so in a fresh clone that file WAS
the user's config:

  * `mp init` honours an existing config's `scenarios_dir` (a deliberate fix - see
    CHANGELOG 0.1.2), found 8 scenarios already in `examples/suite`, scaffolded nothing,
    and still exited 0 saying "Already initialised";
  * `mp baseline`, the very next line of README "The real flow, on your own app", then
    replayed Modelpin's own held-out public suite on the user's paid key - 40 replays
    (8 scenarios x runs: 5). In the one recording committed to this repo
    (`.modelpin/baseline-gpt-4o-mini.json`, 2026-06-24) those cost 60 completion calls,
    because three `kind: agent` scenarios drive a 1-6 turn tool loop; that multiplier is
    the model's decision, so treat 60 as an observation bounded by 40-115, not a constant.

These tests pin the user-visible contract, not the remedy: after `mp init`, the scenarios
`mp baseline` bills for are the user's own. They stay valid if the fix later changes shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from modelpin.cli import _replay_plan, app
from modelpin.config import load_config
from modelpin.models import Scenario, Trace
from modelpin.providers.base import ProviderAdapter
from modelpin.scenarios import load_scenarios

REPO = Path(__file__).resolve().parents[1]

#: Modelpin's own held-out suite. A stranger's key must never be spent on these.
PUBLIC_SUITE_IDS = {p.stem for p in (REPO / "examples" / "suite").glob("*.json")}

#: Where the dogfood config lives now, and where the workflow must point.
DOGFOOD_CONFIG = REPO / ".github" / "modelpin.yaml"
DOGFOOD_WORKFLOW = REPO / ".github" / "workflows" / "modelpin.yml"


class CountingAdapter(ProviderAdapter):
    """Stands in for a paid provider and records what would have been billed."""

    name = "openai"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        self.calls.append((scenario.id, model_id))
        return Trace(
            scenario_id=scenario.id, model_id=model_id, run_idx=run_idx, final_output="stub"
        )


def _tracked(relpath: str) -> bool:
    """Is `relpath` committed to this repo? Skips the test when there is no git checkout.

    Deliberately NOT `Path.exists()`. A contributor who follows the README - clone, then
    `mp init` - legitimately creates a local root `modelpin.yaml`, and failing their test
    run for that would be a false positive shipped inside the suite of a project whose
    north-star metric is the false-positive rate. What must never come back is a *tracked*
    one, because that is what a `git clone` hands a stranger.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--", relpath],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env-specific
        pytest.skip(f"git unavailable, cannot check tracked files: {exc}")
    if proc.returncode != 0:  # pragma: no cover - e.g. running from an unpacked sdist
        pytest.skip(f"not a git checkout; `git ls-files` exited {proc.returncode}")
    return bool(proc.stdout.strip())


def test_repo_root_holds_no_tracked_modelpin_yaml() -> None:
    """The root config is the whole defect: the CLI adopts it by default. Keep it untracked."""
    assert not _tracked("modelpin.yaml"), (
        "A `modelpin.yaml` is tracked at the repo root again. The CLI loads that path by "
        "default, so everyone who clones this repo inherits it as their config and "
        "`mp baseline` spends their key on whatever it points at. That is the MP-32 defect. "
        "Keep the dogfood config in .github/ and pass it with `config:`."
    )


def test_dogfood_config_exists_and_the_workflow_points_at_it() -> None:
    """Moving the config must not quietly disable the self-check that justified it."""
    assert DOGFOOD_CONFIG.is_file(), f"{DOGFOOD_CONFIG} is missing; the dogfood run has no config"
    wf = yaml.safe_load(DOGFOOD_WORKFLOW.read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["model-check"]["steps"] if s.get("with", {}).get("config"))
    configured = REPO / step["with"]["config"]
    assert (
        configured.resolve() == DOGFOOD_CONFIG.resolve()
    ), f"workflow passes config={step['with']['config']!r}, which is not {DOGFOOD_CONFIG}"
    cfg = load_config(DOGFOOD_CONFIG)
    assert (REPO / cfg.scenarios_dir).is_dir(), (
        f"dogfood scenarios_dir {cfg.scenarios_dir!r} does not resolve from the repo root; "
        "paths in that file resolve against the working directory, not against the file"
    )


@pytest.fixture()
def fresh_clone(tmp_path: Path) -> Path:
    """The tracked artifacts a `git clone` of this repo puts in front of a stranger."""
    import shutil

    root = tmp_path / "clone"
    root.mkdir()
    # Tracked files only - a maintainer's own untracked `mp init` output is not something
    # a clone hands anyone, and copying it here would make these tests depend on the state
    # of the working tree they run in.
    if _tracked("modelpin.yaml"):  # the defect, if it ever returns
        shutil.copy2(REPO / "modelpin.yaml", root / "modelpin.yaml")
    shutil.copytree(REPO / "examples" / "suite", root / "examples" / "suite")
    return root


def test_init_in_a_fresh_clone_leaves_the_user_with_their_own_scenarios(
    fresh_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After `mp init`, the dir `mp baseline` will read must not be Modelpin's own suite."""
    monkeypatch.chdir(fresh_clone)
    result = CliRunner().invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    resolved = load_config("modelpin.yaml")
    ids = {s.id for s in load_scenarios(resolved.scenarios_dir)}
    assert not (ids & PUBLIC_SUITE_IDS), (
        f"`mp init` left the user pointed at Modelpin's own held-out suite "
        f"({resolved.scenarios_dir!r} -> {sorted(ids)}); the next README command bills it"
    )


def test_baseline_after_init_bills_only_the_users_own_scenarios(
    fresh_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The billing-boundary assertion: count the replays `mp baseline` would pay for."""
    monkeypatch.chdir(fresh_clone)
    CliRunner().invoke(app, ["init"])

    adapter = CountingAdapter()
    monkeypatch.setattr("modelpin.cli._adapter", lambda provider, fixtures: adapter)
    result = CliRunner().invoke(app, ["baseline", "--store-dir", str(fresh_clone / ".store")])
    assert result.exit_code == 0, result.output

    billed = {sid for sid, _ in adapter.calls}
    assert not (billed & PUBLIC_SUITE_IDS), (
        f"`mp baseline` billed the user for Modelpin's own public suite: "
        f"{len(adapter.calls)} replays across {sorted(billed)}"
    )


def test_baseline_states_the_size_of_the_run_before_spending(
    fresh_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user must be able to see 'N scenarios -> M replays' before the calls, not after."""
    monkeypatch.chdir(fresh_clone)
    CliRunner().invoke(app, ["init"])

    adapter = CountingAdapter()
    monkeypatch.setattr("modelpin.cli._adapter", lambda provider, fixtures: adapter)
    result = CliRunner().invoke(
        app, ["baseline", "--runs", "5", "--store-dir", str(fresh_clone / ".store")]
    )
    assert result.exit_code == 0, result.output

    # Rich hard-wraps to the console width, so compare on whitespace-normalised text -
    # otherwise this asserts on terminal geometry instead of on what was disclosed.
    out = " ".join(result.output.split())
    planned = f"{len(adapter.calls)} replays"
    assert planned in out, (
        f"pre-spend line does not state the run size ({planned!r} absent). Output:\n" f"{out}"
    )
    # ...and it must come BEFORE the line that reports the money already spent.
    assert out.index(planned) < out.index(
        "Baseline recorded"
    ), "the run size is disclosed only after the spend"


class TestReplayPlan:
    """The pre-spend line is a claim about someone's bill. Pin each branch of it.

    ADR-0019 is the contract: a floor never a ceiling, the judge named as its own axis, and
    no cost claimed at all on a path that cannot bill.
    """

    def test_fake_provider_claims_no_cost_at_all(self) -> None:
        plan = _replay_plan(4, "scenarios", 5, "fake", judge_model="gpt-4o-mini")
        assert plan == "4 scenario(s) from scenarios -> 20 replays"
        assert (
            "paid" not in plan and "judge" not in plan
        ), f"the offline demo runs on canned traces and bills nothing: {plan!r}"

    def test_paid_provider_marks_replays_as_a_floor_not_a_ceiling(self) -> None:
        plan = _replay_plan(8, "examples/suite", 5, "openai", judge_model=None)
        assert "40 replays" in plan
        assert ">=40 paid calls" in plan, (
            f"replays are a floor on paid calls - agent scenarios make several per replay "
            f"- so the line must not read as an exact count: {plan!r}"
        )

    def test_a_configured_judge_is_disclosed_as_its_own_axis(self) -> None:
        plan = _replay_plan(8, "examples/suite", 5, "openai", judge_model="gpt-4o-mini")
        # semantic_divergence_flags judges at most once per run per side => 2 per replay.
        assert "up to 80 judge calls" in plan, plan

    def test_no_judge_configured_means_no_judge_claim(self) -> None:
        assert "judge" not in _replay_plan(1, "scenarios", 5, "openai", judge_model=None)

    def test_a_trailing_separator_in_the_dir_is_not_doubled(self) -> None:
        # `--scenarios-dir examples/suite/` used to render `examples/suite// -> ...`.
        assert "//" not in _replay_plan(8, "examples/suite/", 5, "openai", judge_model=None)
