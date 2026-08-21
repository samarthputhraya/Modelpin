"""FakeProvider — replays canned traces for deterministic, offline runs/tests."""

from __future__ import annotations

import json
from pathlib import Path

from modelpin.models import Scenario, Trace
from modelpin.providers.base import ProviderAdapter


class FakeProvider(ProviderAdapter):
    name = "fake"

    def __init__(self, canned: dict[tuple[str, str], Trace] | None = None) -> None:
        # key = (scenario_id, model_id) -> a template Trace
        self.canned = canned or {}

    @classmethod
    def from_fixtures(cls, path: str | Path) -> "FakeProvider":
        """Build a provider from a JSON array of Trace objects (one template per
        scenario_id + model_id). Lets `modelpin baseline`/`modelpin check` run fully offline."""
        records = json.loads(Path(path).read_text())
        canned: dict[tuple[str, str], Trace] = {}
        for record in records:
            trace = Trace(**record)
            canned[(trace.scenario_id, trace.model_id)] = trace
        return cls(canned)

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        key = (scenario.id, model_id)
        if key in self.canned:
            return self.canned[key].model_copy(update={"run_idx": run_idx})
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            final_output="(fake) no canned trace for this scenario/model",
        )
