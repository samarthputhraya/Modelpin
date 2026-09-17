"""Validate `data/models.json` and keep `modelpin/watcher/registry.py` identical to it.

    python scripts/registry_check.py           # validate, and confirm the Python mirror matches
    python scripts/registry_check.py --sync    # regenerate the SEED block in registry.py from the JSON

Exit 1 on any problem, printed one per line. The checks that `modelpin.models.Model` cannot make
on its own live here: the schema number, duplicate ids and alias collisions, a `replacement_id`
that names nothing, a `fetched_at` in the future, an unknown key (pydantic ignores extras, so a
typo such as `retired_on` would otherwise vanish silently), and the mirror.

The wheel ships code only (ADR-0011), so the registry that actually ships is the Python literal;
this script is the only sanctioned way to change it. Contributors edit the JSON.
"""

from __future__ import annotations

import argparse
import json
import pprint
import subprocess
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modelpin.models import Model  # noqa: E402
from modelpin.watcher import REGISTRY_SCHEMA  # noqa: E402

JSON_PATH = ROOT / "data" / "models.json"
MIRROR_PATH = ROOT / "modelpin" / "watcher" / "registry.py"
BEGIN = "# --- BEGIN SEED"
END = "# --- END SEED ---"


def validate(raw: object, *, today: date | None = None) -> list[str]:
    """Every problem with the parsed JSON, as a list of sentences. Empty means valid."""
    today = today or date.today()
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ["the registry is not a JSON object"]
    if raw.get("schema") != REGISTRY_SCHEMA:
        problems.append(f"`schema` must be {REGISTRY_SCHEMA}, found {raw.get('schema')!r}")
    models = raw.get("models")
    if not isinstance(models, list):
        return problems + ["`models` must be a list"]

    allowed = set(Model.model_fields)
    ids: dict[str, int] = {}
    names: dict[str, str] = {}  # every id and alias -> the id that owns it
    for i, entry in enumerate(models):
        if not isinstance(entry, dict):
            problems.append(f"models[{i}] is not an object")
            continue
        where = f"models[{i}] ({entry.get('id', '?')})"
        unknown = sorted(set(entry) - allowed)
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")
        try:
            model = Model(**entry)
        except (ValidationError, TypeError) as exc:
            problems.append(f"{where}: {exc}")
            continue
        if model.id in ids:
            problems.append(f"{where}: duplicate id (first at models[{ids[model.id]}])")
        ids[model.id] = i
        for name in (model.id, *model.aliases):
            owner = names.get(name)
            if owner is not None and owner != model.id:
                problems.append(f"{where}: {name!r} is already the id or an alias of {owner!r}")
            names.setdefault(name, model.id)
        # One calendar day of skew is not the future: a page fetched on the 18th in Kolkata
        # is dated the 18th while a UTC runner is still on the 17th. `[M] 2026-09-17T19:26Z`
        # CI rejected the 30 entries fetched that evening in India for exactly this reason.
        if model.fetched_at is not None and (model.fetched_at - today).days > 1:
            problems.append(f"{where}: fetched_at {model.fetched_at} is in the future")
        if model.source_url is not None and not model.source_url.startswith("https://"):
            problems.append(f"{where}: source_url must be https, found {model.source_url!r}")

    for i, entry in enumerate(models):
        if isinstance(entry, dict) and entry.get("replacement_id"):
            rid = entry["replacement_id"]
            if rid not in names:
                problems.append(
                    f"models[{i}] ({entry.get('id')}): replacement_id {rid!r} names no entry"
                )
    return problems


def mirror_problems(raw: dict) -> list[str]:
    """Is the Python that ships identical to the JSON contributors edit: entries AND rule file?"""
    from modelpin.watcher.registry import REGISTRY_NOTE, SEED_MODELS

    problems = []
    if SEED_MODELS != raw["models"]:
        problems.append(
            "modelpin/watcher/registry.py::SEED_MODELS differs from data/models.json; run "
            "`python scripts/registry_check.py --sync`"
        )
    if REGISTRY_NOTE != raw.get("_note", ""):
        problems.append(
            "modelpin/watcher/registry.py::REGISTRY_NOTE differs from data/models.json's "
            "`_note`; run `python scripts/registry_check.py --sync`"
        )
    return problems


def sync(raw: dict) -> None:
    """Rewrite the SEED block in registry.py from the JSON, then black it."""
    text = MIRROR_PATH.read_text(encoding="utf-8")
    start = text.index(BEGIN)
    end = text.index(END)
    header_end = text.index("\n", start) + 1
    note = pprint.pformat(raw.get("_note", ""), width=96)
    literal = pprint.pformat(raw["models"], width=96, sort_dicts=False)
    block = f"REGISTRY_NOTE: str = {note}\nSEED_MODELS: list[dict[str, Any]] = {literal}\n"
    MIRROR_PATH.write_text(text[:header_end] + block + text[end:], encoding="utf-8", newline="\n")
    subprocess.run([sys.executable, "-m", "black", "-q", str(MIRROR_PATH)], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sync", action="store_true", help="regenerate the Python mirror")
    parser.add_argument("--json", default=str(JSON_PATH), help="registry JSON to check")
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.json).read_text(encoding="utf-8"))
    problems = validate(raw)
    if problems:
        print("registry INVALID:")
        for p in problems:
            print(f"  - {p}")
        return 1
    models = raw["models"]
    if args.sync:
        sync(raw)
        print(f"registry.py regenerated from {args.json}: {len(models)} entries")
        return 0
    problems = mirror_problems(raw)
    if problems:
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"registry ok: {len(models)} entries, mirror identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
