"""OpenAI adapter — replay a Scenario on an OpenAI model via the Chat Completions API.

Why Chat Completions (not the Responses API): the milestone compares an *older* vs a
*newer* GPT, and Chat Completions is the surface every GPT model supports. The shapes
here are verified against the official `openai` Python SDK (v1.x):
``response.choices[0].message.{content,tool_calls,refusal}`` and ``response.usage``.

Guardrails (BYO-key, key-safe):
- Use the END USER's key from ``OPENAI_API_KEY``. Never hardcode, ship, or log it.
- The SDK is an *optional* dependency; its import stays inside the run path so the
  package imports without it. Tests inject a fake client and never touch the network.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from modelpin.models import IncompleteReason, Scenario, ToolCall, Trace
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

#: Reasoning models reject a non-default ``temperature``/``top_p`` and require
#: ``max_completion_tokens`` instead of ``max_tokens``.
#:
#: `[S] 2026-08-31` learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning (page
#: titled "GPT-5 series, o3-mini, o1, o1-mini"): "The following are currently unsupported with
#: reasoning models: `temperature`, `top_p`, `presence_penalty`, `frequency_penalty`,
#: `logprobs`, `top_logprobs`, `logit_bias`, `max_tokens`".
#:
#: **The old comment here said "GPT-family models are unaffected". That is no longer true, and
#: it was the load-bearing sentence.** The gpt-5 family IS reasoning: `[S]` a live 400 reported
#: against `gpt-5.6-luna` reads *"Unsupported value: 'temperature' does not support 0.8 with
#: this model. Only the default (1) value is supported."* `[M] 2026-08-31` our own
#: `data/models.json` ships `gpt-5.5` as `status: active` and points `gpt-5.2` at it as a
#: replacement -- so the registry was recommending models this gate missed, and the failure is
#: a hard 400 on EVERY scenario and every run, not a degraded one.
#:
#: Over-suppression is the deliberate direction. `gpt-5.1` defaults `reasoning_effort` to
#: `none` and may well accept a temperature, so a flat `gpt-5` prefix costs it determinism it
#: could have had. That is strictly better than the alternative: under-suppressing costs the
#: whole run. `[A]` Sending `reasoning_effort: "none"` alongside `temperature: 0` may restore
#: determinism for gpt-5.x -- NOT confirmed against a primary doc, so it is not done here.
#: Falsified by: a primary OpenAI doc documenting temperature support on a gpt-5.x model.
_REASONING_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4", "gpt-5")

#: Cap on model<->tool turns per run so a model that loops on tool calls can't run forever.
MAX_TOOL_TURNS = 6
#: Returned for a tool the scenario didn't give a canned result for — keeps multi-step
#: agent replays deterministic and offline without real tool execution.
_DEFAULT_TOOL_RESULT: dict[str, Any] = {"status": "ok"}

#: Transient-failure budget per API call, replacing the SDK's default of 2 (MP-139).
#:
#: `[M] 2026-08-30`, dogfood on the Groq free tier: `modelpin check` died on
#: `RateLimitError 429 ... tokens per minute (TPM): Limit 8000, Used 6480, Requested 1527.
#: Please try again in 52.5ms`. Twelve scenarios, 60 replays, ZERO reported -- a whole run
#: lost to a wait shorter than one HTTP round trip.
#:
#: `[M] 2026-08-31` THE ROW'S PREMISE IS WRONG AND THE CORRECTION IS WHY THIS IS A CONSTANT
#: RATHER THAN A RETRY LOOP. MP-139 says "No backoff/retry exists on the provider path."
#: Retry does exist -- the SDK's -- it is on by default, and it honours the server's own
#: delay. Verified at the httpx transport layer, which is the ONLY layer that exercises it:
#: a handler returning 429 with `retry-after-ms: 50` twice, then 200, produced **3 HTTP
#: attempts and a successful completion in 1.01s**; at `max_retries=0` the same handler
#: raised `RateLimitError` after 1 attempt. So the free-tier run did not die for want of a
#: retry -- it died because a budget of 2, spread over ~1.5s of backoff, is tuned for a paid
#: tier whose token window is not already exhausted.
#:
#: Writing our own loop around `_complete` would be the wrong shape twice over: it would
#: MULTIPLY with the SDK's (2 x ours), and `_complete` is called inside the
#: `range(MAX_TOOL_TURNS)` loop, so an agent scenario would multiply it again by 6.
#: Raising the SDK's own budget composes with nothing and inherits its `retry-after`
#: handling for free.
#:
#: A retry here cannot bias the measured distribution, which is the objection that would
#: otherwise block this under the north-star: the SDK retries an HTTP request that produced
#: NO response, so no sample is discarded and none is re-rolled. Re-running a call whose
#: response we had already seen would bias it; nothing here does that.
#:
#: `[M]` Worst case this adds ~15.5s per call (SDK backoff 0.5s doubling to an 8s cap), and
#: only when every attempt fails; when the server sends `retry-after` -- as Groq does -- the
#: real wait is that value, 52.5ms in the incident above.
REPLAY_MAX_RETRIES = 5

#: Friendly, actionable hints per OpenAI SDK error class. Keyed by class name so we
#: don't need to import the SDK's exception types at module scope.
_API_ERROR_HINTS: dict[str, str] = {
    "AuthenticationError": "your API key was rejected (invalid or revoked)",
    "PermissionDeniedError": "your key lacks access to this model or resource",
    "NotFoundError": "the model id was not found — check it exists and you have access",
    "RateLimitError": (
        f"rate limit or quota exceeded, and it did not clear after {REPLAY_MAX_RETRIES} "
        "automatic retries honouring the delay the server asked for — wait for your quota "
        "window to reset, lower --runs, or check billing"
    ),
    "BadRequestError": "the request was rejected (often an unsupported param for this model)",
    "APITimeoutError": "the request timed out",
    "APIConnectionError": "could not reach the API endpoint (network/connection error)",
    "InternalServerError": "the API returned a server error — retry later",
}

#: Errors whose message text may embed a redacted key fragment — don't echo it at all.
_KEY_BEARING_ERRORS: frozenset[str] = frozenset({"AuthenticationError", "PermissionDeniedError"})


def _explain_api_error(
    exc: Exception,
    model_id: str,
    label: str = "OpenAI",
    api_key_env: str | None = None,
) -> str:
    """Turn a raw SDK/network exception into a concise, key-safe message.

    ``label`` names the provider (OpenAI, or an OpenAI-compatible host like Groq) so the
    message is accurate when the same adapter drives a different endpoint.

    Default-deny on the secret surface: auth/permission errors drop their text
    entirely, and every other error's text is scrubbed of key-shaped tokens before
    it is interpolated — so a renamed/unlisted error class can't leak a key either.

    ``api_key_env`` names the variable to fix (MP-136). `[M] 2026-08-29`, dogfood from a
    PyPI install with a revoked Groq key: the REJECTED-key path said only *"your API key
    was rejected (invalid or revoked) [AuthenticationError]"* while the sibling UNSET-key
    path said *"GROQ_API_KEY is not set ... export it and retry"* -- so a stranger whose key
    is stale got the less actionable of the two messages and had to guess the variable.

    It is the variable NAME, never the value, and that distinction is why this does not
    weaken the guard above it: the text that is dropped is ``str(exc)``, which may embed a
    redacted key fragment. A variable name is not a secret -- the user set it themselves.
    """
    name = type(exc).__name__
    hint = _API_ERROR_HINTS.get(name, "the API call failed")
    base = f"{label} call for model {scrub_secrets(model_id)!r} failed: {hint}"
    if name in _KEY_BEARING_ERRORS:
        where = (
            f" Modelpin read your key from {api_key_env} — check that variable holds a "
            f"current {label} key."
            if api_key_env
            else ""
        )
        return f"{base} [{name}].{where}"
    detail = scrub_secrets(str(exc))[:300]
    return f"{base} [{name}: {detail}]."


def _is_reasoning_model(model_id: str) -> bool:
    """True when this model rejects `temperature`/`top_p` and needs `max_completion_tokens`.

    The tail is checked as well as the whole id because OpenAI-compatible hosts namespace
    their ids: `[M] 2026-08-31` `openai/gpt-5.6` and `openai/o3` are real OpenRouter/Groq ids,
    and a bare `startswith` can never match one -- so the gate silently stood down on exactly
    the hosts MP-143 just opened the judge to.
    """
    tail = model_id.rsplit("/", 1)[-1]
    return model_id.startswith(_REASONING_PREFIXES) or tail.startswith(_REASONING_PREFIXES)


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


#: Chat Completions finish_reason values that mean the model ENDED ON ITS OWN TERMS.
#: Anything else means something cut the run short, which the diff records but never gates on.
_COMPLETE_FINISH = frozenset({"stop", "tool_calls", "function_call"})
_FINISH_TO_REASON = {
    "length": IncompleteReason.max_tokens,
    "content_filter": IncompleteReason.content_filter,
}


def _incomplete_reason(finish_reason: str | None) -> IncompleteReason | None:
    """Map a provider stop reason to why the run ended early, or None if it finished.

    A MISSING finish_reason returns None, not `provider_other`: OpenAI-compatible hosts
    (Groq/OpenRouter/Together) may omit it entirely, and "we do not know" must not be
    recorded as "we know it was cut short". That silence is a real coverage gap for those
    hosts and is disclosed in ADR-0018 rather than papered over here.
    """
    if not finish_reason:
        return None
    if finish_reason in _COMPLETE_FINISH:
        return None
    return _FINISH_TO_REASON.get(finish_reason, IncompleteReason.provider_other)


def _detect_refusal(message: Any, finish_reason: str | None, text: str) -> bool:
    """True if the model declined or was filtered. Combines hard signals (content
    filter, the SDK's structured-output ``refusal`` field) with a phrase heuristic."""
    if finish_reason == "content_filter":
        return True
    if getattr(message, "refusal", None):
        return True
    return looks_like_refusal(text)


def build_openai_client(
    api_key_env: str = "OPENAI_API_KEY",
    base_url: str | None = None,
    max_retries: int = REPLAY_MAX_RETRIES,
) -> Any:
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
    kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": max_retries}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


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
            raise ProviderError(
                _explain_api_error(exc, model_id, self._label, self._api_key_env)
            ) from exc
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
        incomplete: IncompleteReason | None = None
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
            # First writer wins, like `refused`: a turn-1 truncation must not be erased by a
            # clean turn 5. The run as a whole was degraded.
            incomplete = incomplete or _incomplete_reason(choice.finish_reason)

            usage = getattr(response, "usage", None)
            tokens_in += getattr(usage, "prompt_tokens", 0) or 0
            tokens_out += getattr(usage, "completion_tokens", 0) or 0

            conversation.append(_message_dict(message))
            if not turn_calls:
                break  # the model produced its final answer
            conversation.extend(_tool_result_messages(message, tool_results))
        else:
            # for/else fires ONLY when the loop was never `break`n -- i.e. every one of
            # MAX_TOOL_TURNS turns still wanted another tool. OUR cap ended this run, not the
            # model, and that is the more actionable fact, so it overrides any provider
            # reason. Without this the trace is byte-identical to a genuinely finished run.
            incomplete = IncompleteReason.tool_turns

        latency_ms = (time.perf_counter() - started) * 1000.0
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            messages=conversation,
            tool_calls=all_tool_calls,
            final_output=final_text,
            refused=refused,
            incomplete_reason=incomplete,
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
