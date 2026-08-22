from modelpin.models import Scenario, Trace
from modelpin.providers import FakeProvider
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
