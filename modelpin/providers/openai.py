"""OpenAI adapter — replay a Scenario on an OpenAI model via the Chat Completions API.

Why Chat Completions (not the Responses API): the milestone compares an *older* vs a
*newer* GPT, and Chat Completions is the surface every GPT model supports. The shapes
here are verified against the official `openai` Python SDK (v1.x):
``response.choices[0].message.{content,tool_calls,refusal}`` and ``response.usage``.

Guardrails (see CLAUDE.md / spec §9):
- Use the END USER's key from ``OPENAI_API_KEY``. Never hardcode, ship, or log it.
- The SDK is an *optional* dependency; its import stays inside the run path so the
  package imports without it. Tests inject a fake client and never touch the network.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from modelpin.models import Scenario, ToolCall, Trace
from modelpin.providers._common import looks_like_refusal, scrub_secrets
from modelpin.providers.base import ProviderAdapter, ProviderError

#: Generation params we pass through from ``scenario.input`` when present. Anything
#: else in ``input`` (``messages``, ``tools``) is handled explicitly below.
_GEN_PARAM_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "tool_choice",
    "seed",
    "response_format",
)

#: o-series reasoning models reject a non-default ``temperature``/``top_p`` and require
#: ``max_completion_tokens`` instead of ``max_tokens``. GPT-family models are unaffected,
#: so we keep this set narrow to avoid silently dropping params a GPT model accepts.
_REASONING_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4")

#: Cap on model<->tool turns per run so a model that loops on tool calls can't run forever.
MAX_TOOL_TURNS = 6
#: Returned for a tool the scenario didn't give a canned result for — keeps multi-step
#: agent replays deterministic and offline without real tool execution.
_DEFAULT_TOOL_RESULT: dict[str, Any] = {"status": "ok"}

#: Friendly, actionable hints per OpenAI SDK error class. Keyed by class name so we
#: don't need to import the SDK's exception types at module scope.
_API_ERROR_HINTS: dict[str, str] = {
    "AuthenticationError": "your API key was rejected (invalid or revoked)",
    "PermissionDeniedError": "your key lacks access to this model or resource",
    "NotFoundError": "the model id was not found — check it exists and you have access",
    "RateLimitError": "rate limit or quota exceeded — slow down or check billing",
    "BadRequestError": "the request was rejected (often an unsupported param for this model)",
    "APITimeoutError": "the request timed out",
    "APIConnectionError": "could not reach the API endpoint (network/connection error)",
    "InternalServerError": "the API returned a server error — retry later",
}

#: Errors whose message text may embed a redacted key fragment — don't echo it at all.
_KEY_BEARING_ERRORS: frozenset[str] = frozenset({"AuthenticationError", "PermissionDeniedError"})


def _explain_api_error(exc: Exception, model_id: str, label: str = "OpenAI") -> str:
    """Turn a raw SDK/network exception into a concise, key-safe message.

    ``label`` names the provider (OpenAI, or an OpenAI-compatible host like Groq) so the
    message is accurate when the same adapter drives a different endpoint.

    Default-deny on the secret surface: auth/permission errors drop their text
    entirely, and every other error's text is scrubbed of key-shaped tokens before
    it is interpolated — so a renamed/unlisted error class can't leak a key either.
    """
    name = type(exc).__name__
    hint = _API_ERROR_HINTS.get(name, "the API call failed")
    base = f"{label} call for model {scrub_secrets(model_id)!r} failed: {hint}"
    if name in _KEY_BEARING_ERRORS:
        return f"{base} [{name}]."
    detail = scrub_secrets(str(exc))[:300]
    return f"{base} [{name}: {detail}]."


def _is_reasoning_model(model_id: str) -> bool:
    return model_id.startswith(_REASONING_PREFIXES)


def _to_tools(raw: Any) -> list[dict[str, Any]] | None:
    """Normalize a scenario's ``tools`` into OpenAI function-tool specs.

    Accepts bare tool *names* (the common case in our scenarios) and wraps each as a
    minimal function with no parameters; passes already-shaped dicts through untouched.
    """
    if not raw:
        return None
    if not isinstance(raw, list):
        raise ProviderError(f"scenario 'tools' must be a list, got {type(raw).__name__}")
    tools: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": item,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
        elif isinstance(item, dict):
            if item.get("type") == "function" and "function" in item:
                tools.append(item)  # already a full tool spec
            elif "name" in item:
                tools.append({"type": "function", "function": item})  # bare function def
            else:
                tools.append(item)  # trust an explicit caller-supplied shape
    return tools or None


def _build_request(
    model_id: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, gen: dict
) -> dict[str, Any]:
    """Assemble create() kwargs, honoring o-series param restrictions."""
    req: dict[str, Any] = {"model": model_id, "messages": messages}
    if tools:
        req["tools"] = tools
    reasoning = _is_reasoning_model(model_id)
    for key in ("temperature", "top_p"):
        if key in gen and not reasoning:
            req[key] = gen[key]
    for key in ("tool_choice", "seed", "response_format"):
        if key in gen:
            req[key] = gen[key]
    max_tokens = gen.get("max_completion_tokens", gen.get("max_tokens"))
    if max_tokens is not None:
        req["max_completion_tokens" if reasoning else "max_tokens"] = max_tokens
    return req


def _parse_tool_calls(message: Any) -> list[ToolCall]:
    raw_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for call in raw_calls:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None)
        if not name:
            continue  # partial/malformed call — skip rather than crash the whole run
        try:
            raw_args = fn.arguments
            arguments = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError):
            arguments = {}  # partial/malformed args — record the call, drop the payload
        if not isinstance(arguments, dict):
            arguments = {"_value": arguments}
        calls.append(ToolCall(name=name, arguments=arguments))
    return calls


def _tool_result_messages(message: Any, tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ``role: tool`` messages to feed back after the model's tool calls.

    Results are canned (from ``scenario.input['tool_results']``) so multi-step agent
    replays are deterministic and need no real tool execution; unmatched tools get a
    generic stub so the conversation can still progress.
    """
    messages: list[dict[str, Any]] = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None)
        if not name:
            continue
        result = tool_results.get(name, _DEFAULT_TOOL_RESULT)
        content = result if isinstance(result, str) else json.dumps(result)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": getattr(call, "id", None) or f"call_{name}",
                "content": content,
            }
        )
    return messages


