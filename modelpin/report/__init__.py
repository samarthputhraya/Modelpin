"""Reporter — render DiffResults as a Markdown PR comment and a CLI summary.

Matches the target UX in spec section 7. Framing stays measurement/opinion
("we replayed your scenarios and observed…"), never a bare "model X is worse"
(legal/trust guardrail, spec section 9).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections.abc import Callable, Sequence
from typing import Any, Optional

from rich.markup import escape

from modelpin.config import DEFAULT_RUNS
from modelpin.models import DiffResult, DiffVerdict

#: The run count the persisted artifact tells a reader to re-run at. [M] MP-117: the CLI's
#: pre-spend warning said `Use --runs 5` while `last-report.md` -- the file `action.yml` posts,
#: and the only thing a PR reviewer sees -- said merely "re-run with more runs per side", with
#: no number; and the PARTIALLY-blind branch carried no remedy at all. Bound to the same
#: constant `cli.RECOMMENDED_RUNS` is bound to, and pinned equal to it by a test, so the two
#: surfaces cannot drift into advertising different numbers.
_RECOMMENDED_RUNS = DEFAULT_RUNS

# CLI uses ASCII tokens (+ rich color) so it never hits a UnicodeEncodeError on a
# legacy Windows console (cp1252). Emoji live only in the Markdown report below.
_CLI_MARK = {
    DiffVerdict.regression: "[red]REGRESSION[/]",
    DiffVerdict.changed_minor: "[yellow]MINOR[/]",
    DiffVerdict.unchanged: "[green]OK[/]",
    DiffVerdict.insufficient_evidence: "[yellow]NO EVIDENCE[/]",
}
_MD_MARK = {
    DiffVerdict.regression: "❌",
    DiffVerdict.changed_minor: "⚠️",
    DiffVerdict.unchanged: "✅",
    # Deliberately not the minor-change glyph: an abstention is not a small change.
    DiffVerdict.insufficient_evidence: "❔",
}


def _md_inline(text: Any) -> str:
    """Neutralize model-controlled text (scenario ids, tool names inside ``explanation``)
    for safe inline use in the Markdown PR comment posted to GitHub: collapse newlines so it
    can't break out of its line, drop HTML-comment markers (the sticky comment is found by
    one), and escape pipes. Defends the PR comment against Markdown injection via a crafted
    tool name in a model's response."""
    s = str(text).replace("\r", "").replace("\n", " ")
    s = s.replace("<!--", "<! --").replace("-->", "-- >")
    return s.replace("|", "\\|").strip()


def _bucket(results: list[DiffResult]) -> dict[DiffVerdict, list[DiffResult]]:
    """Group results by verdict, with a list for EVERY member of the enum.

    Built by iterating ``DiffVerdict`` rather than naming three verdicts, so a member added
    later cannot silently vanish from the report. It used to return a 3-tuple, and when a
    fourth verdict was spliced in it landed in none of them: no renderer raised, the PR
    comment still printed "No behavioral regressions found; looks safe to adopt", and the CLI
    printed only its header — the scenario disappeared. That failure mode is worse than the
    bug it was reporting on, which is why this is a dict comprehension over the enum and why
    the KeyError below is deliberate: fail loudly rather than vanish.
    """
    out: dict[DiffVerdict, list[DiffResult]] = {v: [] for v in DiffVerdict}
    for r in results:
        out[r.verdict].append(r)
    return out


def _provenance(provider: str | None) -> str:
    """How the traces were produced. The offline `fake` path spends no key, and this line
    is printed inside the report a first-run user reads under a "no API key" heading."""
    if provider == "fake":
        return "from canned offline traces (no API key)"
    return "using your API key"


#: MP-55 / ADR-0018's reasoning, one level up. An `unchanged` verdict from a run whose
#: permutation test could not have returned p <= ALPHA is not a measurement of sameness --
#: it is the absence of a measurement, wearing the word "unchanged". `[M]` The shipped demo
#: at `--runs 2` printed `OK 4 scenario(s) unchanged` and "looks safe to adopt", where the
#: SAME fixtures at `--runs 5` find two regressions and a minor.
#:
#: Deliberately NOT a verdict change and NOT an exit-code change: `MIN_RUNS` and the floors
#: are sensitivity surfaces (ADR-0016, ADR-0002) and moving them needs its own calibration.
#: This governs only what the tool CLAIMS.
# --------------------------------------------------------------------------------------
# MP-138 — channel availability, the disclosure one level ACROSS from the run-count one.
#
# `_resolve_runs` already refuses to let a run that is structurally incapable of reporting a
# regression pass as a clean bill, and its comment names the defect exactly: "the user is
# about to pay for a run that is structurally incapable of reporting a regression, and 'less
# power' does not say that." But it prices ONLY the permutation floor, i.e. RUN COUNT. A
# suite can clear that floor comfortably and still have most of its detectors switched off
# for reasons that have nothing to do with N.
#
# [M] 2026-08-30, ops/launch/dogfood-kavach.md: a 12-scenario suite at 5v5 (floor 0.003968,
# far below ALPHA, so `underpowered` was empty) rendered "looks safe to adopt" while the
# tool-trajectory and tool-argument channels were inert by construction (no scenario declared
# `tools`, so the model was never OFFERED one) and the semantic judge was off (no
# `judge_model`). The only hard channel left was refusal, which catches "the model started
# declining" and nothing else -- so every CONTENT change routed to advisory-only and could
# not fail CI. Replayed against deliberate garbage, that suite still exited 0.
#
# This is the same promise ADR-0022 makes about the project's own numbers -- "a rate quoted
# without its coverage number is not a result" -- applied to the number we hand the USER.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelCensus:
    """Which verdict-bearing channels could fire at all on this suite.

    Availability here is STRUCTURAL (did the suite/config give the channel anything to work
    with), never statistical -- run-count power is `underpowered`'s job and the two are
    deliberately separate. A channel counts as live if it *could* have fired, even when it
    observed nothing; a true negative is a measurement, an inert channel is not.
    """

    tools_exercised: bool
    assertions_declared: bool
    judge_enabled: bool
    #: WHY the judge is off, carried from the caller and never inferred here. The census
    #: cannot tell "no judge_model in the config" from "disabled because the provider is
    #: the offline fake", and printing the first when the second is true would put a false
    #: statement inside the disclosure that exists to be honest.
    judge_off_reason: str = "no `judge_model` configured"
    #: MP-141. The three booleans above are suite-wide ``any()``, but blindness is
    #: PER-SCENARIO: a tool call happens in one scenario's runs, not across a suite.
    #: MP-159 then made the read a TRACE read -- `tool_calls`, not `input.tools`. `[M] 2026-08-31`
    #: reproduced end to end -- two content-blind scenarios rendered "NOT cleared on
    #: content"; adding ONE unrelated third scenario that declares `tools` flipped the same
    #: two to a green "looks safe to adopt", because the then-`tools_declared` went True for
    #: suite. `[M] 2026-08-31` Scenarios DECLARING `tools`: `examples/suite` 3 of 8,
    #: `examples/report-suite` 3 of 14, and the `init --demo` suite a new user runs first
    #: 2 of 4. Since MP-159 those are LOWER BOUNDS on coverage, not the count: a declared
    #: tool the models never call is blind too, so the blind count is run-dependent and can
    #: only be read off a run. Only the demo is pinned exactly (its fixtures really do call
    #: their tools -- `tests/test_per_scenario_census.py` asserts it).
    #:
    #: These are the compared scenarios with NO live hard content channel of their OWN.
    #: Deliberately shaped like `underpowered` -- a list of ids, named in the output -- so
    #: the two disclosures read the same way and can be reasoned about together.
    blind_scenarios: tuple[str, ...] = ()
    #: How many scenarios were compared, so a partial disclosure can say "N of M". Zero
    #: means the caller supplied no per-scenario data and the suite-wide reading stands.
    compared: int = 0
    #: MP-159. Compared scenarios that DECLARE `tools` which no run on either side called.
    #: Read by the remedy wording alone, and that is the whole point: `[M] 2026-08-31` the
    #: disclosure used to answer this state with "add `tools`" -- advice the user has
    #: already followed, and following it is what turned "NOT cleared on content" into
    #: "looks safe to adopt" over a total content inversion.
    declared_unused_tools: tuple[str, ...] = ()

    @property
    def hard_content_channels(self) -> list[str]:
        """CI-FAILING channels that respond to a change in what the model SAYS or DOES.

        Refusal is excluded on purpose: it is always live (computed from the model's own
        text, needing no scenario declaration) but only fires when the candidate starts
        DECLINING. A model that answers confidently and answers wrong never touches it, so
        counting it here would restore exactly the false comfort this census exists to remove.
        """
        live = []
        if self.tools_exercised:
            live.append("tool trajectory")
        if self.judge_enabled:
            live.append("semantic judge")
        return live

    @property
    def inert(self) -> list[str]:
        """Channels that could not have fired, with the reason, for disclosure."""
        out = []
        if not self.tools_exercised:
            # MP-159. Two ways to be dead, and they take different remedies, so the
            # disclosure must not collapse them: nobody asked for the channel, or somebody
            # asked and no model ever called the tool.
            why = (
                f"{len(self.declared_unused_tools)} scenario(s) declare `tools` but no run "
                f"called one"
                if self.declared_unused_tools
                else "no scenario declares `tools`"
            )
            out.append(f"tool trajectory + arguments ({why})")
        if not self.judge_enabled:
            out.append(f"semantic judge ({self.judge_off_reason})")
        if not self.assertions_declared:
            out.append("text assertions (no scenario declares `assertions`)")
        return out


