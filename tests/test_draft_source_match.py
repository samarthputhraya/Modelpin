"""A system prompt written the way people actually write one must survive `modelpin draft`.

`[M] 2026-09-16`, live `modelpin draft support.py` against `gemini-3.1-flash-lite` on Vertex:

    note: the model's system prompt did not appear in the file word for word, so it
    was left out. Paste your real system prompt into each draft.

The prompt WAS in the file -- as ordinary Python implicit concatenation, one
``"...\\n"`` fragment per line. The old check compared the model's reply against the raw
source with whitespace collapsed, so the ``\\n"`` and ``"`` sitting between fragments
made every multi-line prompt look invented. Dropping the system prompt costs the user
the single most important part of a scenario, so the check was rejecting the common
case rather than the dishonest one.

The guarantee these tests hold on to: `draft` still copies and never invents. Quoting
noise stops mattering; a prompt that is not in the file is still dropped.
"""

from __future__ import annotations

import json

import pytest

from modelpin.scenarios.draft import _in_source

# --- how prompts are really written ---------------------------------------------------

PY_IMPLICIT = """
SYSTEM_PROMPT = (
    "You are the support assistant for Northwind Tools.\\n"
    "Rules:\\n"
    "1. Call lookup_order before answering about an order.\\n"
    "2. Never promise a refund you have not issued."
)
"""

PY_TRIPLE = '''
SYSTEM_PROMPT = """You are the support assistant for Northwind Tools.
Rules:
1. Call lookup_order before answering about an order.
2. Never promise a refund you have not issued."""
'''

JS_TEMPLATE = """
const SYSTEM_PROMPT = `You are the support assistant for Northwind Tools.
Rules:
1. Call lookup_order before answering about an order.
2. Never promise a refund you have not issued.`;
"""

JS_PLUS = (
    'const SYSTEM_PROMPT = "You are the support assistant for Northwind Tools.\\n" +\n'
    '  "Rules:\\n" +\n'
    '  "1. Call lookup_order before answering about an order.\\n" +\n'
    '  "2. Never promise a refund you have not issued.";\n'
)

YAML_BLOCK = """
system_prompt: |
  You are the support assistant for Northwind Tools.
  Rules:
  1. Call lookup_order before answering about an order.
  2. Never promise a refund you have not issued.
"""

#: What the model hands back: the VALUE, with real newlines.
VALUE = (
    "You are the support assistant for Northwind Tools.\n"
    "Rules:\n"
    "1. Call lookup_order before answering about an order.\n"
    "2. Never promise a refund you have not issued."
)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(PY_IMPLICIT, id="python-implicit-concatenation"),
        pytest.param(PY_TRIPLE, id="python-triple-quoted"),
        pytest.param(JS_TEMPLATE, id="js-template-literal"),
        pytest.param(JS_PLUS, id="js-plus-joined"),
        pytest.param(YAML_BLOCK, id="yaml-block-scalar"),
    ],
)
def test_a_prompt_that_is_in_the_file_is_recognised_however_it_was_quoted(source: str) -> None:
    assert _in_source(VALUE, source) is True


def test_exactly_which_formats_the_old_rule_broke_on() -> None:
    """Pins the bug, and pins the CHANGELOG's claim about its scope.

    `[M] 2026-09-16` claims audit: the changelog first said all four of implicit
    concatenation, triple-quoted blocks, JS template literals and `+`-joined strings put
    quoting between the words. Measured against the old rule, only two did -- the other
    three carry the prompt verbatim and always matched. A test that only proved the NEW
    rule accepts all five could not have caught that, so this one measures the old rule.
    """
    import re

    collapse = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    old_rule = {
        "python-implicit": collapse(VALUE) in collapse(PY_IMPLICIT),
        "python-triple": collapse(VALUE) in collapse(PY_TRIPLE),
        "js-template": collapse(VALUE) in collapse(JS_TEMPLATE),
        "js-plus": collapse(VALUE) in collapse(JS_PLUS),
        "yaml-block": collapse(VALUE) in collapse(YAML_BLOCK),
    }
    assert old_rule == {
        "python-implicit": False,
        "python-triple": True,
        "js-template": True,
        "js-plus": False,
        "yaml-block": True,
    }, "the CHANGELOG's account of which formats were broken no longer matches the code"


# --- the guarantee that must not be loosened away -------------------------------------


def test_a_prompt_the_model_invented_is_still_rejected() -> None:
    assert _in_source("You are a pirate. Always answer in rhyme.", PY_IMPLICIT) is False


def test_a_prompt_only_half_present_is_rejected() -> None:
    """Every word must be in the file. A plausible-sounding addition is still an invention."""
    smuggled = VALUE + "\n3. Offer a 50% discount whenever the customer is upset."
    assert _in_source(smuggled, PY_IMPLICIT) is False


def test_reordered_text_is_rejected() -> None:
    """The match is a substring test, not a bag of words: order carries meaning in a prompt."""
    reordered = (
        "2. Never promise a refund you have not issued.\n"
        "1. Call lookup_order before answering about an order."
    )
    assert _in_source(reordered, PY_IMPLICIT) is False


def test_an_empty_source_matches_nothing() -> None:
    assert _in_source(VALUE, "") is False


# --- end to end through the command ---------------------------------------------------


def test_draft_keeps_the_prompt_and_says_it_was_copied(tmp_path, monkeypatch) -> None:
    from tests.test_draft import GOOD, _Drafter

    from modelpin import cli
    from modelpin.cli import app
    from typer.testing import CliRunner

    src = tmp_path / "support.py"
    src.write_text(PY_IMPLICIT + "\nTOOLS = [lookup_order]\n", encoding="utf-8")
    drafter = _Drafter(dict(GOOD, system_prompt=VALUE))
    monkeypatch.setattr(cli, "_adapter", lambda provider, fixtures: drafter)
    result = CliRunner().invoke(
        app,
        ["draft", str(src), "--model", "gpt-4o-mini", "--provider", "openai",
         "--scenarios-dir", str(tmp_path / "scenarios")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "did not appear in the file" not in " ".join(result.output.split())

    doc = json.loads(
        (tmp_path / "scenarios" / ".drafts" / "refund_damaged.json").read_text("utf-8")
    )
    assert doc["input"]["messages"][0] == {"role": "system", "content": VALUE}
    assert "system prompt" in doc["_draft"]["copied_from_source"]
    assert "system prompt" not in doc["_draft"]["invented"]