def _detect_refusal(message: Any, finish_reason: str | None, text: str) -> bool:
    """True if the model declined or was filtered. Combines hard signals (content
    filter, the SDK's structured-output ``refusal`` field) with a phrase heuristic."""
    if finish_reason == "content_filter":
        return True
    if getattr(message, "refusal", None):
        return True
    return looks_like_refusal(text)


def build_openai_client(api_key_env: str = "OPENAI_API_KEY", base_url: str | None = None) -> Any:
    """Construct a real OpenAI(-compatible) client: validate the BYO-key, lazily import the SDK.

    Shared by the adapter and the semantic judge so key/SDK handling lives in one place.
    ``base_url`` points the SDK at an OpenAI-compatible endpoint (e.g. Groq/OpenRouter serving
    Llama) — they implement the same Chat Completions surface, so the whole adapter is reused.
    Raises ``ProviderError`` (never a raw traceback) if the key or SDK is missing.
    """
    api_key = (os.environ.get(api_key_env) or "").strip()
    if not api_key:
        raise ProviderError(
            f"{api_key_env} is not set. Modelpin uses YOUR own API key "
            "(cost + provider ToS) — export it and retry."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # optional dependency
        raise ProviderError(
            "The OpenAI SDK is not installed. Install it with: pip install 'modelpin[providers]'"
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


class OpenAIAdapter(ProviderAdapter):
    name = "openai"

    def __init__(
        self,
        client: Any | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        label: str = "OpenAI",
    ) -> None:
        # An injected client makes the adapter unit-testable with no network or key.
        # base_url + api_key_env + label let this same adapter drive an OpenAI-compatible
        # host (Groq, OpenRouter, ...) so a free Llama endpoint can be a cross-vendor target.
        self._client = client
        self._api_key_env = api_key_env
        self._base_url = base_url
        self._label = label

    def preflight(self) -> None:
        """Validate the key + SDK before any replay runs — no network call."""
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_openai_client(self._api_key_env, self._base_url)
        return self._client

    def _complete(self, client: Any, request: dict[str, Any], scenario_id: str, model_id: str):
        """One chat completion, with friendly error wrapping + a non-empty-choices guard."""
        try:
            response = client.chat.completions.create(**request)
        except ProviderError:
            raise
        except Exception as exc:  # SDK/network error → friendly, key-safe ProviderError
            raise ProviderError(_explain_api_error(exc, model_id, self._label)) from exc
        if not (getattr(response, "choices", None) or []):
            raise ProviderError(
                f"{self._label} returned no choices for scenario {scenario_id!r} on {model_id!r}."
            )
        return response

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        tools = _to_tools(scenario.input.get("tools"))
        gen = {k: scenario.input[k] for k in _GEN_PARAM_KEYS if k in scenario.input}
        tool_results = scenario.input.get("tool_results") or {}

        client = self._get_client()
        conversation = list(scenario.input.get("messages", []))
        all_tool_calls: list[ToolCall] = []
        final_text = ""
        refused = False
        tokens_in = tokens_out = 0
        started = time.perf_counter()

        # Drive the model<->tool loop: keep feeding canned tool results back until the
        # model returns a final answer (no tool calls) or we hit the turn cap. This is
        # what lets multi-step agent trajectories (e.g. lookup_order -> issue_refund)
        # actually emerge instead of stopping at the first tool call.
        for _turn in range(MAX_TOOL_TURNS):
            request = _build_request(model_id, conversation, tools, gen)
            response = self._complete(client, request, scenario.id, model_id)
            choice = response.choices[0]
            message = choice.message
            final_text = message.content or ""
            turn_calls = _parse_tool_calls(message)
            all_tool_calls.extend(turn_calls)
            refused = refused or _detect_refusal(message, choice.finish_reason, final_text)

            usage = getattr(response, "usage", None)
            tokens_in += getattr(usage, "prompt_tokens", 0) or 0
            tokens_out += getattr(usage, "completion_tokens", 0) or 0

            conversation.append(_message_dict(message))
            if not turn_calls:
                break  # the model produced its final answer
            conversation.extend(_tool_result_messages(message, tool_results))

        latency_ms = (time.perf_counter() - started) * 1000.0
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            messages=conversation,
            tool_calls=all_tool_calls,
            final_output=final_text,
            refused=refused,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )


def _message_dict(message: Any) -> dict[str, Any]:
    """Serialize the assistant reply for the trace record, tolerating non-SDK fakes."""
    dump = getattr(message, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    return {"role": "assistant", "content": getattr(message, "content", "") or ""}


#: OpenAI-compatible hosts reachable through this same adapter by pointing the SDK at a
#: different ``base_url``: they implement the Chat Completions surface (incl. tool calls).
#: BYO-key from the named env var. This makes a free Llama endpoint (e.g. Groq's free tier)
#: usable as a cross-vendor target without a bespoke adapter. Note: open-model *hosts* don't
#: retire models on a lab's schedule the way OpenAI/Anthropic/Google do, but they do rotate
#: hosted model ids — so they're a real (bonus) cross-vendor target, not the core wedge.
OPENAI_COMPATIBLE_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "label": "Groq",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "label": "OpenRouter",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "label": "Together",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "label": "Cerebras",
    },
}


def build_openai_compatible_adapter(provider: str) -> OpenAIAdapter:
    """Adapter for a known OpenAI-compatible host (Groq/OpenRouter/Together/Cerebras)."""
    cfg = OPENAI_COMPATIBLE_PROVIDERS[provider]
    return OpenAIAdapter(
        api_key_env=cfg["api_key_env"], base_url=cfg["base_url"], label=cfg["label"]
    )