def _census_clearance(
    census: Optional[ChannelCensus],
    to_model: str,
    *,
    arrow: str = "→",
    findings: str = "above",
) -> str | None:
    """The line that must REPLACE an affirmative clearance when no hard content channel was
    live, or ``None`` when the census raises no objection.

    ``arrow`` exists because of the module invariant stated at the top of this file: the CLI
    surface must stay cp1252-encodable, and U+2192 is not. `[M] 2026-08-30` the first cut of
    MP-138 shipped the arrow into ``render_cli`` and crashed `modelpin check` with a
    ``UnicodeEncodeError`` on a default Windows console -- ONLY when ``hard_content_channels``
    was empty, i.e. only in the exact state the feature exists to serve, aborting before
    ``last-report.md`` was written and exiting 1 like a real regression. The Markdown path is
    written with ``encoding="utf-8"`` and keeps the arrow to match its sibling lines.

    ``findings`` exists for the same reason, one surface further: the CLI and the PR comment
    print this line BELOW the per-scenario findings, the published Report prints it directly
    under the headline and ABOVE the table. "read the advisory findings above" is simply
    false on the Report, and a false direction inside the honesty disclosure is the defect
    this function exists to remove.
    """
    if census is None:
        return None
    blind, total = census.blind_scenarios, census.compared
    # MP-141. A PARTIAL clearance is the case MP-138 could not express: some scenarios armed
    # a content channel and some did not, and the suite-wide `any()` let the armed ones speak
    # for the blind ones. Shaped exactly like `_underpowered_clearance`'s partial branch --
    # same "only partially cleared" verdict, same naming of the affected ids -- because it is
    # the same disclosure on the other axis, and a reader should not have to learn two.
    if blind and total and len(blind) < total:
        return (
            f"{arrow} No behavioral regressions found in the {total - len(blind)} scenario(s) "
            f"where a CI-failing channel could see a change in content; in {len(blind)} no "
            f"run called a tool, so none was live ({', '.join(blind)}) and `{to_model}` is "
            f"only partially cleared. A wrong-but-confident answer in those would have passed."
        )
    # Fully blind -- either every compared scenario is in `blind`, or the caller supplied no
    # per-scenario data at all and the suite-wide reading is all there is.
    if not blind and census.hard_content_channels:
        return None
    # MP-159. The remedy must never be the advice that BOUGHT the false clearance. When the
    # scenarios already declare `tools` and no run called one, "add `tools`" is a loop: the
    # user has done it, doing it again changes nothing about what the models did, and the
    # census used to go green on the declaration alone.
    # Two shapes were found in review and both are fixed by putting the claim BEFORE the
    # list. `[M]` first-run: at 4 ids the old parenthetical wrapped between the last id and
    # `: declared, never called`, so a skimming reader attributed the claim to one id -- the
    # MP-73 failure, in a brand-new string. `[M]` claims: these are SCENARIO ids, and
    # "the declared tools (fraud_check: ...)" invites the reader to grep their config for a
    # tool called `fraud_check`. `_named_blind` caps the list the way every sibling
    # disclosure caps its own.
    remedy = (
        f"set a `judge_model`, or write prompts that actually exercise the declared tools "
        f"(declared but never called in: {_named_blind(census.declared_unused_tools)}),"
        if census.declared_unused_tools
        else "add `tools`, a `judge_model`,"
    )
    return (
        f"{arrow} This run had NO CI-failing channel able to see a change in what the model says: "
        f"{'; '.join(census.inert)}. Only refusal could have failed the build, and it only "
        f"fires if the candidate starts declining. A wrong-but-confident answer would have "
        f"passed. `{to_model}` is NOT cleared on content -- {remedy} or "
        f"read the advisory findings {findings}."
    )


def _census_note(census: Optional[ChannelCensus]) -> str | None:
    """One-line coverage disclosure printed beside every verdict, clean or not."""
    if census is None:
        return None
    parts = []
    if census.inert:
        parts.append("inert this run -- " + "; ".join(census.inert))
    # MP-141. The suite-wide list above says a channel was armed SOMEWHERE. It does not say
    # where, and on every shipped suite the answer is "on a minority of scenarios". Without
    # this clause a reader of `examples/report-suite` sees no `tools` entry under `inert`
    # -- 3 of 14 DECLARE them, and since MP-159 only the ones that CALL one count -- and has
    # no way to learn the other 11 or more were content-blind.
    if census.blind_scenarios and census.compared:
        parts.append(
            f"{len(census.blind_scenarios)} of {census.compared} scenario(s) called no "
            f"tool, so no CI-failing channel could see a content change in them "
            f"({', '.join(census.blind_scenarios)})"
        )
    if not parts:
        return None
    return "coverage: " + "; ".join(parts)


#: The `fmt_drift` reason string, matched so the CLI can say when a finding it just
#: reported is one it is deliberately not failing on (ADR-0032). Kept beside the
#: renderer rather than imported from `diff/`, which is FROZEN under ADR-0030.
_ASSERTION_REASON = "violates the scenario's text assertions"


_UNDERPOWERED_NOTE = (
    "{n} of {total} scenario(s) were compared at a run count where no signal could reach "
    "statistical significance, so this run could not have reported a regression in them"
)


