"""Google (Gemini) adapter via the official ``google-genai`` SDK.

Shapes verified against the installed SDK: ``client.models.generate_content(model,
contents, config)``; function calls live in ``candidate.content.parts[].function_call``
(``.name`` + ``.args`` — already a dict, unlike OpenAI's JSON string); token usage in
``response.usage_metadata.{prompt,candidates}_token_count``; refusals show up as a
``candidate.finish_reason`` of SAFETY/RECITATION/etc. or a ``prompt_feedback.block_reason``.

BYO-key from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``). The SDK import stays lazy and the
client is injectable, so tests run with no network or key. Contents/config/tools are built
as plain dicts that the SDK coerces — this keeps the adapter independent of SDK type names.

NOTE: the single-turn / text path is straightforward. The multi-step tool-result feedback
(function_response role) follows the documented Gemini pattern but should be confirmed with
a live run once a Gemini key is available.
"""

from __future__ import annotations

import os
import time
from typing import Any

from modelpin.models import Scenario, ToolCall, Trace
from modelpin.providers._common import looks_like_refusal, scrub_secrets
from modelpin.providers.base import ProviderAdapter, ProviderError

MAX_TOOL_TURNS = 6
_DEFAULT_TOOL_RESULT: dict[str, Any] = {"status": "ok"}
_API_KEY_ENVS: tuple[str, ...] = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

#: finish_reason names that mean the model was blocked or declined.
_BLOCKED_FINISH: frozenset[str] = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)


def build_google_client(api_key_envs: tuple[str, ...] = _API_KEY_ENVS) -> Any:
    """Construct a real Gemini client: validate the BYO-key, then lazily import the SDK."""
    api_key = next((os.environ.get(e, "").strip() for e in api_key_envs if os.environ.get(e)), "")
    if not api_key:
        raise ProviderError(
            f"{api_key_envs[0]} is not set. Modelpin uses YOUR own API key "
            "(cost + provider ToS) — export it and retry."
        )
    try:
        from google import genai
    except ImportError as exc:  # optional dependency
        raise ProviderError(
            "The Google GenAI SDK is not installed. Install it with: pip install google-genai"
        ) from exc
    return genai.Client(api_key=api_key)


def _explain_api_error(exc: Exception, model_id: str) -> str:
    """Key-safe message for a Gemini SDK/network error (mirrors the OpenAI explainer)."""
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    base = f"Gemini call for model {scrub_secrets(model_id)!r} failed"
    if code in (401, 403):
        return f"{base}: API key rejected or lacks access [{name} {code}]."
    if code == 404:
        return f"{base}: model not found — check the id [{name} 404]."
    if code == 429:
        return f"{base}: rate limit or quota exceeded [{name} 429]."
    detail = scrub_secrets(str(getattr(exc, "message", None) or exc))[:300]
    suffix = f" {code}" if code else ""
    return f"{base} [{name}{suffix}: {detail}]."


