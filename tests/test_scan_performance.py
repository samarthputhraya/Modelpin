"""`mp scan` must not hang on a minified bundle, and must not change its answer to get faster.

MP-242. `[M] 2026-09-09` The per-line de-dupe in `modelpin/detector/__init__.py` compared every
matched span with every other span -- O(S^2) in matches per LINE -- and a minified bundle is a
single enormous line. Minifiers rename locals to short identifiers, `o1`/`o3`/`o4` among them,
and the o-series pattern matches those at every word boundary, so a bundle is dense with spans.

Reproduced before the fix, on `"var o1=a,o3=b,o4=c;"` repeated on one line:

      4 KB  ->  0.03 s
      8 KB  ->  0.10 s
     16 KB  ->  0.56 s        (quadrupling per doubling)

and the security review measured a 96 KB file at 105 s. Every JS/TS repository has a bundle, so
for a front-end shop the first command a stranger runs appeared to hang.

The fix is a sweep instead of a pairwise scan. The risk of such a fix is not that it is slow --
it is that it quietly gives a DIFFERENT answer, and `scan`'s answer is the first thing a user
trusts or doesn't. So the equivalence is tested against the original definition, copied below
verbatim, on random inputs.
"""

from __future__ import annotations

import random
import time

from modelpin.detector import _dedupe_spans, _models_in


def _reference_dedupe(spans: list[tuple[int, int, str]]) -> list[tuple[str, int]]:
    """The ORIGINAL quadratic de-dupe, verbatim from before MP-242. Do not optimise this.

    It is the oracle: its whole value is being the definition the fast version must match.
    """
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for start, end, text in sorted(spans, key=lambda s: s[0]):
        contained = any(
            (o_start <= start and end <= o_end) and (o_end - o_start) > (end - start)
            for o_start, o_end, _ in spans
        )
        if not contained and text not in seen:
            seen.add(text)
            out.append((text, start))
    return out


def _random_spans(rng: random.Random, line: str) -> list[tuple[int, int, str]]:
    """Spans as the detector would produce them: real substrings of ONE line, so two spans at
    the same position always carry the same text -- the invariant the sweep relies on."""
    spans = []
    for _ in range(rng.randint(0, 40)):
        start = rng.randint(0, len(line) - 1)
        end = rng.randint(start + 1, min(len(line), start + 12))
        spans.append((start, end, line[start:end]))
    # Duplicates and exact repeats are the adversarial case for a sweep; include them.
    if spans and rng.random() < 0.5:
        spans.extend(rng.sample(spans, k=min(len(spans), rng.randint(1, 5))))
    rng.shuffle(spans)
    return spans


def test_the_fast_de_dupe_agrees_with_the_original_on_random_inputs() -> None:
    """Set AND order, on 5000 random span sets including nesting, ties and exact repeats."""
    rng = random.Random(20260909)
    alphabet = "gpt-4o1o3o4claude-gemini."
    for trial in range(5000):
        line = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 60)))
        spans = _random_spans(rng, line)
        expected = _reference_dedupe(spans)
        got = _dedupe_spans(spans)
        assert got == expected, (
            f"trial {trial}: the fast de-dupe disagrees with the original.\n"
            f"line  = {line!r}\nspans = {spans!r}\n"
            f"expected {expected!r}\ngot      {got!r}"
        )


def test_a_nested_span_is_dropped_but_a_separate_mention_is_kept() -> None:
    """The case the de-dupe exists for, and the one it must NOT over-apply to.

    `gpt-4o` nested inside `gpt-4o-mini` is an artifact of overlapping patterns and is
    dropped. A separate `gpt-4o` later on the line is a genuine second model and is kept.
    `[M]` The first draft of this test expected the second one dropped too; the original
    quadratic definition disagreed, and it was right -- which is exactly why the random-input
    test above compares against that definition instead of against intuition.
    """
    spans = [(0, 6, "gpt-4o"), (0, 11, "gpt-4o-mini"), (20, 26, "gpt-4o")]
    assert _dedupe_spans(spans) == [("gpt-4o-mini", 0), ("gpt-4o", 20)]