#: How many blind scenario ids to name inline before summarising the rest. `[M]`
#: A first-run review, on the first draft of MP-117: a 30-scenario suite rendered every id
#: into a single ~1,200-character comma-separated wall on BOTH surfaces. Naming them is the
#: point of MP-117 -- naming all of them at any suite size is not, and the full list is in
#: the sidecar JSON either way. Eight fits a terminal line and a PR comment line.
_MAX_NAMED_BLIND = 8


def _named_blind(ids: Sequence[str], fmt: Callable[[str], str] = str) -> str:
    """`a, b, c` -- truncated with `and N more` past ``_MAX_NAMED_BLIND``."""
    shown = [fmt(sid) for sid in ids[:_MAX_NAMED_BLIND]]
    rest = len(ids) - len(shown)
    return ", ".join(shown) + (f", and {rest} more" if rest > 0 else "")


#: MP-148. A scenario the provider REFUSED is a third, independent way a run can fail to
#: mean what its verdict says -- alongside too few runs (`underpowered`) and too few armed
#: channels (the census). It is not an `insufficient_evidence` verdict either: that scenario
#: at least replayed. This one produced no observation at all, so it appears in no bucket,
#: and silence would make it indistinguishable from a scenario that passed.
def _rejected_clearance(
    rejected: Sequence[tuple[str, str]], to_model: str, arrow: str = "→"
) -> str | None:
    if not rejected:
        return None
    named = _named_blind([sid for sid, _ in rejected], lambda sid: f"`{sid}`")
    return (
        f"{arrow} {len(rejected)} scenario(s) were never replayed - the provider rejected "
        f"them - so `{to_model}` is NOT fully cleared: {named}. A rejected scenario is not a "
        f"passing one; re-run, or fix what the provider named."
    )


def _skipped_clearance(
    skipped: Sequence[str],
    from_model: str,
    to_model: str,
    *,
    arrow: str = "→",
    fmt: Optional[Callable[[str], str]] = None,
) -> str | None:
    """MP-160's clearance. A scenario with no recorded baseline was never compared at all.

    A THIRD way a run can fail to mean what its verdict says, alongside too few runs
    (`underpowered`), too few armed channels (the census) and a provider refusal
    (`rejected`). `[M] 2026-08-31` it was disclosed nowhere: `skipped` reached one console
    note that runs AFTER `last-report.md` is written, so the artifact CI posts named the
    scenario ZERO times while the run exited 0.

    The remedy is deliberately NOT phrased as `rejected`'s -- "re-run" is free, recording a
    baseline is not. That asymmetry is also why this condition does not fail the build. The
    measurement, the options weighed and the revisit trigger are recorded once, in ADR-0033;
    do not restate them here, and do not flip the exit code without superseding it.

    ``fmt`` exists because the two surfaces escape differently: ``render_cli`` wraps the
    whole line in rich ``escape()`` at emission, so it must receive RAW ids, while the
    Markdown has no such wrapper and needs ``_md_inline``.
    """
    if not skipped:
        return None
    named = _named_blind(skipped, fmt or (lambda sid: f"`{_md_inline(sid)}`"))
    return (
        f"{arrow} {len(skipped)} scenario(s) had no recorded baseline for `{from_model}` and "
        f"were never compared, so `{to_model}` is NOT fully cleared: {named}. An unmeasured "
        f"scenario is not a passing one; record a baseline to include it."
    )


def _underpowered_clearance(
    underpowered: Sequence[str], total: int, to_model: str, *, arrow: str = "→"
) -> str | None:
    """The line that must REPLACE an affirmative clearance, or ``None`` if none is needed.

    ``arrow`` for the reason its sibling ``_census_clearance`` has one, and it became
    load-bearing the moment MP-141 wired this function into ``render_cli``: this string had
    only ever reached UTF-8 Markdown, so its literal U+2192 was safe by accident. Printed to
    a cp1252 console it is `[M] 2026-08-30`'s ``UnicodeEncodeError`` exactly -- the crash
    MP-138 shipped, arriving a second time through the one clearance that was never called
    from the terminal. The em dash below is fine: U+2014 IS in cp1252 (0x97); U+2192 is not.
    """
    if not underpowered:
        return None
    # The floor depends on BOTH sides (`diff/__init__.py`: a 20-run baseline checked at
    # `--runs 5` scores 20 reference runs, not 5), so a remedy that names only `check` can
    # leave a short BASELINE blind. Both commands are named for that reason.
    remedy = (
        f"re-run with `--runs {_RECOMMENDED_RUNS}` or more on both sides "
        f"(`modelpin baseline --runs {_RECOMMENDED_RUNS}`, then "
        f"`modelpin check --runs {_RECOMMENDED_RUNS}`)"
    )
    if len(underpowered) >= total:
        return (
            f"{arrow} This run could not have reported a regression at all: no signal could reach "
            f"statistical significance at this run count. `{to_model}` is NOT cleared — "
            f"{remedy}."
        )
    return (
        f"{arrow} No behavioral regressions found in the {total - len(underpowered)} scenario(s) "
        f"this run could measure; {len(underpowered)} could not have reported one at this "
        f"run count, so `{to_model}` is only partially cleared — for the scenario(s) named "
        f"above, {remedy}."
    )


