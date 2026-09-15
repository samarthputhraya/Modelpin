"""Draft scenarios from one source file of the user's app, using the user's own model.

Writing scenario JSON by hand is the slowest part of a first run: two independent first-run
reviews of 0.4.0 found no blockers and named the same bottleneck. `modelpin draft <file>` reads
the ONE file the user names, asks their configured model to find the prompts and tools in it,
and writes REVIEWABLE drafts.

The rules that keep a draft honest, each enforced here rather than hoped for in a prompt:

* **Drafts are invisible to `baseline` and `check`.** They are written to `scenarios/.drafts/`,
  a hidden folder the scenario loader skips. Moving a file out is the review step; nothing is
  recorded or billed until a human has done it.
* **Copied, never invented, where the source says it.** A system prompt is kept only if it
  appears in the file (compared with whitespace collapsed); a tool only if its name appears in
  the file. Anything the model made up -- the user messages, suggested assertions -- is listed
  under `_draft.invented`, and suggested assertions are NOT placed in the live `assertions`
  block, because an invented expectation would measure the model's guess rather than the app.
* **One named file, disclosed before sending, secrets redacted.** Key-shaped strings are
  replaced before the file leaves the machine, and the user is told how many.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelpin.models import Scenario
from modelpin.providers._common import contains_secret, scrub_secrets
from modelpin.providers.base import ProviderAdapter, ProviderError

DRAFTS_DIRNAME = ".drafts"
#: Largest source file sent. A prompt module is small; past this it is a bundle or a dataset.
MAX_SOURCE_CHARS = 60_000
DRAFT_MAX_TOKENS = 8192

_INSTRUCTIONS = """You help a developer write regression-test scenarios for their LLM app.
Below is ONE source file from the app. Find the LLM calls in it: the system prompt(s), and any
tools/functions the model is offered. Then propose {count} realistic, distinct user requests
this app would receive, covering its main path, an edge case, and a request it should decline
(if the app has a policy).

Rules:
- "system_prompt" must be copied EXACTLY, character for character, from the file. If the file
  has no system prompt, use "".
- "tools" must only name tools/functions defined or passed to the model in the file, as OpenAI
  function specs: {{"type": "function", "function": {{"name", "description", "parameters"}}}}.
  Use [] if there are none.
- For each scenario give: "id" (snake_case), "name" (short), "user_message", "why" (one
  sentence), "suggested_must_contain" (0-2 short strings a correct answer must contain, only
  when the file makes the expected output unambiguous), and, when there are tools,
  "tool_results": an object mapping each tool name to a realistic JSON result that tool would
  return for THIS request (the test feeds it back to the model instead of running the tool).

Respond with ONLY a JSON object:
{{"system_prompt": "...", "tools": [...], "scenarios": [...]}}

SOURCE FILE ({name}):
{source}
"""


@dataclass
class DraftResult:
    written: list[Path] = field(default_factory=list)
    dropped_system_prompt: bool = False
    dropped_tools: list[str] = field(default_factory=list)
    redacted_secrets: bool = False


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _last_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("scenarios"), list):
            found = obj
    return found


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")[:60] or "draft"


def request_scenario(source_name: str, source: str, count: int) -> Scenario:
    """The one call `draft` makes, as a Scenario so every provider adapter can run it."""
    prompt = _INSTRUCTIONS.format(count=count, name=source_name, source=source)
    return Scenario(
        id="modelpin_draft",
        name="draft scenarios",
        input={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": DRAFT_MAX_TOKENS,
        },
    )


def draft_scenarios(
    source_path: Path,
    scenarios_dir: Path,
    adapter: ProviderAdapter,
    model_id: str,
    count: int = 3,
) -> DraftResult:
    raw = source_path.read_text(encoding="utf-8", errors="replace")
    if len(raw) > MAX_SOURCE_CHARS:
        raise ValueError(
            f"{source_path} is {len(raw):,} characters; draft reads at most {MAX_SOURCE_CHARS:,}. "
            "Point it at the module that builds your prompts, not a bundle or dataset."
        )
    result = DraftResult(redacted_secrets=contains_secret(raw))
    source = scrub_secrets(raw)

    trace = adapter.run(request_scenario(source_path.name, source, count), model_id)
    reply = _last_json_object(trace.final_output or "")
    if reply is None:
        raise ProviderError(
            f"{model_id} did not return the JSON draft format. Nothing was written; try again, "
            "or use a larger model."
        )

    system = str(reply.get("system_prompt") or "")
    if system and _collapse(system) not in _collapse(raw):
        result.dropped_system_prompt = True
        system = ""

    tools: list[dict[str, Any]] = []
    for tool in reply.get("tools") or []:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "")
        if name and re.search(rf"\b{re.escape(name)}\b", raw):
            tools.append({"type": "function", "function": fn})
        elif name:
            result.dropped_tools.append(name)

    out_dir = scenarios_dir / DRAFTS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in reply["scenarios"][:count]:
        if not isinstance(item, dict) or not item.get("user_message"):
            continue
        sid = _slug(str(item.get("id") or item.get("name") or "draft"))
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": str(item["user_message"])}
        ]
        scenario_input: dict[str, Any] = {"messages": messages, "temperature": 0}
        invented = ["user message", "suggested_must_contain"]
        if tools:
            scenario_input["tools"] = tools
            # Canned tool output is what makes an agent scenario runnable: without it every
            # call gets `{"status": "ok"}`, and `[M] 2026-09-15` gemini-3.1-flash-lite then
            # called `get_order` six times in a row looking for data that never came.
            names = {t["function"]["name"] for t in tools}
            results = item.get("tool_results")
            if isinstance(results, dict):
                kept = {k: v for k, v in results.items() if k in names}
                if kept:
                    scenario_input["tool_results"] = kept
                    invented.append("tool_results")
        doc = {
            "id": sid,
            "name": str(item.get("name") or sid),
            "input": scenario_input,
            "_draft": {
                "source": str(source_path),
                "review": (
                    "DRAFT written by `modelpin draft`. Replace the user message with a real "
                    "request from your app, check `tool_results` match what your tools really "
                    "return, add `assertions` you actually expect, then move this file out of "
                    ".drafts/ to use it."
                ),
                "copied_from_source": (["system prompt"] if system else [])
                + [f"tool {t['function']['name']}" for t in tools],
                "invented": invented,
                "why": str(item.get("why") or ""),
                "suggested_must_contain": [
                    str(s) for s in (item.get("suggested_must_contain") or []) if str(s).strip()
                ],
            },
        }
        Scenario(**{k: v for k, v in doc.items() if not k.startswith("_")})  # must load
        path = out_dir / f"{sid}.json"
        n = 2
        while path.exists():
            path = out_dir / f"{sid}_{n}.json"
            n += 1
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result.written.append(path)
    return result
