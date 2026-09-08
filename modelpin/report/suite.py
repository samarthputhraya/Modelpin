"""Public report-suite helpers: a reproducible content hash + a manifest reader.

The public Modelpin Report must be reproducible (spec section 9): a reader needs to know
*exactly* which scenarios produced it. We pin that with a content hash over the **validated**
scenarios (not raw file bytes), so whitespace / key-order churn in the JSON files never
changes the hash, but any semantic scenario change does.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from modelpin.models import Scenario

#: Algorithm name prefixing every emitted hash, so the digest is self-describing.
_HASH_ALGO = "sha256"
#: Hex chars of the digest to surface — enough to pin the suite, short enough for a header.
_HASH_LEN = 12

#: Fallbacks when a suite directory has no (readable) manifest.json. `read_manifest` never
#: raises, so ANY unmanifested directory lands here — and with `--suite-dir` now supplied by
#: the user, defaulting to the official id would let any local folder publish a report
#: header claiming to be the open public suite.
DEFAULT_SUITE_ID = "local-suite"
DEFAULT_SUITE_VERSION = "unversioned"


#: Scenario fields excluded from the content hash because they describe how a recording is
#: COMPARED, not what is recorded. See `compute_suite_hash`.
_NON_BEHAVIOURAL_FIELDS = frozenset({"match"})


def compute_suite_hash(scenarios: list[Scenario]) -> str:
    """A deterministic content fingerprint of a scenario suite.

    Hashes the *validated* pydantic models (sorted by id, canonical JSON) rather than raw
    file bytes, so reformatting a scenario file does not change the hash but editing its
    meaning does. Returns e.g. ``"sha256:1a2b3c4d5e6f"``.

    **`match` is excluded, and that is not an oversight (MP-227).** This function is also
    `scenario_fingerprint`, which ADR-0039 uses to answer one question: *does this recorded
    baseline describe the scenario it is about to be compared against?* `Scenario.match`
    changes how two recordings are compared and cannot change a byte of either -- nothing
    under `replay/` or `providers/` reads it. Including it would have made a user who adds
    ``"match": "subset"`` to stop a false red build find every scenario in their store
    reported stale and be told to pay to re-record traces that were never wrong. `[M]`
    Excluding it also keeps every fingerprint written before MP-227 valid: a `None`-valued
    field would otherwise have serialised as ``"match": null`` into *every* scenario's dump
    and marked *every* existing baseline stale on upgrade, so that `modelpin check` abstained for
    every user who did nothing at all.

    The cost is that the hash alone no longer distinguishes two runs that differed only in a
    `match` declaration, so the published Report discloses the modes separately -- see
    `report.render_markdown`'s "Tool-call match mode" row, which names each override.
    """
    canonical = "\n".join(
        json.dumps(
            s.model_dump(mode="json", exclude=set(_NON_BEHAVIOURAL_FIELDS)),
            sort_keys=True,
            separators=(",", ":"),
        )
        for s in sorted(scenarios, key=lambda s: s.id)
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_HASH_ALGO}:{digest[:_HASH_LEN]}"


def scenario_fingerprint(scenario: Scenario) -> str:
    """A content fingerprint of ONE scenario definition — what a baseline records so it can
    later say whether it describes the scenario it is being compared against (MP-05).

    Deliberately `compute_suite_hash` of a one-element suite rather than a second hashing
    routine: two ways to fingerprint the same object is how the two drift, and the property
    that matters here is exactly the one that function already has — it hashes the VALIDATED
    model, so reformatting a scenario file does not change the hash but editing its meaning
    does.
    """
    return compute_suite_hash([scenario])


def slug(text: str) -> str:
    """Filesystem-safe slug for building report filenames from model ids."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


def read_manifest(suite_dir: str | Path) -> tuple[str, str]:
    """Best-effort ``(suite_id, suite_version)`` from ``<suite_dir>/manifest.json``.

    Never raises — a missing or malformed manifest falls back to documented defaults, so a
    report can still be generated from a bare directory of scenario files.
    """
    path = Path(suite_dir) / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return (DEFAULT_SUITE_ID, DEFAULT_SUITE_VERSION)
        # Bound the lengths: these strings go verbatim into the published report, so an
        # oversized manifest value can't bloat the artifact.
        return (
            str(data.get("suite_id", DEFAULT_SUITE_ID))[:128],
            str(data.get("suite_version", DEFAULT_SUITE_VERSION))[:64],
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return (DEFAULT_SUITE_ID, DEFAULT_SUITE_VERSION)
