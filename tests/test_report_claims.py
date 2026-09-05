"""MP-102 - `docs/reports/*` publishes numbers, and until now nothing checked them.

`[M]` ADR-0024 Consequences names this hole in as many words: *"These guards pin
`docs/fp-measurement.md` and `README.md` only. Other published surfaces - `docs/reports/*` in
particular - remain unguarded."* The drift map is the most-linked page the project has, `graft
docs` ships it in the sdist, and it had drifted in four separate ways at once:

    ~50 of 60 comparisons          the data says exactly 50
    of 9 flags, ~6 are solid       the data says exactly 6 - and the body approximated a number
                                   its own next sentence pinned by complement
    "a 0% -> 100% refusal-rate     the data says TWO carry `refusal_delta = 1.0`; the third
     regression ... in three        (`4o -> 4.1`) has `refusal_delta = 0.0` and was flagged by
     pairs"                         the judge - the report over-attributed its own bug by one
    "every single migration        `gpt-4o-mini -> gpt-4o` surfaced nothing that survives the
     surfaced at least one          traces: its only `regression` is the apostrophe artifact
     genuine behavior change"       and its `changed_minor` is a `negative` -> `Negative`
                                    casing flip the report never disclosed

The first two are MP-82's defect (an approximate numerator on a published rate) one directory
over, on higher-traffic copy. The third and fourth are worse, and are why a name-list guard
would not have been enough: both sentences were *self-critical* or *headline*, so no reviewer
re-read them against the artifact published beside them.

Every assertion here derives its expected value from
`docs/reports/data/drift_results_drift-suite.json` - the artifact the report tells the reader
to recount - so the document cannot drift from the run it publishes. ADR-0024's rule verbatim:
derive the expected value from the evidence, never hardcode it. Four literals ARE hardcoded
(`(9, 3)`, `"Three of the nine"`, `` `6 of 9` ``, the 2/1 refusal split) and each is a
deliberate tripwire: if the published run ever changes, those fail loudly with a message
telling the reader to rewrite the prose rather than silently re-deriving around it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fp_measurement import upper_bound_95  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_MAP = _ROOT / "docs" / "reports" / "modelpin-drift-map-1.md"
_DATA = _ROOT / "docs" / "reports" / "data" / "drift_results_drift-suite.json"

_SOFT_SCENARIO = "borderline_access"
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}

#: How far from a published fraction its interval may sit and still be read as qualifying it.
_ADJACENCY = 350


def _doc() -> str:
    return _MAP.read_text(encoding="utf-8")


def _run() -> dict:
    """The published artifact the drift map invites the reader to recount."""
    return json.loads(_DATA.read_text(encoding="utf-8"))


def _verdicts() -> Counter:
    return Counter(r["verdict"] for p in _run()["pairs"] for r in p["results"])


def _regressions() -> list[dict]:
    return [r for p in _run()["pairs"] for r in p["results"] if r["verdict"] == "regression"]


def test_the_data_file_the_report_cites_is_actually_shipped_beside_it():
    """Every other test here is vacuous if the artifact is missing, so fail loudly first.

    `[M]` The report links `data/drift_results_drift-suite.json` and tells the reader that
    `9 - 3 = 6` is recountable from it. A guard that passed quietly on a deleted evidence file
    would restate the exact problem it exists to prevent.
    """
    assert _DATA.is_file(), f"{_DATA} is missing; the drift map cites it as its evidence."
    assert _run()["pairs"], "the published drift run contains no pairs."
    assert "data/drift_results_drift-suite.json" in _doc(), (
        "the drift map no longer links the artifact its numbers are derived from. "
        "An unrecountable number is an assumption wearing a fraction's clothes."
    )


def test_no_published_count_in_the_drift_map_is_approximated():
    """The generalising guard, and the one that would have caught two of the four defects.

    `[M]` MP-102: `~50 of 60` in the TL;DR and `~6` of 9 flags in the limitations section. Both
    are exactly derivable from the data file published beside the document. This asserts the
    SHAPE - no tilde-prefixed digit anywhere - rather than those two sentences, because the
    defect is a habit and the next copy of it will be in a sentence this file has never read.
    """
    approximations = [ln.strip() for ln in _doc().splitlines() if re.search(r"~\s*\d", ln)]
    assert not approximations, (
        "the drift map states an approximate count:\n  "
        + "\n  ".join(approximations)
        + f"\nEvery count in this report is exactly derivable from {_DATA.name}. An approximate "
        "numerator on a published rate is the defect MP-82 fixed in the harness; this is the "
        "public copy of it."
    )


def test_the_verdict_totals_are_the_ones_the_published_run_carries():
    """The table's Total row and the TL;DR's quiet count, both derived from the artifact."""
    v = _verdicts()
    doc, total = _doc(), sum(v.values())
    row = f"| **Total** | **{v['unchanged']}** | **{v['regression']}** | **{v['changed_minor']}** |"

    assert row in doc, (
        f"the Total row must read {v['unchanged']} / {v['regression']} / "
        f"{v['changed_minor']} - the tally in {_DATA.name}."
    )
    assert (
        f"stayed quiet on {v['unchanged']} of {total} comparisons" in doc
    ), f"the TL;DR must state the exact quiet count {v['unchanged']} of {total}."
    assert (
        f"{total} comparisons total" in doc
    ), f"the per-pair section must state {total} comparisons."


def test_the_solid_soft_split_is_exact_and_shows_its_arithmetic():
    """`9 - 3 = 6`, derived - the number the whole credibility argument rests on."""
    regs = _regressions()
    soft = sum(1 for r in regs if r["scenario_id"] == _SOFT_SCENARIO)
    solid = len(regs) - soft
    doc = _doc()

    assert (len(regs), soft) == (9, 3), (
        "the prose this guard reads is written for a 9 = 6 + 3 split; the published run now "
        f"reads {len(regs)} = {solid} + {soft}. Rewrite the limitations section, do not "
        "loosen this test."
    )
    assert (
        f"of the {len(regs)} `regression` flags, exactly **{solid}**" in doc
    ), f"the drift map must publish exactly **{solid}** solid flags of {len(regs)}."
    assert (
        f"The other **{soft}** are the `{_SOFT_SCENARIO}` flags" in doc
    ), f"the drift map must publish the complement as exactly **{soft}**."
    assert f"`{len(regs)} − {soft} = {solid}` is the whole derivation" in doc, (
        "the drift map must show the subtraction, so a reader can check the split without "
        "parsing JSON."
    )
    assert f'Three of the nine "regression" flags are on `{_SOFT_SCENARIO}`' in doc, (
        "the table footnote must still partition the same denominator. It agreeing with the "
        "body is the whole point of this row."
    )


def test_the_flags_are_not_turned_into_a_precision_rate():
    """The 9 flags are not 9 independent trials, and the report must say so.

    `[M]` They land on 4 distinct scenarios: `prompt_injection` in 4 pairs,
    `borderline_access` in 3, and two singletons. A binomial interval over them would count
    repeats of one scenario as separate evidence - MP-105's defect, which cost a live 70-trial
    run before anyone read the denominator.

    The spelling ban below is a **tripwire, not a proof**: ADR-0024:63 warns by name that a
    banned-string check does not generalise, and `[M]` a mutant that wrote the same rate as
    "roughly two thirds" passed the first version of this test. The load-bearing assertion is
    that the refusal sentence is present; the ban catches the careless case.
    """
    regs = _regressions()
    soft = sum(1 for r in regs if r["scenario_id"] == _SOFT_SCENARIO)
    distinct_all = len({r["scenario_id"] for r in regs})
    distinct_solid = len({r["scenario_id"] for r in regs if r["scenario_id"] != _SOFT_SCENARIO})
    doc = _doc()

    assert (
        "We deliberately do not turn `6 of 9` into a rate" in doc
    ), "the drift map must refuse to publish 6/9 as a precision rate."
    assert (
        f"only **{distinct_all} distinct scenarios**" in doc
    ), f"the {len(regs)} flags cover {distinct_all} distinct scenarios; the report must say so."
    assert (
        f"span **{distinct_solid}** distinct scenarios" in doc
    ), f"the solid flags cover {distinct_solid} distinct scenarios; the report must say so."
    exact = 100 * (len(regs) - soft) / len(regs)
    banned = [f"{exact:.0f}%", f"{exact:.1f}%", "two thirds", "two-thirds"]
    published = [b for b in banned if b.lower() in doc.lower()]
    assert not published, (
        f"the drift map publishes {published} - the precision rate it just declined to compute. "
        "These flags are not exchangeable trials (ADR-0022)."
    )


def test_the_refusal_rate_flag_is_attributed_to_the_pairs_that_actually_carry_it():
    """The report's self-criticism has to be as accurate as its criticism of models.

    `[M]` MP-102: the doc claimed a `0% -> 100%` refusal-rate regression *"in three pairs"*.
    The data says two: the `4o -> 4.1` flag has `refusal_delta = 0.0` and came from the
    semantic judge. Over-attributing your own bug is still a wrong number on a public page,
    and the direction - self-flagellating rather than flattering - is exactly why it survived
    every read.
    """
    soft = [r for r in _regressions() if r["scenario_id"] == _SOFT_SCENARIO]
    by_refusal = [r for r in soft if r["signals"]["refusal_delta"] == 1.0]
    by_judge = [r for r in soft if r["signals"]["refusal_delta"] == 0.0]
    doc = _doc()

    assert (len(by_refusal), len(by_judge)) == (2, 1), (
        "this guard is written for a 2/1 split of the soft flags; the published run now reads "
        f"{len(by_refusal)}/{len(by_judge)}. Re-read the limitations section before editing it."
    )
    assert "In **two** of them (`4o-mini → 4o` and cross-vendor) it is a " in doc, (
        "the report must attribute the refusal-rate move to the two pairs that carry "
        "`refusal_delta = 1.0`, not to all three."
    )
    assert (
        "the refusal signal did not move at all (`refusal_delta = 0.0`)" in doc
    ), "the report must say that the third soft flag is not a refusal-detector artifact."
    assert (
        'refusal-rate "regression" on this scenario in three pairs' not in doc
    ), "the withdrawn three-pair attribution is back in the drift map."


def test_the_prompt_injection_pair_count_is_the_one_in_the_data():
    """`4 of the 5` - the headline finding's only quantitative claim."""
    run = _run()
    pairs = {
        p["label"]
        for p in run["pairs"]
        for r in p["results"]
        if r["scenario_id"] == "prompt_injection" and r["verdict"] == "regression"
    }
    n = len(run["pairs"])
    assert (
        f"prompt-injection change in **{len(pairs)} of the {n}** pairs" in _doc()
    ), f"the drift map must state {len(pairs)} of {n} pairs for the prompt-injection flag."


