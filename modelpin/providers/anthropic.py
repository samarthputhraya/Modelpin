"""Anthropic (Claude) adapter via the official ``anthropic`` SDK's Messages API.

Shapes verified against the INSTALLED SDK (``anthropic`` 1.0.0) and the Messages API docs
(`[S] 2026-09-15` platform.claude.com/docs/en/api/messages,
.../agents-and-tools/tool-use/handle-tool-calls): ``client.messages.create(model, max_tokens,
messages, system, tools)``; ``response.content`` is a list of typed blocks (``text``,
``tool_use`` with ``.id``/``.name``/``.input`` -- already a dict, unlike OpenAI's JSON string --
and ``thinking``/``redacted_thinking``); ``response.stop_reason``; token usage in
``response.usage.{input,output}_tokens``. Tool results go back as a USER message of
``tool_result`` blocks keyed by ``tool_use_id``; there is no ``tool`` role and no ``system``
role in ``messages``.

Two credential BACKENDS, both the end user's own (ADR-0008):
- ``ANTHROPIC_API_KEY`` -> ``anthropic.Anthropic`` (the first-party API);
- ``ANTHROPIC_VERTEX_PROJECT_ID`` -> ``anthropic.AnthropicVertex``, authenticated by Google
  Application Default Credentials. The env names are the SDK's own, so a user who already runs
  Claude on Vertex needs no Modelpin-specific setup.

The SDK import stays lazy and the client is injectable, so the package imports without the SDK
and tests run with no network or key.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from modelpin.models import IncompleteReason, Scenario, ToolCall, Trace
from modelpin.providers._common import elide, looks_like_refusal, scrub_secrets
from modelpin.providers.base import ProviderAdapter, ProviderError

#: Same cap and canned default as the OpenAI adapter, so a trajectory means the same thing
#: whichever vendor produced it.
MAX_TOOL_TURNS = 6
_DEFAULT_TOOL_RESULT: dict[str, Any] = {"status": "ok"}

#: The SDK's own retry budget, raised for the same reason and in the same way as
#: ``openai.REPLAY_MAX_RETRIES`` (MP-139): the SDK already retries 408/409/429/5xx (529
#: ``overloaded_error`` included) honouring ``retry-after``, so raising ITS budget composes
#: with nothing, where a loop of ours would multiply by it and again by ``MAX_TOOL_TURNS``.
#: `[M]` installed SDK: ``_base_client._should_retry`` retries 408, 409, 429 and >= 500.
REPLAY_MAX_RETRIES = 5

API_KEY_ENV = "ANTHROPIC_API_KEY"
#: Read by ``AnthropicVertex`` itself (`[M]` ``lib/vertex/_client.py``), and by us to choose it.
VERTEX_PROJECT_ENV = "ANTHROPIC_VERTEX_PROJECT_ID"
VERTEX_REGION_ENV = "CLOUD_ML_REGION"

#: ``max_tokens`` is REQUIRED by the Messages API (it is a required keyword in the SDK's
#: ``create``); OpenAI and Gemini let us omit it. 4096 rather than a tighter number because
#: current Claude models think by default (`[S]` Opus 5 runs adaptive thinking when
#: ``thinking`` is omitted) and thinking tokens count against this cap -- a small default
#: would truncate answers on exactly the NEW models this tool exists to test. It is a
#: ceiling, not a spend: only generated tokens are billed. A scenario's own ``max_tokens`` or
#: ``max_output_tokens`` wins.
DEFAULT_MAX_TOKENS = 4096

#: Models that still accept ``temperature``/``top_p``.
#:
#: `[S] 2026-09-15` platform.claude.com/docs/en/api/messages, on ``temperature``: *"Deprecated.
#: Models released after Claude Opus 4.6 do not support setting temperature. A value of 1.0
#: will be accepted for backwards compatibility, all other values will be rejected with a 400
#: error."* ``top_p`` reads the same (>= 0.99 accepted). So a scenario's ``temperature: 0`` is
#: a hard 400 on EVERY run against Opus 4.7+, Sonnet 5, Fable -- the failure shape the OpenAI
#: adapter's ``_REASONING_PREFIXES`` exists to prevent.
#:
#: An ALLOWLIST, not a denylist, and that is the same deliberate direction as the OpenAI gate:
#: the next model will almost certainly reject sampling too, and under-suppressing costs the
#: whole run while over-suppressing costs only determinism. Matched: the 3.x family and the
#: 4.x family up to minor 6 (``claude-opus-4-6``, ``claude-sonnet-4-5@20250929``,
#: ``claude-opus-4-20250514``). Falsified by: a primary doc listing a model after Opus 4.6
#: that accepts a non-default temperature.
_SAMPLING_RE = re.compile(r"^claude-(?:3[-.@]|(?:opus|sonnet|haiku)-4(?:-(\d+))?(?:$|[-@]))")

#: `[M]` anthropic 1.0.0 REMOVED ``temperature``/``top_p``/``top_k`` from ``messages.create``'s
#: signature (passing one is a ``TypeError``); the API still honours them on the models above.
#: ``extra_body`` is merged into the request JSON as-is, which is the SDK's documented escape.
_SAMPLING_KEYS: tuple[str, ...] = ("temperature", "top_p")


def accepts_sampling(model_id: str) -> bool:
    """True when this Claude model still accepts a non-default ``temperature``/``top_p``."""
    match = _SAMPLING_RE.match(model_id.strip().lower())
    if not match:
        return False
    minor = match.group(1)
    # An 8-digit group is a dated snapshot of the bare `-4` model (`claude-opus-4-20250514`).
    return minor is None or len(minor) == 8 or int(minor) <= 6


#: Friendly hints per SDK error class, keyed by NAME so the SDK stays unimported at module
#: scope. `[M]` every name below is exported by anthropic 1.0.0; the Vertex client maps 503 and
#: 504 to their own classes, and the first-party client maps 529 to ``OverloadedError``.
_API_ERROR_HINTS: dict[str, str] = {
    "AuthenticationError": "your credentials were rejected (invalid, expired or revoked)",
    "PermissionDeniedError": "your credentials lack access to this model or resource",
    "NotFoundError": "the model id was not found — check it exists and you have access",
    "RateLimitError": (
        f"rate limit or quota exceeded, and it did not clear after {REPLAY_MAX_RETRIES} "
        "automatic retries honouring the delay the server asked for — wait for your quota "
        "window to reset, lower --runs, or check billing"
    ),
    "BadRequestError": "the request was rejected (often an unsupported param for this model)",
    "RequestTooLargeError": "the request exceeded the API's size limit",
    "APITimeoutError": "the request timed out",
    "APIConnectionError": "could not reach the API endpoint (network/connection error)",
    "InternalServerError": "the API returned a server error — retry later",
    "OverloadedError": (
        f"the API is overloaded and stayed so through {REPLAY_MAX_RETRIES} automatic retries "
        "— retry later"
    ),
    "ServiceUnavailableError": "the service is temporarily unavailable — retry later",
    "DeadlineExceededError": "the upstream deadline was exceeded — retry later",
}

#: Errors whose text may embed a credential fragment -- their text is never echoed.
_KEY_BEARING_ERRORS: frozenset[str] = frozenset({"AuthenticationError", "PermissionDeniedError"})


def _explain_api_error(
    exc: Exception,
    model_id: str,
    label: str = "Anthropic",
    credential_hint: str | None = None,
) -> str:
    """Turn a raw SDK/network exception into a concise, key-safe message.

    Mirrors ``openai._explain_api_error``: auth/permission errors drop their text entirely,
    every other error's text is scrubbed before it is interpolated, and a rejected credential
    names WHERE Modelpin read it (MP-136) -- the variable name or the ADC project, never a
    value.
    """
    name = type(exc).__name__
    hint = _API_ERROR_HINTS.get(name, "the API call failed")
    base = f"{label} call for model {scrub_secrets(model_id)!r} failed: {hint}"
    if name in _KEY_BEARING_ERRORS:
        return f"{base} [{name}].{(' ' + credential_hint) if credential_hint else ''}"
    detail = elide(scrub_secrets(str(exc)))
    return f"{base} [{name}: {detail}]."


def _import_anthropic() -> Any:
    try:
        import anthropic
    except ImportError as exc:  # optional dependency
        raise ProviderError(
            'The Anthropic SDK is not installed. Install it with: pip install "modelpin[providers]"'
        ) from exc
    return anthropic


class _Backend:
    """Which credential path a client was built from, for error messages that name it."""

    def __init__(self, label: str, credential_hint: str) -> None:
        self.label = label
        self.credential_hint = credential_hint


_API_BACKEND = _Backend(
    "Anthropic",
    f"Modelpin read your key from {API_KEY_ENV} — check that variable holds a current "
    "Anthropic key.",
)


def _vertex_backend(project: str) -> _Backend:
    return _Backend(
        "Claude on Vertex AI",
        f"Modelpin authenticated with Application Default Credentials for project "
        f"{scrub_secrets(project)!r} — run `gcloud auth application-default login`, and check "
        "the Claude model is enabled for the project in Vertex AI Model Garden.",
    )


def build_anthropic_client(max_retries: int = REPLAY_MAX_RETRIES) -> tuple[Any, _Backend]:
    """Construct a real Claude client from the end user's own credentials. No network call.

    ``ANTHROPIC_VERTEX_PROJECT_ID`` selects Vertex and wins when set, as the analogous Vertex
    switch does in ``google.py``: a user who exported it asked for Google Cloud billing, and
    silently spending an API key they also happen to have would bill the wrong account.
    Shared by the adapter and the judge so credential handling lives in one place.
    """
    project = (os.environ.get(VERTEX_PROJECT_ENV) or "").strip()
    if project:
        anthropic = _import_anthropic()
        # `global` is the docs' recommended endpoint and the only one that serves the newest
        # models (`[S]` claude-on-vertex-ai: "Specific regional endpoints support Claude Sonnet
        # 4.6 and earlier; newer models use the global or multi-region endpoints"). The SDK
        # itself has NO default and raises without one; override for data residency.
        region = (os.environ.get(VERTEX_REGION_ENV) or "").strip() or "global"
        try:
            import google.auth  # the `anthropic[vertex]` extra; kept lazy inside the branch

            # [M] Constructing `AnthropicVertex` proves NOTHING about credentials: it loads ADC
            # only on the first REQUEST (`_ensure_access_token`). Without this line preflight()
            # passes and the run dies mid-replay -- after the user was told what it will cost.
            # The resolved credentials are handed to the client so they are loaded once.
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            client = anthropic.AnthropicVertex(
                project_id=project,
                region=region,
                credentials=credentials,
                max_retries=max_retries,
            )
        except Exception as exc:  # noqa: BLE001 - google-auth raises several unrelated types
            raise ProviderError(
                f"Could not build a Claude-on-Vertex client for project "
                f"{scrub_secrets(project)!r} in {region}: {scrub_secrets(str(exc))}. Vertex uses "
                "Application Default Credentials, NOT an API key — run `gcloud auth "
                "application-default login` (and `pip install 'anthropic[vertex]'` if "
                "google-auth is missing)."
            ) from exc
        return client, _vertex_backend(project)

    api_key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not api_key:
        raise ProviderError(
            f"{API_KEY_ENV} is not set. Modelpin uses YOUR own API key "
            "(cost + provider ToS) — export it and retry. To run Claude on Google Vertex AI "
            f"instead, set {VERTEX_PROJECT_ENV} (and optionally {VERTEX_REGION_ENV}, default "
            "global) and authenticate with `gcloud auth application-default login`."
        )
    anthropic = _import_anthropic()
    # An explicit `api_key=` makes the SDK skip its own env/profile credential chain
    # (`[M]` `_client.py`: "Explicit ctor args are total"), so the key used is the one named.
    return anthropic.Anthropic(api_key=api_key, max_retries=max_retries), _API_BACKEND


def _text_of(content: Any) -> str:
    """A scenario message's content as plain text (OpenAI-style string or text-part list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(p.get("text", "")) if isinstance(p, dict) else str(p)
            for p in content
            if not isinstance(p, dict) or p.get("type", "text") == "text"
        ]
        return "".join(parts)
    return "" if content is None else str(content)


