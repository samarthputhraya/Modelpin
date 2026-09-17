"""Watcher: which of the models a repo depends on are retiring, when, and what to check next.

The registry is embedded in ``registry.py`` (the wheel ships code only, ADR-0011) and mirrored
by ``data/models.json`` for contributors; ``scripts/registry_check.py --sync`` keeps the two
identical and ``tests/test_watcher.py`` pins it. Every dated entry names the vendor page it was
read from and the day it was read (MP-276); ``Model`` refuses one that does not, so an unsourced
date cannot enter from the seed or from a ``--registry`` file.

Nothing here touches the network. ``mp watch`` reads the embedded seed, or a newer JSON passed
with ``--registry``. Fetching is not implemented anywhere in the package; a scheduled workflow
that downloads a newer JSON and passes it in is the intended place for it, never this module.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from modelpin.models import Model, ModelStatus
from modelpin.watcher.registry import SEED_MODELS

#: The ``data/models.json`` layout this version reads. A file without a ``schema`` key still
#: loads; its entries simply carry no dates.
REGISTRY_SCHEMA = 2


class RegistryError(Exception):
    """A registry file could not be read, or one of its entries fails validation."""


def load_registry(path: Optional[Path] = None) -> list[Model]:
    """The model registry: the embedded seed, or an explicit override file.

    ``path=None`` returns the shipped seed -- no filesystem access, so this cannot depend on
    the caller's working directory (an earlier version searched ``./data/models.json``,
    which let an unrelated file in the user's cwd redefine the registry).
    """
    if path is None:
        return [Model(**m) for m in SEED_MODELS]
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"could not read the registry at {path} (--registry): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"the registry at {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise RegistryError(f"the registry at {path} has no `models` list")
    try:
        return [Model(**m) for m in raw["models"]]
    except ValidationError as exc:
        # The validator's own sentence is the message; pydantic's `[type=value_error, ...]`
        # trailer and its docs link are not in the user's vocabulary.
        reasons = "; ".join(
            str(err.get("msg", "")).removeprefix("Value error, ") for err in exc.errors()
        )
        raise RegistryError(f"the registry at {path} failed validation: {reasons}") from exc
    except TypeError as exc:
        raise RegistryError(f"the registry at {path} failed validation: {exc}") from exc


def get_model(model_id: str, registry: Optional[list[Model]] = None) -> Optional[Model]:
    """The entry for ``model_id``, by its id or by any alias the vendor lists for it."""
    reg = registry if registry is not None else load_registry()
    return next((m for m in reg if m.id == model_id or model_id in m.aliases), None)


def deprecations(registry: Optional[list[Model]] = None) -> list[Model]:
    reg = registry if registry is not None else load_registry()
    return [m for m in reg if m.status in (ModelStatus.deprecated, ModelStatus.retired)]


def _today() -> date:
    """Today's UTC calendar date. A function so tests can pin the calendar."""
    return datetime.now(timezone.utc).date()


#: How close a vendor-published shutdown date has to be before an otherwise-active row counts
#: as inside its notice window. Google publishes an "earliest possible" shutdown date for some
#: models at release, a year out; alarming on that from day one would make every such model
#: read as retiring forever. Ninety days is longer than any notice period the three vendors
#: publish as a floor (Anthropic 60 days; OpenAI 2 weeks for previews), so a real notice is
#: always inside it.
NOTICE_DAYS = 90


def effective_status(model: Model, today: Optional[date] = None) -> ModelStatus:
    """The vendor's status, read against the calendar.

    A shutdown date that has passed is ``retired`` whatever the row still says. A row the
    vendor marked deprecated, or whose announced deprecation date has arrived, or whose
    shutdown date is within ``NOTICE_DAYS``, is inside its notice window: ``deprecated``. A
    row with a shutdown date further out is listed with that date but stays ``active``.
    """
    today = today if today is not None else _today()
    if model.status is ModelStatus.retired or (
        model.retired_at is not None and today >= model.retired_at
    ):
        return ModelStatus.retired
    if model.status is ModelStatus.deprecated:
        return ModelStatus.deprecated
    if model.deprecated_at is not None and today >= model.deprecated_at:
        return ModelStatus.deprecated
    if model.retired_at is not None and (model.retired_at - today).days <= NOTICE_DAYS:
        return ModelStatus.deprecated
    return ModelStatus.active


