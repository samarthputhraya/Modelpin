import threading
import time as _time

from modelpin.models import Scenario, Trace
from modelpin.models import Trace as _Trace
from modelpin.providers import FakeProvider
from modelpin.providers.base import ProviderAdapter as _Adapter
from modelpin.providers.base import ProviderError as _ProviderError
from modelpin.replay import replay


def test_replay_returns_n_traces():
    """N traces, correctly keyed.

    The key assertions are `scenario_id`/`model_id`: before MP-28 the fake provider
    fabricated a trace for any key, so this test passed even if `replay` had ignored
    `scenario` and `model_id` entirely. Now it only passes if replay forwards them.
    """
    s = Scenario(id="s1", name="demo", input={"messages": []})
    canned = {
        ("s1", "claude-opus-4-6"): Trace(
            scenario_id="s1", model_id="claude-opus-4-6", final_output="hi"
        )
    }
    traces = replay(s, "claude-opus-4-6", FakeProvider(canned), runs=4)
    assert len(traces) == 4
    assert [t.run_idx for t in traces] == [0, 1, 2, 3]
    assert {t.scenario_id for t in traces} == {"s1"}
    assert {t.model_id for t in traces} == {"claude-opus-4-6"}


# --- concurrent runs ---------------------------------------------------------------------


class _SlowAdapter(_Adapter):
    parallel_safe = True

    def __init__(self, fail_on: int | None = None) -> None:
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.fail_on = fail_on

    def run(self, scenario, model_id, run_idx=0):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        _time.sleep(0.05 * (5 - run_idx))  # later runs finish first
        with self.lock:
            self.active -= 1
        if run_idx == self.fail_on:
            raise _ProviderError("rejected")
        return _Trace(scenario_id=scenario.id, model_id=model_id, run_idx=run_idx)


def _scn():
    from modelpin.models import Scenario

    return Scenario(id="s", name="s", input={"messages": [{"role": "user", "content": "hi"}]})


def test_live_runs_are_sent_together_and_come_back_in_run_order():
    adapter = _SlowAdapter()
    traces = replay(_scn(), "m", adapter, runs=5)
    assert [t.run_idx for t in traces] == [0, 1, 2, 3, 4]
    assert adapter.peak > 1


def test_an_adapter_that_does_not_opt_in_stays_sequential():
    adapter = _SlowAdapter()
    adapter.parallel_safe = False
    replay(_scn(), "m", adapter, runs=5)
    assert adapter.peak == 1


def test_a_rejected_run_still_raises():
    import pytest

    with pytest.raises(_ProviderError):
        replay(_scn(), "m", _SlowAdapter(fail_on=2), runs=5)


def test_the_live_adapters_opt_in_and_the_fake_does_not():
    from modelpin.providers.anthropic import AnthropicAdapter
    from modelpin.providers.fake import FakeProvider
    from modelpin.providers.google import GoogleAdapter
    from modelpin.providers.openai import OpenAIAdapter

    assert OpenAIAdapter.parallel_safe and GoogleAdapter.parallel_safe
    assert AnthropicAdapter.parallel_safe and not FakeProvider.parallel_safe


def test_a_verdict_is_identical_whether_the_runs_were_sent_together_or_in_sequence():
    """Concurrency is execution strategy only: order is preserved, so every downstream
    statistic (modal trajectory, reference output, per-run flags) sees the same list."""
    from modelpin.diff import diff_scenario
    from modelpin.models import ToolCall

    class _ByIndex(_Adapter):
        parallel_safe = True

        def __init__(self, outputs, tools):
            self.outputs, self.tools = outputs, tools

        def run(self, scenario, model_id, run_idx=0):
            _time.sleep(0.02 * (5 - run_idx))  # later runs finish first when concurrent
            return _Trace(
                scenario_id=scenario.id,
                model_id=model_id,
                run_idx=run_idx,
                final_output=self.outputs[run_idx],
                tool_calls=[ToolCall(name=n, arguments={}) for n in self.tools[run_idx]],
            )

    base_adapter = _ByIndex(["a", "a", "b", "a", "c"], [["x"], ["x"], [], ["x"], ["x", "y"]])
    cand_adapter = _ByIndex(["b", "b", "b", "c", "b"], [[], [], [], ["y"], []])
    scenario = _scn()
    results = []
    for workers in (1, 5):
        base = replay(scenario, "base", base_adapter, runs=5, workers=workers)
        cand = replay(scenario, "cand", cand_adapter, runs=5, workers=workers)
        results.append(diff_scenario("s", "base", "cand", base, cand, scenario))
    assert results[0].model_dump() == results[1].model_dump()
