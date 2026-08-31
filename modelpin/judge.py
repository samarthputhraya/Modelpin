"""LLM-as-judge for the semantic diff signal (spec 6B).

BYO-key, deterministic (temperature 0 where the model allows it), and offline-testable
via an injected client. The judge answers one yes/no question: are two responses to the
same request behaviorally equivalent? Errors become a key-safe ``ProviderError``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from modelpin.providers.openai import (
    _explain_api_error,
    _is_reasoning_model,
    build_openai_client,
)
from modelpin.providers.base import ProviderError

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


class OpenAIJudge:
    """OpenAI-backed equivalence judge. Injectable client makes it offline-testable."""

    def __init__(
        self, model: str, client: Any | None = None, api_key_env: str = "OPENAI_API_KEY"
    ) -> None:
        self._model = model
        self._client = client
        self._api_key_env = api_key_env

    def preflight(self) -> None:
        """Validate key + SDK before any judging — no network call."""
        self._get_client()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = build_openai_client(self._api_key_env)
        return self._client

    def equivalent(self, reference: str, candidate: str, task: Optional[str] = None) -> bool:
        client = self._get_client()
        user = (f"User request:\n{task}\n\n" if task else "") + (
            f"Baseline response:\n{reference}\n\nCandidate response:\n{candidate}"
        )
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
        }
        if not _is_reasoning_model(self._model):
            request["temperature"] = 0  # deterministic judging
        try:
            response = client.chat.completions.create(**request)
        except ProviderError:
            raise
        except Exception as exc:  # SDK/network error -> friendly, key-safe ProviderError
            raise ProviderError(
                # MP-136: the judge spends the user's key too, so a rejected key here
                # must name the variable to fix exactly as the replay path does.
                _explain_api_error(exc, self._model, api_key_env=self._api_key_env)
            ) from exc
        choices = getattr(response, "choices", None) or []
        content = (choices[0].message.content or "") if choices else ""
        return _parse_equivalent(content)


def build_judge(model: str, client: Any | None = None) -> OpenAIJudge:
    """Construct a judge from the judge model id. The judge is independent of the models
    being compared (so a cross-vendor check can use an OpenAI judge); only OpenAI judge
    models are supported so far."""
    if model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return OpenAIJudge(model, client=client)
    raise ProviderError(
        f"no semantic judge available for model {model!r} yet (use an OpenAI judge model "
        "such as gpt-4o-mini)."
    )
