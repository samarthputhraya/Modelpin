"""No model-authored or scenario-authored string can inject a link, image or tag into a report.

MP-239. `[M] 2026-09-09` security review: the PR comment's escaper neutralised only BLOCK-level
Markdown — newlines, HTML-comment markers, pipes. Everything inline passed through unchanged:
`[text](url)`, `![img]()`, `<img>`, `<a>`, `<details>`, emphasis, backticks. The source is
untrusted by construction: tool names in ``explanation`` come from the MODEL, and
`cli.py` already records a live run in which the model hallucinated a tool name. Reproduced
end to end: a tool name of

    escalate[Build passed - view logs](https://evil.example/phish)

was written verbatim into `.modelpin/last-report.md` — the file `action.yml` posts as a PR
comment. That is a forged green CI banner, linking to an attacker's page, posted by OUR bot,
into a customer's pull request.

The existing guard (`tests/test_report.py::test_pr_comment_neutralizes_markdown_injection`)
asserted exactly the block-level properties and stopped there, which is why this survived: it
checked the SOURCE for forbidden lines rather than checking what a reader is SHOWN.

So this module asserts on RENDERED HTML, produced by a real CommonMark engine
(`markdown-it-py`, already installed as a dependency of `rich`) — the same shape of thing
GitHub turns the comment into. A string-level check can be satisfied by an escape that a
renderer undoes; a rendered-HTML check cannot.
"""

from __future__ import annotations

import re

import pytest
from markdown_it import MarkdownIt

from modelpin.models import DiffResult, DiffVerdict
from modelpin.report import ChannelCensus, ReportMeta, render_pr_comment, render_report_md

#: Every inline vector the review measured passing through, plus the ones an attacker would
#: try next against a naive fix: a pre-escaped link (defeats "escape `[` once"), a code-span
#: breakout (defeats "wrap it in backticks"), an autolink, and an entity.
HOSTILE = (
    "escalate[Build passed - view logs](https://evil.example/phish)"
    " ![x](https://evil.example/pixel.png)"
    " <img src=https://evil.example/i.png onerror=alert(1)>"
    ' <a href="https://evil.example/a">click</a>'
    " <details open><summary>All checks passed</summary></details>"
    " **CI PASSED**"
    " x` [breakout](https://evil.example/code) `y"
    " \\[pre-escaped\\](https://evil.example/pre)"
    " <https://evil.example/auto>"
    " &lt;script&gt;"
)

_MD = MarkdownIt("commonmark").enable("table")


def _rendered(md: str) -> str:
    return _MD.render(md)


def _attacker_attrs(html: str) -> list[str]:
    """Every href/src in the rendered page that points at the attacker's host."""
    return [v for v in re.findall(r'(?:href|src)="([^"]*)"', html) if "evil.example" in v]


#: The only host the reports legitimately link to. `[M] 2026-09-09` the published Report
#: renders four `<a>` tags of its own, all to `docs/fp-measurement.md` on this repository; a
#: guard that flagged every `<a>` would fail on correct output and teach its readers to
#: ignore it.
_OWN_HOST = "https://github.com/samarthputhraya/modelpin/"


def _attacker_tags(html: str) -> list[str]:
    """Raw tags an attacker smuggled in.

    Tags the reports never emit themselves are hostile wherever they appear. An `<a>` is
    hostile unless it points at this project's own repository -- the reports do link there.
    `<sub>` is the report's own and is allowed.
    """
    never_ours = re.findall(r"<(?:img|details|summary|script)\b", html)
    foreign_links = [
        h for h in re.findall(r'<a\s+href="([^"]*)"', html) if not h.startswith(_OWN_HOST)
    ]
    return never_ours + foreign_links


def _result(sid: str, explanation: str) -> DiffResult:
    return DiffResult(
        scenario_id=sid,
        from_model="a",
        to_model="b",
        verdict=DiffVerdict.regression,
        explanation=explanation,
        confidence=0.95,
    )


def _meta(**overrides) -> ReportMeta:
    base = dict(
        suite_id="suite",
        suite_version="1.0.0",
        suite_hash="sha256:0",
        suite_path="suite",
        candidate_model="b",
        reference_model="a",
        provider="fake",
        runs=5,
        judge_model="disabled",
        match_mode="strict",
        modelpin_version="0.0.0",
        diff_thresholds={
            "alpha": 0.05,
            "min_tool_tvd": 0.5,
            "min_refusal_delta": 0.3,
            "min_semantic_delta": 0.3,
        },
        date_iso="2026-09-09",
        reproduce_cmd="modelpin report",
    )
    base.update(overrides)
    return ReportMeta(**base)


# ------------------------------------------------------------------------------ the PR comment


@pytest.mark.parametrize("verdict", list(DiffVerdict))
def test_a_hostile_tool_name_cannot_render_a_link_image_or_tag_in_the_pr_comment(
    verdict: DiffVerdict,
) -> None:
    """The review's exact reproduction, across every verdict's rendering path."""
    r = DiffResult(
        scenario_id="scn",
        from_model="a",
        to_model="b",
        verdict=verdict,
        explanation=f"tool-call behavior changed: ['{HOSTILE}'] -> []",
        confidence=0.95,
    )
    html = _rendered(render_pr_comment([r], "a", "b", 5))
    assert not _attacker_attrs(html), (
        "a model-authored tool name produced a clickable/loadable attacker URL in the PR "
        f"comment Modelpin posts: {_attacker_attrs(html)}"
    )
    assert not _attacker_tags(
        html
    ), f"a model-authored tool name smuggled raw HTML into the PR comment: {_attacker_tags(html)}"


