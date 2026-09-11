"""Tests for baseline persistence (no network)."""

import os

import pytest

from modelpin.models import ToolCall, Trace
from modelpin.storage import BaselineError, baseline_path, load_baseline, save_baseline


def _traces():
    return {
        "s1": [
            Trace(scenario_id="s1", model_id="m", run_idx=i, tool_calls=[ToolCall(name="t")])
            for i in range(3)
        ]
    }


def test_save_then_load_roundtrip(tmp_path):
    save_baseline(_traces(), "gpt-4o-mini", tmp_path)
    loaded = load_baseline("gpt-4o-mini", tmp_path)
    assert len(loaded["s1"]) == 3
    assert loaded["s1"][0].tool_calls[0].name == "t"


def test_baseline_with_gemini_signature_bytes_persists(tmp_path):
    # A live Gemini 3.x tool loop stashes opaque, non-UTF-8 `thought_signature` bytes in
    # Trace.messages (they must stay raw in-memory to feed back to the SDK). Persisting
    # such a baseline must not crash on JSON serialization — the regression that aborted
    # `mp baseline --provider google --model gemini-3.1-flash-lite` on agent scenarios.
    import base64

    sig = b"\xd6\x00\xff opaque-thought-sig"
    trace = Trace(
        scenario_id="s1",
        model_id="gemini-3.1-flash-lite",
        messages=[
            {
                "role": "model",
                "parts": [{"function_call": {"name": "f", "args": {}}, "thought_signature": sig}],
            }
        ],
    )
    # The guarantee this test exists for is the one in its first comment: persisting must not
    # CRASH. `[M] 2026-09-09` MP-240 strengthened how it holds. The bytes used to be
    # base64-encoded on the way to disk; now `messages` is not persisted at all, because the
    # diff never reads it and this is the file users are told to commit. So the signature
    # never reaches the serializer -- which cannot crash on what it is never handed.
    path = save_baseline({"s1": [trace]}, "gemini-3.1-flash-lite", tmp_path)
    raw = path.read_text(encoding="utf-8")
    assert "thought_signature" not in raw, "the opaque signature reached the committed file"
    assert base64.b64encode(sig).decode("ascii") not in raw
    loaded = load_baseline("gemini-3.1-flash-lite", tmp_path)
    assert loaded["s1"][0].messages == [], "a loaded baseline carries no transcript"
    # the in-memory live object keeps the raw bytes — SDK feed-back is unaffected
    assert trace.messages[0]["parts"][0]["thought_signature"] == sig


def test_save_is_atomic_no_tmp_left_behind(tmp_path):
    path = save_baseline(_traces(), "gpt-4o-mini", tmp_path)
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_failed_save_leaves_prior_baseline_intact(tmp_path, monkeypatch):
    # Record a good baseline, then make the next write blow up mid-flight. Because the
    # write goes to a temp file before os.replace, the existing baseline must survive.
    save_baseline(_traces(), "gpt-4o-mini", tmp_path)

    real_replace = os.replace

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    # MP-197. `BaselineError`, not the raw `OSError` this asserted before: an unwritable store
    # used to reach the user as an unhandled traceback. The typed error is what lets `baseline`
    # print a message and exit EXIT_SETUP_FAILED. The property this test exists for -- the
    # PRIOR baseline survives a failed write -- is unchanged and still asserted below.
    with pytest.raises(BaselineError, match="could not write the baseline"):
        save_baseline(_traces(), "gpt-4o-mini", tmp_path)
    monkeypatch.setattr(os, "replace", real_replace)

    # the original baseline is still loadable and uncorrupted
    loaded = load_baseline("gpt-4o-mini", tmp_path)
    assert len(loaded["s1"]) == 3
    # MP-197. And the failed write leaves nothing behind in the directory the docs tell the
    # user to `git add`.
    assert not list(tmp_path.rglob("*.tmp"))


def test_model_id_with_slashes_is_sanitized(tmp_path):
    # provider/model style ids must not escape the store directory
    save_baseline(_traces(), "openai/gpt-4o", tmp_path)
    p = baseline_path("openai/gpt-4o", tmp_path)
    assert p.parent == tmp_path
    assert load_baseline("openai/gpt-4o", tmp_path)["s1"]


def test_missing_baseline_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="No baseline"):
        load_baseline("never-recorded", tmp_path)


def test_corrupt_baseline_raises_baseline_error(tmp_path):
    path = baseline_path("gpt-4o-mini", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json")
    with pytest.raises(BaselineError, match="corrupt"):
        load_baseline("gpt-4o-mini", tmp_path)
