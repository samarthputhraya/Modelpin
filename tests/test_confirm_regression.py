"""A regression must reproduce on fresh candidate runs before `mp check` fails a build.

The design and its safety argument live in `modelpin/diff/confirm.py`. These tests pin:

  * the policy itself (a non-regression replays nothing; a reproduced regression stands; one
    that does not reproduce is reported as `changed_minor`, never a red build);
  * that the fresh sample is scored ALONE -- pooling it with the first sample carries the
    first sample's selection bias into the confirmation;
  * the property that makes it safe: it can withhold a regression, never create one;
  * MP-220's real same-model false alarm, re-checked against the nine other same-model
    candidate samples in its own run of record, is withheld every time;
  * a consistent real change still fails the build, end to end through the CLI;
  * a judge or replay failure during the check costs one scenario, not the whole run.

Fully offline (ADR-0006).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from modelpin import cli
from modelpin.cli import app
from modelpin.diff import diff_scenario
from modelpin.diff.confirm import CONFIRMED_NOTE, UNCONFIRMED_NOTE, confirm_regression
from modelpin.models import DiffResult, DiffVerdict, Scenario, ToolCall, Trace
from modelpin.providers.base import ProviderAdapter, ProviderError
from modelpin.report.suite import scenario_fingerprint
from modelpin.scenarios import load_scenarios
from modelpin.storage import save_baseline

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _trace(sid: str, model: str, tools: list[str], i: int = 0, text: str = "done") -> Trace:
    return Trace(
        scenario_id=sid,
        model_id=model,
        run_idx=i,
        tool_calls=[ToolCall(name=t, arguments={}) for t in tools],
        final_output=text,
    )


def _side(sid: str, model: str, trajectories: list[list[str]]) -> list[Trace]:
    return [_trace(sid, model, tools, i) for i, tools in enumerate(trajectories)]


def _result(verdict: DiffVerdict, explanation: str = "x", confidence: float = 0.9) -> DiffResult:
    return DiffResult(
        scenario_id="s",
        from_model="a",
        to_model="b",
        verdict=verdict,
        confidence=confidence,
        explanation=explanation,
    )


# --- the policy --------------------------------------------------------------------------


def test_a_non_regression_replays_nothing():
    for verdict in (
        DiffVerdict.unchanged,
        DiffVerdict.changed_minor,
        DiffVerdict.insufficient_evidence,
    ):
        calls = []
        first = _result(verdict)
        final, traces = confirm_regression(
            first,
            [],
            replay_more=lambda: calls.append("replay") or [],
            rediff=lambda t: calls.append("diff") or first,
        )
        assert final is first and traces == [] and calls == [], verdict


def test_a_reproduced_regression_stands():
    first = _result(DiffVerdict.regression, "tool-call behavior changed: [a] -> []", 0.992)
    fresh = [_trace("s", "b", [])]
    final, traces = confirm_regression(
        first,
        [_trace("s", "b", [])],
        replay_more=lambda: fresh,
        rediff=lambda t: _result(DiffVerdict.regression, "tool-call behavior changed: x", 0.96),
    )
    assert final.verdict is DiffVerdict.regression
    assert final.confidence == 0.992
    assert "tool-call behavior changed" in final.explanation
    assert CONFIRMED_NOTE in final.explanation
    assert len(traces) == 2


def test_only_the_channel_that_reproduced_is_published():
    first = _result(
        DiffVerdict.regression, "tool-call behavior changed: [a] -> []; refusal rate 0% -> 100%"
    )
    final, _ = confirm_regression(
        first, [], lambda: [], lambda t: _result(DiffVerdict.regression, "refusal rate 0% -> 80%")
    )
    assert final.verdict is DiffVerdict.regression
    assert final.explanation.startswith("refusal rate 0% -> 100%")
    assert "tool-call" not in final.explanation, "the tool change did not reproduce"


def test_a_regression_on_a_different_channel_does_not_confirm():
    """FP review: a first-sample tool alarm must not 'reproduce' through a refusal alarm."""
    first = _result(DiffVerdict.regression, "tool-call behavior changed: [a, b] -> [a]")
    final, _ = confirm_regression(
        first, [], lambda: [], lambda t: _result(DiffVerdict.regression, "refusal rate 0% -> 100%")
    )
    assert final.verdict is DiffVerdict.changed_minor


def test_an_unmeasurable_second_sample_is_not_reported_as_did_not_reproduce():
    first = _result(DiffVerdict.regression, "tool-call behavior changed: [a] -> []")
    final, _ = confirm_regression(
        first, [], lambda: [], lambda t: _result(DiffVerdict.insufficient_evidence, "empty")
    )
    assert final.verdict is DiffVerdict.insufficient_evidence
    assert final.confidence == 0.0
    assert "could not confirm" in final.explanation


def test_an_unreproduced_regression_is_reported_but_does_not_fail_the_build():
    first = _result(DiffVerdict.regression, "tool-call behavior changed: [a] -> []")
    final, _ = confirm_regression(
        first,
        [],
        replay_more=lambda: [],
        rediff=lambda t: _result(DiffVerdict.unchanged),
    )
    assert final.verdict is DiffVerdict.changed_minor
    assert final.explanation.startswith(UNCONFIRMED_NOTE)
    assert "tool-call behavior changed" in final.explanation, "the first finding stays visible"


def test_the_fresh_sample_is_scored_alone_never_pooled():
    """Pooling carries the first sample's selection into the confirmation."""
    first_cand = [_trace("s", "b", [], i) for i in range(5)]
    fresh = [_trace("s", "b", ["x"], i) for i in range(5)]
    seen: list[list[Trace]] = []
    confirm_regression(
        _result(DiffVerdict.regression),
        first_cand,
        replay_more=lambda: fresh,
        rediff=lambda t: seen.append(t) or _result(DiffVerdict.unchanged),
    )
    assert seen == [fresh]