def test_a_hostile_scenario_id_cannot_inject_anything_either(tmp_path) -> None:
    """Scenario ids are author-controlled, and they are rendered bare in several places."""
    html = _rendered(render_pr_comment([_result(HOSTILE, "tool-call changed")], "a", "b", 5))
    assert not _attacker_attrs(html), _attacker_attrs(html)
    assert not _attacker_tags(html), _attacker_tags(html)


def test_hostile_values_in_every_side_channel_of_the_pr_comment_are_inert() -> None:
    """Skipped, rejected, overridden and census-named scenarios each have their own sentence.

    `[M]` Three of those sentences reached untrusted text without the escaper at all — a
    `_named_blind` call with the default `fmt=str`, a code span built from an unescaped id, and
    a raw `', '.join(skipped)` on the published Report. One missed site is the whole defect.
    """
    # `judge_off_reason` is deliberately NOT hostile here. It is set in exactly one place --
    # `cli.py`, to one of two literals Modelpin authors -- and it carries our own
    # `judge_model` code span, which escaping would turn into literal backticks. `[M]
    # 2026-09-09` escaping the whole coverage note to cover it did exactly that on every PR
    # comment. Untrusted ids are escaped where they enter the note instead.
    census = ChannelCensus(
        tools_exercised=True,
        assertions_declared=False,
        judge_enabled=False,
        blind_scenarios=(HOSTILE,),
        compared=1,
        declared_unused_tools=(HOSTILE,),
    )
    md = render_pr_comment(
        [_result("scn", "tool-call changed")],
        HOSTILE,
        HOSTILE,
        5,
        census=census,
        rejected=[(HOSTILE, HOSTILE)],
        skipped=[HOSTILE],
        match_overrides={HOSTILE: "subset"},
    )
    html = _rendered(md)
    assert not _attacker_attrs(html), _attacker_attrs(html)
    assert not _attacker_tags(html), _attacker_tags(html)


# ------------------------------------------------------------------------------ the Report


def test_the_published_report_is_inert_to_the_same_input() -> None:
    """`mp report` PUBLISHES. The same vectors must be dead there too, including skipped ids."""
    md = render_report_md(
        [_result(HOSTILE, f"tool-call behavior changed: ['{HOSTILE}'] -> []")],
        _meta(scenario_ids=[HOSTILE], skipped=[HOSTILE], match_overrides={HOSTILE: "subset"}),
    )
    html = _rendered(md)
    assert not _attacker_attrs(
        html
    ), f"the published Report renders an attacker URL: {_attacker_attrs(html)}"
    assert not _attacker_tags(
        html
    ), f"the published Report renders raw HTML: {_attacker_tags(html)}"


# ------------------------------------------------------------------------------ not over-fired


def test_the_hostile_text_is_still_readable_not_silently_dropped() -> None:
    """Neutralised, never deleted: a reviewer must be able to SEE that something odd happened.

    Dropping the string would hide the very evidence that a model emitted a strange tool name,
    which is the thing this report exists to surface.
    """
    html = _rendered(render_pr_comment([_result("scn", f"changed: ['{HOSTILE}']")], "a", "b", 5))
    text = re.sub(r"<[^>]+>", "", html)
    assert "Build passed - view logs" in text
    assert "evil.example/phish" in text, "the URL must remain visible as TEXT, just not as a link"


def test_ordinary_tool_names_render_exactly_as_before() -> None:
    """A fix that uglifies every benign report is a fix nobody keeps.

    Backslash escapes are invisible once rendered, so a normal tool name must come out as
    itself — underscores and all, and not italicised by them.
    """
    html = _rendered(
        render_pr_comment(
            [_result("support_order_status", "tool-call behavior changed: ['lookup_order'] -> []")],
            "gpt-4o-mini",
            "gpt-4.1-mini",
            5,
        )
    )
    text = re.sub(r"<[^>]+>", "", html)
    assert "lookup_order" in text
    assert "support_order_status" in text
    assert "gpt-4.1-mini" in text
    assert "<em>" not in html, "a benign underscore was rendered as emphasis"
    assert "\\" not in text, "a backslash escape leaked into the rendered text"


def test_our_own_code_spans_still_render_as_code() -> None:
    """Escaping untrusted text must never escape OUR markup.

    `[M] 2026-09-09` The first version of this fix wrapped the whole coverage note in the
    escaper. The note is Modelpin's own Markdown -- `` `tools` `` and `` `judge_model` `` are
    deliberate code spans -- so every PR comment began showing literal backtick characters
    instead. It was caught only because an unrelated census test happened to assert the raw
    string. This pins it where it belongs: in the rendered output a reader sees.
    """
    census = ChannelCensus(tools_exercised=False, assertions_declared=False, judge_enabled=False)
    html = _rendered(
        render_pr_comment([_result("scn", "tool-call changed")], "a", "b", 5, census=census)
    )
    assert "<code>tools</code>" in html, "our own `tools` code span no longer renders as code"
    assert "<code>judge_model</code>" in html, "our own `judge_model` span no longer renders"
    visible = re.sub(r"<[^>]+>", "", html)
    assert "`tools`" not in visible, "our code span leaked as literal backtick characters"
