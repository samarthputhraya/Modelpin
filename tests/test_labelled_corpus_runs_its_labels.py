"""A labelled set must be able to run its own labels.

MP-224 authored `examples/calibration/tool/` — 35 pairs, ground truth in `labels.json`, named
as such by ADR-0041 and by the set's own README — and `scripts/tool_gate_price.py` to score
them. What nothing connected was the RUNNER: `scripts/fp_measurement.py` sourced its recall-arm
perturbations from a module-level `PERTURBATIONS` dict keyed by scenario id, and
`grep -c labels scripts/fp_measurement.py` returned **0**.

`[M] 2026-09-09` The consequence, on the first paid run of that corpus (35 trials,
gemini-2.5-flash-lite on Vertex, 10.0 min, 208,444/23,027 replay tokens):

    False-positive rate: 0/17 = 0%
    95% upper bound on the true rate: 16.2% (one-sided Clopper-Pearson, n=17)
    ...
    Detection: 0/0 injected perturbations caught
    *** THE DETECTION ARM CHECKED NOTHING. ***

The 19 `changed` pairs were silently invisible because their ids are not in the dict — and the
FP half looked perfectly healthy beside the hole. A rule chosen on that run would have been
chosen on false-positive suppression with **no measurement of what detection it cost**, which
is the precise failure ADR-0025 and the set itself exist to prevent.

The fix reads the corpus's `labels.json`. The alternative — pasting 19 perturbation strings
into the dict — is the MP-03 shape (one fact, two copies, free to drift) that has already cost
this project the scaffolded `runs:` default and the hardcoded `__version__`.

This module is the guard: for every labelled corpus in the tree, every `changed` label must
produce a recall trial, and every `equivalent` label must not.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from modelpin.scenarios import load_scenarios


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _labelled_corpora() -> list[Path]:
    """Every scenario directory that ships a `labels.json`.

    Discovered, not enumerated: a second labelled corpus must inherit this guard without
    anyone remembering to add it here.
    """
    return sorted(p.parent for p in (_repo_root() / "examples").rglob("labels.json"))


def _fp_measurement():
    """The script's namespace. It is a script, not a package, so run it for its globals."""
    return runpy.run_path(str(_repo_root() / "scripts" / "fp_measurement.py"))


def test_at_least_one_labelled_corpus_exists() -> None:
    """If this fails the rest of the module is vacuous, which is how MP-224's gap survived."""
    assert _labelled_corpora(), (
        "no examples/**/labels.json found. Either the labelled corpus was removed — in which "
        "case ADR-0041's requirement for a fit set is unmet — or it moved and this guard has "
        "silently stopped checking anything."
    )


@pytest.mark.parametrize("corpus", _labelled_corpora(), ids=lambda p: p.name)
def test_every_changed_label_produces_a_recall_trial(corpus: Path) -> None:
    """`changed` means "the gate should fire"; a pair that never runs cannot show that."""
    mod = _fp_measurement()
    labels = json.loads((corpus / "labels.json").read_text(encoding="utf-8"))
    changed = {sid for sid, e in labels.items() if e.get("label") == "changed"}

    perturbations = mod["load_perturbations"](str(corpus))
    missing = sorted(changed - set(perturbations))

    assert not missing, (
        f"{corpus.name}: {len(missing)} scenario(s) labelled `changed` have no perturbation "
        f"the runner can find: {', '.join(missing)}. The recall arm will skip them and the "
        "run will print `Detection: 0/0` beside a healthy-looking false-positive rate."
    )


@pytest.mark.parametrize("corpus", _labelled_corpora(), ids=lambda p: p.name)
def test_no_equivalent_label_is_given_a_perturbation(corpus: Path) -> None:
    """`equivalent` is a same-model null by construction; perturbing it destroys the pair."""
    mod = _fp_measurement()
    labels = json.loads((corpus / "labels.json").read_text(encoding="utf-8"))
    equivalent = {sid for sid, e in labels.items() if e.get("label") == "equivalent"}

    perturbations = mod["load_perturbations"](str(corpus))
    wrongly = sorted(equivalent & set(perturbations))

    assert not wrongly, (
        f"{corpus.name}: {', '.join(wrongly)} are labelled `equivalent` but the runner would "
        "perturb them. An `equivalent` pair is the null this corpus measures false positives "
        "on; injecting an instruction into it turns the null into a detection trial and the "
        "false-positive rate silently stops being one."
    )


@pytest.mark.parametrize("corpus", _labelled_corpora(), ids=lambda p: p.name)
def test_the_plan_actually_schedules_those_trials(corpus: Path) -> None:
    """End to end: the trial PLAN, not just the map, must contain the recall rows.

    The map being right and the plan still not scheduling them is exactly the shape of the
    original defect — two correct halves with nothing joining them.
    """
    mod = _fp_measurement()
    labels = json.loads((corpus / "labels.json").read_text(encoding="utf-8"))
    changed = {sid for sid, e in labels.items() if e.get("label") == "changed"}

    scenarios = load_scenarios(str(corpus))
    perturbations = mod["load_perturbations"](str(corpus))
    plan = mod["plan_trials"](scenarios, 1, perturbations)

    # An FP row and a recall row share the scenario id; the recall row is the one whose
    # candidate is not the baseline object.
    recall_ids = {sid for _key, sid, base, cand in plan if base is not cand}
    missing = sorted(changed - recall_ids)

    assert not missing, (
        f"{corpus.name}: plan_trials scheduled no recall trial for {', '.join(missing)}. "
        "The perturbation map knows about them but the plan does not, so they cost nothing "
        "and measure nothing."
    )