def test_the_tldr_does_not_credit_a_pair_whose_only_flags_are_artifacts():
    """`[M]` MP-102: the TL;DR read *"every single migration surfaced at least one **genuine**
    behavior change"*. `gpt-4o-mini` -> `gpt-4o` surfaced no such thing: its only `regression`
    is the apostrophe artifact the same document discloses, and its `changed_minor` is a
    `negative` -> `Negative` casing flip. All five pairs DO carry a `regression` - the table
    says so - which is why the honest sentence needs the solidity qualifier and not a smaller
    count.

    `[M]` The first correction of this sentence was ALSO wrong: it said four pairs flagged a
    `regression` and the fifth a `changed_minor`, contradicting the table three screens down.
    That is why this test asserts the qualifier and the total separately.
    """
    run = _run()
    solid_pairs = {
        p["label"]
        for p in run["pairs"]
        for r in p["results"]
        if r["verdict"] == "regression" and r["scenario_id"] != _SOFT_SCENARIO
    }
    flagged_pairs = {
        p["label"] for p in run["pairs"] for r in p["results"] if r["verdict"] == "regression"
    }
    n = len(run["pairs"])
    doc = _doc()

    assert len(flagged_pairs) == n, (
        f"only {len(flagged_pairs)} of {n} pairs carry a `regression`; the TL;DR's "
        "'every pair drew at least one flag' no longer holds."
    )
    assert len(solid_pairs) == n - 1, (
        f"{len(solid_pairs)} of {n} pairs carry a solid regression; the TL;DR's wording is "
        "written for exactly one exception."
    )
    assert (
        f"**{_WORDS[len(solid_pairs)]} of the {_WORDS[n]} pairs** carry a `regression` that "
        "survives a read of the raw traces" in doc
    ), (
        f"the TL;DR must say {len(solid_pairs)} of {n} pairs carry a SOLID regression - not "
        "that only that many were flagged at all."
    )
    for withdrawn in (
        "genuine behavior change",
        f"{_WORDS[len(solid_pairs)]} of the {_WORDS[n]} pairs as a `regression`",
    ):
        assert (
            withdrawn not in doc
        ), f"the drift map has re-adopted the withdrawn TL;DR wording {withdrawn!r}."


