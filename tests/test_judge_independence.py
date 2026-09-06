"""MP-196 — the semantic judge must not be a model it is judging.

Two halves, and the second is why the first was invisible.

`[M] 2026-09-06` `_build_judge` compared `cfg.judge_model` against **`to_model` only**, so a
judge equal to the BASELINE never triggered the note — and that is exactly the state `mp init`
shipped, because `_SAMPLE_CONFIG` wrote both:

    models:
      - gpt-4o-mini
    judge_model: gpt-4o-mini

So a brand-new user's first `check` ran with the judge reading its own output as the reference,
silently, on the run they trust most.

**The baseline side is not the milder case.** A judge grading its own output as the *reference*
biases toward calling the pair equivalent — a false NEGATIVE, which is the failure the semantic
channel exists to prevent. The candidate side biases the other way. Neither is independent, and
ADR-0009 stakes the published Report on the reading being one we can defend.

The note is deliberately a note and not an error: it can be a considered, cheap choice. What it
must not be is silent.
"""

from __future__ import annotations

import pytest

import modelpin.cli as cli
from modelpin.config import ModelpinConfig


class _Judge:
    def preflight(self) -> None:  # pragma: no cover - trivial
        return None


@pytest.fixture()
def built(monkeypatch):
    """Build a judge with the provider machinery stubbed out; return what was printed."""
    printed: list[str] = []
    monkeypatch.setattr(
        cli.console, "print", lambda *a, **k: printed.append(str(a[0] if a else ""))
    )

    import modelpin.judge as judge_mod

    monkeypatch.setattr(judge_mod, "build_judge", lambda *a, **k: _Judge())
    monkeypatch.setattr(judge_mod, "infer_judge_provider", lambda _m: "openai")

    def _build(judge_model: str, *, to_model: str | None, from_model: str | None):
        printed.clear()
        cfg = ModelpinConfig(models=["m1"], judge_model=judge_model, providers=["openai"])
        cli._build_judge("openai", cfg, to_model=to_model, from_model=from_model)
        return "\n".join(printed)

    return _build


_NOTE = "not an independent reading"


def test_a_judge_equal_to_the_candidate_is_flagged(built) -> None:
    assert _NOTE in built("gpt-4o", to_model="gpt-4o", from_model="gpt-4o-mini")


def test_a_judge_equal_to_the_baseline_is_flagged(built) -> None:
    """The half that was missing, and the one `mp init` shipped by default."""
    out = built("gpt-4o-mini", to_model="gpt-4o", from_model="gpt-4o-mini")
    assert _NOTE in out, "a judge equal to the BASELINE model was not flagged: " + out


def test_the_note_says_which_side_collided(built) -> None:
    """ "the model being checked" and "the model it is compared against" are different facts,
    and a user with two similar ids needs to know which one to change."""
    assert "being checked" in built("gpt-4o", to_model="gpt-4o", from_model="gpt-4o-mini")
    assert "compared against" in built("gpt-4o-mini", to_model="gpt-4o", from_model="gpt-4o-mini")


def test_an_independent_judge_is_not_flagged(built) -> None:
    """Control. The note must not fire on the configuration we are telling people to use."""
    assert _NOTE not in built("gemini-2.5-pro", to_model="gpt-4o", from_model="gpt-4o-mini")


def test_the_scaffold_does_not_silently_ship_a_self_judging_config() -> None:
    """`mp init`'s own output is the first config most users ever run.

    It may still name the same id in both places -- `models:` is explicitly a placeholder the
    user is told to replace -- but it must SAY so at the point of editing, because the runtime
    note only appears later, after they have already committed the file.
    """
    cfg = cli._SAMPLE_CONFIG
    assert "judge_model:" in cfg
    assert "MP-196" in cfg or "independent" in cfg or "NEITHER" in cfg, (
        "The scaffolded config names the same model in `models:` and `judge_model:` and says "
        "nothing about it:\n" + cfg
    )


def test_the_scaffold_still_loads(tmp_path) -> None:
    """Control: the comment must not break the file it is written into."""
    from modelpin.config import load_config

    p = tmp_path / "modelpin.yaml"
    p.write_text(cli._SAMPLE_CONFIG, encoding="utf-8")
    loaded = load_config(p)
    assert loaded.judge_model == "gpt-4o-mini"
    assert loaded.models == ["gpt-4o-mini"]