def test_confirmation_can_never_create_a_regression():
    """The monotonicity that makes this safe for the false-positive rate."""
    for verdict in DiffVerdict:
        if verdict is DiffVerdict.regression:
            continue
        final, _ = confirm_regression(
            _result(verdict),
            [],
            replay_more=lambda: [],
            rediff=lambda t: _result(DiffVerdict.regression, "refusal rate 0% -> 100%"),
        )
        assert final.verdict is verdict


# --- real data: MP-220's same-model false alarm ------------------------------------------

_MP220 = ROOT / "reports" / "channel-exposure" / "2026-09-07" / "v2a-fp-suite-v2-gpt-4o-mini.jsonl"
_MP220_SID = "optional_notify_after_status_update"


def _mp220_trials() -> dict[str, dict]:
    trials = {}
    for line in _MP220.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("kind") == "trial" and record["key"].startswith(f"fp:{_MP220_SID}#"):
                trials[record["key"]] = record
    return trials


def test_mp220s_false_alarm_is_withheld_against_every_other_same_model_sample():
    """`[M]` The one tool-channel false alarm of record, re-checked with each of the nine
    other same-model candidate samples from the same run as the confirmation sample. Every
    one is withheld. Under POOLING, two of the nine (1 of 5 notifying) would still have
    confirmed -- which is why the fresh sample is scored alone."""
    trials = _mp220_trials()
    assert len(trials) == 10, sorted(trials)
    flagged = trials.pop(f"fp:{_MP220_SID}#6")
    scenario = {s.id: s for s in load_scenarios(ROOT / "examples" / "fp-suite-v2")}[_MP220_SID]
    base = [Trace.model_validate(t) for t in flagged["base_traces"]]
    cand = [Trace.model_validate(t) for t in flagged["cand_traces"]]

    def rediff(candidate: list[Trace]) -> DiffResult:
        return diff_scenario(_MP220_SID, "gpt-4o-mini", "gpt-4o-mini", base, candidate, scenario)

    first = rediff(cand)
    assert first.verdict is DiffVerdict.regression, "the premise: the first sample false-alarms"

    pooled_would_confirm = 0
    for key, record in sorted(trials.items()):
        fresh = [Trace.model_validate(t) for t in record["cand_traces"]]
        final, _ = confirm_regression(first, cand, lambda fresh=fresh: fresh, rediff)
        assert final.verdict is DiffVerdict.changed_minor, (key, final.explanation)
        if rediff(cand + fresh).verdict is DiffVerdict.regression:
            pooled_would_confirm += 1
    assert pooled_would_confirm == 2


