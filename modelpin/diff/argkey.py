"""A total, deterministic, order-insensitive, hashable key for tool-call arguments.

``stats.py`` types its inputs ``Sequence[Hashable]`` and buckets them in a ``Counter``, so a
``dict`` cannot reach the distributional test unencoded. That forces an encoding — and the
obvious ones are false-positive channels. Encoding is therefore where MP-04 spends or protects
the north-star metric, not an implementation detail.

[M] The key-order channel is REAL, not theoretical: ``providers/openai.py`` parses the model's
arguments with ``json.loads``, which preserves whatever order the model emitted, so the same
model can emit the same payload with keys in a different order across runs of the SAME side.
``sort_keys=True`` is what makes that jitter invisible.

What this deliberately does NOT normalize, and why: string values are compared verbatim (no
case-folding, no unicode normalization, no whitespace stripping). Each of those would close an
unmeasured false-positive channel at the cost of an unmeasured false-negative one, and this
repo does not have the corpus to price either — [M] 5 of 65 non-empty tool calls in
docs/reports/data/ + examples/ carry any arguments at all, and all 5 are identical.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

#: Deeper than any real tool payload. Makes a self-referential structure terminate rather
#: than raise — this function's contract is that it never raises.
_MAX_DEPTH = 64

#: Beyond 2**53 a float cannot represent an integer exactly, so int/float unification has to
#: stop there or two distinct large ints would collide into one key (a false NEGATIVE).
_SAFE_INT = 2**53


def _canon(value: Any, depth: int) -> Any:
    """Map one argument value onto a JSON-safe, order-insensitive normal form."""
    if depth > _MAX_DEPTH:
        return "<max-depth>"
    # bool BEFORE int is LOAD-BEARING and must not be tidied away: isinstance(True, int) is
    # True, True == 1, and hash(True) == hash(1) — so a bool falling through to the int
    # branch would merge a FLAG with a QUANTITY inside the Counter in stats.py. That is a
    # silent false negative, the failure mode this whole row exists to remove.
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value) if -_SAFE_INT < value < _SAFE_INT else f"<int:{value}>"
    if isinstance(value, float):
        # A baseline is persisted through model_dump(mode="json"), which turns nan/inf into
        # None. Match that here, or a fresh candidate could never equal its own baseline.
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        # str() the keys: a nested non-str key survives pydantic's dict[str, Any] and would
        # make json.dumps(sort_keys=True) raise TypeError. When two distinct keys collide
        # under str(), fall back to a pair LIST so neither entry is silently dropped.
        pairs = sorted((str(k), _canon(v, depth + 1)) for k, v in value.items())
        names = [k for k, _ in pairs]
        if len(set(names)) == len(names):
            return dict(pairs)
        return [[k, v] for k, v in pairs]
    if isinstance(value, (set, frozenset)):
        return sorted((_canon(v, depth + 1) for v in value), key=repr)
    if isinstance(value, Sequence):  # list, tuple, duck-typed SDK sequences
        return [_canon(v, depth + 1) for v in value]
    # Never repr(): a default repr embeds a memory address, which would mint a fresh key on
    # every run and turn one unrecognised type into a permanent false positive.
    return f"<{type(value).__name__}>"


def canonical_arguments(arguments: Mapping[str, Any] | None) -> str:
    """Hashable canonical form of ONE tool call's arguments. Never raises.

    DELIBERATE BLIND SPOT: a pure int/float type flip (``qty=1`` -> ``qty=1.0``) is invisible
    here. The spelling is not attributable to the model — ``providers/openai.py`` json.loads()
    a string the model wrote, while ``providers/google.py`` passes an already-typed SDK dict
    through — so the same model keys differently depending only on which adapter saw it.
    Pinned by tests/test_tool_arguments.py.
    """
    try:
        return json.dumps(
            _canon(arguments or {}, 0),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,  # cp1252-safe on a Windows console (MP-33)
        )
    except Exception:  # pragma: no cover — _canon emits only JSON-safe values
        return "<unencodable>"


#: Argument VALUES are model-authored and land in a PR comment. They are exactly where a
#: customer email, an order id or a key-shaped token lives, so the explanation never prints a
#: string value — only its argument path, and only numbers and booleans verbatim.
_SAFE_PATH = "".join(chr(c) for c in range(32, 127) if chr(c).isalnum() or chr(c) in "_.-")
_MAX_PATHS = 4


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a canonical payload to ``path -> scalar`` so two payloads can be diffed."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(_leaves(v, f"{prefix}[{i}]"))
    else:
        out[prefix or "()"] = value
    return out


def _safe_path(path: str) -> str:
    cleaned = "".join(c if c in _SAFE_PATH else "_" for c in path)
    return cleaned[:48]


def _safe_value(value: Any) -> str:
    """Numbers and booleans print verbatim; strings never do.

    Integral floats render without the trailing ".0": every int is unified to float on the
    way into the KEY, and echoing that artefact back at a developer who wrote amount: 5
    reads as a change that did not happen.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return "(changed)"


def describe_argument_change(base_seq: Any, cand_seq: Any) -> str:
    """A human-readable, leak-free summary of how two modal argument payloads differ.

    Computed by a DIFFERENT function than the comparison key on purpose: the key must be
    exhaustive, the explanation must be safe. ``report/__init__.py`` drops this string whole
    into a Markdown table in a PR comment.
    """
    parts: list[str] = []
    for i in range(max(len(base_seq), len(cand_seq))):
        b = base_seq[i] if i < len(base_seq) else None
        c = cand_seq[i] if i < len(cand_seq) else None
        if b == c:
            continue
        present = c if c is not None else b
        if present is None:  # pragma: no cover -- b == c already covers both-None
            continue
        name = present[0]
        try:
            bl = _leaves(json.loads(b[1])) if b else {}
            cl = _leaves(json.loads(c[1])) if c else {}
        except Exception:  # pragma: no cover — keys are produced by canonical_arguments
            parts.append(f"{name}(arguments changed)")
            continue
        if b and c and b[0] != c[0]:
            parts.append(f"{b[0]} -> {c[0]}")
            continue
        clauses: list[str] = []
        for path in sorted(set(bl) | set(cl))[:_MAX_PATHS]:
            if path not in cl:
                clauses.append(f"dropped {_safe_path(path)}")
            elif path not in bl:
                clauses.append(f"added {_safe_path(path)}")
            elif bl[path] != cl[path]:
                old_v, new_v = _safe_value(bl[path]), _safe_value(cl[path])
                clauses.append(
                    f"{_safe_path(path)} (changed)"
                    if old_v == new_v == "(changed)"
                    else f"{_safe_path(path)} {old_v}->{new_v}"
                )
        if clauses:
            parts.append(f"{name}({'; '.join(clauses)})")
    return "; ".join(parts) if parts else "tool-call arguments changed"
