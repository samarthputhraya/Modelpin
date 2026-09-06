"""The console must never decide the exit code (MP-191), and a model id is TEXT (MP-174).

Two defects that share one line and must not be allowed to close each other.

**MP-191 — the encoding half.** `cli.console` writes through whatever `sys.stdout` happens to
be. On a stock Windows console, or any piped stdout on a machine whose locale is not UTF-8,
that stream is cp1252, and a scenario or model id outside Latin-1 raises `UnicodeEncodeError`
*while printing the summary*. The command then exits **1** -- and `cli.py` documents 1 as
"at least one real regression (the CI gate)", while `action.yml` turns any non-zero, non-3
code into `::error::Modelpin detected a behavioral regression`. So a display failure posts a
false regression claim on someone's PR, over a migration that did not regress. `[M] 2026-09-06`
reproduced end to end by bug-reproducer on `2ef82d5`, over byte-identical traces.

`[M]` PR #73 did not cause this -- it UNCOVERED it. Before `utf-8-sig`, the same fixtures file
died earlier, at *read*. MP-190 fixed text going INTO the engine; nothing fixed text coming
OUT of it, and that asymmetry is this file.

**MP-174 — the markup half.** The same interpolations pass model ids into rich markup
unescaped, so `--to 'm2[/]'` raises `MarkupError` at the very same line.

`[M]` **They are distinct and neither fix closes the other**, which is the trap this file
exists to hold shut: `_rich_escape` is `rich.markup.escape`, and it does nothing whatsoever
for an unencodable codepoint. A reviewer who sees "model ids are escaped now" would
reasonably assume MP-191 went with it. It did not.

Not covered here: error-path escaping (MP-161 + MP-170, `test_error_messages_survive_rich.py`).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import modelpin.cli as cli
from modelpin.report import render_pr_comment


#: A scenario id that cp1252 cannot represent at all. Japanese rather than an accented Latin
#: character on purpose: an accented character IS encodable in cp1252, so it would pass on a
#: broken console and make this guard measure nothing.
JP = "問い合わせ"


def _cp1252_stream() -> io.TextIOWrapper:
    """A stream with exactly the property that matters: it cannot encode `JP`."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


# --------------------------------------------------------------------------- MP-191


def test_console_does_not_raise_when_the_stream_cannot_encode_the_text(monkeypatch) -> None:
    """The root guard. Everything else in this file is a consequence of this one holding.

    Asserted against `cli.console` itself, not a Console the test builds: a locally
    constructed console measures the test's own configuration, which is the mistake
    `test_error_messages_survive_rich.py` already had to correct once.
    """
    stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    cli.console.print(f"REGRESSION {JP}: tool-call behavior changed")


def test_the_unencodable_text_is_degraded_but_the_line_still_arrives(monkeypatch) -> None:
    """Dropping the line would be its own defect: the verdict must still be readable.

    The id itself cannot survive a cp1252 stream by definition, so what is asserted is that
    the SURROUNDING verdict text does, and that something stands where the id was. A user who
    cannot read the id can still see that a scenario regressed and go look it up.
    """
    stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    cli.console.print(f"REGRESSION {JP}: refusal rate 0% -> 100%")
    stream.flush()
    written = stream.buffer.getvalue().decode("cp1252")  # type: ignore[attr-defined]
    assert "REGRESSION" in written
    assert "refusal rate 0% -> 100%" in written


def test_an_ascii_id_is_untouched_on_the_same_stream(monkeypatch) -> None:
    """Control. The fix must not disturb the overwhelmingly common path."""
    stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    cli.console.print("REGRESSION refund_request: tool-call behavior changed")
    stream.flush()
    written = stream.buffer.getvalue().decode("cp1252")  # type: ignore[attr-defined]
    assert "refund_request" in written


def test_a_utf8_stream_still_prints_the_id_verbatim(monkeypatch) -> None:
    """Control, and the one that would catch an over-eager fix.

    Sanitising unconditionally -- rather than only when the stream cannot take the text --
    would mangle ids for every user on a modern terminal to protect a minority on cp1252.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    cli.console.print(JP)
    stream.flush()
    assert JP in stream.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- MP-174


def test_a_model_id_containing_markup_does_not_raise(monkeypatch) -> None:
    """`--to 'm2[/]'` used to raise MarkupError at the pre-spend line, before any replay."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    cli.console.print(f"[dim]provider=fake from=m1 to={cli._rich_escape('m2[/]')} runs=5[/]")
    stream.flush()
    assert "m2[/]" in stream.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]