def render_pr_comment(
    results: list[DiffResult],
    from_model: str,
    to_model: str,
    runs: int,
    provider: str | None = None,
    underpowered: Sequence[str] = (),
    census: Optional[ChannelCensus] = None,
    rejected: Sequence[tuple[str, str]] = (),
    skipped: Sequence[str] = (),
) -> str:
    """The Markdown PR comment (spec section 7). The header reflects the actual outcome —
    only a real regression leads with 🚨, so an all-unchanged result reads calm/green and
    doesn't contradict its own "safe to adopt" line."""
    _b = _bucket(results)
    regs = _b[DiffVerdict.regression]
    minors = _b[DiffVerdict.changed_minor]
    unchanged = _b[DiffVerdict.unchanged]
    unmeasured = _b[DiffVerdict.insufficient_evidence]
    if not results:
        # MP-160. Nothing was compared at all, so no bucket can speak for the run. Without
        # this branch the chain below falls through to `underpowered or rejected or skipped`
        # and publishes "partially measured" over a run that measured NOTHING -- a stronger
        # claim than the evidence supports, on the line `action.yml` posts as the top of the
        # PR comment.
        header = f"❔ **Modelpin: could not measure — `{from_model}` → `{to_model}`**"
    elif regs:
        header = f"\U0001f6a8 **Modelpin: behavioral regression — `{from_model}` → `{to_model}`**"
    elif unmeasured:
        # Above `minors`: "we could not measure" outranks "we measured a small change",
        # because the unmeasured scenarios might hold anything at all.
        header = f"❔ **Modelpin: could not measure — `{from_model}` → `{to_model}`**"
    elif minors:
        header = f"⚠️ **Modelpin: minor changes — `{from_model}` → `{to_model}`**"
    elif (underpowered and len(underpowered) >= len(results)) or (
        census is not None and not census.hard_content_channels
    ):
        # (`rejected` is handled in the `partially measured` branch below: a run that
        # measured SOMETHING and lost a scenario is partial, not blind.)
        # A green check over a run that could not have gone red is the worst header we ship.
        # MP-138 adds the second way to get there: every CI-failing channel that reads the
        # model's CONTENT was inert, so no answer -- however wrong -- could have gone red.
        # MP-116 fixed this exact contradiction for blind runs; shipping it again for inert
        # channels would be the same defect with a new cause.
        header = f"❔ **Modelpin: could not measure — `{from_model}` → `{to_model}`**"
    elif underpowered or rejected or skipped:
        # MP-160 joins `skipped` for the same reason MP-148 joined `rejected`: a scenario
        # that was never compared makes the suite partial, whatever the compared ones said.
        # MP-148 joins `rejected` to this branch: a suite one scenario short is exactly as
        # partial as a suite one scenario cannot measure, and a green tick over either is
        # the same false clearance.
        # [M] MP-116: this branch did not exist, so PARTIAL blindness fell through to the
        # green tick while the bucket label below it flagged those scenarios as unmeasurable
        # and the footer called the model "only partially cleared" -- the document
        # contradicting its own first line, which is the line `action.yml` posts as the top
        # of the PR comment.
        # Reproduced end to end over a heterogeneous baseline (2 of 4 scenarios at 2 recorded
        # runs, checked at 4): line 1 read "no behavioral change" and the run exited 0.
        header = f"❔ **Modelpin: partially measured — `{from_model}` → `{to_model}`**"
    else:
        header = f"✅ **Modelpin: no behavioral change — `{from_model}` → `{to_model}`**"
    # MP-160. Two independent ways to lose a scenario, so this is composed rather than a
    # single `if/else` on one suffix. `[M]` The reviewer cannot otherwise detect the
    # shrinkage: this line published `Replayed N scenario(s)` with no denominator, so a suite
    # that quietly went from 12 scenarios to 1 read exactly like a suite of 1.
    _gaps = []
    if rejected:
        _gaps.append(f"{len(rejected)} could not be replayed at all")
    if skipped:
        _gaps.append(f"{len(skipped)} scenario(s) had no baseline")
    lines = [
        header,
        f"Replayed {len(results)} scenario(s) ×{runs} runs {_provenance(provider)}"
        + ("; " + "; ".join(_gaps) + "." if _gaps else "."),
        "",
    ]
    if rejected:
        # Before every verdict bucket: what was NOT measured changes how the measured
        # numbers should be read, so a reviewer must meet it first.
        lines.append(f"**COULD NOT REPLAY ({len(rejected)})** - excluded from every number below")
        for sid, reason in rejected:
            lines.append(f"❗ `{_md_inline(sid)}` — {_md_inline(reason)}")
        lines.append("")
    if skipped:
        # MP-160, and this is the piece that carries the whole fix. It renders on EVERY run
        # shape -- red, minor, unmeasured, clean -- because it sits ABOVE the verdict
        # buckets. `[M]` The clearance line below cannot do this job: `render_report_md` and
        # this function both reach it only in the all-clean `else` branch, so a
        # clearance-shaped disclosure alone would be INERT on exactly the run a reviewer
        # most needs it -- a red verdict pronounced over a fraction of the suite.
        lines.append(f"**NO BASELINE ({len(skipped)})** - never compared, and in no number below")
        for sid in skipped:
            lines.append(
                f"❗ `{_md_inline(sid)}` — no recorded baseline for " f"`{_md_inline(from_model)}`"
            )
        lines.append("")
    if regs:
        lines.append(f"**REGRESSIONS ({len(regs)})**")
        for r in regs:
            lines.append(
                f"{_MD_MARK[r.verdict]} **{_md_inline(r.scenario_id)}** — {_md_inline(r.explanation)}"
            )
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;confidence {r.confidence:.2f}")
        lines.append("")
    if unmeasured:
        lines.append(f"**COULD NOT MEASURE ({len(unmeasured)})**")
        for r in unmeasured:
            lines.append(
                f"{_MD_MARK[r.verdict]} {_md_inline(r.scenario_id)} — {_md_inline(r.explanation)}"
            )
        lines.append("")
    if minors:
        lines.append(f"**MINOR CHANGES ({len(minors)})**")
        for r in minors:
            lines.append(
                f"{_MD_MARK[r.verdict]} {_md_inline(r.scenario_id)} — {_md_inline(r.explanation)}"
            )
        lines.append("")
    _blind_set = set(underpowered)
    blind_ids = [r.scenario_id for r in unchanged if r.scenario_id in _blind_set]
    if not results:
        # MP-160. The unchanged bucket was emitted with no guard, so the zero-comparison
        # artifact this row newly writes carried `**UNCHANGED (0)**` with a green tick
        # between the NO BASELINE section and the clearance -- a green marker over a run
        # that compared nothing. `render_cli` has always guarded the same bucket with
        # `if unchanged:`; the two surfaces simply disagreed until now.
        pass
    elif blind_ids:
        # A green tick over a scenario that could not have gone red contradicts the very
        # line below it. Name the blind ones inside the bucket, not only in the footer.
        # [M] MP-117: this printed a COUNT and nothing else, so a reviewer told "2 of 4
        # could not have reported a regression" had no way to learn WHICH two short of
        # reading the sidecar JSON -- and the ids appear in no other bucket, because by
        # construction every other bucket is empty whenever this path is live.
        lines.append(
            f"**UNCHANGED ({len(unchanged)})** — ❔ {len(blind_ids)} of these could not have "
            f"reported a regression at this run count: "
            + _named_blind(blind_ids, lambda sid: f"`{_md_inline(sid)}`")
        )
    elif census is not None and not census.hard_content_channels:
        lines.append(
            f"**UNCHANGED ({len(unchanged)})** ✔ — on the channels that were live; "
            "no CI-failing channel could see a content change"
        )
    else:
        lines.append(f"**UNCHANGED ({len(unchanged)})** ✅")
    lines.append("")
    if not results:
        # MP-160. Nothing was compared, so there is no bucket to speak from and no clearance
        # to give. `[M]` Without this the chain below fell through to its `else` and printed
        # "No behavioral regressions found; X looks safe to adopt." directly under a
        # "could not measure" header -- an affirmative clearance over a run that replayed
        # nothing, on a shape this row newly made reachable AND newly writes to disk.
        # It still has to say something: this artifact is posted to a PR on its own, and
        # silence under a "could not measure" header reads like an omission rather than a
        # finding.
        lines.append(
            f"→ Nothing was compared, so nothing is known about `{_md_inline(to_model)}`. "
            f"This is NOT a clean result and NOT a regression -- it is the absence of a "
            f"measurement. Record a baseline for `{_md_inline(from_model)}` and re-run."
        )
    elif regs or minors:
        lines.append(f"→ Pin to `{from_model}` until resolved, or review the full diff above.")
    elif unmeasured:
        # MP-49 was exactly this line rendering over a run that measured nothing. "Safe to
        # adopt" must be reachable ONLY when every scenario produced a real comparison.
        lines.append(
            f"→ {len(unmeasured)} scenario(s) could not be measured; `{to_model}` is NOT "
            "cleared. Re-run, or inspect the provider responses."
        )
    else:
        # MP-55, the same rule one level up: an `unchanged` verdict from a comparison whose
        # permutation test could not have returned p <= ALPHA is the ABSENCE of a
        # measurement, not a measurement of sameness. "Safe to adopt" must not render over it.
        # MP-138: a second way to be structurally unable to fail. `underpowered` prices RUN
        # COUNT; the census prices CHANNEL AVAILABILITY. Either alone makes an affirmative
        # clearance false, so both must be able to replace it.
        # They are INDEPENDENT diagnoses -- too few runs, and too few armed channels -- so
        # they compose. `[M]` An earlier cut short-circuited with `or` and printed only the
        # first, hiding half the reason a clearance was withheld.
        weak = [
            x
            for x in (
                _underpowered_clearance(underpowered, len(results), to_model),
                _census_clearance(census, to_model),
                _rejected_clearance(rejected, to_model),
                _skipped_clearance(skipped, from_model, to_model),
            )
            if x
        ]
        (
            lines.extend(weak)
            if weak
            else lines.append(
                f"→ No behavioral regressions found; `{to_model}` looks safe to adopt."
            )
        )
    # Coverage is disclosed on EVERY verdict, not only clean ones -- a red run whose
    # detectors were half off is just as misread as a green one (ADR-0022's rule, applied
    # to the number handed to the USER rather than to our own).
    note = _census_note(census)
    if note:
        lines.append("")
        lines.append(f"<sub>{_md_inline(note)}</sub>")
    return "\n".join(lines)


