"""`modelpin init` writes a config that runs on the first try.

The old scaffold was one fixed file: `gpt-4o-mini` on OpenAI, judged by `gpt-4o-mini`. A Gemini
or Claude user's first `modelpin baseline` then asked for an OPENAI_API_KEY they did not have,
and everyone's judge was the model it was judging. These tests pin the three choices `init`
now makes from what it can see: the model the code calls, its provider, and an independent judge.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from modelpin.cli import _SAMPLE_CONFIG, _SAMPLE_SCENARIO, app
from modelpin.config import load_config
from modelpin.judge import infer_judge_provider
from modelpin.models import Scenario
from modelpin.scaffold import JUDGE_MODEL, _same_model, infer_setup, render_config

runner = CliRunner()


def _hit(model: str, context: str = "code") -> dict:
    return {"model": model, "file": "app.py", "line": 1, "context": context}


@pytest.mark.parametrize(
    "model, provider",
    [
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("claude-sonnet-4-5", "anthropic"),
        ("gemini-2.5-flash", "google"),
    ],
)
def test_the_model_the_code_calls_decides_model_and_provider(model, provider):
    setup = infer_setup([_hit(model)], env={})
    assert (setup.model, setup.provider) == (model, provider)
    assert not _same_model(setup.judge_model, setup.model), "the judge must be independent"


def test_a_mention_in_a_comment_is_not_a_dependency():
    setup = infer_setup([_hit("claude-opus-4-1", context="comment")], env={})
    assert setup.provider == "openai" and "placeholder" in setup.model_source


def test_the_most_used_runnable_model_wins():
    hits = [_hit("gpt-4o"), _hit("gpt-4o"), _hit("gemini-2.5-flash")]
    assert infer_setup(hits, env={}).model == "gpt-4o"
    # With only a Google credential set, the model the user can actually run wins.
    assert infer_setup(hits, env={"GEMINI_API_KEY": "x"}).model == "gemini-2.5-flash"


@pytest.mark.parametrize(
    "env, provider",
    [
        ({"OPENAI_API_KEY": "x"}, "openai"),
        ({"ANTHROPIC_API_KEY": "x"}, "anthropic"),
        ({"GEMINI_API_KEY": "x"}, "google"),
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, "google"),
        ({"GROQ_API_KEY": "x"}, "groq"),
    ],
)
def test_with_no_model_in_the_code_the_credential_you_hold_decides(env, provider):
    setup = infer_setup([], env=env)
    assert setup.provider == provider
    assert not _same_model(setup.judge_model, setup.model)


def test_an_empty_credential_does_not_count():
    assert infer_setup([], env={"ANTHROPIC_API_KEY": "  "}).provider == "openai"


def test_claude_on_vertex_uses_the_dated_vertex_id():
    setup = infer_setup([], env={"ANTHROPIC_VERTEX_PROJECT_ID": "p"})
    assert setup.model == "claude-haiku-4-5@20251001"
    assert setup.judge_model == "claude-sonnet-4-5"


@pytest.mark.parametrize("provider", sorted(JUDGE_MODEL))
def test_every_scaffolded_config_loads_and_routes_its_judge(provider, tmp_path):
    setup = infer_setup([], env={})
    setup = type(setup)(
        model=JUDGE_MODEL[provider][0],
        provider=provider,
        judge_model=JUDGE_MODEL[provider][1],
        model_source="test",
        provider_source="test",
    )
    path = tmp_path / "modelpin.yaml"
    path.write_text(render_config(setup), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.models == [setup.model] and cfg.providers == [provider]
    # The judge's host must be resolvable without guessing, exactly as `check` resolves it.
    assert (cfg.judge_provider or infer_judge_provider(cfg.judge_model)) == provider


def test_the_fallback_scaffold_has_an_independent_judge():
    cfg = load_config_text(_SAMPLE_CONFIG)
    assert cfg.models and cfg.judge_model and not _same_model(cfg.models[0], cfg.judge_model)


def load_config_text(text: str):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "modelpin.yaml"
        p.write_text(text, encoding="utf-8")
        return load_config(p)


def test_the_starter_scenario_is_valid_and_its_assertion_is_attainable():
    scenario = Scenario(**json.loads(_SAMPLE_SCENARIO))
    assert scenario.assertions and scenario.assertions.must_contain == ["positive"]
    system = scenario.input["messages"][0]["content"]
    assert "lowercase" in system, "must_contain is case-sensitive; the prompt must pin the case"


def test_init_end_to_end_reads_the_repo(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        'client.messages.create(model="claude-sonnet-4-5", max_tokens=100, messages=m)\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    cfg = load_config(tmp_path / "modelpin.yaml")
    assert cfg.models == ["claude-sonnet-4-5"] and cfg.providers == ["anthropic"]
    assert cfg.judge_model == "claude-haiku-4-5"
    flat = " ".join(result.output.split())
    assert "ANTHROPIC_API_KEY" in flat and "modelpin baseline" in flat
    assert "MP-" not in (tmp_path / "modelpin.yaml").read_text(
        encoding="utf-8"
    ), "internal backlog ids must not leak into a user's config"


def test_init_numbers_its_next_steps_without_gaps(tmp_path, monkeypatch):
    """Re-running init with --agent-example skipped step 2 and printed 1, 3, 4."""
    import re

    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    second = runner.invoke(app, ["init", str(tmp_path), "--agent-example"])
    numbers = [int(n) for n in re.findall(r"^\s+(\d)\. ", second.output, re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), second.output


def test_a_malformed_fixtures_file_is_a_setup_error_not_a_traceback(tmp_path):
    fixtures = tmp_path / "traces.json"
    fixtures.write_text('["not a trace"]', encoding="utf-8")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.json").write_text(_SAMPLE_SCENARIO, encoding="utf-8")
    result = runner.invoke(
        app,
        ["baseline", "--provider", "fake", "--fixtures", str(fixtures), "--model", "m",
         "--scenarios-dir", str(tmp_path / "scenarios"), "--store-dir", str(tmp_path / "s")],
    )  # fmt: skip
    assert result.exit_code == 4, result.output
    assert "not a JSON array of trace objects" in " ".join(result.output.split())


def test_a_dead_regression_threshold_setting_is_called_out(tmp_path):
    cfg = tmp_path / "modelpin.yaml"
    cfg.write_text("models: [m]\nregression_threshold: 0.5\n", encoding="utf-8")
    result = runner.invoke(app, ["baseline", "--config", str(cfg), "--provider", "fake"])
    assert "regression_threshold" in " ".join(result.output.split()), result.output