def test_a_pipe_in_a_model_id_does_not_break_the_markdown_table() -> None:
    """The published Report's settings table is an ADR-0009 surface; a raw `|` splits a row."""
    md = render_pr_comment([], "m1|evil", "m2|evil", 5, "fake")
    for line in md.splitlines():
        if line.startswith("|") and "evil" in line:
            assert line.count("|") - line.count("\\|") <= 8, (
                "A model id containing `|` added cells to a Markdown table row: " + line
            )


# --------------------------------------------------------------------------- end to end


REPO = Path(__file__).resolve().parents[1]


def _write_case(tmp_path: Path, scenario_id: str) -> Path:
    """A migration where both sides are BYTE-IDENTICAL. Ground truth: unchanged, exit 0."""
    work = tmp_path / "case"
    (work / "scenarios").mkdir(parents=True)
    (work / "modelpin.yaml").write_text(
        "models:\n  - m1\nscenarios_dir: scenarios\nproviders:\n  - fake\nruns: 5\n",
        encoding="utf-8",
    )
    (work / "scenarios" / "s.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "name": "case",
                "kind": "single",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
            }
        ),
        encoding="utf-8",
    )
    (work / "fx.json").write_text(
        json.dumps(
            [
                {"scenario_id": scenario_id, "model_id": m, "final_output": "ok", "refused": False}
                for m in ("m1", "m2")
            ]
        ),
        encoding="utf-8",
    )
    return work


def _run(work: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run the real CLI with a stdout that CANNOT encode `JP`.

    `PYTHONIOENCODING=cp1252` is the whole point of the test, not an incidental setting: it
    reproduces the shipped Windows configuration on any machine, so this guard is meaningful
    on the Linux CI runners too. Bytes, not text -- decoding here would hide the defect.
    """
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    return subprocess.run(
        [sys.executable, "-m", "modelpin.cli", *args],
        cwd=str(work),
        capture_output=True,
        env=env,
        timeout=180,
    )


@pytest.mark.parametrize("scenario_id", [JP, "ascii_id"], ids=["non_ascii_id", "ascii_control"])
def test_a_clean_migration_exits_zero_on_a_cp1252_console(tmp_path, scenario_id) -> None:
    """The defect in one assertion: a display failure must not emit the CI-gate exit code.

    Parametrised with an ASCII control because the non-ASCII case alone cannot distinguish
    "the encoding bug is fixed" from "the whole command stopped working".
    """
    work = _write_case(tmp_path, scenario_id)
    assert _run(work, "baseline", "--fixtures", "fx.json").returncode == 0
    proc = _run(work, "check", "--to", "m2", "--fixtures", "fx.json")
    assert proc.returncode == 0, (
        "A byte-identical migration exited "
        f"{proc.returncode} on a cp1252 console. `cli.py` documents 1 as 'at least one real "
        "regression (the CI gate)' and `action.yml` publishes it as 'Modelpin detected a "
        "behavioral regression'. stderr tail:\n" + proc.stderr.decode("cp1252", "replace")[-1500:]
    )


def test_a_non_ascii_model_id_exits_zero_on_a_cp1252_console(tmp_path) -> None:
    """The worse half of MP-191: the model id crashes at the PRE-SPEND line.

    Earlier than the scenario-id crash and with a strictly larger blast radius -- it dies
    before any replay runs and before any artifact is written, so there is not even a report
    on disk to contradict the exit code.
    """
    work = _write_case(tmp_path, "ascii_id")
    (work / "fx.json").write_text(
        json.dumps(
            [
                {"scenario_id": "ascii_id", "model_id": m, "final_output": "ok", "refused": False}
                for m in ("m1", JP)
            ]
        ),
        encoding="utf-8",
    )
    assert _run(work, "baseline", "--fixtures", "fx.json").returncode == 0
    proc = _run(work, "check", "--to", JP, "--fixtures", "fx.json")
    assert proc.returncode == 0, (
        f"A byte-identical migration to a non-ASCII model id exited {proc.returncode}. "
        "stderr tail:\n" + proc.stderr.decode("cp1252", "replace")[-1500:]
    )