def render_cli(
    results: list[DiffResult],
    from_model: str,
    to_model: str,
    runs: int,
    underpowered: Sequence[str] = (),
    census: Optional[ChannelCensus] = None,
    rejected: Sequence[tuple[str, str]] = (),
    skipped: Sequence[str] = (),
) -> str:
    """The CLI summary — ASCII text + rich color markup (safe on any console)."""
    _b = _bucket(results)
    regs = _b[DiffVerdict.regression]
    minors = _b[DiffVerdict.changed_minor]
    unchanged = _b[DiffVerdict.unchanged]
    unmeasured = _b[DiffVerdict.insufficient_evidence]
    lines = [
        f"[bold]Modelpin[/]: {from_model} -> {to_model}  "
        f"[dim]({len(results)} scenario(s) x{runs} runs)[/]",
        "",
    ]
    if rejected:
        # MP-148. Named FIRST, and never with a verdict marker: these scenarios produced no
        # observation, so they belong to no bucket. Every id and message is `escape`d --
        # both are provider- or author-controlled text reaching a rich console.
        lines.append(
            f"[yellow]!![/] {len(rejected)} scenario(s) could not be replayed and are in "
            f"NO number below:"
        )
        for sid, reason in rejected:
            lines.append(f"   [yellow]-[/] [bold]{escape(sid)}[/]: [dim]{escape(reason)}[/]")
        lines.append("")
    if skipped:
        # MP-160. Same shape and same rule as the block above: named FIRST, never with a
        # verdict marker, and every id `escape`d -- a scenario id is author-controlled text
        # reaching a rich console. `[M] 2026-08-31` the one pre-existing `skipped` message
        # did NOT escape it, and `Scenario.id` has no pattern validator, so an id containing
        # `[/]` raised `MarkupError` and aborted the command AFTER the report had been
        # written -- CI publishing the artifact and then failing on a markup typo.
        lines.append(
            f"[yellow]!![/] {len(skipped)} scenario(s) had no baseline, were never compared, "
            f"and are in NO number below:"
        )
        for sid in skipped:
            lines.append(f"   [yellow]-[/] [bold]{escape(sid)}[/]")
        lines.append("")
    for r in regs + unmeasured + minors:
        # `escape` on the id as well as the explanation. `[M] 2026-09-01` first-run review
        # found this by execution: the id was raw while its own neighbour on the SAME line
        # was escaped, so a scenario id containing `[/]` raised `MarkupError` and killed
        # `check` AND `report` -- at exit code 1, the code this round just finished defining
        # as "a real regression". It crashes inside the `console.print(render_cli(...))` that
        # runs BEFORE the report is written, so no artifact survives either. Untouched since
        # the file's first commit; the FOURTH such site, found after two separate comments in
        # this codebase claimed the last one had been closed.
        lines.append(
            f"{_CLI_MARK[r.verdict]} [bold]{escape(r.scenario_id)}[/]: "
            f"{escape(r.explanation)} [dim](confidence {r.confidence:.2f})[/]"
        )
    if unchanged:
        # MP-138. Same rule as the Markdown bucket: no green marker over a bucket that could
        # not have gone red. `[M]` The first cut guarded only the Markdown side, so the CLI --
        # the surface a first-run user actually reads -- still printed a green OK above a line
        # saying the model was NOT cleared.
        # MP-141: a scenario is content-blind on its OWN declarations, not the suite's. The
        # suite-wide reading is kept as the fallback for a census carrying no per-scenario
        # data, so the marker never gets LESS honest than it was.
        if census is None:
            inert = False
        elif census.blind_scenarios:
            inert = any(r.scenario_id in set(census.blind_scenarios) for r in unchanged)
        else:
            inert = not census.hard_content_channels
        ok_mark = "[yellow]OK?[/]" if inert else "[green]OK[/]"
        blind = set(underpowered)
        n_blind = sum(1 for r in unchanged if r.scenario_id in blind)
        if n_blind:
            # Never "OK" over a scenario that could not have gone red. `unchanged` here is
            # the absence of a measurement, not a measurement of sameness.
            named = _named_blind(
                [escape(r.scenario_id) for r in unchanged if r.scenario_id in blind]
            )
            lines.append(
                f"[yellow]??[/] [dim]{n_blind} scenario(s) reported `unchanged` at a run "
                f"count that could not reach significance -- NOT a clean result: {named}[/]"
            )
            rest = len(unchanged) - n_blind
            if rest:
                lines.append(f"{ok_mark} [dim]{rest} scenario(s) unchanged[/]")
        else:
            lines.append(f"{ok_mark} [dim]{len(unchanged)} scenario(s) unchanged[/]")
    if regs or minors:
        lines.append("")
        lines.append(f"[yellow]-> Pin to[/] [bold]{from_model}[/] until resolved.")
        # ADR-0032, the interim it requires. `[M] 2026-08-29` the dogfood flagged 6 of 12
        # scenarios at confidence 1.00 -- all 6 confirmed TRUE positives by an independent
        # oracle -- printed "Pin to ... until resolved", and EXITED 0, because a violated
        # text assertion caps at `changed_minor`. Reporting a finding and then silently
        # declining to act on it is the same defect MP-138/MP-140/MP-141 removed elsewhere:
        # a result whose own limits are unstated. Renderer-only, so the ADR-0030 freeze does
        # not block it; the promotion ADR-0032 gates does touch `diff/` and is frozen.
        if not regs and any(_ASSERTION_REASON in r.explanation for r in minors):
            lines.append(
                "[yellow]note:[/] [dim]a scenario violated an assertion you wrote, and this "
                "run still exits 0. Text assertions are advisory today because the "
                "comparison is byte-exact, so a capitalisation change would fail your build "
                "-- see ADR-0032. Treat the finding above as real.[/]"
            )
    elif not unmeasured:
        # MP-138. Only reached on an all-clean run: say plainly when nothing could have
        # caught a wrong-but-confident answer, instead of letting silence read as a pass.
        # ASCII arrow: this string goes to the console, which may be cp1252.
        #
        # MP-141. `_underpowered_clearance` was never called from here at all -- `[M]` grep
        # found it only in `render_pr_comment` -- so a run blind for RUN-COUNT reasons was
        # handed the CHANNEL remedy ("add `tools`, a `judge_model`") on the terminal, which
        # fixes nothing. Both compose, for the same reason they compose in the Markdown: too
        # few runs and too few armed channels are independent diagnoses and a run can carry
        # both. The ASCII arrow is passed to BOTH, or the cp1252 crash MP-138 shipped comes
        # straight back through the sibling that was never wired up.
        weak = [
            x
            for x in (
                _underpowered_clearance(underpowered, len(results), to_model, arrow="->"),
                _census_clearance(census, to_model, arrow="->"),
                _rejected_clearance(rejected, to_model, arrow="->"),
                _skipped_clearance(
                    skipped, from_model, to_model, arrow="->", fmt=lambda sid: f"`{sid}`"
                ),
            )
            if x
        ]
        for line in weak:
            lines.append("")
            lines.append(f"[yellow]{escape(line)}[/]")
    note = _census_note(census)
    if note:
        lines.append("")
        lines.append(f"[dim]{escape(note)}[/]")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Public Modelpin Report (spec sections 4.6 / 7) — a reproducible, opinion-framed document.
