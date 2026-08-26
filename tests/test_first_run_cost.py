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

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml
from typer.testing import CliRunner

from modelpin.cli import _replay_plan, app
from modelpin.config import load_config
from modelpin.models import Scenario, Trace
from modelpin.providers.base import ProviderAdapter
from modelpin.scenarios import load_scenarios
from modelpin.storage import save_baseline

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
        # semantic_divergence_flags judges at most once per run per side. BOTH sides hold
        # `runs` traces in this call, so the bound is 2 x scenarios x runs; that is a
        # property of this input, NOT a general rule -- `check` against a stored baseline of
        # a different depth discloses `sum(len(stored)) + scenarios x runs` (ADR-0026).
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
        queues 140, and `report` makes at most 126 judge calls. `check` matches that figure
        only when the stored baseline holds `runs` traces per scenario -- otherwise its
        reference side is the RECORDED count (MP-72, `ref_runs`). Deriving the judge count from
        `replays` would bill `report`'s user a claim 2x too large - ADR-0019's rejected
        false-point-estimate defect, pointed the other way.
        """
        one = _replay_plan(14, "examples/report-suite", 5, "openai", "gpt-4o-mini")
        two = _replay_plan(14, "examples/report-suite", 5, "openai", "gpt-4o-mini", sides=2)
        assert "up to 140 judge calls" in one, one
        assert (
            "up to 140 judge calls" in two
        ), f"the judge axis must not scale with sides - it counts RUNS per side: {two!r}"
        assert "280" not in two, f"judge calls derived from replays instead of runs: {two!r}"

    def test_ref_runs_defaults_to_the_pre_MP_72_line(self) -> None:
        """`ref_runs=None` and `ref_runs=count*runs` must both render the OLD string.

        MP-72 widened the judge axis for `check` only. `baseline` passes no judge at all and
        `report` replays both sides itself, so both keep `count * runs` references and must
        be byte-identical to before -- a diff in either is a regression, not an improvement.
        """
        args = (8, "examples/suite", 5, "openai", "gpt-4o-mini")
        expected = (
            "8 scenario(s) from examples/suite -> 40 replays, >=40 paid calls "
            "+ up to 80 judge calls"
        )
        assert _replay_plan(*args) == expected
        assert _replay_plan(*args, ref_runs=40) == expected, "the no-op default is not a no-op"
        two = _replay_plan(14, "examples/report-suite", 5, "openai", "gpt-4o-mini", sides=2)
        assert "up to 140 judge calls" in two, f"report's judge axis moved: {two!r}"

    def test_a_larger_stored_baseline_widens_the_judge_bound(self) -> None:
        """MP-72: the reference side is what was RECORDED, not `--runs`.

        [M] a 20-run baseline checked at `--runs 5` issues 24 judge calls (19 baseline --
        the modal run is the reference and skips the judge -- plus 5 candidate). The bound
        must cover that. It stays `up to`: 25 >= 24 because bounding at the raw stored count
        is honest and simple, where subtracting the modal run per scenario would be a
        point estimate of exactly the kind ADR-0019 rejects.
        """
        plan = _replay_plan(1, "scenarios", 5, "openai", "gpt-4o-mini", ref_runs=20)
        assert "up to 25 judge calls" in plan, plan
        assert "up to 10 judge calls" not in plan, f"still bounded by --runs: {plan!r}"
        # A SMALLER stored baseline must narrow it too - the bound tracks the recording.
        assert "up to 8 judge calls" in _replay_plan(
            1, "scenarios", 5, "openai", "gpt-4o-mini", ref_runs=3
        )

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


class CountingJudge:
    """Stands in for the paid LLM-judge and records every equivalence call it is asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool:
        self.calls.append((reference, candidate))
        return True  # always "equivalent": this test is about the bill, not the verdict


def _disclosed_judge_calls(output: str) -> int:
    """The number the pre-spend line PROMISED, read back off the line the user saw."""
    flat = " ".join(output.split())  # Rich hard-wraps; assert on text, not terminal geometry
    match = re.search(r"up to (\d+) judge calls", flat)
    assert match, f"no judge-call disclosure in the pre-spend line: {flat}"
    return int(match.group(1))


def test_check_judge_disclosure_bounds_the_judge_calls_it_makes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MP-72 - `up to N judge calls` must be a BOUND, including on an older, larger baseline.

    THE DEFECT, in the past tense because it is fixed: `_replay_plan` USED TO derive the
    judge axis from `2 x scenarios x --runs`, which assumes both sides hold `--runs` traces.
    Only the candidate side does: `load_baseline` returns however many runs were RECORDED,
    `check` passes them straight into `diff_scenario`, and `semantic_divergence_flags` then
    scores every run on BOTH sides. A baseline recorded at `--runs 20` and checked at
    `--runs 5` therefore issued 24 judge calls for ONE scenario (19 baseline - the modal run
    IS the reference and skips the judge - plus 5 candidate) against a disclosed bound of
    `2 x 1 x 5 = 10`. It NOW sums the stored traces off disk (ADR-0026); the current rule is
    `J = <reference runs> + <candidate runs>`.

    Asserted against what the judge was actually ASKED to do, so the number cannot drift away
    from the behaviour. `mp report` is NOT affected - `replay()` hands it exactly `runs`
    traces per side - and the sides test above pins that half, so do not "fix" this one by
    scaling the judge axis with `sides`.
    """
    monkeypatch.chdir(tmp_path)  # hermetic: never adopt a dev's local modelpin.yaml
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "one.json").write_text(
        json.dumps(
            {
                "id": "one",
                "name": "one scenario",
                "kind": "single",
                "input": {"messages": [{"role": "user", "content": "say something"}]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "modelpin.yaml").write_text(
        "models: [old-model]\n" "scenarios_dir: scenarios\n" "judge_model: gpt-4o-mini\n",
        encoding="utf-8",
    )
    # A baseline recorded at --runs 20, i.e. before the user lowered --runs to keep CI cheap.
    # Distinct outputs per run: the modal one becomes the reference, the other 19 are judged.
    save_baseline(
        {
            "one": [
                Trace(
                    scenario_id="one",
                    model_id="old-model",
                    run_idx=i,
                    final_output=f"baseline answer {i}",
                )
                for i in range(20)
            ]
        },
        "old-model",
        tmp_path / ".store",
    )

    adapter = CountingAdapter()
    judge = CountingJudge()
    monkeypatch.setattr("modelpin.cli._adapter", lambda provider, fixtures: adapter)
    monkeypatch.setattr("modelpin.cli._build_judge", lambda provider, cfg: judge)

    result = CliRunner().invoke(
        app,
        [
            "check",
            "--to",
            "new-model",
            "--from",
            "old-model",
            "--provider",
            "openai",  # the PAID branch: the only one that discloses a judge bill
            "--runs",
            "5",
            "--scenarios-dir",
            str(tmp_path / "scenarios"),
            "--config",
            str(tmp_path / "modelpin.yaml"),
            "--store-dir",
            str(tmp_path / ".store"),
        ],
    )
    assert result.exit_code == 0, result.output

    disclosed = _disclosed_judge_calls(result.output)
    assert len(judge.calls) <= disclosed, (
        f"`mp check` disclosed `up to {disclosed} judge calls` and then made "
        f"{len(judge.calls)}. The baseline on disk holds 20 runs, not --runs 5, and the "
        f"semantic judge scores every run on BOTH sides. `up to` is a promise about "
        f"someone's bill (ADR-0019); under-disclosing a paid axis is the same defect class "
        f"as claiming an exact count, pointed the other way."
    )
