"""No user-facing text may advertise a provider that crashes (MP-128).

`[M] 2026-08-27` `action.yml:22` listed `anthropic` in the `provider` input description with
no marker, while `modelpin/providers/anthropic.py:17` is `raise NotImplementedError`. That was
the one place where following the documentation reaches a crash.

`[M] 2026-08-31`, measured while fixing it: the row understated the surface. The unmarked
advertisement was in **five** places, not one -- `action.yml`, the `--provider` help on
`baseline`, `check` and `report` (so `mp check --help` said it too), and the unknown-provider
`ValueError` in `providers/__init__.py`, which is what a user sees after a typo. The row's
claim that "the CLI itself is honest" was true of the RUNTIME path (`_preflight_or_fail`,
`_unimplemented_msg`) and false of the HELP text.

The durable fix is one source of truth (`provider_help()`), and these tests tie the text to
the CODE rather than to a reviewer noticing. They are also SYMMETRIC: the day the Anthropic
adapter lands, the stale caveat fails the build instead of quietly outliving the stub.

That day came: the adapter runs (API key or Vertex AI), `UNIMPLEMENTED_PROVIDERS` is empty, and
the tests below now hold the other half of the promise -- a working provider is advertised
WITHOUT a caveat, everywhere the list is shown. Each test stays written against the list rather
than against `anthropic`, so the next provider named before it runs is caught the same way.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from modelpin.providers import UNIMPLEMENTED_PROVIDERS, get_adapter, provider_help
from modelpin.providers.openai import OPENAI_COMPATIBLE_PROVIDERS

REPO = Path(__file__).resolve().parents[1]

#: Every provider `get_adapter` will construct, however it ends up behaving.
ADVERTISABLE = ("openai", "google", "anthropic", *OPENAI_COMPATIBLE_PROVIDERS)


def _is_stub(name: str) -> bool:
    """True when the adapter's `run` is a `NotImplementedError` placeholder.

    Read from the SOURCE rather than by calling `run`, which would need a live key for
    every implemented adapter and would turn this file into a network test. It ties the
    claim to the code that would have to change, which is the point.
    """
    src = inspect.getsource(type(get_adapter(name)).run)
    return "raise NotImplementedError" in src


@pytest.mark.parametrize("name", ADVERTISABLE)
def test_the_unimplemented_list_matches_the_actual_adapters(name):
    """The list is a CLAIM about the code, so it is checked against the code."""
    assert _is_stub(name) == (name in UNIMPLEMENTED_PROVIDERS), (
        f"{name!r} is {'a stub' if _is_stub(name) else 'implemented'} but "
        f"{'not ' if _is_stub(name) else ''}listed in UNIMPLEMENTED_PROVIDERS. "
        "Update the list in the same commit as the adapter."
    )


def test_the_help_string_marks_every_stub():
    text = provider_help()
    for name in UNIMPLEMENTED_PROVIDERS:
        assert name in text, f"{name} is not even mentioned; a typo would say 'unknown'"
    assert ("NOT yet implemented" in text) == bool(UNIMPLEMENTED_PROVIDERS), text


def test_the_help_string_does_not_mark_a_working_provider():
    """Symmetry. A caveat that outlives the defect is its own false claim."""
    text = provider_help()
    marker_tail = text.split("(", 1)[1] if "(" in text else ""
    working = [name for name in ADVERTISABLE if name not in UNIMPLEMENTED_PROVIDERS]
    for name in working:
        assert name in text, f"{name} works, but the help text does not offer it"
        assert name not in marker_tail, f"{name} works, but the help text disclaims it"


def test_anthropic_is_advertised_as_a_working_provider():
    """The adapter shipped. `[M] 2026-09-15` before this change `provider_help()` ended in
    `(anthropic: NOT yet implemented and will fail the run.)` -- true then, false now."""
    assert "anthropic" not in UNIMPLEMENTED_PROVIDERS
    assert not _is_stub("anthropic")
    assert "anthropic" in provider_help()
    assert "NOT yet implemented" not in provider_help()


def test_the_unknown_provider_error_uses_the_same_marked_list():
    """`[M]` The typo path built its own copy of the list, unmarked. It is the FIRST place a
    confused user reads a provider list, so it was the worst copy to leave stale."""
    with pytest.raises(ValueError) as exc:
        get_adapter("gpt5")
    message = str(exc.value)
    assert "anthropic" in message
    assert ("NOT yet implemented" in message) == bool(UNIMPLEMENTED_PROVIDERS), message
    # `[M]` first-run review, 2026-08-31: this read `... run.))`. `provider_help()` ends in
    # its own parenthetical, and the caller wrapped it in another. A message a confused user
    # is reading is the worst place for a stray bracket.
    assert message.count("(") == message.count(")"), message
    assert "))" not in message, message


def _action_provider_description() -> str:
    text = (REPO / "action.yml").read_text(encoding="utf-8")
    block = re.search(r"^  provider:\n(?:.*\n)*?    description: \"(.*)\"$", text, re.M)
    assert block, "action.yml no longer has a `provider` input with a description"
    return block.group(1)


def test_the_action_input_marks_every_stub():
    """`action.yml` is the surface a CI user configures from, and the only one they cannot
    correct by reading a `--help`. It carries the marker or names no stub at all."""
    description = _action_provider_description()
    for name in UNIMPLEMENTED_PROVIDERS:
        if name in description:
            assert re.search(r"NOT yet implemented", description), (
                f"action.yml advertises {name!r} with no marker; "
                "following the documentation would reach a crash"
            )


def test_the_action_input_offers_every_working_provider_without_a_caveat():
    """Symmetry for `action.yml`: the stale `anthropic ... NOT yet implemented` caveat is the
    one a CI user would copy from, so it must not outlive the stub there either."""
    description = _action_provider_description()
    for name in ADVERTISABLE:
        if name not in UNIMPLEMENTED_PROVIDERS:
            assert name in description, f"action.yml does not offer working provider {name!r}"
    if not UNIMPLEMENTED_PROVIDERS:
        assert "NOT yet implemented" not in description, description


def test_the_readme_provider_table_marks_every_stub():
    """The README's table is the public claim (ADR-0009). It already said `Stub` -- this
    keeps it that way, and makes it fail loudly if the adapter ships without the table
    being updated."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for name in UNIMPLEMENTED_PROVIDERS:
        row = [
            ln
            for ln in readme.splitlines()
            if ln.startswith("|") and name.lower() in ln.lower() and "|" in ln[1:]
        ]
        assert row, f"the README provider table no longer mentions {name}"
        assert any(
            "stub" in ln.lower() or "not yet implemented" in ln.lower() for ln in row
        ), f"the README lists {name} without saying it is a stub: {row}"
