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

from modelpin.models import IncompleteReason, Scenario, ToolCall, Trace
from modelpin.providers._common import looks_like_refusal, scrub_secrets
from modelpin.providers.base import ProviderAdapter, ProviderError

MAX_TOOL_TURNS = 6
_DEFAULT_TOOL_RESULT: dict[str, Any] = {"status": "ok"}
_API_KEY_ENVS: tuple[str, ...] = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

#: finish_reason names that mean the model was blocked or declined.
_BLOCKED_FINISH: frozenset[str] = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)


def _import_genai() -> Any:
    try:
        from google import genai
    except ImportError as exc:  # optional dependency
        raise ProviderError(
            "The Google GenAI SDK is not installed. Install it with: pip install google-genai"
        ) from exc
    return genai


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


#: The SDK reads BOTH of these and `GOOGLE_GENAI_USE_ENTERPRISE` WINS on conflict
#: (`google-genai` 2.19.0, `_api_client.py:650-676`); `vertexai=` is documented there as the
#: "legacy flag for `enterprise`". Reading only the legacy name is not a style choice - see
#: `_vertex_selected`.
_VERTEX_ENVS: tuple[str, ...] = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI")


def _vertex_selected() -> bool:
    """Whether the caller asked for the Vertex backend, in the SDK's own precedence order.

    [M] provider-sdk-verifier 2026-08-25: reading only `GOOGLE_GENAI_USE_VERTEXAI` was a
    correctness bug, not a naming nit. With `GOOGLE_GENAI_USE_ENTERPRISE=true` set, Modelpin
    took its API-key branch while the SDK - reading the env itself on an unpinned `vertexai=` -
    built a VERTEX client underneath: `base_url=https://aiplatform.googleapis.com/`,
    `api_version=v1beta1`, and an AI Studio key posted to Vertex. Modelpin would have believed
    it was on one backend while running on the other.
    """
    for name in _VERTEX_ENVS:
        raw = os.environ.get(name)
        if raw is not None and raw.strip():
            return _truthy(raw)
    return False


def build_google_client(api_key_envs: tuple[str, ...] = _API_KEY_ENVS) -> Any:
    """Construct a real Gemini client. Two BACKENDS, both the end user's own credentials.

    Default is the AI Studio path: an API key from ``GEMINI_API_KEY``. Setting
    ``GOOGLE_GENAI_USE_VERTEXAI=true`` selects Vertex AI instead, authenticated by Application
    Default Credentials rather than a key. Env names are the SDK's own documented contract, so
    a user who already runs the SDK elsewhere needs no Modelpin-specific setup.

    ADR-0008 is unchanged and both paths honour it: Modelpin never hardcodes, stores or ships a
    credential, and reads only what the caller already put in their own environment. ADC is a
    different SHAPE of the user's credential, not somebody else's.

    Why the second backend exists, `[M]` 2026-08-25, measured on a real account: the AI Studio
    path bills a **prepay wallet that is separate from Google Cloud billing**. With ₹28,582 of
    Cloud credit available, every current model returned
    ``429 RESOURCE_EXHAUSTED - "Your prepayment credits are depleted"`` — and a freshly created
    key in the credited project was refused identically, because prepay is a property of the
    BILLING ACCOUNT, not the project. The same project on Vertex answered normally and billed
    against the Cloud credit. Vertex also still serves models AI Studio has retired: `[M]`
    ``gemini-2.5-flash`` is ``404 "no longer available to new users"`` on one and live on the
    other. So the backend is not a preference, it decides what a user can run and pay for.
    """
    genai = _import_genai()

    if _vertex_selected():
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project:
            raise ProviderError(
                f"{_VERTEX_ENVS[1]} (or {_VERTEX_ENVS[0]}) is set but GOOGLE_CLOUD_PROJECT is "
                "empty. Vertex bills a specific project — export it and retry, e.g. "
                "GOOGLE_CLOUD_PROJECT=my-project."
            )
        # `global` is BOTH the SDK's own default and the only location that serves the CURRENT
        # models. [M] provider-sdk-verifier 2026-08-25, free `count_tokens` against a real
        # project: every `gemini-3.x` id 404s on `us-central1` and is served on `global`; only
        # the legacy 2.5 family works regionally. Modelpin exists to test NEW models, so a
        # regional default would have made the backend unable to reach the models that matter.
        # Override for data residency - `global` routes dynamically and guarantees no
        # processing region.
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "global"
        try:
            import google.auth  # hard dependency of google-genai; kept lazy inside the branch

            # [M] Constructing a Vertex client proves NOTHING about credentials: the SDK only
            # calls `load_auth` when `project` is absent, and we require it, so ADC is deferred
            # to the first REQUEST. Without this line `preflight()` passes and the run dies
            # mid-replay with a raw `DefaultCredentialsError` - after the user has been told
            # what the run will cost.
            google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            return genai.Client(vertexai=True, project=project, location=location)
        except Exception as exc:  # noqa: BLE001 - SDK raises several unrelated types here
            raise ProviderError(
                f"Could not build a Vertex client for project {scrub_secrets(project)!r} in "
                f"{location}: {scrub_secrets(str(exc))}. Vertex uses Application Default "
                "Credentials, NOT an API key — run `gcloud auth application-default login`, "
                "and ensure aiplatform.googleapis.com is enabled on the project."
            ) from exc

    api_key = next((os.environ.get(e, "").strip() for e in api_key_envs if os.environ.get(e)), "")
    if not api_key:
        raise ProviderError(
            f"{api_key_envs[0]} is not set. Modelpin uses YOUR own API key "
            "(cost + provider ToS) — export it and retry. To bill Google Cloud instead of an "
            "API key, set GOOGLE_GENAI_USE_VERTEXAI=true (or GOOGLE_GENAI_USE_ENTERPRISE=true) "
            "and GOOGLE_CLOUD_PROJECT."
        )
    # `vertexai=False` is NOT redundant. [M] Left unpinned, the SDK re-reads the environment and
    # silently builds a VERTEX client here when GOOGLE_GENAI_USE_ENTERPRISE is set - posting this
    # AI Studio key to aiplatform.googleapis.com. Pin the backend the branch decided on.
    return genai.Client(api_key=api_key, vertexai=False)


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


