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
from modelpin.providers._common import elide, looks_like_refusal, scrub_secrets
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


#: Transient-failure retries for every Gemini request, run by the SDK's own retry loop.
#:
#: `[M] 2026-09-15` live on Vertex: `google-genai` 2.19.0 builds its retry policy from
#: `http_options.retry_options`, and when that is absent it uses `stop_after_attempt(1)` --
#: NO retry at all (`_api_client.py::retry_args`). One transient `429 RESOURCE_EXHAUSTED`
#: from Vertex's shared quota therefore killed `modelpin baseline` (10 replays, exit 4) and
#: `modelpin check` after every candidate call had already been paid for. The same commands
#: re-run a minute later succeeded. The OpenAI adapter already gets this from its SDK
#: (`REPLAY_MAX_RETRIES`); this puts the Google path on equal footing.
#:
#: Retrying cannot bias a measurement: the SDK retries only requests that returned an error
#: status (408, 429, 5xx) or a transport failure, so no model output is ever discarded and
#: re-rolled. Worst case is roughly 2+4+8+16+32+60+60 s of backoff on one call before the
#: error surfaces, which is the right trade against losing a whole paid run.
RETRY_OPTIONS: dict[str, Any] = {"attempts": 8, "initial_delay": 2.0, "max_delay": 60.0}
_HTTP_OPTIONS: dict[str, Any] = {"retry_options": RETRY_OPTIONS}
#: Sent in every request config; see `_build_config`.
AFC_DISABLED: dict[str, bool] = {"disable": True}


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


#: The SDK reads BOTH of these and `GOOGLE_GENAI_USE_ENTERPRISE` WINS on conflict
#: (`google-genai` 2.19.0, `_api_client.py:650-676`); `vertexai=` is documented there as the
#: "legacy flag for `enterprise`". Reading only the legacy name is not a style choice - see
#: `_vertex_selected`.
_VERTEX_ENVS: tuple[str, ...] = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI")


def _vertex_selected() -> bool:
    """Whether the caller asked for the Vertex backend, in the SDK's own precedence order.

    [M] provider-SDK review 2026-08-25: reading only `GOOGLE_GENAI_USE_VERTEXAI` was a
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
        # models. [M] provider-SDK review 2026-08-25, free `count_tokens` against a real
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
            return genai.Client(
                vertexai=True, project=project, location=location, http_options=_HTTP_OPTIONS
            )
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
    return genai.Client(api_key=api_key, vertexai=False, http_options=_HTTP_OPTIONS)


def _explain_api_error(
    exc: Exception, model_id: str, api_key_envs: tuple[str, ...] = _API_KEY_ENVS
) -> str:
    """Key-safe message for a Gemini SDK/network error (mirrors the OpenAI explainer).

    MP-136 applies here too, and the row did not mention it: the 401/403 branch named no
    variable either, so a Gemini user with a stale key got the same guess-the-variable
    message. The NAME is safe to print; the exception text (which may embed a key fragment)
    is still dropped on this branch.
    """
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    base = f"Gemini call for model {scrub_secrets(model_id)!r} failed"
    if code in (401, 403):
        where = f" Modelpin read your key from {api_key_envs[0]} — check it holds a current key."
        return f"{base}: API key rejected or lacks access [{name} {code}].{where}"
    if code == 404:
        return f"{base}: model not found — check the id [{name} 404]."
    if code == 429:
        # Retries were already spent by the SDK (`RETRY_OPTIONS`), so say so: `[M] 2026-09-15` a
        # heavily loaded Vertex project exhausted all of them, and the bare "rate limit" line
        # read as if nothing had been tried.
        return (
            f"{base}: rate limit or quota exceeded, and it did not clear after "
            f"{RETRY_OPTIONS['attempts'] - 1} automatic retries with backoff -- wait and re-run, "
            f"lower --runs, or raise the model's quota [{name} 429]."
        )
    detail = elide(scrub_secrets(str(getattr(exc, "message", None) or exc)))
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
    # Modelpin drives the tool loop itself with the scenario's canned results, so the SDK's
    # automatic function calling is never what we replay. It must be disabled EXPLICITLY, and on
    # every request: `[M] 2026-09-15` google-genai 2.19.0 treats an unset flag as "enabled" even
    # when no tools are sent, and logs "Direct use of automatic function calling (AFC) ... is
    # not recommended" onto the user's console in the middle of every live run.
    config: dict[str, Any] = {"automatic_function_calling": AFC_DISABLED}
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


#: Namespaces Gemini sometimes prepends to a DECLARED function's name.
#:
#: `[M] 2026-09-09` `gemini-2.5-flash-lite` called `default_api.request_box` and
#: `default_api_charge_customer` for tools declared as `request_box` / `charge_customer`, in 13
#: of 540 runs (MP-237). The trajectory channel keys on the name, so the prefix alone made one
#: tool look like two, and 4 prefixed runs of 5 is enough to publish a hard regression on a
#: same-model null. The prefix is stripped only when what remains is a tool the scenario
#: declared; any other undeclared name (e.g. the built-in `run_code`) is recorded as-is,
#: because a model calling a tool it was never given IS behavior worth seeing.
_SDK_NAMESPACE_PREFIXES: tuple[str, ...] = ("default_api.", "default_api_")


def _declared_name(raw: str, declared: frozenset[str]) -> str:
    if raw in declared:
        return raw
    for prefix in _SDK_NAMESPACE_PREFIXES:
        if raw.startswith(prefix) and raw[len(prefix) :] in declared:
            return raw[len(prefix) :]
    return raw


def _declared_tool_names(tools: list[dict[str, Any]] | None) -> frozenset[str]:
    return frozenset(
        str(d.get("name"))
        for group in tools or []
        for d in group.get("function_declarations", [])
        if isinstance(d, dict) and d.get("name")
    )


def _parse_function_calls(
    parts: list[Any], declared: frozenset[str] = frozenset()
) -> list[tuple[str, ToolCall]]:
    """Each function call as (the name the model sent, the call as recorded)."""
    calls: list[tuple[str, ToolCall]] = []
    for part in parts:
        fc = getattr(part, "function_call", None)
        name = getattr(fc, "name", None)
        if not name:
            continue
        args = getattr(fc, "args", None)
        recorded = ToolCall(
            name=_declared_name(name, declared), arguments=args if isinstance(args, dict) else {}
        )
        calls.append((name, recorded))
    return calls


def _part_text(parts: list[Any]) -> str:
    return "".join(getattr(p, "text", None) or "" for p in parts)


def _finish_reason_name(candidate: Any) -> str:
    """The SDK's FinishReason as a plain string ('' when absent). Enum in new SDKs, str in old."""
    fr = getattr(candidate, "finish_reason", None)
    return getattr(fr, "name", None) or (str(fr) if fr is not None else "")