def _to_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages to (system, Anthropic messages).

    System messages fold into the top-level ``system`` -- the Messages API has no ``system``
    role in ``messages`` (`[S]` api/messages). Empty turns are dropped because the API rejects
    an empty text block, and a scenario's blank turn carries no behaviour to replay.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        text = _text_of(message.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if not text:
            continue
        out.append({"role": "assistant" if role == "assistant" else "user", "content": text})
    return ("\n".join(system_parts) or None), out


def _to_tools(raw: Any) -> list[dict[str, Any]] | None:
    """Normalize a scenario's tools into Anthropic ``{name, description, input_schema}`` specs.

    Accepts bare names, OpenAI function-tool specs and bare function definitions -- the same
    three shapes the OpenAI and Gemini adapters accept, so one scenario runs on every vendor.
    """
    if not raw:
        return None
    if not isinstance(raw, list):
        raise ProviderError(f"scenario 'tools' must be a list, got {type(raw).__name__}")
    tools: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            tools.append({"name": item, "input_schema": {"type": "object", "properties": {}}})
        elif isinstance(item, dict):
            if "input_schema" in item:
                tools.append(item)  # already Anthropic-shaped
                continue
            fn = item.get("function", item) if item.get("type") == "function" else item
            if not isinstance(fn, dict) or "name" not in fn:
                continue
            tool: dict[str, Any] = {
                "name": fn["name"],
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            if fn.get("description"):
                tool["description"] = fn["description"]
            tools.append(tool)
    return tools or None


def _max_tokens(scenario_input: dict[str, Any]) -> int:
    value = scenario_input.get("max_tokens", scenario_input.get("max_output_tokens"))
    return int(value) if value is not None else DEFAULT_MAX_TOKENS


def _sampling_body(model_id: str, scenario_input: dict[str, Any]) -> dict[str, Any]:
    """The sampling params to send through ``extra_body``, or {} when the model rejects them.

    Only ONE of temperature/top_p is sent. `[S] 2026-09-15`
    platform.claude.com/docs/en/models/haiku-4-5/migration-guide: "Use only `temperature` OR
    `top_p`, not both. Setting both returns a 400 error on Claude Haiku 4.5." Temperature wins
    because it is the knob our scenarios set.
    """
    if not accepts_sampling(model_id):
        return {}
    for key in _SAMPLING_KEYS:
        if key in scenario_input:
            return {key: scenario_input[key]}
    return {}


def _block_dict(block: Any) -> dict[str, Any]:
    """A response content block as a request-ready dict, preserved verbatim.

    Thinking blocks MUST go back unmodified (`[M]` ``ThinkingBlockParam``: "a modified block
    results in a 400"), so SDK blocks are dumped whole rather than rebuilt field by field.
    """
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    kind = getattr(block, "type", None)
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", None) or {},
        }
    if kind == "thinking":
        return {
            "type": "thinking",
            "thinking": getattr(block, "thinking", ""),
            "signature": getattr(block, "signature", ""),
        }
    return {"type": kind or "text", "text": getattr(block, "text", "") or ""}


