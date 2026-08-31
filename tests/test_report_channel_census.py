"""MP-140 -- the PUBLIC Modelpin Report discloses no coverage at all.

MP-138 (`c9c2e98`) taught `modelpin check` to say which verdict-bearing channels could
fire, and `tests/test_channel_census.py` pins that behaviour for ``render_cli`` and
``render_pr_comment``. `modelpin report` was not touched. `[M] 2026-08-31`:

    cli.py:780              console.print(render_cli(results, from_, to, n))
    report/__init__.py:583  def render_report_md(results, meta) -> str
    report/__init__.py:640  def to_report_sidecar(results, meta) -> dict[str, Any]

Neither ``census`` nor ``underpowered`` reaches any of the three, and ``ReportMeta``
(report/__init__.py:416-438) has no field to carry them. So over a suite that declares no
`tools`, no `assertions`, and configures no `judge_model` -- where the only CI-failing
channel left is refusal, which fires only when the candidate starts DECLINING -- the one
artifact we hand to strangers headlines "No behavioral change observed" and says nothing
about what could not fire.

This is the ADR-0009 surface ("measurement/opinion", "the independent voice labs can't
be") and wedge item 3. `check`, the private CI surface, is now more honest than the public
one. These tests are REPRODUCTIONS: they are expected to FAIL on the current tree.

Fully offline (ADR-0006): the `fake` provider replays canned traces, identical on both
sides, so every verdict is `unchanged` and the headline is the clean one.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from modelpin.cli import app

runner = CliRunner()

_FROM = "old-model-v1"
_TO = "new-model-v2"

#: The dogfood's shape, minimised: plain text in, plain text out. No `tools` key and no
#: `assertions` block -- so `tools_declared` and `assertions_declared` are both False --
#: and no `judge_model`, which the offline `fake` provider would disable anyway.
_SCENARIOS = {
    "greeting": "Hello, who are you?",
    "summarize": "Summarize this invoice.",
}

_CONFIG = """models:
  - old-model-v1
scenarios_dir: suite
providers:
  - fake
runs: 5
"""


def _blind_suite(tmp_path: Path) -> Path:
    """A suite on which NO hard content channel can fire. Returns the working dir."""
    suite = tmp_path / "suite"
    suite.mkdir(parents=True, exist_ok=True)
    traces = []
    for sid, prompt in _SCENARIOS.items():
        (suite / f"{sid}.json").write_text(
            json.dumps(
                {
                    "id": sid,
                    "name": f"{sid} (no tools, no assertions)",
                    "kind": "single",
                    "input": {"messages": [{"role": "user", "content": prompt}]},
                }
            ),
            encoding="utf-8",
        )
        # Identical on both sides: the candidate is indistinguishable from the reference,
        # which is exactly the state that earns the clean headline under test.
        for model in (_FROM, _TO):
            traces.append(
                {
                    "scenario_id": sid,
                    "model_id": model,
                    "final_output": f"canned answer for {sid}",
                    "tool_calls": [],
                    "refused": False,
                    "latency_ms": 100,
                    "tokens_out": 7,
                }
            )
    (tmp_path / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    (tmp_path / "modelpin.yaml").write_text(_CONFIG, encoding="utf-8")
    return tmp_path


def _run_report(tmp_path: Path) -> tuple[str, str, dict]:
    """Drive the real `modelpin report` command. Returns (cli_output, markdown, sidecar)."""
    root = _blind_suite(tmp_path)
    out = root / "out"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            _TO,
            "--from",
            _FROM,
            "--provider",
            "fake",
            "--fixtures",
            str(root / "traces.json"),
            "--suite-dir",
            str(root / "suite"),
            "--config",
            str(root / "modelpin.yaml"),
            "--runs",
            "5",
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    sidecar = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    return r.output, md, sidecar


def _flat(text: str) -> str:
    """Rich hard-wraps the console; collapse whitespace before substring matching."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------------------
