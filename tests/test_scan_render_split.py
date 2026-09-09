"""`scan`'s headline count must mean models this repo CALLS.

MP-236. `[M] 2026-09-09` A first-run audit of the published 0.3.0 ran `modelpin init` beside a
one-file app and got six rows and "3 distinct model(s)" — of which half the rows and one of the
three models were Modelpin talking about itself, in comments inside the config it had just
written. Meanwhile a README saying "we use claude-3-5-sonnet as a fallback" produced nothing at
all. The count was therefore neither a safe ceiling nor a safe floor, on the first command a
stranger runs.

The detector half (MP-236) stopped reporting our own config commentary and taught the scanner
to read documentation, labelling every hit `code` or `comment`. This module guards the half
that faces the user: that the label is actually *rendered*, and that the headline count is
computed over `code` alone.

Keeping mentions visible is deliberate and equally load-bearing. `# TODO: evaluate gpt-5.5` is
a real intention about a real model, and the audit called it out as a true positive; deleting
it would be the blind half of the same defect. It is listed and counted separately so that
neither number is asked to mean both things.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()

APP_PY = """import openai

client = openai.OpenAI()


def ask(q):
    return client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": q}]
    )


# TODO: evaluate gpt-5.5
"""


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / "README.md").write_text("We use `claude-3-5-sonnet` as a fallback.\n", encoding="utf-8")
    return root


def _scan(root: Path) -> str:
    result = runner.invoke(app, ["scan", str(root)])
    assert result.exit_code == 0, result.output
    return result.output


def _unwrapped(text: str) -> str:
    """Collapse whitespace before matching prose.

    `rich` hard-wraps to the terminal width, and the width under `CliRunner` is not the width
    in a real terminal — so a sentence that renders on one line for a user arrives split here.
    Asserting on the wrapped form would make this guard fail for a reason that has nothing to
    do with what it is guarding, and the fix would be to shorten the message rather than to
    keep it true.
    """
    return " ".join(text.split())


def test_the_headline_counts_only_models_the_repo_calls(tmp_path: Path) -> None:
    """One wired-up model, so the headline says one — whatever else is mentioned."""
    out = _scan(_repo(tmp_path))
    assert "1 distinct model(s) called." in out, (
        "the headline count is not over called models alone. It is the first number a "
        f"stranger reads and it must mean models this repo CALLS.\n\n{out}"
    )


def test_a_model_named_only_in_a_comment_is_not_counted_as_a_dependency(
    tmp_path: Path,
) -> None:
    out = _scan(_repo(tmp_path))
    head, _, tail = out.partition("Also mentioned")
    assert "gpt-5.5" not in head, (
        "`# TODO: evaluate gpt-5.5` is being reported as a dependency. It is an intention, "
        f"not a call.\n\n{out}"
    )
    assert "gpt-5.5" in tail, (
        "the TODO model vanished entirely. Suppressing it is the blind half of the same "
        f"defect — the audit called it a true positive worth showing.\n\n{out}"
    )


def test_a_model_named_only_in_the_readme_is_shown_but_not_counted(tmp_path: Path) -> None:
    """The blind half: prose was invisible before MP-236, and must not now inflate the count."""
    out = _scan(_repo(tmp_path))
    head, _, tail = out.partition("Also mentioned")
    assert (
        "claude-3-5-sonnet" in tail
    ), f"a model named in README.md is still invisible to scan.\n\n{out}"
    assert (
        "claude-3-5-sonnet" not in head
    ), f"a README mention is being counted as a dependency.\n\n{out}"


def test_the_mentions_section_says_a_mention_is_not_a_dependency(tmp_path: Path) -> None:
    """The label carries the meaning; without it the second table reads like a second answer."""
    out = _unwrapped(_scan(_repo(tmp_path)))
    assert "not counted above" in out, "the mentions table no longer says it is not counted"
    assert "A mention is not a dependency" in out, (
        "the mentions section no longer tells the reader what it is. Two tables of model ids "
        f"with no stated difference is worse than one.\n\n{out}"
    )


def test_a_repo_with_no_mentions_prints_no_mentions_section(tmp_path: Path) -> None:
    """No empty second table: a section that is always there stops being read."""
    root = tmp_path / "clean"
    root.mkdir()
    (root / "app.py").write_text(
        'import openai\nopenai.OpenAI().responses.create(model="gpt-4o-mini")\n',
        encoding="utf-8",
    )
    out = _scan(root)
    assert "1 distinct model(s) called." in out, out
    assert (
        "Also mentioned" not in out
    ), f"an empty mentions table was printed for a repo that has none.\n\n{out}"
