"""LLM-as-judge for the semantic diff signal (spec 6B).

BYO-key, deterministic (temperature 0 where the model allows it), and offline-testable
via an injected client. The judge answers one yes/no question: are two responses to the
same request behaviorally equivalent? Errors become a key-safe ``ProviderError``.

WHY MORE THAN ONE JUDGE (MP-143). `[M] 2026-08-31` `build_judge` rejected any non-OpenAI
judge model -- honestly, with a clear error, but it left a real hole. Cross-vendor is wedge
item 3, and for an app with no tool calls (a classifier, a summariser -- the long-tail wedge
this project exists for) the judge is the ONLY CI-failing channel that responds to a change
in MEANING. Without it, a Groq- or Gemini-only user gets an engine that cannot catch a
wrong-but-confident answer at all; only a refusal could fail their build.

The adapters already solved this shape once, so the judges follow them rather than inventing
a second pattern: the four OpenAI-compatible hosts reuse the OpenAI judge with a different
``base_url`` and key env (exactly as ``build_openai_compatible_adapter`` reuses the OpenAI
adapter), and Gemini gets a thin judge whose SDK calls mirror ``GoogleAdapter`` line for line
-- the shapes there are already verified against the installed SDK, and this project has been
bitten twice by writing provider calls from memory.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol

from modelpin.providers.base import ProviderError
from modelpin.providers.google import build_google_client
from modelpin.providers.google import _explain_api_error as _explain_google_error
from modelpin.providers.openai import (
    OPENAI_COMPATIBLE_PROVIDERS,
    _explain_api_error,
    _is_reasoning_model,
    build_openai_client,
)

_SYSTEM = (
    "You compare two AI assistant responses to the SAME user request and decide whether "
    "the second (candidate) is behaviorally EQUIVALENT to the first (baseline). "
    "Equivalent = it accomplishes the same task and conveys the same essential meaning or "
    "decision, even if the wording, length, formatting, or tone differ. "
    "NOT equivalent = a different conclusion or answer, a changed/missing key fact that "
    "alters the outcome, or a refusal where the baseline helped (or vice versa). "
    "Ignore superficial differences in phrasing or style. "
    'Respond with ONLY a JSON object: {"equivalent": true|false, "reason": "<=12 words"}.'
)


def _parse_equivalent(content: str) -> bool:
    """Parse the judge's JSON verdict. FP-safe default: anything unparseable -> equivalent
    (no flag), so a malformed judge response can never manufacture a false alarm."""
    text = (content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return bool(obj.get("equivalent", True))
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    low = text.lower()
    if '"equivalent": false' in low or '"equivalent":false' in low or "not equivalent" in low:
        return False
    return True  # FP-safe default


def _judge_prompt(reference: str, candidate: str, task: Optional[str]) -> str:
    """The user turn, identical across judges.

    Shared deliberately: two judges asking materially different questions would make a
    cross-vendor comparison of their verdicts meaningless, and the semantic channel's
    calibration (`docs/fp-measurement.md`) is measured against THIS wording.
    """
    return (f"User request:\n{task}\n\n" if task else "") + (
        f"Baseline response:\n{reference}\n\nCandidate response:\n{candidate}"
    )


class Judge(Protocol):
    """What `diff/semantic.py` needs of a judge. The whole surface, deliberately two calls."""

    def preflight(self) -> None: ...

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool: ...


class OpenAIJudge:
    """OpenAI(-compatible) equivalence judge. Injectable client makes it offline-testable.

    ``base_url`` + ``api_key_env`` are what make this cover Groq/OpenRouter/Together/Cerebras
    too: they serve the same Chat Completions surface, so the judge is reused rather than
    duplicated -- the same reasoning as ``build_openai_compatible_adapter``.
    """

    def __init__(
        self,
        model: str,
        client: Any | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        label: str = "OpenAI",
    ) -> None:
        self._model = model
        self._client = client
        self._api_key_env = api_key_env
        self._base_url = base_url
        self.label = label

    def preflight(self) -> None:
        """Validate key + SDK before any judging — no network call."""
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_openai_client(self._api_key_env, base_url=self._base_url)
        return self._client

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool:
        client = self._get_client()
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _judge_prompt(reference, candidate, task)},
            ],
        }
        if not _is_reasoning_model(self._model):
            request["temperature"] = 0  # deterministic judging
            # OpenRouter ROUTES. `[S] 2026-08-31` openrouter.ai/docs/features/provider-routing:
            # "providers that don't support all the LLM parameters specified in your request
            # can still receive the request, but will ignore unknown parameters." So
            # `temperature: 0` returns HTTP 200 and is silently dropped upstream, and the
            # judge quietly becomes non-deterministic -- no error to catch, and
            # `docs/fp-measurement.md`'s calibration, which is measured at temperature 0,
            # stops describing the run. `require_parameters` narrows routing to upstreams that
            # honour it. The cost is fewer (possibly pricier) providers; for a judge whose
            # entire value is determinism that is the right side to fail on.
            if self._base_url and "openrouter.ai" in self._base_url:
                request["extra_body"] = {"provider": {"require_parameters": True}}
        try:
            response = client.chat.completions.create(**request)
        except ProviderError:
            raise
        except Exception as exc:  # SDK/network error -> friendly, key-safe ProviderError
            raise ProviderError(
                # MP-136: the judge spends the user's key too, so a rejected key here
                # must name the variable to fix exactly as the replay path does.
                #
                # `self.label` is passed, and that is not cosmetic. `[M]` a provider-SDK review
                # 2026-08-31: omitting it defaulted to "OpenAI", so a rejected GROQ key read
                # "OpenAI call for model 'llama-3.3-70b-versatile' failed ... Modelpin read
                # your key from GROQ_API_KEY - check that variable holds a current OpenAI key."
                # The message named the right variable and the wrong vendor in one sentence --
                # MP-136's exact defect, reintroduced on the host path MP-143 opened.
                _explain_api_error(exc, self._model, self.label, api_key_env=self._api_key_env)
            ) from exc
        choices = getattr(response, "choices", None) or []
        content = (choices[0].message.content or "") if choices else ""
        return _parse_equivalent(content)


class GoogleJudge:
    """Gemini equivalence judge. Every SDK shape here mirrors ``GoogleAdapter``.

    `system_instruction` and `temperature` go in `config`, the prompt goes in `contents`, and
    the answer is read off `candidates[0].content.parts[*].text` -- the same three facts the
    adapter uses, which are the ones verified against the installed SDK. Written this way on
    purpose: a judge that called the SDK from memory would be the third time this project was
    bitten by drift.
    """

    def __init__(self, model: str, client: Any | None = None, label: str = "Google") -> None:
        self._model = model
        self._client = client
        self.label = label

    def preflight(self) -> None:
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_google_client()
        return self._client

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool:
        client = self._get_client()
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[
                    {
                        "role": "user",
                        "parts": [{"text": _judge_prompt(reference, candidate, task)}],
                    }
                ],
                config={"system_instruction": _SYSTEM, "temperature": 0},
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(_explain_google_error(exc, self._model)) from exc
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            # FP-safe, like `_parse_equivalent`'s default: a judge that said nothing must
            # never be read as having said "not equivalent".
            return True
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        return _parse_equivalent("".join(getattr(p, "text", None) or "" for p in parts))


#: Model-id prefixes that name their own vendor unambiguously. Used ONLY to spare the common
#: case a config key -- never to guess for an id that could belong to two hosts. `[M]` A Groq
#: id (`llama-3.3-70b-versatile`) and an OpenRouter id (`openai/gpt-oss-120b`) are
#: indistinguishable from the string alone, and OpenRouter ids legitimately contain a slash,
#: so a `vendor/model` parse would misroute rather than fail. Those cases must say which host.
_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt")
_GOOGLE_PREFIXES = ("gemini-", "models/gemini")


def infer_judge_provider(model: str) -> str | None:
    """The judge's host, when the model id names it beyond doubt. None otherwise."""
    if model.startswith(_OPENAI_PREFIXES):
        return "openai"
    if model.startswith(_GOOGLE_PREFIXES):
        return "google"
    return None