# Surface 1 -- the published Markdown. The artifact that goes to strangers.
# ---------------------------------------------------------------------------------------


def test_the_published_report_does_not_headline_green_over_a_blind_suite(tmp_path):
    """`render_pr_comment` already refuses this exact header for this exact census
    (`test_the_header_does_not_lead_green_over_an_inert_run`). The public Report ships it."""
    _, md, _ = _run_report(tmp_path)
    headline = md.splitlines()[3]
    assert "No behavioral change observed." not in headline, headline


def test_the_published_report_names_the_channels_that_could_not_fire(tmp_path):
    """ADR-0022's rule -- "a rate quoted without its coverage number is not a result" --
    applied to the surface it was written for."""
    _, md, _ = _run_report(tmp_path)
    assert "no scenario declares `tools`" in md
    assert "NOT cleared on content" in md


# ---------------------------------------------------------------------------------------
# Surface 2 -- the JSON sidecar, the machine-readable audit trail.
# ---------------------------------------------------------------------------------------


def test_the_sidecar_records_which_channels_were_live(tmp_path):
    """The sidecar exists so a published claim is traceable to what produced it. A verdict
    whose channel coverage is absent from the audit trail cannot be audited."""
    _, _, sidecar = _run_report(tmp_path)
    blob = json.dumps(sidecar).lower()
    assert "census" in blob or "inert" in blob, sorted(sidecar["meta"])


# ---------------------------------------------------------------------------------------
# Surface 3 -- the terminal. `report` and `check` must not disagree about the same run.
# ---------------------------------------------------------------------------------------


def test_report_and_check_agree_about_coverage_on_the_same_suite(tmp_path):
    """`[M] 2026-08-31` on one identical blind suite: `check` prints "OK?" plus the full
    census clearance; `report` prints a green "OK" and nothing. Same scenarios, same two
    models, same channels -- opposite claims."""
    cli_out, _, _ = _run_report(tmp_path)
    assert "NOT cleared on content" in _flat(cli_out), _flat(cli_out)


# ---------------------------------------------------------------------------------------
# MP-123, absorbed here because it is the same three call sites: the RUN-COUNT axis.
#
# `mp report --runs 2` writes a document over a run where the exact permutation test cannot
# return a p-value below 0.167, so no signal can reach ALPHA whatever the models do. The
# console said so before it spent; the published artifact did not.
# ---------------------------------------------------------------------------------------

#: An ARMED suite -- declares `tools` AND `assertions`, so `hard_content_channels` is
#: non-empty and the census raises no objection. Isolates the run-count axis: anything the
#: document withholds here is withheld for N, not for coverage.
_ARMED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order by id.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    }
]