#
# The renderer below is a PURE function over (results, meta): no clock, no filesystem, no
# network, no randomness. Every non-deterministic input (date, suite hash, version,
# thresholds) lives on ``ReportMeta`` and is injected by the CLI caller, so the document is
# golden-testable offline.
#
# Measurement/opinion framing (spec section 9): the static frame is opinion-framed by
# construction (a banned-words test guards the rendered prose). The only *dynamic* tokens
# in a report are the model ids, the scenario id, and the diff engine's templated
# ``explanation`` (itself built from the suite's own tool names) — all author/CI-controlled.
# ``tests/test_report_suite.py`` asserts the public suite carries no comparative-quality
# words, so those tokens cannot smuggle a "model X is worse" claim into a published report.
# --------------------------------------------------------------------------------------

#: Disclaimer printed in every report (spec section 9: "decision-support, verify
#: independently; no warranty").
_DISCLAIMER = "Decision-support only; verify independently. No warranty."


@dataclass(frozen=True)
class ReportMeta:
    """Everything a public report needs that is NOT derivable from the DiffResults.

    All non-deterministic inputs (date, suite hash, engine version, thresholds) are injected
    here by the CLI, keeping :func:`render_report_md` a pure, deterministic function.
    """

    suite_id: str
    suite_version: str
    suite_hash: str
    suite_path: str
    candidate_model: str
    reference_model: str
    provider: str
    runs: int
    judge_model: str  # the judge model id, or "disabled" when no judge ran
    match_mode: str
    modelpin_version: str
    diff_thresholds: dict[str, float]
    date_iso: str
    reproduce_cmd: str
    scenario_ids: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: MP-140. Coverage rides on the meta rather than on a new ``render_report_md``
    #: parameter for one reason: ``to_report_sidecar`` serialises ``asdict(meta)``, so a
    #: field here reaches the Markdown AND the JSON audit trail together and cannot be
    #: passed to one surface but forgotten on the other -- which is precisely how MP-138
    #: fixed ``check`` and left the published Report untouched.
    #:
    #: ``None`` means NO census was taken, which is not the same claim as "every channel
    #: was live" (``ChannelCensus(...)`` with an empty ``inert``). A Report rendered from a
    #: sidecar written before this field existed must keep saying nothing about coverage
    #: rather than assert full coverage it never measured.
    census: Optional[ChannelCensus] = None
    #: Scenario ids compared at a run count where NO signal could reach ALPHA (MP-55/MP-123).
    #: The run-count axis of the same disclosure; ``census`` prices channel availability.
    underpowered: list[str] = field(default_factory=list)


def _fmt(value: Optional[float], spec: str, *, none: str = "—") -> str:
    """Format a possibly-``None`` numeric signal; ``None`` renders as an em dash."""
    return none if value is None else format(value, spec)


