"""A baseline stores what the diff reads and nothing more -- because we tell people to commit it.

MP-240. `actions/README.md` instructs `git add ... .modelpin/`, and `.gitignore` deliberately
UN-ignores `baseline-*.json` so that instruction works. So a baseline file is, by our own
documented default, a file that gets published -- often to a public repository.

`[M] 2026-09-09` security review: `save_baseline` persisted each trace with
`model_dump(mode="json")`, i.e. the FULL trace, including `messages` -- every prompt, verbatim,
once per run. A key placed in a 2-scenario baseline's prompt was measured appearing **10 times**
in the file. The only guard was a warning that fires on key-SHAPED tokens, which is silent for
proprietary system prompts, customer records and PII.

And none of it was needed. Nothing in `modelpin/diff/` reads `Trace.messages`; the comparison
runs on tool calls, final output, refusal, tokens and latency. The prompts already live in the
user's scenario files, which they commit deliberately as their test fixtures. What `messages`
added on disk was a copy of those prompts multiplied by the run count, the full intermediate
tool-loop transcript, and Gemini's `thought_signature` bytes -- which must stay raw in memory to
feed back to the SDK mid-loop, and which are opaque base64 once written.

`scripts/fp_measurement.py` had already made exactly this choice for its own artifacts
("Traces minus `messages`: everything the diff reads, none of the prompt it does not"). The
baseline, the file we actually ask users to publish, had not.
"""

from __future__ import annotations

import json
from pathlib import Path

from modelpin.models import ToolCall, Trace
from modelpin.storage import load_baseline, save_baseline

#: Shaped like a real key so the existing warning machinery would recognise it, but inert.
PLANTED = "sk-proj-PLANTEDnotarealkey000000000000000000"


def _trace(sid: str, run: int) -> Trace:
    return Trace(
        scenario_id=sid,
        model_id="m",
        run_idx=run,
        messages=[
            {"role": "system", "content": f"Internal policy. Service key: {PLANTED}."},
            {"role": "user", "content": "Refund order 4471, customer jane@example.com."},
        ],
        tool_calls=[ToolCall(name="lookup_order", arguments={"order_id": "4471"})],
        final_output="Refunded order 4471.",
        tokens_in=12,
        tokens_out=4,
    )


def _write(tmp_path: Path) -> Path:
    traces = {sid: [_trace(sid, r) for r in range(5)] for sid in ("refund", "status")}
    save_baseline(traces, "m", store_dir=str(tmp_path))
    files = list(tmp_path.rglob("baseline-*.json"))
    assert len(files) == 1, files
    return files[0]


def test_a_baseline_does_not_persist_the_prompt(tmp_path: Path) -> None:
    """The review's measurement, inverted: the planted key must appear ZERO times."""
    raw = _write(tmp_path).read_text(encoding="utf-8")
    copies = raw.count(PLANTED)
    assert copies == 0, (
        f"the planted key appears {copies} time(s) in the baseline file. A baseline is a file "
        "our own docs tell users to commit; it must not carry their prompts, which the diff "
        "never reads."
    )
    assert "Internal policy" not in raw, "the system prompt was persisted"
    assert "jane@example.com" not in raw, "the user's message (and its PII) was persisted"


def test_no_persisted_trace_carries_a_messages_field(tmp_path: Path) -> None:
    data = json.loads(_write(tmp_path).read_text(encoding="utf-8"))
    for sid, traces in data["scenarios"].items():
        for i, t in enumerate(traces):
            assert "messages" not in t, f"{sid} run {i} still persists `messages`"


def test_everything_the_diff_reads_survives_the_round_trip(tmp_path: Path) -> None:
    """Stripping must not cost a single signal, or it is a verdict change in disguise."""
    _write(tmp_path)
    loaded = load_baseline("m", store_dir=str(tmp_path))
    t = loaded["refund"][0]
    assert t.final_output == "Refunded order 4471."
    assert [c.name for c in t.tool_calls] == ["lookup_order"]
    assert t.tool_calls[0].arguments == {"order_id": "4471"}
    assert (t.tokens_in, t.tokens_out) == (12, 4)
    assert t.messages == [], "a loaded baseline rehydrates with no messages, by the field default"
    assert len(loaded["refund"]) == 5 and len(loaded["status"]) == 5


def test_an_old_baseline_with_messages_still_loads(tmp_path: Path) -> None:
    """Every baseline written before this change carries `messages`; they must keep loading."""
    path = _write(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for traces in data["scenarios"].values():
        for t in traces:
            t["messages"] = [{"role": "user", "content": "from an older version"}]
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_baseline("m", store_dir=str(tmp_path))
    assert loaded["refund"][0].final_output == "Refunded order 4471."