def _armed_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(parents=True, exist_ok=True)
    traces = []
    for sid, prompt in _SCENARIOS.items():
        (suite / f"{sid}.json").write_text(
            json.dumps(
                {
                    "id": sid,
                    "name": f"{sid} (tools + assertions declared)",
                    "kind": "single",
                    "input": {
                        "messages": [{"role": "user", "content": prompt}],
                        "tools": _ARMED_TOOLS,
                    },
                    "assertions": {"must_contain": ["canned"]},
                }
            ),
            encoding="utf-8",
        )
        for model in (_FROM, _TO):
            traces.append(
                {
                    "scenario_id": sid,
                    "model_id": model,
                    "final_output": f"canned answer for {sid}",
                    "tool_calls": [],
                    "refused": False,
                    "latency_ms": 100,
                    "tokens_out": 7,
                }
            )
    (tmp_path / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    (tmp_path / "modelpin.yaml").write_text(_CONFIG, encoding="utf-8")
    return tmp_path


def _run_report_on(root: Path, *, runs: str = "5") -> tuple[str, str, dict]:
    out = root / f"out{runs}"
    r = runner.invoke(
        app,
        [
            "report",
            "--to",
            _TO,
            "--from",
            _FROM,
            "--provider",
            "fake",
            "--fixtures",
            str(root / "traces.json"),
            "--suite-dir",
            str(root / "suite"),
            "--config",
            str(root / "modelpin.yaml"),
            "--runs",
            runs,
            "--output-dir",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    sidecar = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    return r.output, md, sidecar


def test_the_published_report_does_not_headline_green_at_a_blind_run_count(tmp_path):
    """MP-123. At 2v2 the exact permutation floor is 0.167, so nothing can reach p <= 0.05.
    The suite here is fully ARMED, so the only reason to withhold a clearance is N."""
    _, md, _ = _run_report_on(_armed_suite(tmp_path), runs="2")
    assert "No behavioral change observed." not in md.splitlines()[3]
    assert "could not have reported a change" in md
    assert "NOT cleared" in md


def test_the_sidecar_names_the_scenarios_that_could_not_have_reported_a_change(tmp_path):
    _, _, sidecar = _run_report_on(_armed_suite(tmp_path), runs="2")
    assert sorted(sidecar["coverage"]["underpowered_scenarios"]) == sorted(_SCENARIOS)
    # The channel axis is INDEPENDENT: this suite arms the trajectory channel, and a run
    # count too low to conclude must not be reported as a missing channel.
    assert sidecar["coverage"]["channels_live"] == ["tool trajectory"]


def test_an_armed_well_powered_run_still_earns_its_clean_headline(tmp_path):
    """The anti-crying-wolf half. A disclosure that fires on every run discloses nothing,
    and the north-star metric is the false-POSITIVE rate -- including false alarms about
    our own coverage."""
    _, md, sidecar = _run_report_on(_armed_suite(tmp_path), runs="5")
    assert "✅ **No behavioral change observed.**" in md
    assert "NOT cleared" not in md
    assert sidecar["coverage"]["underpowered_scenarios"] == []
    # `[M]` The judge is inert on EVERY offline run -- the `fake` provider disables it
    # whatever the config says -- so a clean headline here is not "nothing was inert". It
    # is "a hard content channel was live", which is the distinction the clearance turns on
    # and the reason the coverage block still ships beneath the green header.
    assert sidecar["coverage"]["channels_live"] == ["tool trajectory"]
    assert sidecar["coverage"]["channels_inert"] == [
        "semantic judge (disabled on the offline `fake` provider)"
    ]


def test_coverage_is_published_even_when_the_run_is_fully_armed(tmp_path):
    """`[M]` ADR-0022 applies to a clean result too: the coverage number is what makes the
    verdict readable, so it ships whether or not it raises an objection."""
    _, md, _ = _run_report_on(_armed_suite(tmp_path), runs="5")
    assert "## Coverage" in md
    assert "tool trajectory" in md


def test_both_axes_are_disclosed_when_both_apply(tmp_path):
    """`[M]` MP-138's first cut short-circuited two clearances with `or` and printed only
    the first. Too few runs and too few armed channels are independent diagnoses."""
    _, md, sidecar = _run_report_on(_blind_suite(tmp_path), runs="2")
    assert "could not have reported a change" in md  # the run-count clearance
    assert "NO CI-failing channel able to see a change" in md  # the channel clearance
    assert sidecar["coverage"]["underpowered_scenarios"]
    assert sidecar["coverage"]["channels_inert"]


# ---------------------------------------------------------------------------------------
# The two claims-auditor blockers, `[M] 2026-08-31`. Both were published documents that
# contradicted their OWN per-scenario table, and the 11 tests above were green over both --
# they only ever asked a CLEAN run what it said. ADR-0022's safety property, restated for
# this surface: no channel the engine actually FLAGGED may be described as unable to fire.
# ---------------------------------------------------------------------------------------


def _one_scenario_suite(tmp_path: Path, *, assertions: dict | None, traces: list) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir(parents=True, exist_ok=True)
    scenario = {
        "id": "s1",
        "name": "s1",
        "kind": "single",
        "input": {"messages": [{"role": "user", "content": "Give me the invoice total."}]},
    }
    if assertions is not None:
        scenario["assertions"] = assertions
    (suite / "s1.json").write_text(json.dumps(scenario), encoding="utf-8")
    (tmp_path / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    (tmp_path / "modelpin.yaml").write_text(_CONFIG, encoding="utf-8")
    return tmp_path


def _trace(model: str, output: str, *, refused: bool = False) -> dict:
    return {
        "scenario_id": "s1",
        "model_id": model,
        "final_output": output,
        "tool_calls": [],
        "refused": refused,
        "latency_ms": 100,
        "tokens_out": 7,
    }


def test_a_refusal_regression_is_never_published_as_an_uncovered_run(tmp_path):
    """`[M]` The first cut rendered, six lines apart in ONE document:

        - **Live:** none. No CI-failing channel on this run could see a change in what
          the model says.
        | s1 | regression | ... | refusal rate 0% -> 100% |

    Refusal IS a hard, CI-failing channel (`diff/__init__.py:393-398`); it is absent from
    `hard_content_channels` because it fires only on a DECLINE, which is the right rule for
    withholding a clearance and the wrong description of what produced a finding.
    """
    root = _one_scenario_suite(
        tmp_path,
        assertions=None,
        traces=[
            _trace(_FROM, "I am an assistant."),
            _trace(_TO, "I'm sorry, I can't help with that.", refused=True),
        ],
    )
    _, md, _ = _run_report_on(root)
    assert "refusal rate" in md, "the fixture must actually produce a refusal finding"
    assert "**Live:** none" not in md
    assert "Live, and able to report a regression:** refusal" in md


def test_a_firing_assertion_channel_appears_in_the_coverage_lists(tmp_path):
    """`[M]` The second blocker: a declared, armed, FIRING `must_contain` appeared in
    neither list, under "Live: none", above its own `changed_minor` row. `fmt_drift` caps at
    `changed_minor` (`diff/__init__.py:428-431`), so assertions are advisory-live -- a third
    state the original two-list rendering could not express."""
    root = _one_scenario_suite(
        tmp_path,
        assertions={"must_contain": ["TOTAL"]},
        traces=[_trace(_FROM, "TOTAL: 42.00"), _trace(_TO, "the amount is 42.00")],
    )
    _, md, _ = _run_report_on(root)
    assert "violates the scenario's text assertions" in md, "fixture must violate it"
    assert "Live, advisory only" in md
    assert "text assertions" in md.split("## Settings")[0]


def test_the_headline_claims_only_what_the_census_can_support(tmp_path):
    """`[M]` The headline said "no channel could observe a change in content" over a suite
    whose assertion channel was armed and comparing. `hard_content_channels` means "could
    have produced a REGRESSION", so that is what the headline is allowed to say."""
    root = _one_scenario_suite(
        tmp_path,
        assertions={"must_contain": ["TOTAL"]},
        traces=[_trace(_FROM, "TOTAL: 42.00"), _trace(_TO, "TOTAL: 42.00")],
    )
    _, md, _ = _run_report_on(root)
    assert "only a refusal would have registered as a regression" in md
    assert "no channel could observe a change in content" not in md


def test_the_clearance_points_at_the_table_that_is_actually_below_it(tmp_path):
    """On the CLI and the PR comment the findings are printed ABOVE this line; in the
    published Report they are below it. A false direction inside the honesty disclosure."""
    _, md, _ = _run_report(tmp_path)
    assert "read the advisory findings below." in md
    assert "read the advisory findings above." not in md


def test_a_fully_blind_run_says_so_before_it_lists_any_live_channel(tmp_path):
    """At 2v2 nothing can reach ALPHA -- refusal included -- so a `Live` list read at face
    value would name channels that could not have concluded."""
    _, md, _ = _run_report_on(_blind_suite(tmp_path), runs="2")
    coverage = md.split("## Coverage")[1].split("## Settings")[0]
    warning = "nothing below could have fired"
    assert warning in coverage
    assert coverage.index(warning) < coverage.index("- **Live")