#: Gemini finish reasons that mean the run was cut short. `_BLOCKED_FINISH` is REUSED verbatim
#: rather than widened -- it also drives `refused`, which is an FP review sensitivity surface.
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
    calls: list[tuple[str, ToolCall]], tool_results: dict[str, Any]
) -> dict[str, Any]:
    parts = []
    for sent_name, call in calls:
        result = tool_results.get(call.name, _DEFAULT_TOOL_RESULT)
        response = result if isinstance(result, dict) else {"result": result}
        # Answered under the name the model SENT, so the conversation stays well-formed.
        parts.append({"function_response": {"name": sent_name, "response": response}})
    return {"role": "user", "parts": parts}


def _prompt_blocked(response: Any) -> bool:
    """True when Gemini returned no candidate because it blocked the prompt itself."""
    if getattr(response, "candidates", None):
        return False
    feedback = getattr(response, "prompt_feedback", None)
    return bool(feedback is not None and getattr(feedback, "block_reason", None))


class GoogleAdapter(ProviderAdapter):
    name = "google"
    #: The SDK client is thread-safe, so `replay` may send a scenario's runs together.
    parallel_safe = True

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
        if not (getattr(response, "candidates", None) or []) and not _prompt_blocked(response):
            raise ProviderError(
                f"Gemini returned no candidates for scenario {scenario_id!r} on {model_id!r}."
            )
        return response

    def run(self, scenario: Scenario, model_id: str, run_idx: int = 0) -> Trace:
        system_instruction, contents = _to_contents(list(scenario.input.get("messages", [])))
        tools = _to_tools(scenario.input.get("tools"))
        declared = _declared_tool_names(tools)
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
            if _prompt_blocked(response):
                # Gemini's safety filter declined the PROMPT: no candidate at all. That is a
                # refusal -- the behavior the refusal channel exists to see -- not a failed
                # call. `[M] 2026-09-15` gemini-3.8-flash blocked a voicerag-suite prompt on
                # every run, and treating it as an error killed the whole `baseline`.
                refused = True
                incomplete = incomplete or IncompleteReason.content_filter
                final_text = ""
                break
            candidate = response.candidates[0]
            parts = _candidate_parts(candidate)
            final_text = _part_text(parts)
            turn_calls = _parse_function_calls(parts, declared)
            all_tool_calls.extend(call for _, call in turn_calls)
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
