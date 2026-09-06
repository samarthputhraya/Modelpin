"""Cross-provider helpers shared by the adapters and the judge: refusal heuristics and
key-safe secret scrubbing. Kept provider-agnostic so OpenAI and Google behave the same.
"""

from __future__ import annotations

import re

#: Conservative, first-person refusal/decline markers. Refusal is a per-run 0/1 signal
#: that goes through the distributional test, so an occasional miss washes out — only a
#: shift in the refusal *rate* is ever flagged. Kept tight to protect the FP north-star.
#: Every contraction here is written with a plain ASCII apostrophe (U+0027); inbound text
#: is normalized to match (see ``_normalize_for_refusal``), so a model that emits a curly
#: apostrophe doesn't dodge the marker. Markers must be COMPLETE decline phrases — a bare
#: prefix like "i'm sorry, but i can" would misfire on "I'm sorry, but I can help".
REFUSAL_MARKERS: tuple[str, ...] = (
    "i can't",
    "i cannot",
    "i can not",
    "i'm not able",
    "i am not able",
    "i'm unable",
    "i am unable",
    "i won't",
    "i will not",
)

#: Apostrophe-like codepoints that LLMs emit interchangeably for a contraction. We fold all
#: of them to the ASCII apostrophe (U+0027) before matching so ``can't`` and ``can't`` (and
#: the prime/backtick/acute lookalikes) are the *same* string to the detector. This is the
#: calibration fix for the soft-decline inconsistency: two identical polite declines that
#: differed only in apostrophe glyph were being classified refused=False vs True, driving a
#: spurious refusal-rate "regression". Folding is glyph-only — it never widens *which*
#: phrases count as a refusal, so it can't raise false positives on genuine answers. Each
#: variant is annotated with its Unicode name below, so the set stays reviewable even where a
#: viewer renders the glyphs identically — the per-line names are the source of truth.
_APOSTROPHE_VARIANTS: str = (
    "’"  # RIGHT SINGLE QUOTATION MARK (the curly apostrophe in can't / I'm)
    "ʼ"  # MODIFIER LETTER APOSTROPHE
    "′"  # PRIME
    "`"  # GRAVE ACCENT / BACKTICK
    "´"  # ACUTE ACCENT
)
_APOSTROPHE_MAP: dict[int, str] = {ord(ch): "'" for ch in _APOSTROPHE_VARIANTS}

#: Key-shaped tokens to redact from ANY text before it is shown or logged. Covers OpenAI
#: keys (``sk-``/``sk-proj-``) — which also matches Anthropic ``sk-ant-`` — Google
#: credentials in their several prefixes (``AIza...`` API keys, ``ya29.`` / ``AQ.``
#: OAuth-style tokens), Groq keys (``gsk_``, an OpenAI-compatible host we support), and raw
#: ``Bearer`` headers.
_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{4,}"
    r"|gsk_[A-Za-z0-9_\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{10,}"
    r"|ya29\.[A-Za-z0-9_.\-]{10,}"
    r"|AQ\.[A-Za-z0-9_.\-]{10,}"
    r"|Bearer\s+\S+",
    re.IGNORECASE,
)


def _normalize_for_refusal(text: str) -> str:
    """Lowercase and fold apostrophe-like glyphs to ASCII so contraction matching is
    glyph-insensitive. Keeps ``can't`` (curly) and ``can't`` (straight) identical."""
    return (text or "").lower().translate(_APOSTROPHE_MAP)


def looks_like_refusal(text: str) -> bool:
    normalized = _normalize_for_refusal(text)
    return any(marker in normalized for marker in REFUSAL_MARKERS)


def scrub_secrets(text: str) -> str:
    """Redact key-shaped tokens so a secret can never reach the terminal or a log."""
    return _SECRET_RE.sub("[redacted]", text or "")


def contains_secret(text: str) -> bool:
    """Whether `text` holds a key-shaped token, using the SAME pattern as `scrub_secrets`.

    Detection and redaction must never drift apart, which is why this reads `_SECRET_RE`
    rather than re-stating the pattern. `[M] 2026-09-06` (MP-189): every one of
    `scrub_secrets`'s call sites is inside a provider ERROR message, so nothing on the
    baseline WRITE path had ever asked this question -- and a key-shaped token in a model
    output was persisted verbatim into the one file the docs tell users to `git add`.
    """
    return bool(_SECRET_RE.search(text or ""))


#: How much provider error text survives into a Modelpin error message. Long enough for a real
#: 400 body, short enough that the console line stays readable.
ERROR_DETAIL_LIMIT = 300


def elide(text: str, limit: int = ERROR_DETAIL_LIMIT) -> str:
    """Shorten provider text to ``limit`` chars, SAYING SO, and keeping both ends.

    MP-193. This was ``str(exc)[:limit]``, with the result then wrapped in ``[... ].`` -- so an
    over-long message was cut mid-thought and closed with punctuation, producing a sentence
    that reads as complete and is missing the half that says what to do. Providers put the
    remedy at the END (*"...set the parameter tool_choice to auto and retry the request"*), so
    a head-only cut deletes exactly the part the user needs and leaves no sign it did.

    Two properties, and the first is the one that matters:

    1. **The elision is visible.** A silent truncation is worse than a long error: nothing
       tells the reader something was removed, so they act on a partial instruction.
    2. **Both ends survive.** The head carries the error code and type; the tail carries the
       remedy. Keeping only one loses a diagnosis the user cannot reconstruct.
    """
    if len(text) <= limit:
        return text
    # Split the budget between the two ends, leaving room for the marker itself.
    keep = max(1, (limit - 40) // 2)
    dropped = len(text) - 2 * keep
    return f"{text[:keep].rstrip()} [... {dropped} chars elided ...] {text[-keep:].lstrip()}"