def test_the_casing_flip_behind_the_only_changed_minor_is_disclosed():
    """`[M]` MP-102, found by the claims review gate: the tenth verdict was never explained.

    `sarcasm_sentiment` on `4o-mini` -> `4o` is `negative` (5/5) -> `Negative` (5/5) against a
    case-sensitive `must_contain: ["negative"]` (`structural.py:123`, `s in out`). We are NOT
    withdrawing the verdict - the flip is deterministic, it really would break a strict parser,
    and `changed_minor` is the correct severity. But a report that discloses one of its own
    typography artifacts and silently ships a second is not making the credibility move it
    claims to be making (ADR-0009).
    """
    run = _run()
    minors = [
        (p["label"], r)
        for p in run["pairs"]
        for r in p["results"]
        if r["verdict"] == "changed_minor"
    ]
    assert len(minors) == 1, f"this guard is written for one `changed_minor`; found {len(minors)}."
    label, row = minors[0]
    assert row["signals"]["format_valid"] is False, (
        "the only `changed_minor` is no longer a format-assertion flag; the disclosure below "
        "describes a mechanism the run no longer exhibits."
    )
    doc = _doc()
    for required in (
        "### The one `changed_minor` is a casing flip, and we should have said so",
        row["scenario_id"],
        "`negative` (5/5)",
        "`Negative` (5/5)",
        "case-sensitive",
    ):
        assert required in doc, (
            f"the drift map no longer discloses the mechanism behind its only `changed_minor` "
            f"({row['scenario_id']} on {label}); {required!r} is missing."
        )


