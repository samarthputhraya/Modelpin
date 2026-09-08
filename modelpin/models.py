"""Canonical typed data models (pydantic v2). See spec section 5."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe_bytes(obj: Any) -> Any:
    """Recursively base64-encode any ``bytes`` so a Trace serializes to JSON.

    Provider tool-loops can stash opaque binary metadata in ``Trace.messages`` (e.g.
    Gemini 3.x ``thought_signature`` bytes, which must stay raw in-memory to feed back to
    the SDK). Those bytes are rarely valid UTF-8, so ``model_dump(mode="json")`` — used
    when persisting a baseline — would otherwise raise ``UnicodeDecodeError``.
    """
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    if isinstance(obj, dict):
        return {k: _json_safe_bytes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_bytes(v) for v in obj]
    return obj


class ModelStatus(str, Enum):
    active = "active"
    deprecated = "deprecated"
    retired = "retired"


class IncompleteReason(str, Enum):
    """Why a run did NOT end with the model's own finished answer.

    ``None`` means NOT RECORDED — it does **not** mean "complete". Every baseline written
    before this field existed is ``None``, as is every trace from an OpenAI-compatible host
    that omits ``finish_reason``.

    **Nothing in the diff gates on this field.** It is recorded and reported only, the same
    shape ADR-0003 gives latency and tokens. A run can be incomplete and still carry a
    perfectly comparable answer, and the rate at which that happens has never been measured —
    MP-54 is what produces that data. See ADR-0018.
    """

    max_tokens = "max_tokens"  # the token budget cut the answer off
    tool_turns = "tool_turns"  # our MAX_TOOL_TURNS cap, or the provider's, ended the loop
    content_filter = "content_filter"  # provider blocked or filtered (also sets `refused`)
    malformed_tool_call = "malformed_tool_call"  # the model emitted an invalid tool call
    provider_other = "provider_other"  # a stop reason this version does not recognise


class Model(BaseModel):
    """A provider model and its lifecycle status."""

    id: str
    provider: str
    family: Optional[str] = None
    status: ModelStatus = ModelStatus.active
    released_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None
    replacement_id: Optional[str] = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Assertion(BaseModel):
    """What the ENGINE can check about a scenario's output.

    `expected_tool_calls` and `output_schema` used to live here and were consulted by
    NOTHING (MP-142, proved by differential over five trace configurations, not by grep).
    They were deleted in MP-147, which is why `examples/report-suite` moved to suite version
    3.0.0: `compute_suite_hash` hashes the VALIDATED model, so removing a field is a suite
    change even though no scenario's meaning moved.

    Nothing was lost with them. `expected_tool_calls` was redundant with a channel that
    works: the tool-TRAJECTORY diff is what actually measures whether a scenario called the
    tool it should have, and it does so distributionally over N runs instead of by a static
    list. A field that silently does nothing is the same class of defect as a green tick
    over an unmeasured run -- so the fix is to remove it, not to keep documenting it.

    Every field below is READ by `diff/structural.py::violates_text_assertions`. Adding one
    that is not is the defect this docstring exists to prevent a second time.
    """

    must_contain: Optional[list[str]] = None
    must_not_contain: Optional[list[str]] = None


#: The tool-call trajectory match modes. **Mirrors `diff/structural.py::MatchMode`**, which
#: cannot be imported here: `structural.py` imports `Trace` from this module, so the arrow
#: only points one way. `test_the_three_match_mode_declarations_agree` pins this literal
#: against both `MatchMode` and `cli.VALID_MATCH_MODES`, so the mirror cannot drift silently
#: -- the same failure shape MP-03 (three copies of `DEFAULT_RUNS`) and MP-204 (two copies of
#: the version) both were.
MatchModeName = Literal["strict", "unordered", "subset", "superset"]

MATCH_MODES: tuple[str, ...] = ("strict", "unordered", "subset", "superset")


class Scenario(BaseModel):
    """A representative case for a user's app (a single prompt or an agent run)."""

    id: str
    name: str
    kind: Literal["single", "agent"] = "single"
    input: dict[str, Any]  # { "messages": [...], "tools": [...]? }
    assertions: Optional[Assertion] = None
    #: How this scenario's tool-call trajectory is compared, overriding the global `--match`
    #: for this scenario alone. `None` (the default) means "use whatever the run was given",
    #: which is how every scenario written before MP-227 behaves.
    #:
    #: **Why this is per-scenario and not a better global default.** `[M] 2026-09-07`,
    #: `reports/channel-exposure/2026-09-07/v2a-*.jsonl`: `optional_notify_after_status_update`
    #: tells the model of its second tool *"use it when it would be useful"*, the model sent
    #: the courtesy email on 4 of 5 baseline samples and 0 of 5 candidate samples, and the
    #: engine published `regression` @ 0.952 -- exit 1, a red build -- over a SAME-MODEL,
    #: SAME-PROMPT null. That is the north-star promise inverted (MP-220). `[M]` Under
    #: `subset` that trial does not fire at all. But `[M]` making `subset` the GLOBAL default
    #: exposes only 3 of the 10 detection rows, costing 7 real detections, so the relation has
    #: to be declared by the scenario that actually holds it, not chosen once for a whole
    #: suite. A prompt that says "call this when useful" is *stating* a subset relation; this
    #: field is where it gets written down.
    #:
    #: **It is a COMPARISON directive, not scenario content**, and that distinction is
    #: load-bearing in two places:
    #:   * `report/suite.py::scenario_fingerprint` excludes it, so declaring `match` does not
    #:     invalidate a baseline the user already paid to record (ADR-0039), and
    #:   * nothing under `replay/` or `providers/` reads it -- it cannot change a single byte
    #:     sent to a provider, which is why it is safe outside `modelpin/diff/`'s freeze
    #:     (ADR-0030 D1). No threshold moves and nothing is fitted on a scored corpus.
    match: Optional[MatchModeName] = None

    @model_validator(mode="after")
    def _check_input_shape(self) -> "Scenario":
        """Fail fast on malformed scenarios so a bad file can't reach a paid API call.

        ``messages`` must be present and a list (an empty list is allowed for the
        offline/fake path); ``tools``, when present, must be a list.
        """
        messages = self.input.get("messages")
        if not isinstance(messages, list):
            raise ValueError(
                f"scenario {self.id!r}: input.messages must be a list of message dicts"
            )
        if "tools" in self.input and not isinstance(self.input["tools"], list):
            raise ValueError(f"scenario {self.id!r}: input.tools must be a list when present")
        return self