def _tool_use_blocks(content: list[Any]) -> list[Any]:
    return [
        b for b in content if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None)
    ]


def _parse_tool_calls(blocks: list[Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for block in blocks:
        args = getattr(block, "input", None)
        calls.append(ToolCall(name=block.name, arguments=args if isinstance(args, dict) else {}))
    return calls


def _tool_result_message(blocks: list[Any], tool_results: dict[str, Any]) -> dict[str, Any]:
    """ONE user message holding a ``tool_result`` per ``tool_use``, in call order.

    One message, not one per call: `[S]` tool-use docs -- parallel results belong in a single
    user message, and tool_result blocks must come first in it.
    """
    results: list[dict[str, Any]] = []
    for block in blocks:
        result = tool_results.get(block.name, _DEFAULT_TOOL_RESULT)
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": getattr(block, "id", None) or f"toolu_{block.name}",
                "content": result if isinstance(result, str) else json.dumps(result),
            }
        )
    return {"role": "user", "content": results}


def _text(content: list[Any]) -> str:
    return "".join(
        getattr(b, "text", None) or "" for b in content if getattr(b, "type", None) == "text"
    )


#: Stop reasons that mean the model ended on its own terms.
_COMPLETE_STOP: frozenset[str] = frozenset({"end_turn", "tool_use", "stop_sequence"})
_STOP_TO_REASON: dict[str, IncompleteReason] = {
    "max_tokens": IncompleteReason.max_tokens,
    # `[S]` api/messages: "we exceeded the model's context window" -- the token budget cut the
    # answer off, which is what `max_tokens` records; `provider_other` would hide that.
    "model_context_window_exceeded": IncompleteReason.max_tokens,
    # `[S]` "streaming classifiers intervene to handle potential policy violations".
    "refusal": IncompleteReason.content_filter,
}