#: Every host a judge can run on. `fake` is absent on purpose: `_build_judge` disables the
#: judge entirely on the offline provider, so a "fake judge" would be a second, divergent way
#: to express the same thing.
JUDGE_PROVIDERS: tuple[str, ...] = ("openai", "google", *OPENAI_COMPATIBLE_PROVIDERS)


def build_judge(model: str, provider: str | None = None, client: Any | None = None) -> Judge:
    """Construct a judge from the judge model id and, when needed, its host.

    The judge is independent of the models being compared, so a cross-vendor check can use
    any host's judge. `provider` is resolved in this order:

      1. what the caller passed (`judge_provider:` in modelpin.yaml, or the replay provider);
      2. what the model id unambiguously names (`gpt-*` -> OpenAI, `gemini-*` -> Google);
      3. nothing -- and then this raises, naming the key to set. It must not guess: routing a
         Groq id to OpenAI would spend the wrong key against the wrong host and report the
         failure as a judge error.
    """
    resolved = (provider or "").strip().lower() or infer_judge_provider(model)
    if resolved is None:
        raise ProviderError(
            f"cannot tell which host should run the judge model {model!r}. Set "
            f"`judge_provider:` in modelpin.yaml to one of: {', '.join(JUDGE_PROVIDERS)}."
        )
    if resolved == "openai":
        return OpenAIJudge(model, client=client)
    if resolved == "google":
        return GoogleJudge(model, client=client)
    if resolved in OPENAI_COMPATIBLE_PROVIDERS:
        cfg = OPENAI_COMPATIBLE_PROVIDERS[resolved]
        return OpenAIJudge(
            model,
            client=client,
            api_key_env=cfg["api_key_env"],
            base_url=cfg["base_url"],
            label=cfg["label"],
        )
    raise ProviderError(
        f"no semantic judge available on host {resolved!r} (judge model {model!r}). "
        f"Supported: {', '.join(JUDGE_PROVIDERS)}."
    )