class Trace(BaseModel):
    """The recorded behavior of one model on one scenario, for one run."""

    scenario_id: str
    model_id: str
    run_idx: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_output: str = ""
    refused: bool = False
    #: Why the run ended early, or None when it finished normally OR was never recorded.
    #: Recording only — see IncompleteReason. Added after 0.1.2, so older baselines read None.
    incomplete_reason: Optional[IncompleteReason] = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    ts: datetime = Field(default_factory=_utcnow)

    @field_validator("incomplete_reason", mode="before")
    @classmethod
    def _tolerate_unknown_reason(cls, v: Any) -> Any:
        """A newer Modelpin's baseline must never read as CORRUPT to an older one.

        `storage.load_baseline` turns any ValidationError into
        ``BaselineError("... is corrupt ... Delete it and re-run")`` — i.e. a version skew
        would cost the user their recorded baseline. An unrecognised reason degrades to
        ``provider_other`` instead of raising.
        """
        if isinstance(v, str) and v not in {m.value for m in IncompleteReason}:
            return IncompleteReason.provider_other
        return v

    @field_serializer("messages")
    def _serialize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep opaque provider bytes (e.g. Gemini ``thought_signature``) JSON-safe on dump."""
        return [_json_safe_bytes(m) for m in messages]


class Baseline(BaseModel):
    """N recorded traces of the current (known-good) model on a scenario."""

    scenario_id: str
    model_id: str
    traces: list[Trace] = Field(default_factory=list)
    summary_stats: dict[str, Any] = Field(default_factory=dict)


class DiffVerdict(str, Enum):
    unchanged = "unchanged"
    changed_minor = "changed_minor"
    regression = "regression"
    #: The run could not be measured — one side recorded no behavior to compare. NOT the same
    #: as "no change": see ADR-0018. Deliberately NOT named for low N; MP-55 extends this
    #: member's predicate rather than adding a fifth.
    insufficient_evidence = "insufficient_evidence"


class DiffSignals(BaseModel):
    #: The tool-NAME trajectory distance, and only that: ``1 - tool_tvd``. It was briefly
    #: the worse of the two tool sub-signals (``1 - max(tool_tvd, arg_tvd)``) in 0.2.0, which
    #: published `Tool match 0.00` beside an `unchanged` verdict whenever a free-text argument
    #: jittered under identical tool names. The argument payload is `tool_arg_match` below.
    #: MP-74.
    tool_call_match: Optional[float] = None
    #: The argument sub-signal alone. ``None`` means the argument gate did not RUN -- either
    #: no run on either side carried arguments, or the name trajectory was not stable enough
    #: to compare them. ``None`` is NOT 1.0; it means "not measured", the same distinction
    #: ADR-0018 draws for IncompleteReason.
    tool_arg_match: Optional[float] = None
    format_valid: Optional[bool] = None
    refusal_delta: Optional[float] = None
    semantic_score: Optional[float] = None
    latency_delta_ms: Optional[float] = None
    token_delta: Optional[int] = None
    #: How many runs on each side recorded nothing at all, and out of how many. Always
    #: populated so a reader can see degradation BELOW the abstention threshold, which is
    #: the largest gap this fix deliberately leaves open (ADR-0018).
    degenerate_baseline: Optional[int] = None
    degenerate_candidate: Optional[int] = None
    baseline_runs: Optional[int] = None
    candidate_runs: Optional[int] = None


class DiffResult(BaseModel):
    """The behavioral-diff verdict for one scenario across two models."""

    scenario_id: str
    from_model: str
    to_model: str
    verdict: DiffVerdict
    signals: DiffSignals = Field(default_factory=DiffSignals)
    confidence: float = 0.0
    explanation: str = ""


class CheckRun(BaseModel):
    """A full migration check across scenarios (hosted phase persists these)."""

    id: str
    repo: Optional[str] = None
    from_model: str
    to_model: str
    results: list[DiffResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