def test_neither_disclosure_can_be_deleted_quietly():
    """ADR-0009 Consequences: *"Removing an inconvenient disclosure would be the single worst
    move this project could make."*

    `[M]` claims review 2026-08-25 ran this as a mutant against the first version of this
    file: deleting the whole apostrophe-disclosure body - both quoted traces and the root
    cause - left the suite GREEN, because every other assertion here reads sentences that
    survive the deletion. A guard written for one document that cannot see its central
    disclosure vanish is the MP-98 shape: a rule that exists and does not bind.
    """
    doc = _doc()
    for required in (
        "### The `borderline_access` flags were a refusal-**detector** bug — now fixed",
        "`Trace.refused = False`",
        "`Trace.refused = True`",
        "U+0027",
        "U+2019",
        'manufactured a "regression."',
        "marked **suspect**",
        "### Other caveats (read these before quoting us)",
        "**Small suite.**",
        "**Single-vendor judge.**",
        "**Not a quality ranking.**",
    ):
        assert required in doc, (
            f"the drift map no longer contains {required!r}. This report's whole claim to being "
            "an independent voice is that it discloses its own misfires and its own limits; "
            "removing one must be a red build, not an edit nobody notices."
        )


@pytest.mark.parametrize(
    ("detected", "n", "where"),
    [
        (4, 6, "README.md"),
        (4, 6, "docs/fp-measurement.md"),
        (5, 6, "docs/fp-measurement.md"),
        (4, 6, "CHANGELOG.md"),
        (5, 6, "CHANGELOG.md"),
    ],
)
def test_every_published_recall_fraction_carries_the_bound_its_own_helper_computes(
    detected: int, n: int, where: str
):
    """`[M]` MP-102 / claims review: `4/6` and `5/6` shipped bare on both surfaces.

    A bare `4/6` reads as 67%. The one-sided 95% lower bound at six trials is **27.1%**, and at
    `5/6` it is **41.8%**. `1 - upper_bound_95(misses, checked)` is the same reflection the
    detection arm publishes through (MP-82); this file does not own a second formula. ADR-0022
    forbids the closed form `1 - alpha ** (1 / n)`, which returns 39.3% here whatever
    `detected` is - flattering at 4/6 and understated at 5/6, which is precisely why it cannot
    be a bound.

    **Adjacency is the assertion, not presence.** `[M]` claims review: two mutants passed the
    first version of this test - adding a fourth BARE `4/6`, and moving `**27.1%**` away from
    the fraction it qualifies - because it only checked that both strings existed somewhere in
    the same file. That is MP-72's shape exactly: a correct paragraph coexisting with copies of
    what it retracted.
    """
    text = (_ROOT / where).read_text(encoding="utf-8")
    frac = f"{detected}/{n}"
    bound = f"**{1 - upper_bound_95(n - detected, n):.1%}**"

    sites = [m.start() for m in re.finditer(re.escape(frac), text)]
    assert sites, f"{where} no longer states the {frac} recall fraction."

    orphans = [
        i for i in sites if bound not in text[max(0, i - _ADJACENCY) : i + len(frac) + _ADJACENCY]
    ]
    assert not orphans, (
        f"{where} states {frac} bare at offset(s) {orphans} - no {bound} within {_ADJACENCY} "
        f"characters. Every published {frac} must carry "
        f"`1 - upper_bound_95({n - detected}, {n})`."
    )

    closed_form = f"**{1 - 0.05 ** (1 / n):.1%}**"
    if closed_form != bound:
        assert (
            closed_form not in text
        ), f"{where} publishes {closed_form}, the closed form ADR-0022 records as wrong at k>0."