def _finish_reason_name(candidate: Any) -> str:
    """The SDK's FinishReason as a plain string ('' when absent). Enum in new SDKs, str in old."""
    fr = getattr(candidate, "finish_reason", None)
    return getattr(fr, "name", None) or (str(fr) if fr is not None else "")


#: Gemini finish reasons that mean the run was cut short. `_BLOCKED_FINISH` is REUSED verbatim
#: rather than widened -- it also drives `refused`, which is an fp-guardian sensitivity surface.
_FINISH_TO_REASON: dict[str, IncompleteReason] = {
    "MAX_TOKENS": IncompleteReason.max_tokens,
    "MALFORMED_FUNCTION_CALL": IncompleteReason.malformed_tool_call,
    "UNEXPECTED_TOOL_CALL": IncompleteReason.malformed_tool_call,
    "TOO_MANY_TOOL_CALLS": IncompleteReason.tool_turns,
}
#: Reasons that mean the model finished on its own terms.
_COMPLETE_FINISH: frozenset[str] = frozenset({"STOP", "FINISH_REASON_UNSPECIFIED", ""})


def _incomplete_reason(candidate: Any) -> IncompleteReason | None:
    """Why this Gemini turn ended early, or None when it finished normally."""
    name = _finish_reason_name(candidate)
    if name in _COMPLETE_FINISH:
        return None
    if name in _BLOCKED_FINISH:
        return IncompleteReason.content_filter
    return _FINISH_TO_REASON.get(name, IncompleteReason.provider_other)


def _detect_refusal(candidate: Any, prompt_feedback: Any, text: str) -> bool:
    fr_name = _finish_reason_name(candidate)
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
        incomplete: IncompleteReason | None = None
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
            # First writer wins, like `refused`.
            incomplete = incomplete or _incomplete_reason(candidate)

            usage = getattr(response, "usage_metadata", None)
            tokens_in += getattr(usage, "prompt_token_count", 0) or 0
            tokens_out += getattr(usage, "candidates_token_count", 0) or 0

            if not turn_calls:
                break  # final answer reached
            contents.append(_model_turn_content(parts, final_text))
            contents.append(_function_response_content(turn_calls, tool_results))
        else:
            # for/else: never `break`n, so every turn still wanted a tool -- OUR cap ended it.
            # Overrides any provider reason; our cap is the more actionable fact.
            incomplete = IncompleteReason.tool_turns

        latency_ms = (time.perf_counter() - started) * 1000.0
        return Trace(
            scenario_id=scenario.id,
            model_id=model_id,
            run_idx=run_idx,
            messages=contents,
            tool_calls=all_tool_calls,
            final_output=final_text,
            refused=refused,
            incomplete_reason=incomplete,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