def _to_contents(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages to (system_instruction, Gemini contents).

    System messages fold into the system instruction; user/assistant become
    user/model turns. (Gemini uses 'model', not 'assistant'.)
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        text = message.get("content") or ""
        if role == "system":
            if text:
                system_parts.append(str(text))
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": str(text)}]})
        else:  # user (and anything else) -> user turn
            contents.append({"role": "user", "parts": [{"text": str(text)}]})
    return ("\n".join(system_parts) or None), contents


def _to_tools(raw: Any) -> list[dict[str, Any]] | None:
    """Normalize a scenario's tools into a Gemini ``tools`` list (function declarations)."""
    if not raw:
        return None
    if not isinstance(raw, list):
        raise ProviderError(f"scenario 'tools' must be a list, got {type(raw).__name__}")
    declarations: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            declarations.append(
                {"name": item, "parameters_json_schema": {"type": "object", "properties": {}}}
            )
        elif isinstance(item, dict):
            declarations.append(item.get("function", item))  # accept OpenAI-shaped or bare
    return [{"function_declarations": declarations}] if declarations else None


def _build_config(
    system_instruction: str | None, tools: list[dict[str, Any]] | None, gen: dict
) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if system_instruction:
        config["system_instruction"] = system_instruction
    if tools:
        config["tools"] = tools
    if "temperature" in gen:
        config["temperature"] = gen["temperature"]
    if "top_p" in gen:
        config["top_p"] = gen["top_p"]
    max_tokens = gen.get("max_output_tokens", gen.get("max_tokens"))
    if max_tokens is not None:
        config["max_output_tokens"] = max_tokens
    if "seed" in gen:
        config["seed"] = gen["seed"]
    return config


def _candidate_parts(candidate: Any) -> list[Any]:
    content = getattr(candidate, "content", None)
    return getattr(content, "parts", None) or []


def _parse_function_calls(parts: list[Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for part in parts:
        fc = getattr(part, "function_call", None)
        name = getattr(fc, "name", None)
        if not name:
            continue
        args = getattr(fc, "args", None)
        calls.append(ToolCall(name=name, arguments=args if isinstance(args, dict) else {}))
    return calls


def _part_text(parts: list[Any]) -> str:
    return "".join(getattr(p, "text", None) or "" for p in parts)


def _detect_refusal(candidate: Any, prompt_feedback: Any, text: str) -> bool:
    fr = getattr(candidate, "finish_reason", None)
    fr_name = getattr(fr, "name", None) or (str(fr) if fr is not None else "")
    if fr_name in _BLOCKED_FINISH:
        return True
    if prompt_feedback is not None and getattr(prompt_feedback, "block_reason", None):
        return True
    return looks_like_refusal(text)


def _model_turn_content(parts: list[Any], text: str) -> dict[str, Any]:
    """Rebuild the model's turn (incl. function_call parts) to append to the conversation.

    Gemini 3.x rejects a fed-back function call whose opaque ``thought_signature`` was
    dropped ("Function call is missing a thought_signature in functionCall parts"), so we
    echo it (and the call ``id``) back verbatim on each reconstructed function-call part.
    Earlier (2.5) models don't emit it; the field is simply absent there, so this is safe.
    """
    out: list[dict[str, Any]] = []
    for part in parts:
        fc = getattr(part, "function_call", None)
        if getattr(fc, "name", None):
            call: dict[str, Any] = {"name": fc.name, "args": getattr(fc, "args", {}) or {}}
            fc_id = getattr(fc, "id", None)
            if fc_id:
                call["id"] = fc_id
            fc_part: dict[str, Any] = {"function_call": call}
            signature = getattr(part, "thought_signature", None)
            if signature:
                fc_part["thought_signature"] = signature
            out.append(fc_part)
    if text:
        out.append({"text": text})
    return {"role": "model", "parts": out or [{"text": text}]}


def _function_response_content(
    calls: list[ToolCall], tool_results: dict[str, Any]
) -> dict[str, Any]:
    parts = []
    for call in calls:
        result = tool_results.get(call.name, _DEFAULT_TOOL_RESULT)
        response = result if isinstance(result, dict) else {"result": result}
        parts.append({"function_response": {"name": call.name, "response": response}})
    return {"role": "user", "parts": parts}


class GoogleAdapter(ProviderAdapter):
    name = "google"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def preflight(self) -> None:
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_google_client()
        return self._client

    def _generate(self, client: Any, model_id: str, contents: list, config: dict, scenario_id: str):
        try:
            response = client.models.generate_content(
                model=model_id, contents=contents, config=config
            )
        except ProviderError:
            raise
        except Exception as exc:  # SDK/network error → friendly, key-safe ProviderError
            raise ProviderError(_explain_api_error(exc, model_id)) from exc
        if not (getattr(response, "candidates", None) or []):
            raise ProviderError(
                f"Gemini returned no candidates for scenario {scenario_id!r} on {model_id!r}."
            )
        return response

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        system_instruction, contents = _to_contents(list(scenario.input.get("messages", [])))
        tools = _to_tools(scenario.input.get("tools"))
        gen = {
            k: scenario.input[k]
            for k in ("temperature", "top_p", "max_tokens", "max_output_tokens", "seed")
            if k in scenario.input
        }
        config = _build_config(system_instruction, tools, gen)
        tool_results = scenario.input.get("tool_results") or {}

        client = self._get_client()
        all_tool_calls: list[ToolCall] = []
        final_text = ""
        refused = False
        tokens_in = tokens_out = 0
        started = time.perf_counter()

        for _turn in range(MAX_TOOL_TURNS):
            response = self._generate(client, model_id, contents, config, scenario.id)
            candidate = response.candidates[0]
            parts = _candidate_parts(candidate)
            final_text = _part_text(parts)
            turn_calls = _parse_function_calls(parts)
            all_tool_calls.extend(turn_calls)
            refused = refused or _detect_refusal(
                candidate, getattr(response, "prompt_feedback", None), final_text
            )

            usage = getattr(response, "usage_metadata", None)
            tokens_in += getattr(usage, "prompt_token_count", 0) or 0
            tokens_out += getattr(usage, "candidates_token_count", 0) or 0

            if not turn_calls:
                break  # final answer reached
            contents.append(_model_turn_content(parts, final_text))
            contents.append(_function_response_content(turn_calls, tool_results))

        latency_ms = (time.perf_counter() - started) * 1000.0
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            messages=contents,
            tool_calls=all_tool_calls,
            final_output=final_text,
            refused=refused,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
