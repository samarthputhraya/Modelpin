"""`init` must not recommend a command that cannot run yet.

`[M] 2026-09-16`, first-run walk-through from a freshly built wheel in a clean venv:
``pip install modelpin`` (no ``[providers]`` extra) then ``modelpin init`` printed

    2. Credentials for google are already set in your environment.
    3. modelpin baseline            # record how your current model behaves

and step 3 died with ``error: The Google GenAI SDK is not installed`` and exit 4. The
information needed to warn was already in the process; `init` simply never looked.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.scaffold import sdk_installed

runner = CliRunner()


def _plain(text: str) -> str:
    """rich hard-wraps long tokens mid-word; compare on whitespace-stripped output."""
    return "".join(text.split())


# --- sdk_installed --------------------------------------------------------------------


def test_reports_true_for_a_provider_whose_sdk_is_importable() -> None:
    pytest.importorskip("openai")
    assert sdk_installed("openai") is True


def test_reports_false_when_the_module_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name, package=None: None if name == "google.genai" else object(),
    )
    assert sdk_installed("google") is False


def test_a_missing_parent_package_reads_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`find_spec("google.genai")` raises when `google` itself is gone, rather than returning
    None. The adapter's own import fails identically, so the answer must be the same."""

    def boom(name: str, package: object = None) -> object:
        raise ModuleNotFoundError("No module named 'google'")

    monkeypatch.setattr("importlib.util.find_spec", boom)
    assert sdk_installed("google") is False


def test_the_openai_compatible_hosts_all_ride_on_the_openai_sdk() -> None:
    """A user who installed only `openai` can run Groq, OpenRouter, Together and Cerebras;
    telling them to install something else would be wrong."""
    pytest.importorskip("openai")
    for provider in ("groq", "openrouter", "together", "cerebras"):
        assert sdk_installed(provider) is True


def test_an_unknown_provider_is_never_reported_as_missing() -> None:
    """A provider with no entry has no SDK requirement we know of. Guessing 'missing' would
    print an install line for a package that does not exist."""
    assert sdk_installed("some-future-host") is True


# --- what `init` prints ---------------------------------------------------------------


def _init_in(tmp_path, monkeypatch: pytest.MonkeyPatch, *, installed: bool) -> str:
    (tmp_path / "app.py").write_text(
        'client.chat.completions.create(model="gpt-4o-mini")\n', encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr("modelpin.cli.sdk_installed", lambda provider: installed)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return result.output


def test_init_says_the_sdk_is_missing_before_it_says_run_baseline(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _init_in(tmp_path, monkeypatch, installed=False)
    flat = _plain(out)
    assert "pipinstall'modelpin[providers]'" in flat
    # Order matters: the fix is worthless if the user reads `baseline` first.
    assert flat.index("pipinstall'modelpin[providers]'") < flat.index("modelpinbaseline")


def test_init_stays_quiet_about_the_sdk_when_it_is_installed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Almost every user installs the extra. Printing an install line at them every time
    would train them to skip the `Next:` list, which is where the real steps live."""
    out = _init_in(tmp_path, monkeypatch, installed=True)
    assert "providers]" not in _plain(out)


def test_the_credentials_line_survives_the_new_step(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _init_in(tmp_path, monkeypatch, installed=False)
    assert "Credentials for openai are already set" in " ".join(out.split())


def test_every_provider_init_can_choose_has_an_sdk_entry() -> None:
    """A provider added to the scaffold without an entry here silently loses the warning."""
    from modelpin.scaffold import PROVIDER_CREDENTIALS, _PROVIDER_SDK

    assert {name for name, _ in PROVIDER_CREDENTIALS} == set(_PROVIDER_SDK)