def test_a_minified_bundle_line_scans_in_well_under_a_second() -> None:
    """The reproduction, as a budget. 64 KB took ~9 s by the quadratic curve measured above.

    The budget is generous on purpose -- a timing test that flakes on a slow CI runner gets
    muted, and a muted guard is worse than none. The fixed algorithm does this in
    milliseconds; the broken one cannot come anywhere near the budget.
    """
    line = "var o1=a,o3=b,o4=c;" * (64 * 1024 // 19)
    t0 = time.perf_counter()
    hits = _models_in(line)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"scanning one 64 KB minified line took {elapsed:.2f} s. The per-line de-dupe has "
        "gone quadratic again; a front-end repository's first `scan` will appear to hang."
    )
    assert {m for m, _ in hits} == {"o1", "o3", "o4"}, hits


# ------------------------------------------------------------------------ the size cap


def _big_file(root, name: str, body: str, size: int) -> None:
    """A file just over `size` bytes whose model id is at the very START.

    The id is placed first on purpose: if the cap were applied AFTER reading, or were
    checked against the wrong number, the id would still be found and the test would pass
    for the wrong reason.
    """
    # EXACTLY `size` bytes: the body is ASCII, so characters are bytes. `[M]` The first draft
    # padded with `" " + "x" * (size - len(body))`, one byte too many -- which silently made
    # the "exactly at the cap" test an "over the cap" test.
    (root / name).write_text(body + "x" * (size - len(body)), encoding="utf-8")
    assert (root / name).stat().st_size == size


def test_an_oversized_file_is_not_read_and_is_disclosed(tmp_path) -> None:
    from typer.testing import CliRunner

    from modelpin.cli import app
    from modelpin.detector import MAX_SCAN_BYTES

    _big_file(tmp_path, "bundle.js", 'const m = "gpt-4o-mini";', MAX_SCAN_BYTES + 1)
    (tmp_path / "app.py").write_text('MODEL = "claude-3-5-sonnet"\n', encoding="utf-8")

    out = " ".join(CliRunner().invoke(app, ["scan", str(tmp_path)]).output.split())
    assert "claude-3-5-sonnet" in out, "the ordinary file must still be scanned"
    assert "gpt-4o-mini" not in out, "the oversized file was read despite the cap"
    assert "bundle.js" in out and "not scanned" in out, (
        "the oversized file was skipped SILENTLY. A scan that looks past a file without "
        f"saying so is claiming coverage it does not have.\n\n{out}"
    )


def test_the_skip_is_disclosed_even_when_nothing_else_was_found(tmp_path) -> None:
    """ "No model identifiers found" is exactly when a skipped file matters most."""
    from typer.testing import CliRunner

    from modelpin.cli import app
    from modelpin.detector import MAX_SCAN_BYTES

    _big_file(tmp_path, "bundle.js", 'const m = "gpt-4o-mini";', MAX_SCAN_BYTES + 1)
    out = " ".join(CliRunner().invoke(app, ["scan", str(tmp_path)]).output.split())
    assert "No model identifiers found" in out
    assert "bundle.js" in out and "not scanned" in out, (
        "scan reported finding nothing while hiding that it skipped the only file with a "
        f"model id in it.\n\n{out}"
    )


def test_a_file_at_the_cap_is_still_scanned(tmp_path) -> None:
    """The boundary: exactly MAX_SCAN_BYTES is read. Off-by-one here silently drops files."""
    from modelpin.detector import MAX_SCAN_BYTES, scan_repo

    _big_file(tmp_path, "edge.js", 'const m = "gpt-4o-mini";', MAX_SCAN_BYTES)
    skipped: list[str] = []
    hits = scan_repo(tmp_path, skipped=skipped)
    assert skipped == [], skipped
    assert {h["model"] for h in hits} == {"gpt-4o-mini"}
