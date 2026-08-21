"""The offline demo must actually run — it is the first thing a stranger executes.

`mp init --demo` replaced a quickstart that read files the wheel never shipped (MP-01).
That trade only pays if the generated sandbox works end to end, so this drives the whole
documented path: generate, load, baseline, check, and assert the verdicts the README
promises.

It also closes the trap that makes a broken demo silent: FakeProvider returns a placeholder
trace for any (scenario, model) key it does not hold, so a single typo in a fixture key
yields `unchanged` + exit 0 — "safe to adopt" from a run that measured nothing. See MP-28.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import app
from modelpin.config import load_config
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo
from modelpin.models import DiffVerdict
from modelpin.providers.fake import FakeProvider
from modelpin.scenarios import load_scenarios

runner = CliRunner()


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    write_demo(tmp_path)
    return tmp_path / DEMO_DIRNAME


def test_write_demo_creates_a_complete_sandbox(demo: Path) -> None:
    assert (demo / "modelpin.yaml").is_file()
    assert (demo / DEMO_FIXTURES).is_file()
    assert (demo / "README.md").is_file()
    assert sorted(p.name for p in (demo / "scenarios").glob("*.json")) == [
        "angry_customer.json",
        "greeting.json",
        "invoice_parse.json",
        "refund_request.json",
    ]


def test_generated_scenarios_and_config_are_valid(demo: Path) -> None:
    """Serialized from live pydantic models, so a schema change breaks HERE, not in a
    stranger's terminal."""
    scenarios = load_scenarios(demo / "scenarios")
    assert len(scenarios) == 4
    cfg = load_config(demo / "modelpin.yaml")
    assert cfg.models == [DEMO_FROM]
    assert cfg.providers == ["fake"]
    assert cfg.scenarios_dir == "scenarios"
    assert cfg.runs >= 5, "fewer than 5 runs/side cannot reach p <= ALPHA on the tool signal"
    assert cfg.judge_model is None, "the demo must never require a paid judge call"


def test_every_scenario_model_pair_has_a_real_fixture(demo: Path) -> None:
    """MP-28 guard: a missing key does NOT raise, it silently yields `unchanged`.

    This is the single most likely way the demo ships broken, and the failure mode is a
    false negative — the exact thing Modelpin exists to not do.
    """
    provider = FakeProvider.from_fixtures(demo / DEMO_FIXTURES)
    scenarios = load_scenarios(demo / "scenarios")
    missing = [
        (s.id, model)
        for s in scenarios
        for model in (DEMO_FROM, DEMO_TO)
        if (s.id, model) not in provider.canned
    ]
    assert not missing, f"fixture keys missing (would silently report `unchanged`): {missing}"


def test_write_demo_never_overwrites(demo: Path) -> None:
    edited = demo / DEMO_FIXTURES
    edited.write_text("[]", encoding="utf-8")
    written = write_demo(demo.parent)
    assert written == [], f"re-running clobbered user edits: {written}"
    assert edited.read_text(encoding="utf-8") == "[]"


def test_generated_traces_carry_no_wallclock_noise(demo: Path) -> None:
    """`ts` would make the file differ on every run; `messages: []` is empty bookkeeping.
    Both are noise in the one file the README tells the user to open and edit."""
    records = json.loads((demo / DEMO_FIXTURES).read_text(encoding="utf-8"))
    assert records, "no traces generated"
    for r in records:
        assert "ts" not in r
        assert "messages" not in r


def test_the_documented_quickstart_runs_end_to_end(demo: Path) -> None:
    """The README's four lines, in order, with the verdicts it promises."""
    common = ["--config", str(demo / "modelpin.yaml"), "--scenarios-dir", str(demo / "scenarios")]
    store = str(demo / ".modelpin")
    fixtures = str(demo / DEMO_FIXTURES)

    base = runner.invoke(app, ["baseline", "--fixtures", fixtures, "--store-dir", store, *common])
    assert base.exit_code == 0, base.output

    chk = runner.invoke(
        app,
        ["check", "--to", DEMO_TO, "--fixtures", fixtures, "--store-dir", store, *common],
    )
    assert chk.exit_code == 1, f"the demo must exit 1 - that IS the CI gate.\n{chk.output}"
    assert "regression" in chk.output.lower()


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    [
        ("greeting", DiffVerdict.unchanged),
        ("refund_request", DiffVerdict.regression),
        ("angry_customer", DiffVerdict.regression),
        ("invoice_parse", DiffVerdict.changed_minor),
    ],
)
def test_demo_reaches_the_verdict_the_readme_promises(
    demo: Path, scenario_id: str, expected: DiffVerdict
) -> None:
    """Every verdict the engine can produce is demonstrated, including `unchanged` —
    the negative result is the one that makes the false-positive claim legible."""
    from modelpin.diff import diff_scenario
    from modelpin.replay import replay

    provider = FakeProvider.from_fixtures(demo / DEMO_FIXTURES)
    scenario = next(s for s in load_scenarios(demo / "scenarios") if s.id == scenario_id)
    base = replay(scenario, DEMO_FROM, provider, runs=5)
    cand = replay(scenario, DEMO_TO, provider, runs=5)
    result = diff_scenario(scenario_id, DEMO_FROM, DEMO_TO, base, cand, scenario, "strict")
    assert result.verdict == expected, f"{scenario_id}: {result.verdict} ({result.explanation})"


def test_printed_advice_never_tells_a_powershell_user_to_run_mp() -> None:
    """Every command the CLI PRINTS must use `modelpin`, never the bare `mp` alias.

    On PowerShell `mp` is a ReadOnly, AllScope alias for `Move-ItemProperty`, resolved
    BEFORE PATH. The README warns about it one paragraph above the quickstart -- and the
    CLI used to print the broken form anyway, so a user who read the warning and did
    everything right was then handed a command that silently runs the wrong program.
    `modelpin` works on every shell, so printed advice has no reason to say anything else.
    """
    import re

    import modelpin

    package_root = Path(modelpin.__file__).parent
    offenders: list[tuple[str, str]] = []
    # The WHOLE package, not just cli.py: the first version of this test scanned cli/demo
    # only and missed `Run \`mp baseline\` first.` in storage.py -- an error message on the
    # single most common first-run failure path.
    for source_file in sorted(package_root.rglob("*.py")):
        source = source_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            # Only user-facing strings: skip pure comments (`#` lines carry design notes
            # that legitimately discuss the `mp` alias by name).
            if stripped.startswith("#"):
                continue
            if re.search(r"(?<![\w.-])mp (init|scan|baseline|check|report|version)\b", line):
                offenders.append((f"{source_file.relative_to(package_root)}:{lineno}", stripped))
    assert not offenders, "printed advice uses the PowerShell-broken `mp` alias:\n" + "\n".join(
        f"  {where}: {text}" for where, text in offenders
    )