def test_the_readme_publishes_no_relative_links():
    """MP-30. `pyproject.toml` makes README.md the PyPI **long description**, and PyPI renders
    it on its own domain where a relative path resolves to nothing. Worse, the wheel ships no
    `docs/` at all (ADR-0011), so those targets do not exist for an installed user either.

    `[M] 2026-08-27` 16 of 22 README links were relative, every one of them dead on PyPI. A
    description is FIXED AT UPLOAD -- it cannot be amended without cutting a new version --
    so this guard runs before a release, not after one.
    """
    import re
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    relative = [
        f"[{label}]({url})"
        for label, url in re.findall(r"\[([^\]]*)\]\(([^)]+)\)", readme)
        if not url.startswith(("http://", "https://", "#"))
    ]
    assert (
        not relative
    ), "README.md is the PyPI long description; these links are dead there: " + ", ".join(relative)


#: Hosts a `[project.urls]` value may point at. Deliberately tiny: every entry must be somewhere
#: that demonstrably exists today. Adding a host here is a claim that it resolves -- make it
#: deliberately, and only after checking.
_LIVE_URL_HOSTS = ("https://github.com/", "https://pypi.org/")


def test_every_published_project_url_points_somewhere_that_exists():
    """MP-126, the sibling of the relative-link guard above and the same failure one field over.

    `[M] 2026-08-27` `pyproject.toml` set `Homepage = "https://modelpin.dev"` and
    `nslookup modelpin.dev` returned a name with **no address**. PyPI renders `[project.urls]`
    as the sidebar link list on the project page, so the single most-visited Modelpin surface a
    stranger reaches led with a dead link to a domain that has never existed. `[M]` It shipped
    that way in 0.2.0 -- verified live on `pypi.org/pypi/modelpin/json`.

    `docs/PUBLISHING.md` carried this as an unticked checkbox from 2026-06-25 and nothing could
    see that it was still unticked. This is that check, executable.

    Offline by construction: an allowlist, not a DNS lookup. A network probe would be flaky in
    CI and would pass for any domain someone happened to park -- the property worth guarding is
    "we only publish URLs to hosts we have actually confirmed", which is a decision, not a
    lookup. (ADR-0006 bans live PROVIDER calls specifically; the no-network-in-tests habit is
    its consequence rather than its text, so this is that habit, not that decision.)

    Parsed with `tomllib`, never a regex. `[M] 2026-08-27` a packaging review showed the first
    draft's regex was double-quote-only: a single-quoted or multi-line dead URL parsed to
    nothing and the guard went **green with the dead link still present**. A guard that can
    silently pass is the defect class this file exists to catch, committed inside the fix for it.
    """
    import tomllib
    from pathlib import Path

    raw = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_bytes()
    urls = tomllib.loads(raw.decode("utf-8")).get("project", {}).get("urls", {})
    assert urls, "[project.urls] is missing or empty; PyPI would render no links at all"

    dead = {k: v for k, v in urls.items() if not v.startswith(_LIVE_URL_HOSTS)}
    assert not dead, (
        f"[project.urls] publishes {dead} -- PyPI renders these on the project page and a "
        f"description is FIXED AT UPLOAD, so a dead one can only be corrected by cutting a new "
        f"version. Confirm the host resolves, then add it to _LIVE_URL_HOSTS deliberately."
    )


