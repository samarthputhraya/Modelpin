"""FakeProvider — replays canned traces for deterministic, offline runs/tests.

This adapter never invents a trace. It used to return a placeholder Trace for any
``(scenario_id, model_id)`` it did not hold, which made BOTH sides of a comparison
identical: the run printed ``OK n scenario(s) unchanged`` and exited 0 — "safe to
adopt" from a run that measured nothing, with `tool_calls` and `refused` structurally
null on both sides so no signal could ever fire. A false negative of that shape is the
exact failure the project's north-star metric exists to prevent, so a miss is now a
hard error. See MP-28 and ADR-0015.

The guard has two layers because there are two ways to reach zero coverage:
``preflight()`` catches "no canned traces at all" once, before any scenario runs, and
``run()`` catches an individual key miss. Both raise ``MissingCannedTrace``, which the
CLI already knows how to render.
"""

from __future__ import annotations

import json
from pathlib import Path

from modelpin.models import Scenario, Trace
from modelpin.providers.base import ProviderAdapter, ProviderError


class MissingCannedTrace(ProviderError):
    """No canned trace covers a (scenario, model) pair this run needs.

    Subclasses ``ProviderError`` deliberately: the CLI's existing handlers
    (``_preflight_or_fail``, ``_guard_replay``, and ``report()``'s per-scenario skip)
    already convert it into a friendly exit 1, so closing MP-28 needs no new CLI
    plumbing and no new exception handling. Intentionally NOT added to
    ``providers.__all__`` — it is a narrow, test-facing name, not top-level provider API.
    """


class FakeProvider(ProviderAdapter):
    name = "fake"

    def __init__(self, canned: dict[tuple[str, str], Trace] | None = None) -> None:
        # key = (scenario_id, model_id) -> a template Trace
        self.canned = canned or {}
        # Whether a fixtures FILE was the source. Only affects which remedy the
        # zero-coverage message suggests: telling a user to "pass --fixtures" when they
        # just passed one is a message that contradicts them.
        self._loaded_from_file = False

    @classmethod
    def from_fixtures(cls, path: str | Path) -> "FakeProvider":
        """Build a provider from a JSON array of Trace objects (one template per
        scenario_id + model_id). Lets `modelpin baseline`/`modelpin check` run fully offline."""
        records = json.loads(Path(path).read_text())
        canned: dict[tuple[str, str], Trace] = {}
        for record in records:
            trace = Trace(**record)
            canned[(trace.scenario_id, trace.model_id)] = trace
        provider = cls(canned)
        provider._loaded_from_file = True
        return provider

    def preflight(self) -> None:
        """Refuse a run that has no canned traces at all, before any replay happens.

        This is the likeliest way to hit MP-28 — likelier than mistyping a model id,
        because it is just a forgotten flag. `get_adapter("fake")` also lands here.
        """
        if self.canned:
            return None
        if self._loaded_from_file:
            raise MissingCannedTrace(
                "that fixtures file holds no traces, so every replay would miss and the "
                "run could not measure anything. Regenerate it with `modelpin init "
                "--demo`, or point --fixtures at a file containing traces for the models "
                "being compared."
            )
        raise MissingCannedTrace(
            "`--provider fake` replays canned traces and none were supplied, so every "
            "replay would return the same placeholder and the run would report "
            "'unchanged' without measuring anything. Pass `--fixtures <file>` "
            "(`modelpin init --demo` writes one), or choose a real provider."
        )

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        key = (scenario.id, model_id)
        if key in self.canned:
            return self.canned[key].model_copy(update={"run_idx": run_idx})
        raise MissingCannedTrace(
            f"no canned trace for scenario {scenario.id!r} on model {model_id!r}; "
            f"{self._coverage_hint(scenario.id)}. Refusing to invent one: both sides of "
            "the comparison would be identical and the run would report 'unchanged' "
            "without measuring anything."
        )

    def _coverage_hint(self, scenario_id: str) -> str:
        """Name what the fixtures DO cover, so a key mismatch shows its own fix.

        The mismatched-model-id case is the one the user cannot guess: listing the models
        this scenario has traces for turns "it said unchanged" into "I typed the wrong id".
        """
        for_scenario = sorted(model for (sid, model) in self.canned if sid == scenario_id)
        if for_scenario:
            return "the fixtures cover that scenario on: " + ", ".join(for_scenario)
        known = sorted({sid for (sid, _) in self.canned})
        if known:
            return "the fixtures hold no traces for it (they cover: " + ", ".join(known) + ")"
        return "the fixtures hold no traces at all"
