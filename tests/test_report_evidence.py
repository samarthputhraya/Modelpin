"""A flagged verdict shows one baseline run and one candidate run beside it.

Without this a reviewer read "semantic drift: candidate answers diverge in meaning" and had no
way to see what the candidate actually said: the candidate traces were not written anywhere.
The example is chosen after the verdict and cannot move one. Offline.
"""

from __future__ import annotations

from rich.console import Console

from modelpin.models import DiffResult, DiffVerdict, ToolCall, Trace
from modelpin.report import render_cli, render_pr_comment
from modelpin.report.evidence import EXAMPLE_CHARS, Example, pick_examples


def _t(text: str, tools: tuple[str, ...] = (), refused: bool = False, i: int = 0) -> Trace:
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=i,
        final_output=text,
        refused=refused,
        tool_calls=[ToolCall(name=n, arguments={}) for n in tools],
    )


def _result(verdict: DiffVerdict) -> DiffResult:
    return DiffResult(
        scenario_id="s", from_model="a", to_model="b", verdict=verdict, explanation="changed"
    )


def test_the_candidate_example_is_a_behavior_the_baseline_never_showed():
    base = [_t("bug"), _t("bug"), _t("bug"), _t("other")]
    cand = [_t("bug"), _t("other"), _t("feature"), _t("feature")]
    pair = pick_examples(base, cand)
    assert pair is not None
    assert pair[0].output == "bug", "the baseline's most common behavior"
    assert pair[1].output == "feature", "the candidate's most common NEW behavior"


def test_with_no_new_behavior_the_candidates_most_common_run_is_shown():
    pair = pick_examples([_t("a"), _t("b")], [_t("b"), _t("b"), _t("a")])
    assert pair is not None and pair[1].output == "b"


def test_no_runs_no_example():
    assert pick_examples([], [_t("x")]) is None
    assert pick_examples([_t("x")], []) is None


def test_tools_refusal_and_empty_output_are_described():
    assert Example.of(_t("", tools=("a", "b"))).describe() == "tools a -> b; (no text)"
    assert Example.of(_t("no", refused=True)).describe() == 'refused; "no"'


def test_long_output_is_shortened_visibly():
    example = Example.of(_t("x" * 1000))
    assert len(example.output) == EXAMPLE_CHARS and example.output.endswith("...")


def test_model_output_cannot_inject_markdown_into_the_pr_comment():
    """Model output is untrusted text in a comment our bot posts (see MP-239)."""
    hostile = _t("[Build passed](https://evil.example) <img src=x> `code`")
    pair = pick_examples([_t("fine")], [hostile])
    md = render_pr_comment(
        [_result(DiffVerdict.regression)], "a", "b", 5, "openai", examples={"s": pair}
    )
    line = next(ln for ln in md.splitlines() if ln.startswith("- candidate:"))
    body = line[len("- candidate: ") :]
    assert body.startswith("``") and body.endswith("``"), "the whole value is one code span"
    assert "<details><summary>example runs</summary>" in md


def test_model_output_cannot_break_the_console_markup():
    pair = pick_examples([_t("fine")], [_t("[/] [bold]not markup[/bold]")])
    out = render_cli([_result(DiffVerdict.changed_minor)], "a", "b", 5, examples={"s": pair})
    console = Console(record=True, width=200)
    console.print(out)
    assert "[/] [bold]not markup[/bold]" in console.export_text()


def test_unchanged_scenarios_and_runs_without_examples_render_as_before():
    results = [_result(DiffVerdict.regression)]
    assert render_pr_comment(results, "a", "b", 5, "openai") == render_pr_comment(
        results, "a", "b", 5, "openai", examples={}
    )