def test_a_published_report_never_cites_a_path_the_reader_cannot_open():
    """A Report travels: it lands as a PR comment on someone else's repo, and the wheel ships
    no `docs/`. A bare `docs/fp-measurement.md` resolves for nobody outside this checkout."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_report import _meta, _r  # the canonical fixtures, not a second copy

    from modelpin.models import DiffVerdict
    from modelpin.report import render_report_md

    md = render_report_md([_r("s1", DiffVerdict.unchanged)], _meta())
    # Strip COMPLETE markdown links first. A link LABEL may legitimately read `docs/...` so
    # long as its target is absolute; what survives the strip is a bare path with nothing to
    # resolve it. A lookahead on `)` cannot make that distinction -- the label is followed by
    # a backtick, so the first draft of this guard flagged its own correct link.
    stripped = re.sub(r"\[[^\]]*\]\(https?://[^)]+\)", "", md)
    bare = re.findall(r"docs/[\w./-]+\.md", stripped)
    assert not bare, f"published report cites unreachable path(s): {sorted(set(bare))}"
    assert "https://github.com/samarthputhraya/modelpin/blob/main/docs/fp-measurement.md" in md


# --------------------------------------------------------------------------------------
# MP-183 -- the published report must state the run date its own traces carry.
#
# `[M] 2026-09-02`, BEFORE this change: `grep -c "2026-06-24" docs/reports/modelpin-drift-map-1.md`
# -> 0 (reproduce with `git show HEAD~1:docs/reports/modelpin-drift-map-1.md | grep -c`). Today it
# returns non-zero, which is the point -- the command that documented the defect had to stop
# returning zero for the fix to be real. The one
# artifact this project asks a stranger to open carried NO run date in its text, while
# `drift-map-launch-assets.md`'s guardrail says results stated in the present tense without the
# run date are "the tool's own argument used against the post". The date lived only in the JSON
# and in the private launch drafts, so the outreach templates instructed the sender to say a date
# the recipient could not check against the document they were sent.
# --------------------------------------------------------------------------------------

_CACHE = _ROOT / "docs" / "reports" / "data" / "drift_cache_drift-suite.json"


def _trace_run_dates() -> Counter:
    """Every `ts` stamp in the shipped cache, by calendar day."""
    stamps: Counter = Counter()

    def walk(o: object) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "ts" and isinstance(v, str):
                    stamps[v[:10]] += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(_CACHE.read_text(encoding="utf-8")))
    return stamps


def test_the_published_report_states_the_run_date_its_own_traces_carry():
    """Not "a date" -- THE date, read from the artifact shipped beside the report. A guard that
    accepted any date would pass on a stale one, which is the failure it exists to prevent."""
    dates = _trace_run_dates()
    assert dates, "no `ts` stamps in the shipped cache -- the guard has nothing to check against"
    run_date, n = dates.most_common(1)[0]
    doc = _doc()
    assert run_date in doc, (
        f"the drift map does not state its own run date {run_date!r}, which {n} of its shipped "
        "traces carry. A reader told to 'say the date' cannot check it against this document."
    )


def test_the_reports_self_check_block_still_produces_the_output_it_claims():
    """The report hands the reader a paste-able block and prints its expected output. If the two
    ever disagree, the report is teaching a stranger to run a check that fails -- worse than
    publishing no check at all. So the test RUNS the published code and diffs it against the
    published output, rather than asserting on either one alone."""
    doc = _doc()
    code_blocks = re.findall(r"```python\n(.*?)```", doc, re.S)
    code = next((c for c in code_blocks if "drift_cache_drift-suite.json" in c), None)
    assert code, "the drift map no longer carries its self-check block"

    # Skip PAST the python block's own closing fence first. Without this the search runs
    # from that fence to the next one and captures the prose in between, not the output.
    after = doc[doc.index(code) + len(code) :]
    after = after[after.index("```") + 3 :]
    claimed = re.search(r"```\n(.*?)```", after, re.S)
    assert claimed, "the self-check block no longer publishes its expected output"

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"the published self-check block does not run: {proc.stderr}"

    # Compare with all whitespace collapsed. The report wraps and the terminal does not; an
    # assertion about CONTENT must not become an assertion about where a line broke.
    def norm(s: str) -> str:
        return "".join(s.split())

    assert norm(proc.stdout) == norm(claimed.group(1)), (
        "the drift map's self-check block no longer prints what the report says it prints.\n"
        f"published:\n{claimed.group(1)}\nactual:\n{proc.stdout}"
    )


def test_the_self_check_block_survives_being_pasted_into_a_repl():
    """`[M] 2026-09-02` review. The prose tells a reader to run the block; the sibling
    guard above executes it in SCRIPT mode, which is not the only way a reader will. Pasted into
    an interactive `>>>` session, a top-level statement flush against the preceding `def` is a
    SyntaxError -- and the REPL then CONTINUES and prints `run date(s): {}`, exit 0. The one
    number the section exists to establish, silently empty, with no failure to notice. A blank
    line before `walk(cache)` is the whole fix; this test is what keeps it there."""
    doc = _doc()
    code = next(
        c
        for c in re.findall(r"```python\n(.*?)```", doc, re.S)
        if "drift_cache-suite" in c or "drift_cache_drift-suite.json" in c
    )
    proc = subprocess.run(
        [sys.executable, "-i"],
        input=code,
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "run date(s): {'2026-06-24': 360}" in proc.stdout, (
        "the self-check block does not survive a REPL paste -- a reader following the prose gets "
        f"an empty result and no error. stdout: {proc.stdout!r}"
    )
