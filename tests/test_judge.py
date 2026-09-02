"""Unit tests for the semantic LLM-judge — fully mocked, no network, no key."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modelpin.judge import OpenAIJudge, _parse_equivalent, build_judge
from modelpin.providers import ProviderError


class FakeClient:
    def __init__(self, content):
        self._content = content
        self.last_kwargs: dict | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


# --- _parse_equivalent (FP-safe parsing) ------------------------------------------


def test_parse_plain_json_true():
    assert _parse_equivalent('{"equivalent": true, "reason": "same"}') is True


def test_parse_plain_json_false():
    assert _parse_equivalent('{"equivalent": false, "reason": "different total"}') is False


def test_parse_code_fenced_json():
    assert _parse_equivalent('```json\n{"equivalent": false}\n```') is False


def test_parse_unparseable_defaults_to_equivalent():
    # FP-safe: a garbage response must NOT manufacture a divergence flag.
    assert _parse_equivalent("the model rambled with no json") is True
    assert _parse_equivalent("") is True


# --- OpenAIJudge ------------------------------------------------------------------


def test_judge_returns_true_on_equivalent_verdict():
    judge = OpenAIJudge("gpt-4o-mini", client=FakeClient('{"equivalent": true}'))
    assert judge.equivalent("Total: $5", "The total is 5 dollars", task="Extract total") is True


def test_judge_returns_false_on_divergent_verdict():
    judge = OpenAIJudge("gpt-4o-mini", client=FakeClient('{"equivalent": false}'))
    assert judge.equivalent("Approved", "Denied") is False


def test_judge_sends_temperature_zero_for_normal_model():
    client = FakeClient('{"equivalent": true}')
    OpenAIJudge("gpt-4o-mini", client=client).equivalent("a", "b")
    assert client.last_kwargs["temperature"] == 0


def test_judge_omits_temperature_for_reasoning_model():
    client = FakeClient('{"equivalent": true}')
    OpenAIJudge("o3-mini", client=client).equivalent("a", "b")
    assert "temperature" not in client.last_kwargs


def test_judge_includes_task_and_both_answers_in_prompt():
    client = FakeClient('{"equivalent": true}')
    OpenAIJudge("gpt-4o-mini", client=client).equivalent("REF-ANSWER", "CAND-ANSWER", task="DO-X")
    user = client.last_kwargs["messages"][-1]["content"]
    assert "DO-X" in user and "REF-ANSWER" in user and "CAND-ANSWER" in user


def test_judge_api_error_becomes_provider_error():
    def _boom(**_):
        raise RuntimeError("network down")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_boom)))
    judge = OpenAIJudge("gpt-4o-mini", client=client)
    with pytest.raises(ProviderError, match="failed"):
        judge.equivalent("a", "b")


def test_judge_preflight_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set"):
        OpenAIJudge("gpt-4o-mini").preflight()


def test_build_judge_infers_openai_from_model():
    assert isinstance(build_judge("gpt-4o-mini", client=FakeClient("{}")), OpenAIJudge)


def test_build_judge_infers_google_from_model():
    """MP-143 replaced the old "no judge for gemini" case. A `gemini-*` id names its host
    beyond doubt, so it now builds -- the hole was that a Gemini-only user had NO channel
    that reads meaning, which for a suite with no `tools` means no CI-failing channel at all."""
    from modelpin.judge import GoogleJudge

    assert isinstance(build_judge("gemini-1.5-flash", client=object()), GoogleJudge)


def test_build_judge_refuses_to_GUESS_a_host():
    """The replacement guard, and the sharper one. `[M]` A Groq id
    (`llama-3.3-70b-versatile`) and an OpenRouter id (`openai/gpt-oss-120b`) are
    indistinguishable from the string alone, so routing by prefix would spend the wrong key
    against the wrong host and report it as a judge failure. Naming the key to set is the
    only honest answer."""
    with pytest.raises(ProviderError, match="judge_provider"):
        build_judge("llama-3.3-70b-versatile")