def _cell(text: Any) -> str:
    """Escape a value so it is safe inside a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\r", "").replace("\n", " ").strip()


def _report_header(meta: ReportMeta, results: list[DiffResult]) -> list[str]:
    """Title + subtitle + outcome-driven TL;DR (✅/⚠️/🚨 by ACTUAL verdict, never alarmist)."""
    _b = _bucket(results)
    regs = _b[DiffVerdict.regression]
    minors = _b[DiffVerdict.changed_minor]
    unchanged = _b[DiffVerdict.unchanged]
    unmeasured = _b[DiffVerdict.insufficient_evidence]
    same_model = meta.reference_model == meta.candidate_model
    if same_model:
        title = f"# Modelpin Report — baseline characterization of `{meta.candidate_model}`"
        compare = f"`{meta.candidate_model}` against itself"
    else:
        title = f"# Modelpin Report — `{meta.candidate_model}` vs `{meta.reference_model}`"
        compare = f"`{meta.candidate_model}` against `{meta.reference_model}`"

    if regs:
        glyph, head = "🚨", "Behavioral regressions found."
    elif unmeasured:
        # Above `minors`, and above the clean headline especially: a published Report whose
        # banner reads "No behavioral change observed" over scenarios that produced no
        # comparison is the ADR-0009 surface MP-49 exposed. See ADR-0018.
        glyph, head = "❔", "Incomplete: some scenarios could not be measured."
    elif minors:
        glyph, head = "⚠️", "Minor behavioral changes observed."
    elif meta.underpowered and len(meta.underpowered) >= len(results):
        # MP-123. Every scenario was compared where no signal could reach ALPHA, so the
        # document is reporting the ABSENCE of a measurement in the words of a result.
        glyph, head = "❔", "Incomplete: this run could not have reported a change."
    elif meta.census is not None and not meta.census.hard_content_channels:
        # MP-140. The other way to be structurally unable to conclude, and the one the
        # dogfood hit: the run had the power but not the channels. `render_pr_comment`
        # has refused this header since MP-138; the published Report still shipped it.
        #
        # `[M]` a claims review 2026-08-31 rejected the first wording of this headline --
        # "no channel could observe a change in content". Reproduced on a one-scenario
        # suite declaring `must_contain`: the assertion channel WAS armed, DID fire, and
        # the table below the headline published `changed_minor -- output format drift:
        # violates the scenario's text assertions`. `hard_content_channels` means "could
        # have produced a REGRESSION", not "could have observed anything", because
        # `fmt_drift` caps at `changed_minor` (`diff/__init__.py:428-431`). The headline
        # now says the narrower thing, which is the thing that is true.
        glyph, head = "❔", "Incomplete: only a refusal would have registered as a regression."
    else:
        glyph, head = "✅", "No behavioral change observed."

    lines = [
        title,
        "> A behavioral measurement on the open Modelpin suite, under the settings below — "
        "not a model-quality ranking. We report behavior *change* relative to the reference, "
        "never an absolute verdict on a model.",
        "",
        f"{glyph} **{head}** On our open suite of {len(results)} scenario(s) "
        f"×{meta.runs} runs, comparing {compare} under the settings below, we observed "
        f"{len(unchanged)} unchanged, {len(minors)} minor change(s), and "
        f"{len(regs)} regression(s)"
        # The four buckets MUST sum to len(results): a published claim whose own counts do
        # not add up is the failure this verdict exists to prevent (ADR-0009, ADR-0018).
        + (f", and could not measure {len(unmeasured)}." if unmeasured else "."),
    ]
    # MP-140. The counts above stay true whatever the coverage was -- they are what the
    # engine observed. What must not stand unqualified is the READING of them as a clean
    # bill, so the qualification goes in the same paragraph as the headline, not in a
    # section a reader may never reach. Only on an otherwise-clean run: a Report that
    # already leads with a regression is not being misread as a clearance.
    #
    # Both clearances are appended, never short-circuited with `or` -- `[M]` MP-138's first
    # cut did exactly that on the PR surface and printed only the first, hiding half the
    # reason a clearance was withheld. Too few runs and too few armed channels are
    # INDEPENDENT diagnoses and a run can carry both.
    if not (regs or minors or unmeasured):
        weak = [
            x
            for x in (
                _underpowered_clearance(meta.underpowered, len(results), meta.candidate_model),
                # `findings="below"`: on this surface the per-scenario table is under the
                # headline, not over it.
                _census_clearance(meta.census, meta.candidate_model, findings="below"),
            )
            if x
        ]
        for line in weak:
            lines += ["", line]
    return lines


def _report_coverage(meta: ReportMeta, n_results: int) -> list[str]:
    """MP-140 — what could have produced a finding on this run, published beside the verdict.

    Two INDEPENDENT axes, and an affirmative result needs both: channel availability (did
    the suite and the config arm a detector at all) and run count (could any signal reach
    ALPHA). ``check`` has disclosed both since MP-138; this is the same disclosure on the
    artifact that goes to strangers, which is the surface ADR-0009 governs.

    It applies ADR-0022's principle — a rate quoted without its coverage number is not a
    result — to the number handed to the USER rather than to our own published rate. It is
    NOT what ADR-0022 mandates, and the distinction matters: that ADR chose "ask the engine"
    precisely to avoid a second, subtly different notion of coverage drifting from the
    engine's. This census IS a second notion (structural declaration, not what fired), so it
    inherits ADR-0022's safety property, restated for this surface:

        **No channel the engine actually flagged may be described here as unable to fire.**

    `[M]` a claims review 2026-08-31 caught two violations of exactly that in the first cut,
    both reproduced as published documents that contradicted their own tables:

    - A refusal regression rendered under "**Live:** none. No CI-failing channel … could see
      a change in what the model says", six lines above ``| greeting | ❌ regression | …
      refusal rate 0% -> 100% |``. Refusal is a hard, CI-failing channel
      (``diff/__init__.py:393-398``) and is deliberately absent from
      ``hard_content_channels`` — which is right for deciding a CLEARANCE, and wrong as a
      description of what produced a FINDING.
    - A declared, armed, firing ``must_contain`` assertion appearing in NEITHER list, under
      the same "**Live:** none", above ``| formatted | ⚠️ changed_minor | … violates the
      scenario's text assertions |``. ``hard_content_channels`` excludes assertions because
      ``fmt_drift`` caps at ``changed_minor`` (``diff/__init__.py:428-431``), so they are
      advisory-live, which is a third state the two-list rendering could not express.

    Hence three states, not two, and refusal named in the live list rather than in a footnote
    that reads as an exclusion. The lists are still rendered from the census's OWN ``inert``
    and ``hard_content_channels``, never re-derived, so the terminal, the PR comment and the
    published Report cannot drift into three descriptions of one run.

    Absent entirely when no census was taken — an empty section would read as "we checked and
    everything was live".
    """
    census = meta.census
    if census is None and not meta.underpowered:
        return []
    fully_blind = bool(meta.underpowered) and len(meta.underpowered) >= n_results
    lines = ["## Coverage", ""]
    if census is not None:
        lines += [
            "Which channels could have produced a finding on this run. A channel listed as "
            "inert could not have fired however the models behaved, so an ABSENCE of "
            "findings is an absence only on the channels listed as live. The lists below "
            "are suite-wide — a channel appears as live if any scenario armed it — and the "
            "per-scenario count that follows them is what says how far that reaches.",
            "",
        ]
        if fully_blind:
            # Say it before the lists, not after: a reader who takes "live" at face value
            # here would be reading a list of channels that could not have concluded.
            lines += [
                "**At this run count nothing below could have fired regardless of what the "
                "lists say — see the run-count line.**",
                "",
            ]
        # Refusal leads the build-failing list because it is always computed, and its
        # caveat travels WITH it: it fires only when the candidate starts declining, so an
        # answer that is confident and wrong never touches it. That caveat is why the
        # census excludes it from `hard_content_channels` when deciding a clearance; it is
        # not a reason to omit it from a description of what could produce a finding.
        failing = ["refusal (always computed; fires only if the candidate starts declining)"]
        failing += census.hard_content_channels
        lines.append(f"- **Live, and able to report a regression:** {'; '.join(failing)}.")
        advisory = []
        if census.assertions_declared:
            advisory.append("text assertions")
        if census.tools_exercised:
            advisory.append("tool-call arguments")
        if advisory:
            lines.append(
                f"- **Live, advisory only** (can raise a scenario to *minor*, never to a "
                f"regression): {'; '.join(advisory)}."
            )
        if census.inert:
            lines.append(f"- **Inert** (could not have fired): {'; '.join(census.inert)}.")
        # A published safety net a reader may lean on deserves its own limits stated. `[M]`
        # Refusal is detected from a fixed list of first-person English decline phrases.
        lines.append(
            "- Refusal is detected from a fixed list of first-person English decline phrases "
            "(`modelpin/providers/_common.py`), so a decline phrased otherwise is missed."
        )
        # MP-141. The single most misreadable thing about the lists above is that "live"
        # means "somewhere". `[M]` On the published suite at most 3 of 14 scenarios can arm
        # the trajectory channel (that many declare `tools`; since MP-159 only the ones that
        # actually CALL one count), so a reader who took `tool trajectory` at face value
        # would credit content coverage to 11 or more scenarios that had none. This line is
        # the one that makes the lists safe to read.
        if census.blind_scenarios and census.compared:
            lines.append(
                f"- **Per scenario:** {len(census.blind_scenarios)} of {census.compared} "
                f"scenario(s) called no tool, so with the judge off no CI-failing channel "
                f"could see a change in what they say — "
                f"{_named_blind(census.blind_scenarios)}."
            )
    if meta.underpowered:
        lines.append(
            f"- **Run count:** {len(meta.underpowered)} of {n_results} scenario(s) were "
            f"compared at a run count where no signal could reach "
            f"p ≤ {meta.diff_thresholds['alpha']}, so no change could have been reported "
            f"in them at any effect size."
        )
    return lines


def _report_settings(meta: ReportMeta, n_scenarios: int) -> list[str]:
    """The reproducibility block — a keyed table a reader/provider can re-run from."""
    t = meta.diff_thresholds
    # `.get`, not `[...]`: a Report rendered from a sidecar written before the argument signal
    # existed has four keys, and re-rendering it must not raise. A missing floor is omitted,
    # never defaulted -- a fabricated threshold in the reproducibility block is worse than an
    # absent one.
    arg_floor = t.get("min_tool_arg_tvd")
    thresholds = (
        f"α={t['alpha']}, tool-TVD≥{t['min_tool_tvd']}, "
        + (f"arg-TVD≥{arg_floor} (advisory), " if arg_floor is not None else "")
        + f"refusal Δ≥{t['min_refusal_delta']}, semantic Δ≥{t['min_semantic_delta']}"
    )
    return [
        "## Settings (reproducibility)",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Suite | `{meta.suite_id}` v{meta.suite_version} (`{meta.suite_hash}`) |",
        f"| Scenarios | {n_scenarios} |",
        f"| Candidate model | `{meta.candidate_model}` |",
        f"| Reference model | `{meta.reference_model}` |",
        f"| Provider | `{meta.provider}` |",
        f"| Runs per scenario | {meta.runs} |",
        f"| Tool-call match mode | `{meta.match_mode}` |",
        f"| Semantic judge | `{meta.judge_model}` |",
        f"| Decision thresholds | {thresholds} |",
        f"| Engine version | modelpin {meta.modelpin_version} |",
        f"| Generated | {meta.date_iso} |",
    ]


def _report_methodology(meta: ReportMeta) -> list[str]:
    return [
        "## Methodology",
        "",
        f"Each scenario is replayed {meta.runs} times on **both** models using the caller's "
        "own API key. A verdict comes from the *distribution* of runs, not a single sample: "
        f"a two-sample permutation test (p ≤ {meta.diff_thresholds['alpha']}) gated by a "
        "minimum effect size. We compare five behavioral signals — tool-call trajectory match "
        f"({meta.match_mode}), tool-call ARGUMENT match, refusal-rate change, output-format / "
        "assertion drift, and (when a judge runs) calibrated LLM-as-judge semantic "
        "equivalence. The argument signal is **advisory**: its effect-size floor is not yet "
        "calibrated on a labelled set, so it can raise a scenario to *minor* but never to a "
        "build-failing *regression* — see [the method](https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md). The north-star is a low "
        "false-positive rate: a flagged regression should be a real, repeated change, not model "
        "nondeterminism. Full method: [`docs/fp-measurement.md`](https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md).",
    ]


def _report_table(results: list[DiffResult]) -> list[str]:
    """One row per scenario, sorted regression → minor → unchanged for scannability."""
    _b = _bucket(results)
    regs = _b[DiffVerdict.regression]
    minors = _b[DiffVerdict.changed_minor]
    unchanged = _b[DiffVerdict.unchanged]
    unmeasured = _b[DiffVerdict.insufficient_evidence]
    lines = [
        "## Per-scenario results",
        "",
        "| Scenario | Verdict | Tool match | Arg match | Refusal Δ | Semantic | "
        "Latency Δ (ms) | Token Δ | Confidence | What we observed |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in regs + unmeasured + minors + unchanged:
        s = r.signals
        semantic = "—" if s.semantic_score is None else format(s.semantic_score, ".0%")
        lines.append(
            f"| {_cell(r.scenario_id)} | {_MD_MARK[r.verdict]} {r.verdict.value} "
            f"| {_fmt(s.tool_call_match, '.2f')} | {_fmt(s.tool_arg_match, '.2f')} "
            f"| {_fmt(s.refusal_delta, '+.2f')} "
            f"| {semantic} | {_fmt(s.latency_delta_ms, '+.0f')} "
            f"| {_fmt(s.token_delta, '+d')} | {format(r.confidence, '.2f')} "
            f"| {_cell(r.explanation)} |"
        )
    flagged = regs + minors
    summary = (
        f"**Summary:** {len(regs)} regression(s), {len(minors)} minor, "
        f"{len(unchanged)} unchanged"
        + (f", {len(unmeasured)} unmeasurable" if unmeasured else "")
        + f" across {len(results)} scenario(s)."
    )
    if flagged:
        mean_conf = sum(r.confidence for r in flagged) / len(flagged)
        summary += f" Mean confidence on flagged scenarios: {mean_conf:.2f}."
    lines += ["", summary]
    return lines


def render_report_md(results: list[DiffResult], meta: ReportMeta) -> str:
    """Render the public Modelpin Report as a Markdown document (pure function).

    Framing is locked to measurement/opinion (spec section 9): every claim is phrased as
    "we observed …", the header glyph reflects the ACTUAL outcome (calm when nothing
    regressed), the limitations + disclaimer always ship, and any errored/skipped scenarios
    are disclosed so an omission is never read as "unchanged".
    """
    n_scenarios = len(meta.scenario_ids) if meta.scenario_ids else len(results)
    sections: list[str] = []
    sections += _report_header(meta, results)
    # Above Settings on purpose: what could NOT be measured qualifies the headline, and a
    # reader who stops after the first screen must still have it.
    coverage = _report_coverage(meta, len(results))
    if coverage:
        sections += ["", *coverage]
    sections += ["", *_report_settings(meta, n_scenarios)]
    sections += ["", *_report_methodology(meta)]
    sections += ["", *_report_table(results)]

    if meta.skipped:
        sections += [
            "",
            "## Skipped scenarios",
            "",
            "These scenario(s) errored during replay and are excluded from the counts above "
            '(disclosed so an omission is never read as "unchanged"): '
            f"{', '.join(meta.skipped)}.",
        ]

    sections += [
        "",
        "## Limitations & framing",
        "",
        "This is a measurement on a fixed, open suite under the exact settings above — not a "
        "claim about which model to choose for your app. A *regression* here means the "
        "candidate's behavior diverged from the reference on this suite; for some apps that "
        "divergence may be neutral or even desirable. The suite is small and the semantic "
        "judge is calibrated on a modest, partly-synthetic set with a single-vendor judge "
        "(see [the known limitations](https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md)). Models are "
        "non-deterministic, so exact numbers vary run to run; the distribution-level verdict "
        f"is what reproduces. {_DISCLAIMER}",
        "",
        "## Reproduce this report",
        "",
        "```bash",
        meta.reproduce_cmd,
        "```",
        "",
        "You supply your own API key (read from the environment). Exact outputs vary because "
        "models are non-deterministic; the distribution-level verdicts are what reproduce.",
        "",
        "---",
        "",
        f"Open suite: `{meta.suite_path}` ({meta.suite_id} v{meta.suite_version}, "
        f"`{meta.suite_hash}`). A machine-readable JSON sidecar with the raw per-scenario "
        "results is written alongside this report. Harness + scenarios are open source under "
        "Apache-2.0. Method & false-positive measurement: [`docs/fp-measurement.md`](https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md).",
    ]
    return "\n".join(sections) + "\n"


def to_report_sidecar(results: list[DiffResult], meta: ReportMeta) -> dict[str, Any]:
    """The machine-readable audit artifact emitted next to the Markdown report.

    Pure: ``{meta, results, coverage}`` where all three are plain JSON-serializable
    structures, so any flagged behavior change is traceable to the exact per-scenario
    verdict + signals, and any CLEAN one is traceable to what could have fired at all.

    ``coverage`` is written out even though ``meta.census`` already serialises its three
    booleans: the audit trail must record the DERIVED lists the document published, not
    only the inputs a reader would have to re-derive them from. `[M]` MP-81 is this exact
    lesson — the number we publish and the number the harness computes must be the same
    object, or they drift.
    """
    census = meta.census
    return {
        "meta": asdict(meta),
        "results": [r.model_dump(mode="json") for r in results],
        "coverage": {
            # `None`, not `[]`: "no census was taken" is a different claim from "nothing
            # was inert", and a sidecar that cannot tell them apart cannot audit either.
            # `is not None`, not truthiness: a dataclass with no `__bool__`/`__len__` is
            # always truthy today, but if `ChannelCensus` ever gains either, an empty census
            # would silently serialise as "not measured" -- the exact distinction the
            # comment above defends.
            "channels_live": census.hard_content_channels if census is not None else None,
            "channels_inert": census.inert if census is not None else None,
            "underpowered_scenarios": list(meta.underpowered),
        },
    }
