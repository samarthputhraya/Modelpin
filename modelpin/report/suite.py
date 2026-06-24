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

#: Fallbacks when a suite directory has no (readable) manifest.json.
DEFAULT_SUITE_ID = "modelpin-public-suite"
DEFAULT_SUITE_VERSION = "unversioned"


def compute_suite_hash(scenarios: list[Scenario]) -> str:
    """A deterministic content fingerprint of a scenario suite.

    Hashes the *validated* pydantic models (sorted by id, canonical JSON) rather than raw
    file bytes, so reformatting a scenario file does not change the hash but editing its
    meaning does. Returns e.g. ``"sha256:1a2b3c4d5e6f"``.
    """
    canonical = "\n".join(
        json.dumps(s.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        for s in sorted(scenarios, key=lambda s: s.id)
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_HASH_ALGO}:{digest[:_HASH_LEN]}"


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