def days_remaining(model: Model, today: Optional[date] = None) -> Optional[int]:
    """Days until the shutdown date (negative once it has passed); None when none is announced."""
    if model.retired_at is None:
        return None
    return (model.retired_at - (today if today is not None else _today())).days


def newest_fetch(registry: Iterable[Model]) -> Optional[date]:
    """The most recent day any entry was read from its vendor page: the registry's freshness."""
    dates = [m.fetched_at for m in registry if m.fetched_at is not None]
    return max(dates) if dates else None


@dataclass(frozen=True)
class Declared:
    """One model id this repo depends on, and where that dependency was seen."""

    id: str
    source: str  # "config" | "scan" | "baseline"
    role: str = "app"  # "app" | "judge"
    #: Whether this sighting may decide the exit code. A stored baseline never does: its store
    #: key can be fictional by design (ADR-0021's ``modelpin-dogfood``), so an unknown
    #: baseline-only id is listed, not alarmed on.
    decides_exit: bool = True


@dataclass(frozen=True)
class WatchRow:
    """One model this repo depends on, crossed with the registry."""

    id: str
    sources: tuple[str, ...]
    role: str
    decides_exit: bool
    model: Optional[Model]
    effective: Optional[ModelStatus]
    days_remaining: Optional[int]
    replacement_id: Optional[str]
    replacement: Optional[Model]
    has_baseline: bool
    baseline_path: Optional[str]

    @property
    def known(self) -> bool:
        return self.model is not None

    @property
    def affected(self) -> bool:
        """Inside its notice window, or already retired."""
        return self.effective is not None and self.effective is not ModelStatus.active

    @property
    def replacement_provider(self) -> Optional[str]:
        if self.replacement is not None:
            return self.replacement.provider
        return self.model.provider if self.model is not None and self.replacement_id else None


def assess(
    declared: Iterable[Declared],
    registry: list[Model],
    *,
    today: Optional[date] = None,
    baselines: Optional[Mapping[str, str]] = None,
) -> list[WatchRow]:
    """Cross the models a repo declares with the registry. Pure; the CLI renders.

    One row per id, sources merged, in first-seen order. An id declared as the app's model AND
    as the judge is an app row. ``baselines`` maps a model id to the path of its stored
    baseline; presence only sets ``has_baseline`` (the Action keys on it), never the exit.
    """
    today = today if today is not None else _today()
    stores = dict(baselines or {})
    order: list[str] = []
    seen: dict[str, list[Declared]] = {}
    for d in declared:
        if d.id not in seen:
            order.append(d.id)
            seen[d.id] = []
        seen[d.id].append(d)
    rows: list[WatchRow] = []
    for mid in order:
        sightings = seen[mid]
        model = get_model(mid, registry)
        replacement_id = model.replacement_id if model is not None else None
        rows.append(
            WatchRow(
                id=mid,
                sources=tuple(dict.fromkeys(d.source for d in sightings)),
                role="app" if any(d.role == "app" for d in sightings) else "judge",
                decides_exit=any(d.decides_exit for d in sightings),
                model=model,
                effective=effective_status(model, today) if model is not None else None,
                days_remaining=days_remaining(model, today) if model is not None else None,
                replacement_id=replacement_id,
                replacement=get_model(replacement_id, registry) if replacement_id else None,
                has_baseline=mid in stores,
                baseline_path=stores.get(mid),
            )
        )
    return rows


def watch_exit_code(rows: Iterable[WatchRow]) -> int:
    """1 if any exit-driving row is affected; else 3 if any is unknown; else 0.

    Affected outranks unknown, as a regression outranks an unmeasurable scenario in ``check``.
    Unknown is not a clearance: the registry cannot vouch for a model it has never heard of,
    and a cron that stayed green on an unknown id would be the false comfort MP-55 removed.
    """
    driving = [r for r in rows if r.decides_exit]
    if any(r.affected for r in driving):
        return 1
    if any(not r.known for r in driving):
        return 3
    return 0