# --- end to end through the CLI ----------------------------------------------------------


class _ScriptedAdapter(ProviderAdapter):
    """Replays pre-scripted candidate samples in order, one list per `replay()` call."""

    name = "scripted"

    def __init__(self, samples: list[list[list[str]]]) -> None:
        self._samples = samples
        self.replays = 0

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        sample = self._samples[min(self.replays, len(self._samples) - 1)]
        trace = _trace(scenario.id, model_id, sample[run_idx], run_idx)
        if run_idx == len(sample) - 1:
            self.replays += 1
        return trace


_SCENARIO = {
    "id": "notify",
    "name": "update then maybe notify",
    "kind": "agent",
    "input": {
        "messages": [{"role": "user", "content": "Mark order 7 shipped."}],
        "tools": ["update_order_status", "notify_customer"],
    },
}
_BASE = [["update_order_status", "notify_customer"]] * 4 + [["update_order_status"]]
_DROPPED = [["update_order_status"]] * 5
_LIKE_BASE = [["update_order_status", "notify_customer"]] * 3 + [["update_order_status"]] * 2


def _project(root: Path) -> None:
    (root / "scenarios").mkdir()
    (root / "scenarios" / "notify.json").write_text(json.dumps(_SCENARIO), encoding="utf-8")
    (root / "modelpin.yaml").write_text(
        "models:\n  - base-model\nproviders:\n  - openai\nruns: 5\n", encoding="utf-8"
    )
    scenario = Scenario(**_SCENARIO)
    save_baseline(
        {"notify": _side("notify", "base-model", _BASE)},
        "base-model",
        root / ".modelpin",
        fingerprints={"notify": scenario_fingerprint(scenario)},
    )


def _check(root: Path, monkeypatch, adapter: ProviderAdapter, *extra: str):
    monkeypatch.setattr(cli, "_adapter", lambda provider, fixtures: adapter)
    return runner.invoke(
        app,
        [
            "check",
            "--to",
            "cand-model",
            "--config",
            str(root / "modelpin.yaml"),
            "--scenarios-dir",
            str(root / "scenarios"),
            "--store-dir",
            str(root / ".modelpin"),
            *extra,
        ],
    )


def test_a_fluke_does_not_fail_the_build(tmp_path, monkeypatch):
    _project(tmp_path)
    adapter = _ScriptedAdapter([_DROPPED, _LIKE_BASE])
    result = _check(tmp_path, monkeypatch, adapter)
    assert result.exit_code == 0, result.output
    assert adapter.replays == 2, "the candidate must have been replayed a second time"
    flat = " ".join(result.output.split())
    assert "not confirmed" in flat, flat
    report = (tmp_path / ".modelpin" / "last-report.md").read_text(encoding="utf-8")
    assert "not confirmed" in " ".join(report.split())


def test_the_same_fluke_fails_the_build_with_confirmation_off(tmp_path, monkeypatch):
    """The control: without it, the test above could pass because nothing fired at all."""
    _project(tmp_path)
    adapter = _ScriptedAdapter([_DROPPED, _LIKE_BASE])
    result = _check(tmp_path, monkeypatch, adapter, "--no-confirm")
    assert result.exit_code == 1, result.output
    assert adapter.replays == 1


def test_a_consistent_change_still_fails_the_build(tmp_path, monkeypatch):
    _project(tmp_path)
    adapter = _ScriptedAdapter([_DROPPED, _DROPPED])
    result = _check(tmp_path, monkeypatch, adapter)
    assert result.exit_code == 1, result.output
    assert adapter.replays == 2
    assert "reproduced" in " ".join(result.output.split())


