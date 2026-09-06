"""A run that never happened is not a regression (ADR-0035).

`mp check --help` has always documented ``1`` as *"at least one real regression (the CI
gate)"*, and `action.yml` publishes that code on a contributor's PR as
``::error::Modelpin detected a behavioral regression``. But `_fail` exited ``1`` from all 28 of
its call sites, every one of which is a setup, configuration or environment failure.

`[M] 2026-09-06` reproduced, both of these:

    $ mp baseline                       # provider: openai, no key exported
    error: OPENAI_API_KEY is not set...                             EXIT=1
    $ mp check --to m2 --match nonsense                             EXIT=1

So the loudest, most public thing this product says -- a red annotation on a stranger's first
pull request -- could assert a measurement that was never taken. That is the north-star promise
inverted on the surface where it costs the most.

This file pins the contract in both directions: setup failure must NOT return the gate code, and
a real regression must still return it. The second half matters as much as the first -- a fix
that stopped exit 1 ever happening would "pass" a one-sided test while removing the CI gate.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from modelpin.cli import EXIT_SETUP_FAILED, EXIT_UNMEASURED

REPO = Path(__file__).resolve().parents[1]


def _run(work: Path, *args: str, env_overrides: dict[str, str] | None = None) -> int:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GROQ_API_KEY")}
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "-m", "modelpin.cli", *args],
        cwd=str(work),
        capture_output=True,
        env=env,
        timeout=180,
    ).returncode


@pytest.fixture()
def offline_case(tmp_path: Path) -> Path:
    """A complete, working offline case: a regression is genuinely present between m1 and m2."""
    work = tmp_path / "case"
    (work / "scenarios").mkdir(parents=True)
    (work / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    (work / "scenarios" / "s.json").write_text(
        json.dumps(
            {
                "id": "s",
                "name": "s",
                "kind": "single",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
            }
        ),
        encoding="utf-8",
    )
    (work / "fx.json").write_text(
        json.dumps(
            [
                {"scenario_id": "s", "model_id": "m1", "final_output": "sure", "refused": False},
                {"scenario_id": "s", "model_id": "m2", "final_output": "I cannot", "refused": True},
            ]
        ),
        encoding="utf-8",
    )
    return work


def test_a_real_regression_still_exits_one(offline_case: Path) -> None:
    """The half that must not be broken by the fix: the CI gate still fires.

    Without this, a change that simply stopped emitting 1 would satisfy every other assertion
    in this file while silently disarming the product.
    """
    assert _run(offline_case, "baseline", "--fixtures", "fx.json") == 0
    assert _run(offline_case, "check", "--to", "m2", "--fixtures", "fx.json") == 1


def test_a_missing_api_key_does_not_claim_a_regression(offline_case: Path) -> None:
    """The first-run case, and the one that reaches a stranger's PR.

    A contributor who has not added `OPENAI_API_KEY` to repository secrets must not be told
    their model migration regressed.
    """
    (offline_case / "modelpin.yaml").write_text(
        "models:\n  - gpt-4o-mini\nscenarios_dir: scenarios\nproviders:\n  - openai\nruns: 5\n",
        encoding="utf-8",
    )
    code = _run(offline_case, "baseline")
    assert code == EXIT_SETUP_FAILED, f"a missing API key exited {code}, not {EXIT_SETUP_FAILED}"
    assert code != 1, "a missing API key returned the CI-gate code"


def test_an_unusable_flag_does_not_claim_a_regression(offline_case: Path) -> None:
    code = _run(offline_case, "check", "--to", "m2", "--fixtures", "fx.json", "--match", "nonsense")
    assert code == EXIT_SETUP_FAILED, f"a bad --match exited {code}"


def test_an_unreadable_config_does_not_claim_a_regression(offline_case: Path) -> None:
    (offline_case / "modelpin.yaml").write_text("models: [m1]\nproviderz: [fake]\n", "utf-8")
    code = _run(offline_case, "check", "--to", "m2", "--fixtures", "fx.json")
    assert code == EXIT_SETUP_FAILED, f"an unknown config key exited {code}"


def test_no_scenarios_found_does_not_claim_a_regression(tmp_path: Path) -> None:
    work = tmp_path / "empty"
    work.mkdir()
    code = _run(work, "check", "--to", "m2", "--provider", "fake")
    assert code == EXIT_SETUP_FAILED, f"an empty directory exited {code}"


def test_the_four_codes_are_distinct() -> None:
    """0/1/3/4 must stay four different numbers, and 2 stays Click's."""
    assert len({0, 1, EXIT_UNMEASURED, EXIT_SETUP_FAILED}) == 4
    assert EXIT_SETUP_FAILED != 2, "2 is Click's usage code; we must not collide with it"


# ------------------------------------------------------------------ the published surfaces


def test_action_yml_never_calls_a_setup_failure_a_regression() -> None:
    """`action.yml` is where the false claim was actually PUBLISHED, so pin it there too.

    Asserted structurally rather than by wording: the guard must survive a copy edit, but must
    fail if the `4` branch is ever removed and setup failures fall back to the regression
    message the way they used to.
    """
    text = (REPO / "action.yml").read_text(encoding="utf-8")
    assert '"$MP_CODE" = "4"' in text, (
        "action.yml no longer branches on exit 4, so a setup failure falls through to the "
        "regression message again. That is the ADR-0035 defect."
    )
    regression_line = [
        ln for ln in text.splitlines() if "detected a behavioral regression" in ln and "echo" in ln
    ]
    assert len(regression_line) == 1, "expected exactly one regression annotation"
    guarded = re.search(
        r'elif \[ "\$MP_CODE" = "1" \]; then\s*\n\s*echo "::error::Modelpin detected a behavioral',
        text,
    )
    assert guarded, (
        "The 'detected a behavioral regression' annotation must be guarded by an explicit "
        "`= 1` test, never by a catch-all `else` -- the catch-all is what published the claim "
        "for missing API keys."
    )


@pytest.mark.parametrize("doc", ["README.md", "actions/README.md"])
def test_the_public_docs_state_the_fourth_code(doc: str) -> None:
    """A contract the user is told is 0/1/3 while the tool emits 4 is a documentation defect
    of exactly the kind this repo files rows about."""
    path = REPO / doc
    if not path.exists():  # pragma: no cover - a distribution without the docs tree
        pytest.skip(f"{doc} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert re.search(r"\*\*4\*\*|`4`", text), f"{doc} does not document exit code 4"
