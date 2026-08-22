"""FakeProvider must never invent a trace (MP-28).

The adapter used to return a placeholder Trace for any `(scenario_id, model_id)` it did
not hold. Because the SAME placeholder came back for both sides of a comparison, every
signal was structurally null and the run reported `unchanged` + exit 0 -- "safe to adopt"
from a run that measured nothing. That is a false negative, the mirror of the project's
north-star failure, so absence of coverage is now a hard error.

These tests are the adapter-level guard. `tests/test_cli_hardening.py` holds the
end-to-end versions, and `tests/test_demo.py` checks the shipped demo's own coverage.
"""

from __future__ import annotations

import pytest

from modelpin.models import Scenario, Trace
from modelpin.providers import FakeProvider, ProviderError, get_adapter
from modelpin.providers.fake import MissingCannedTrace

SCENARIO = Scenario(id="greeting", name="Greeting", input={"messages": []})


def _provider() -> FakeProvider:
    return FakeProvider(
        {
            ("greeting", "demo-model-v1"): Trace(
                scenario_id="greeting", model_id="demo-model-v1", final_output="hi"
            )
        }
    )


def test_a_covered_pair_still_replays_and_is_stamped_with_the_run_index():
    trace = _provider().run(SCENARIO, "demo-model-v1", run_idx=3)
    assert trace.final_output == "hi"
    assert trace.run_idx == 3


def test_an_unknown_model_raises_and_names_the_models_that_are_covered():
    """The mismatched-model-id case: the fix is unguessable unless the error shows it."""
    with pytest.raises(MissingCannedTrace) as exc:
        _provider().run(SCENARIO, "gpt-4o-mini")
    message = str(exc.value)
    assert "greeting" in message
    assert "gpt-4o-mini" in message
    assert "demo-model-v1" in message  # what the fixtures DO cover


def test_an_unknown_scenario_raises_and_names_the_scenarios_that_are_covered():
    unknown = Scenario(id="not_in_fixtures", name="X", input={"messages": []})
    with pytest.raises(MissingCannedTrace) as exc:
        _provider().run(unknown, "demo-model-v1")
    message = str(exc.value)
    assert "not_in_fixtures" in message
    assert "greeting" in message


def test_missing_canned_trace_is_a_provider_error():
    """Load-bearing: the CLI's existing handlers catch ProviderError, so this subclassing
    is what makes the fix need no new CLI plumbing."""
    assert issubclass(MissingCannedTrace, ProviderError)


def test_preflight_refuses_a_fake_run_that_has_no_canned_traces():
    """The widened surface: forgetting `--fixtures` entirely, which is likelier than
    mistyping a model id. Caught once, before any scenario runs."""
    with pytest.raises(MissingCannedTrace) as exc:
        FakeProvider().preflight()
    assert "--fixtures" in str(exc.value)


def test_preflight_does_not_tell_a_user_to_pass_a_flag_they_just_passed(tmp_path):
    """An empty fixtures FILE is still zero coverage, but the remedy is different --
    advising `--fixtures <file>` to someone who supplied one contradicts them."""
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(MissingCannedTrace) as exc:
        FakeProvider.from_fixtures(empty).preflight()
    assert "Pass `--fixtures <file>`" not in str(exc.value)
    assert "holds no traces" in str(exc.value)


def test_preflight_passes_once_there_is_coverage():
    assert _provider().preflight() is None


def test_get_adapter_fake_cannot_fabricate():
    """`get_adapter("fake")` builds a bare FakeProvider -- the other route to zero
    coverage, reachable from the maintainer scripts as well as the CLI."""
    adapter = get_adapter("fake")
    with pytest.raises(MissingCannedTrace):
        adapter.preflight()
    with pytest.raises(MissingCannedTrace):
        adapter.run(SCENARIO, "any-model")
