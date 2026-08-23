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
    (`.modelpin/baseline-modelpin-dogfood.json`, 2026-06-24) those cost 60 completion calls,
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
        # semantic_divergence_flags judges at most once per run per side => 2 per
        # scenario-run. That equals 2 per replay only when ONE side is replayed live.
        assert "up to 80 judge calls" in plan, plan

    def test_no_judge_configured_means_no_judge_claim(self) -> None:
        assert "judge" not in _replay_plan(1, "scenarios", 5, "openai", judge_model=None)

    def test_a_trailing_separator_in_the_dir_is_not_doubled(self) -> None:
        # `--scenarios-dir examples/suite/` used to render `examples/suite// -> ...`.
        assert "//" not in _replay_plan(8, "examples/suite/", 5, "openai", judge_model=None)

    def test_a_two_sided_run_doubles_the_replays_and_the_floor(self) -> None:
        # MP-70: `mp report` replays --from AND --to, so the same suite costs twice what
        # `check` costs. [M] 14x5x2 = 140 adapter.run() calls, measured, not derived.
        plan = _replay_plan(14, "examples/report-suite", 5, "openai", None, sides=2)
        assert "14 scenario(s)" in plan, f"the scenario count is not doubled - {plan!r}"
        assert "x 2 models -> 140 replays" in plan, plan
        assert ">=140 paid calls" in plan, plan

    def test_a_two_sided_run_does_NOT_double_the_judge_axis(self) -> None:
        """The trap MP-70's one-line fix walks into. Deleting this test hides a false claim.

        The judge scores every run on BOTH sides whatever their origin (semantic.py:68-69),
        so its bound is 2 x scenarios x runs and is INDEPENDENT of how many sides were
        replayed live. [M] the same 14-scenario suite: `check` queues 70 replays, `report`
        queues 140, and both make at most 126 judge calls. Deriving the judge count from
        `replays` would bill `report`'s user a claim 2x too large - ADR-0019's rejected
        false-point-estimate defect, pointed the other way.
        """
        one = _replay_plan(14, "examples/report-suite", 5, "openai", "gpt-4o-mini")
        two = _replay_plan(14, "examples/report-suite", 5, "openai", "gpt-4o-mini", sides=2)
        assert "up to 140 judge calls" in one, one
        assert (
            "up to 140 judge calls" in two
        ), f"the judge axis must not scale with sides - it is per scenario-run: {two!r}"
        assert "280" not in two, f"judge calls derived from replays instead of runs: {two!r}"

    def test_a_two_sided_fake_run_still_claims_no_cost(self) -> None:
        plan = _replay_plan(14, "examples/report-suite", 5, "fake", "gpt-4o-mini", sides=2)
        assert plan == "14 scenario(s) from examples/report-suite x 2 models -> 140 replays"
        assert "paid" not in plan and "judge" not in plan, plan

    def test_the_single_sided_line_is_untouched_by_the_sides_parameter(self) -> None:
        # `baseline`/`check` must render byte-identically to before MP-70 - the pre-spend
        # line they already ship is not up for silent revision.
        args = (8, "examples/suite", 5, "openai", "gpt-4o-mini")
        assert _replay_plan(*args) == _replay_plan(*args, sides=1)
        assert _replay_plan(*args) == (
            "8 scenario(s) from examples/suite -> 40 replays, >=40 paid calls "
            "+ up to 80 judge calls"
        )
        assert "models" not in _replay_plan(*args), "the x N models hint leaked into check"


#: The open public suite `mp report --suite-dir` is documented against (README:313).
REPORT_SUITE = REPO / "examples" / "report-suite"


def test_report_states_the_size_of_the_run_before_spending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MP-70: `mp report` inherits ADR-0019's obligation, and both sides are billed.

    `report()` replays --from AND --to live with the user's own key (ADR-0008), so the
    run is TWICE what a single-sided command of the same shape costs. The disclosure
    must therefore count both sides: the trap here is reusing `check`'s single-sided
    figure and understating the bill by 2x. Asserted against what the adapter was
    actually asked to do, so the number cannot drift away from the behaviour.
    """
    monkeypatch.chdir(tmp_path)  # hermetic: never adopt a dev's local modelpin.yaml
    adapter = CountingAdapter()
    monkeypatch.setattr("modelpin.cli._adapter", lambda provider, fixtures: adapter)
    result = CliRunner().invoke(
        app,
        [
            "report",
            "--to",
            "model-b",
            "--from",
            "model-a",
            "--provider",
            "openai",  # the PAID branch: this is the path that spends
            "--runs",
            "5",
            "--suite-dir",
            str(REPORT_SUITE),
            "--config",
            str(tmp_path / "absent.yaml"),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0, result.output

    # Rich hard-wraps to the console width, so compare on whitespace-normalised text -
    # otherwise this asserts on terminal geometry instead of on what was disclosed.
    out = " ".join(result.output.split())
    planned = f"{len(adapter.calls)} replays"
    assert planned in out, (
        f"`mp report` never disclosed the size of the run ({planned!r} absent): it replayed "
        f"{len(adapter.calls)} times on the user's key with no pre-spend line. ADR-0019 "
        f"requires 'N scenario(s) from <dir> -> M replays, >=M paid calls' BEFORE the first "
        f"billable call. Output:\n{out}"
    )
    assert f">={len(adapter.calls)} paid calls" in out, (
        "replays are a FLOOR on paid calls, not a count - an agent scenario's replay drives a "
        f"tool loop of up to MAX_TOOL_TURNS completions (ADR-0019). Output:\n{out}"
    )
    # ...and it must come BEFORE the first billable call, not with the results.
    assert out.index(planned) < out.index("Modelpin:"), (
        "the run size reached the user only alongside the results - i.e. after the money was "
        "spent. That ordering IS the MP-32/MP-70 defect."
    )