def test_a_confirmation_replay_the_provider_rejects_costs_the_scenario_not_the_run(
    tmp_path, monkeypatch
):
    _project(tmp_path)

    class _FailsSecondTime(_ScriptedAdapter):
        def run(self, scenario, model_id, run_idx=0):
            if self.replays >= 1:
                raise ProviderError("Gemini call for model 'cand-model' failed: quota [429].")
            return super().run(scenario, model_id, run_idx)

    result = _check(tmp_path, monkeypatch, _FailsSecondTime([_DROPPED]))
    assert result.exit_code == cli.EXIT_UNMEASURED, result.output
    assert "could not be compared" in " ".join(result.output.split())


def test_a_judge_failure_costs_the_scenario_not_the_run(tmp_path, monkeypatch):
    _project(tmp_path)

    class _BrokenJudge:
        def preflight(self) -> None:
            return None

        def equivalent(self, reference, candidate, task=None):
            raise ProviderError("Gemini call for model 'judge' failed: quota [429].")

    monkeypatch.setattr(cli, "_build_judge", lambda *a, **k: _BrokenJudge())
    # Different text per side so the judge is actually consulted.
    adapter = _ScriptedAdapter([_LIKE_BASE])
    original_run = adapter.run

    def run(scenario, model_id, run_idx=0):
        return original_run(scenario, model_id, run_idx).model_copy(
            update={"final_output": f"reworded {run_idx}"}
        )

    adapter.run = run  # type: ignore[method-assign]
    result = _check(tmp_path, monkeypatch, adapter)
    assert result.exit_code == cli.EXIT_UNMEASURED, result.output
    assert "Traceback" not in result.output
    assert "could not be compared" in " ".join(result.output.split())


def test_the_offline_demo_still_fails_on_its_real_regressions(tmp_path, monkeypatch):
    """Fake-provider replays are deterministic, so a demo regression always reproduces."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "modelpin-demo")
    assert runner.invoke(app, ["baseline", "--fixtures", "traces.json"]).exit_code == 0
    result = runner.invoke(app, ["check", "--to", "demo-model-v2", "--fixtures", "traces.json"])
    assert result.exit_code == 1, result.output


def test_an_empty_second_sample_exits_3_through_the_cli(tmp_path, monkeypatch):
    _project(tmp_path)

    class _EmptySecond(_ScriptedAdapter):
        def run(self, scenario, model_id, run_idx=0):
            trace = super().run(scenario, model_id, run_idx)
            if self.replays >= 1 and not (self.replays == 1 and run_idx == 4):
                return trace.model_copy(update={"tool_calls": [], "final_output": ""})
            return trace

    result = _check(tmp_path, monkeypatch, _EmptySecond([_DROPPED, _DROPPED]))
    assert result.exit_code == cli.EXIT_UNMEASURED, result.output
    assert "could not confirm" in " ".join(result.output.split())


def test_the_confirmation_asks_the_judge_afresh(tmp_path, monkeypatch):
    """No cached judge answers: the fresh sample must not inherit the first sample's readings."""
    _project(tmp_path)
    calls: list[tuple[str, str]] = []

    class _CountingJudge:
        def preflight(self) -> None:
            return None

        def equivalent(self, reference, candidate, task=None):
            calls.append((reference, candidate))
            return True

    monkeypatch.setattr(cli, "_build_judge", lambda *a, **k: _CountingJudge())
    adapter = _ScriptedAdapter([_DROPPED, _DROPPED])
    base_run = adapter.run

    def run(scenario, model_id, run_idx=0):
        return base_run(scenario, model_id, run_idx).model_copy(
            update={"final_output": f"reworded {run_idx}"}
        )

    adapter.run = run  # type: ignore[method-assign]
    result = _check(tmp_path, monkeypatch, adapter)
    assert result.exit_code == 1, result.output
    first_sample = [c for c in calls[: len(calls) // 2]]
    assert len(calls) == 2 * len(first_sample), "the second diff re-asks every question"