def _incomplete_reason(stop_reason: str | None) -> IncompleteReason | None:
    """Map a stop reason to why the run ended early, or None when it finished.

    A missing stop_reason returns None (`[S]` "In non-streaming mode this value is always
    non-null", so absence means a fake or a proxy, and "unknown" must not read as "cut short").
    ``pause_turn`` falls to ``provider_other``: it only arises from server tools, which this
    adapter never declares.
    """
    if not stop_reason or stop_reason in _COMPLETE_STOP:
        return None
    return _STOP_TO_REASON.get(stop_reason, IncompleteReason.provider_other)


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    #: The SDK client is thread-safe, so `replay` may send a scenario's runs together.
    parallel_safe = True

    def __init__(self, client: Any | None = None, label: str = "Anthropic") -> None:
        # An injected client makes the adapter unit-testable with no network or credentials.
        self._client = client
        self._backend = _Backend(label, _API_BACKEND.credential_hint)

    def preflight(self) -> None:
        """Validate credentials + SDK before any replay runs — no network call."""
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client, self._backend = build_anthropic_client()
        return self._client

    def _create(self, client: Any, request: dict[str, Any], scenario_id: str, model_id: str):
        """One Messages call, with friendly error wrapping + a non-empty-content guard."""
        try:
            response = client.messages.create(**request)
        except ProviderError:
            raise
        except Exception as exc:  # SDK/network error → friendly, key-safe ProviderError
            raise ProviderError(
                _explain_api_error(
                    exc, model_id, self._backend.label, self._backend.credential_hint
                )
            ) from exc
        if getattr(response, "content", None) is None:
            raise ProviderError(
                f"{self._backend.label} returned no content for scenario {scenario_id!r} "
                f"on {model_id!r}."
            )
        return response

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        original = list(scenario.input.get("messages", []))
        system, conversation = _to_messages(original)
        tools = _to_tools(scenario.input.get("tools"))
        tool_results = scenario.input.get("tool_results") or {}
        base: dict[str, Any] = {"model": model_id, "max_tokens": _max_tokens(scenario.input)}
        if system:
            base["system"] = system
        if tools:
            base["tools"] = tools
        if sampling := _sampling_body(model_id, scenario.input):
            base["extra_body"] = sampling

        client = self._get_client()
        # The record keeps the scenario's own messages (system prompt included, strings intact
        # for the baseline secret scan) followed by the turns this replay added.
        record: list[dict[str, Any]] = list(original)
        all_tool_calls: list[ToolCall] = []
        final_text = ""
        refused = False
        incomplete: IncompleteReason | None = None
        tokens_in = tokens_out = 0
        started = time.perf_counter()

        for _turn in range(MAX_TOOL_TURNS):
            request = {**base, "messages": list(conversation)}
            response = self._create(client, request, scenario.id, model_id)
            content = list(response.content or [])
            stop_reason = getattr(response, "stop_reason", None)
            final_text = _text(content)
            tool_blocks = _tool_use_blocks(content)
            all_tool_calls.extend(_parse_tool_calls(tool_blocks))
            refused = refused or stop_reason == "refusal" or looks_like_refusal(final_text)
            # First writer wins, like `refused`: a turn-1 truncation is not erased by a clean
            # turn 5.
            incomplete = incomplete or _incomplete_reason(stop_reason)

            usage = getattr(response, "usage", None)
            tokens_in += getattr(usage, "input_tokens", 0) or 0
            tokens_out += getattr(usage, "output_tokens", 0) or 0

            assistant = {"role": "assistant", "content": [_block_dict(b) for b in content]}
            conversation.append(assistant)
            record.append(assistant)
            # A truncated turn is not continued: a `tool_use` cut off by `max_tokens` may carry
            # partial input, and answering it risks a 400 that would cost the whole scenario
            # rather than record one degraded run.
            if not tool_blocks or stop_reason in _STOP_TO_REASON:
                break
            results = _tool_result_message(tool_blocks, tool_results)
            conversation.append(results)
            record.append(results)
        else:
            # for/else fires ONLY when the loop never `break`s -- every one of MAX_TOOL_TURNS
            # turns still wanted a tool. OUR cap ended the run, which overrides any provider
            # reason, exactly as in the OpenAI adapter.
            incomplete = IncompleteReason.tool_turns

        latency_ms = (time.perf_counter() - started) * 1000.0
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            messages=record,
            tool_calls=all_tool_calls,
            final_output=final_text,
            refused=refused,
            incomplete_reason=incomplete,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
