"""MP-208: the false-positive harness can score with a judge that is not OpenAI's, and can
re-score a recorded run under a second judge without buying the replay again.

Why these exist. `[M] 2026-09-07` every false-positive and detection number Modelpin had ever
published was produced by an OpenAI judge, and the harness *physically could not reach another
one*: `scripts/fp_measurement.py` called `build_judge(args.judge)` with no provider, while
`modelpin/judge.py` had accepted a `provider=` since MP-143. A Groq judge id
(`openai/gpt-oss-120b` - a vendor prefix that names the model's ORIGIN, not its host) therefore
died at preflight with `cannot tell which host should run the judge model`. That mattered
structurally rather than cosmetically: `[M]` 51 of the 82 scored trials in the run of record
could only have fired on the SEMANTIC channel, so the OpenAI judge *was* the number, and its
agreement with any other judge was unpriced.

The second half is `--rejudge`. The artifacts written by MP-205 store every trace field the
diff reads (`traces_to_json` drops only `messages`), and the semantic channel reads
`final_output` and nothing else - so a second judge can score the SAME recorded behaviour for
the price of the judge calls alone. That is not merely cheaper: re-replaying would confound
judge disagreement with fresh model noise, and the agreement rate would measure neither.

Every provider and judge below is a stub: ADR-0006, no live call from the suite, ever.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import Trace  # noqa: E402
from scripts import fp_measurement as fp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "examples" / "suite"

# Two scenarios, one round: small enough to read a whole artifact in a failure message.
ONLY = "refund_request,order_status"


def _trace(i: int, out: str) -> Trace:
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=i,
        tool_calls=[],
        final_output=out,
        refused=False,
        tokens_in=10,
        tokens_out=3,
        latency_ms=1.0,
    )


class _TwoAnswerAdapter:
    """Answers `A` for the first `runs` calls of a trial and `B` for the next `runs`.

    That asymmetry is the point. If both sides emitted identical text the judge would never be
    consulted at all - `semantic_divergence_flags` skips the call when the normalised text
    matches the reference - and a test that swapped judges would prove nothing, because
    neither judge would have been asked a question.
    """

    def __init__(self, runs: int) -> None:
        self.runs = runs
        self.calls = 0

    def preflight(self) -> None:
        pass

    def run(self, scenario, model_id, run_idx=0):
        self.calls += 1
        side = ((self.calls - 1) // self.runs) % 2
        return _trace(run_idx, "The refund was issued." if side == 0 else "Refund complete.")


class _Judge:
    """A judge with a fixed opinion, which records what it was asked."""

    def __init__(self, verdict: bool) -> None:
        self.verdict = verdict
        self.asked: list[tuple[str, str]] = []

    def preflight(self) -> None:
        pass

    def equivalent(self, reference, candidate, task=None) -> bool:
        self.asked.append((reference, candidate))
        return self.verdict


def _main(monkeypatch, argv, *, adapter=None, judge=None, seen=None):
    """Run `main()` with the given stubs, returning its stdout.

    `seen` is a list that collects the `(model, provider)` every `build_judge` call received -
    the seam MP-208 was broken at.
    """

    def _build(model, provider=None):
        if seen is not None:
            seen.append((model, provider))
        return judge if judge is not None else _Judge(True)

    if adapter is not None:
        monkeypatch.setattr(fp, "get_adapter", lambda name: adapter)
    else:

        def _no_adapter(name):
            raise AssertionError("a replay adapter was constructed")

        monkeypatch.setattr(fp, "get_adapter", _no_adapter)
    monkeypatch.setattr(fp, "build_judge", _build)
    monkeypatch.setattr(sys, "argv", ["fp_measurement.py", *argv])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fp.main()
    return buf.getvalue()


def _live_argv(out: Path, judge: str, provider: str | None, *extra: str) -> list[str]:
    argv = [
        "--provider", "stub",
        "--model", "m",
        "--runs", "4",
        "--judge", judge,
        "--scenarios-dir", str(SUITE),
        "--only", ONLY,
        "--out", str(out),
    ]  # fmt: skip
    if provider is not None:
        argv += ["--judge-provider", provider]
    return [*argv, *extra]


def _record(monkeypatch, out: Path, judge_verdict: bool, runs: int = 4) -> _TwoAnswerAdapter:
    adapter = _TwoAnswerAdapter(runs)
    _main(
        monkeypatch,
        _live_argv(out, "gpt-4o-mini", None),
        adapter=adapter,
        judge=_Judge(judge_verdict),
    )
    return adapter


# --- the defect itself ---------------------------------------------------------------------


def test_the_harness_hands_build_judge_the_host_it_was_told(monkeypatch, tmp_path):
    """`[M]` The whole of MP-208 in one assertion: before the fix this call site passed the
    model alone, so no `--judge-provider` value could reach `build_judge` and only judges whose
    host is inferable from their id were reachable."""
    seen: list[tuple[str, str | None]] = []
    _record_argv = _live_argv(tmp_path / "r.jsonl", "openai/gpt-oss-120b", "groq")
    _main(monkeypatch, _record_argv, adapter=_TwoAnswerAdapter(4), seen=seen)
    assert seen == [("openai/gpt-oss-120b", "groq")]


def test_a_judge_whose_host_cannot_be_inferred_is_refused_by_name_not_by_accident(
    monkeypatch, tmp_path
):
    """`openai/gpt-oss-120b` is served by Groq. Nothing in the string says so, and the harness
    must not guess - but it must say which flag settles it, which the old `ProviderError` did
    not: it named `judge_provider:` in modelpin.yaml, a file this harness never reads."""
    with pytest.raises(SystemExit) as exc:
        _main(
            monkeypatch,
            _live_argv(tmp_path / "r.jsonl", "openai/gpt-oss-120b", None),
            adapter=_TwoAnswerAdapter(4),
        )
    msg = str(exc.value)
    assert "--judge-provider" in msg and "groq" in msg
    assert "modelpin.yaml" not in msg, "the harness must name a flag it actually has"


def test_a_judge_host_that_is_not_a_judge_host_is_refused(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="not a judge host"):
        _main(
            monkeypatch,
            _live_argv(tmp_path / "r.jsonl", "some-model", "mistral"),
            adapter=_TwoAnswerAdapter(4),
        )


def test_the_artifact_records_the_resolved_judge_host_even_when_nobody_typed_it(
    monkeypatch, tmp_path
):
    """The header must carry the vendor that produced the semantic verdicts. `[M]` Its absence
    is why 'every number was scored by an OpenAI judge' had to be established by reading the
    call site rather than by reading the artifacts."""
    out = tmp_path / "r.jsonl"
    _record(monkeypatch, out, True)
    header, _ = fp.load_artifact(str(out))
    assert header["judge"] == "gpt-4o-mini" and header["judge_provider"] == "openai"


# --- --rejudge: the same replay, a second judge ---------------------------------------------


def test_rejudge_reuses_the_stored_replay_and_never_constructs_an_adapter(monkeypatch, tmp_path):
    """The cost claim, pinned. `_main` with `adapter=None` fails the test if `get_adapter` is
    called at all, so 'no replay key is read' is enforced rather than asserted in prose."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    text = _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "openai/gpt-oss-120b",
            "--judge-provider",
            "groq",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    assert "REJUDGED from" in text and "no replay call is made" in text
    _, src_rows = fp.load_artifact(str(src))
    _, dst_rows = fp.load_artifact(str(dst))
    assert {r["key"] for r in dst_rows} == {r["key"] for r in src_rows}
    for a, b in zip(
        sorted(src_rows, key=lambda r: r["key"]), sorted(dst_rows, key=lambda r: r["key"])
    ):
        assert a["base_traces"] == b["base_traces"], "the rejudged trial re-used the traces"
        assert a["cand_traces"] == b["cand_traces"]


def test_two_judges_over_one_recorded_replay_can_reach_different_verdicts(monkeypatch, tmp_path):
    """The measurement MP-208 exists to make possible.

    Identical traces, identical constants, identical everything but the judge - and the verdict
    moves. That is exactly why an unpriced judge is a hole in a published false-positive rate:
    `[M]` 51 of 82 scored trials could only have fired on this channel.
    """
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    strict = _Judge(False)
    _main(
        monkeypatch,
        ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini"],
        judge=strict,
    )
    assert strict.asked, "the strict judge was actually consulted"

    def verdicts(path: Path) -> dict[str, str]:
        _, rows = fp.load_artifact(str(path))
        return {r["key"]: r["result"]["verdict"] for r in rows if r["result"]}

    before, after = verdicts(src), verdicts(dst)
    assert set(before) == set(after)
    # `fp:order_status` is the clean isolation: under the lenient judge it returned p = 1.00 on
    # EVERY channel - the harness prints it as "could not have fired" - so nothing structural
    # or statistical is available to move it. Only the judge can, and it does.
    assert before["fp:order_status"] == "unchanged"
    assert after["fp:order_status"] == "regression"


def test_the_rejudged_artifact_says_whose_replay_it_is(monkeypatch, tmp_path):
    """A rejudged file is the same sample scored twice, not a second sample. Pooling the two
    would double-count every trial, so the artifact states it in its own header rather than
    leaving it to the write-up."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "openai/gpt-oss-120b",
            "--judge-provider",
            "groq",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    src_header, _ = fp.load_artifact(str(src))
    header, _ = fp.load_artifact(str(dst))
    assert header["rejudged_from"] == "src.jsonl"
    assert header["replay_reused"] is True
    assert header["rejudged_from_judge"] == "gpt-4o-mini"
    assert header["rejudged_from_judge_provider"] == "openai"
    assert header["judge"] == "openai/gpt-oss-120b" and header["judge_provider"] == "groq"
    # The measured configuration is the source's, not the command line's default.
    for k in ("provider", "model", "runs", "repeats", "role", "scenarios"):
        assert header[k] == src_header[k], k


def test_rejudge_resumes_so_a_rate_limited_free_judge_tier_is_survivable(monkeypatch, tmp_path):
    """`[M]` The non-OpenAI judge that makes this affordable is free precisely because it is
    rate-limited (Groq's free tier caps requests per day), so a rejudge run that cannot be
    continued tomorrow is a rejudge run that cannot be afforded at all."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    rejudge = ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini"]
    _main(monkeypatch, rejudge, judge=_Judge(True))
    _, first = fp.load_artifact(str(dst))
    second = _Judge(True)
    text = _main(monkeypatch, [*rejudge, "--resume"], judge=second)
    assert f"{len(first)} reused from --out" in text
    assert second.asked == [], "a resumed rejudge spends no judge call on a scored trial"


def test_rejudge_refuses_the_shapes_that_would_corrupt_a_record(monkeypatch, tmp_path):
    src = tmp_path / "src.jsonl"
    _record(monkeypatch, src, True)
    j = {"--judge": "gpt-4o-mini"}
    with pytest.raises(SystemExit, match="--rejudge needs --out"):
        _main(monkeypatch, ["--rejudge", str(src), *sum(map(list, j.items()), [])])
    with pytest.raises(SystemExit, match="same file"):
        _main(monkeypatch, ["--rejudge", str(src), "--out", str(src), "--judge", "gpt-4o-mini"])
    with pytest.raises(SystemExit, match="re-score with nothing"):
        _main(
            monkeypatch,
            ["--rejudge", str(src), "--out", str(tmp_path / "d.jsonl"), "--no-judge"],
        )
    with pytest.raises(SystemExit, match="Pick one"):
        _main(
            monkeypatch,
            ["--rejudge", str(src), "--rescore", str(src), "--out", str(tmp_path / "e.jsonl")],
        )


def test_rejudge_refuses_an_artifact_that_was_never_judged(monkeypatch, tmp_path):
    """An unjudged artifact has no semantic verdict to disagree with, so 'agreement' over it
    would be a comparison against nothing - reported as a number, which is worse than refusing."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _main(
        monkeypatch,
        _live_argv(src, "gpt-4o-mini", None, "--no-judge"),
        adapter=_TwoAnswerAdapter(4),
    )
    with pytest.raises(SystemExit, match="no semantic verdict to disagree with"):
        _main(monkeypatch, ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini"])


def test_rejudge_refuses_an_artifact_with_no_traces(monkeypatch, tmp_path):
    """Artifacts written before MP-205 kept verdicts but not traces. Re-scoring one is
    impossible, and must say so rather than publish a rate over zero trials."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    stripped = []
    for line in src.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "trial":
            rec["base_traces"] = rec["cand_traces"] = None
        stripped.append(json.dumps(rec))
    src.write_text("\n".join(stripped) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit, match="records no trial with traces"):
        _main(monkeypatch, ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini"])


def test_resuming_a_pre_mp208_artifact_is_not_refused_for_a_field_it_predates(
    monkeypatch, tmp_path
):
    """`judge_provider` was added to the header after the run of record was committed. Absent
    must mean UNRECORDED, not `None` - otherwise every artifact written before MP-208 becomes
    un-resumable by a field whose configuration never actually differed."""
    out = tmp_path / "r.jsonl"
    _record(monkeypatch, out, True)
    lines = []
    for line in out.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "header":
            rec.pop("judge_provider")
        lines.append(json.dumps(rec))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    text = _main(
        monkeypatch,
        _live_argv(out, "gpt-4o-mini", None, "--resume"),
        adapter=_TwoAnswerAdapter(4),
    )
    assert "reused from the artifact" in text
    # ...but a header that DOES record it is still held to it.
    header, _ = fp.load_artifact(str(out))
    fp.check_resume_header(header, {**header, "judge_provider": "groq"})  # header lacks the key
    with pytest.raises(SystemExit, match="different configuration"):
        fp.check_resume_header(
            {**header, "judge_provider": "openai"}, {**header, "judge_provider": "groq"}
        )


# --- narrowing: a daily free quota is the constraint the design has to survive --------------


def _record_rounds(monkeypatch, out: Path, repeats: int) -> None:
    _main(
        monkeypatch,
        _live_argv(out, "gpt-4o-mini", None, "--repeats", str(repeats)),
        adapter=_TwoAnswerAdapter(4),
        judge=_Judge(True),
    )


def _keys(path: Path) -> set[str]:
    _, rows = fp.load_artifact(str(path))
    return {r["key"] for r in rows}


def test_rejudge_narrows_to_the_first_k_recorded_rounds(monkeypatch, tmp_path):
    """`[M] 2026-09-07` the run of record holds 20 rounds x 12 scenarios x 2 surfaces = 3,260
    judge calls on the FP arm, which is ~9 days of Groq's free tier. A rejudge that can only be
    bought whole cannot be bought at all. Rounds are exchangeable replicates, so a PREFIX of
    them is an unbiased subset - unlike a subset picked by scenario or by what the first judge
    flagged."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record_rounds(monkeypatch, src, 4)
    _main(
        monkeypatch,
        ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini", "--repeats", "2"],
        judge=_Judge(True),
    )
    got = _keys(dst)
    assert got < _keys(src), "a narrowed rejudge is a strict subset of its source"
    assert {k for k in got if k.startswith("fp:")} == {
        f"fp:{s}#{i}" for s in ("refund_request", "order_status") for i in (1, 2)
    }
    assert got - {k for k in got if k.startswith("fp:")}, "the recall arm is still included"
    header, _ = fp.load_artifact(str(dst))
    assert header["repeats"] == 2 and header["rejudged_repeats_of"] == 4


def test_a_narrowed_rejudge_keeps_the_keys_it_narrowed_to(monkeypatch, tmp_path):
    """The narrowing must not shift the labels. `fp_label` drops the `#i` suffix when repeats
    is 1, so a narrowing to one round would produce keys matching nothing in the source and
    every trial would silently miss instead of being re-scored. 1 therefore means 'all'."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record_rounds(monkeypatch, src, 3)
    _main(
        monkeypatch,
        ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini", "--repeats", "1"],
        judge=_Judge(True),
    )
    assert _keys(dst) == _keys(src), "--repeats 1 is argparse's default, so it takes everything"
    _, rows = fp.load_artifact(str(dst))
    assert all(r["result"] is not None for r in rows), "no trial missed on a key mismatch"


def test_rejudge_can_buy_one_arm_without_the_other(monkeypatch, tmp_path):
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record_rounds(monkeypatch, src, 2)
    _main(
        monkeypatch,
        ["--rejudge", str(src), "--out", str(dst), "--judge", "gpt-4o-mini", "--arm", "fp"],
        judge=_Judge(True),
    )
    assert all(k.startswith("fp:") for k in _keys(dst))
    header, _ = fp.load_artifact(str(dst))
    assert header["rejudged_arm"] == "fp"


def test_rejudge_only_narrows_and_refuses_ids_the_source_never_measured(monkeypatch, tmp_path):
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "gpt-4o-mini",
            "--only",
            "order_status",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    assert _keys(dst) == {"fp:order_status"}
    with pytest.raises(SystemExit, match="never measured"):
        _main(
            monkeypatch,
            [
                "--rejudge",
                str(src),
                "--out",
                str(tmp_path / "e.jsonl"),
                "--judge",
                "gpt-4o-mini",
                "--only",
                "order_status,not_in_there",
            ],  # fmt: skip
            judge=_Judge(True),
        )


def test_rescore_restores_the_judge_host_it_recorded(monkeypatch, tmp_path):
    """Inert today - `--rescore` builds no judge and forbids `--out` - but a `--rescore` that
    forgot the host would let `resolve_judge_provider` re-infer one for an id that has none."""
    out = tmp_path / "r.jsonl"
    _main(
        monkeypatch,
        _live_argv(out, "openai/gpt-oss-120b", "groq"),
        adapter=_TwoAnswerAdapter(4),
    )

    def _boom(*a, **k):
        raise AssertionError("--rescore built a judge or a provider")

    monkeypatch.setattr(fp, "get_adapter", _boom)
    monkeypatch.setattr(fp, "build_judge", _boom)
    monkeypatch.setattr(sys, "argv", ["fp_measurement.py", "--rescore", str(out)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fp.main()
    assert "RESCORED OFFLINE" in buf.getvalue()


# --- the agreement report itself -----------------------------------------------------------
#
# `judge_agreement.py` turns two artifacts into a published rate, so it is held to the same
# standard as the harness: it must refuse the comparisons that would be meaningless rather
# than print a number for them.

from scripts import judge_agreement as ja  # noqa: E402


def _pair(monkeypatch, tmp_path, judge_b_verdict=False, judge_b="gpt-4o-mini", host_b=None):
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    argv = ["--rejudge", str(src), "--out", str(dst), "--judge", judge_b]
    if host_b:
        argv += ["--judge-provider", host_b]
    _main(monkeypatch, argv, judge=_Judge(judge_b_verdict))
    return src, dst


def test_agreement_counts_a_verdict_move_over_one_shared_replay(monkeypatch, tmp_path):
    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=False, judge_b="openai/gpt-oss-120b",
                     host_b="groq")  # fmt: skip
    s = ja.summarise(str(src), str(dst))
    c = s["compare"]
    assert c["n"] == 3 and c["replay_mismatch"] == []
    assert c["verdict_agree"] < c["n"], "a strict judge over the same traces disagreed"
    assert any(d["key"] == "fp:order_status" for d in c["disagreements"])
    assert s["b"]["judge"] == "openai/gpt-oss-120b @ groq"
    assert s["a"]["judge"] == "gpt-4o-mini @ openai"
    text = "\n".join(ja.render(s))
    assert "Do not pool them" in text and "DISAGREEMENTS" in text


def test_agreement_excludes_trials_only_one_judge_reached(monkeypatch, tmp_path):
    """An absence of evidence is not a disagreement. `[M]` It is also the normal case: a
    rejudge is routinely a pre-registered subset of its source."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "openai/gpt-oss-120b",
            "--judge-provider",
            "groq",
            "--only",
            "order_status",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    s = ja.summarise(str(src), str(dst))
    assert s["compare"]["n"] == 1
    assert "fp:refund_request" in s["unpaired"] and "recall:refund_request" in s["unpaired"]


def test_agreement_refuses_two_artifacts_that_do_not_share_a_measurement(monkeypatch, tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _record(monkeypatch, a, True)
    _main(
        monkeypatch,
        [
            *_live_argv(b, "gpt-4o-mini", None)[:4],
            "--runs",
            "2",
            *_live_argv(b, "gpt-4o-mini", None)[6:],
        ],  # fmt: skip
        adapter=_TwoAnswerAdapter(2),
        judge=_Judge(True),
    )
    with pytest.raises(SystemExit, match="differ in 'runs'"):
        ja.summarise(str(a), str(b))


def test_agreement_refuses_one_judge_compared_to_itself(monkeypatch, tmp_path):
    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=True, judge_b="gpt-4o-mini")
    with pytest.raises(SystemExit, match="no disagreement to measure"):
        ja.summarise(str(src), str(dst))


def test_agreement_flags_a_pair_whose_replays_are_not_the_same(monkeypatch, tmp_path):
    """The load-bearing precondition. Two judges shown different model behaviour cannot have
    their disagreement attributed to the judges - that is model noise wearing a judge's name,
    and it is the exact confound `--rejudge` exists to remove."""
    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=False, judge_b="openai/gpt-oss-120b",
                     host_b="groq")  # fmt: skip
    lines = []
    for line in dst.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "trial" and rec.get("base_traces"):
            rec["base_traces"][0]["final_output"] = "something else entirely"
        lines.append(json.dumps(rec))
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    s = ja.summarise(str(src), str(dst))
    assert s["compare"]["replay_mismatch"], "a swapped trace must not pass as an agreement"
    assert "DO NOT share a replay" in "\n".join(ja.render(s))


def test_a_pre_mp208_artifact_has_its_host_labelled_as_inferred_never_asserted(
    monkeypatch, tmp_path
):
    """Artifacts written before MP-208 record no `judge_provider`. Their judge could only have
    been OpenAI's - that IS the defect - but the report says so as an inference, because a
    header that never recorded the fact is not evidence of it."""
    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=False, judge_b="openai/gpt-oss-120b",
                     host_b="groq")  # fmt: skip
    lines = []
    for line in src.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "header":
            rec.pop("judge_provider")
        lines.append(json.dumps(rec))
    src.write_text(chr(10).join(lines) + chr(10), encoding="utf-8", newline=chr(10))
    s = ja.summarise(str(src), str(dst))
    assert s["a"]["judge"] == "gpt-4o-mini @ openai (unrecorded; inferred)"


def _reheaded(path: Path, dest: Path, **header_updates) -> Path:
    """Copy an artifact, overriding header fields. For building double-count shapes."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "header":
            rec.update(header_updates)
        lines.append(json.dumps(rec))
    dest.write_text(chr(10).join(lines) + chr(10), encoding="utf-8", newline=chr(10))
    return dest


def test_the_aggregator_refuses_to_read_one_sample_twice(monkeypatch, tmp_path):
    """The failure this guard prevents is silent and flattering: a glob over a directory
    holding both a run and its rejudge would count every trial twice, doubling the denominator
    of the north-star bound while adding no new evidence. It must fail loudly.

    The rule is about the SET, not about any single artifact — `[M] 2026-09-07` (MP-216) an
    earlier version refused on the `replay_reused` FLAG alone, which also refused a set of
    re-scores of DISTINCT sources. That made MP-206's re-scored run of record — five surfaces,
    710 distinct trials — unaggregatable by any command, so the block `docs/fp-measurement.md`
    publishes could not be regenerated and the page kept a bound the engine no longer produced.
    """
    from scripts import fp_aggregate as agg

    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=True, judge_b="openai/gpt-oss-120b",
                     host_b="groq")  # fmt: skip

    agg.summarise([str(src)])  # the source alone is still aggregable

    # (1) a source pooled with a re-score OF it — the original MP-208 shape.
    with pytest.raises(SystemExit, match="double-count"):
        agg.summarise([str(src), str(dst)])

    # (2) TWO re-scores of the SAME source: a second judge and a changed engine, say. The old
    #     flag-based check caught this only as collateral of refusing everything.
    twin = _reheaded(dst, tmp_path / "twin.jsonl", judge="gpt-4.1-mini")
    with pytest.raises(SystemExit, match="double-count"):
        agg.summarise([str(dst), str(twin)])

    # (3) a re-score ALONE is legal, and is exactly how a re-scored run of record is published.
    alone = agg.summarise([str(dst)])
    assert alone["pooled"]["attempted"] > 0

    # (4) re-scores of DISTINCT sources are separate samples and pool normally.
    other = _reheaded(dst, tmp_path / "other.jsonl", rejudged_from="some-other-surface.jsonl")
    both = agg.summarise([str(dst), str(other)])
    assert both["pooled"]["attempted"] == 2 * alone["pooled"]["attempted"]


def test_a_rescored_aggregation_says_so_before_it_shows_a_number(monkeypatch, tmp_path):
    """A re-scored table is indistinguishable from a fresh run's, and a reader who mistakes
    one for the other has silently doubled the evidence behind the bound. So the provenance
    banner is printed FIRST, names each source, and says the re-score REPLACES it. ADR-0037."""
    from scripts import fp_aggregate as agg

    src, dst = _pair(monkeypatch, tmp_path, judge_b_verdict=True, judge_b="openai/gpt-oss-120b",
                     host_b="groq")  # fmt: skip

    assert agg.summarise([str(src)])["provenance"] == []
    assert agg.render(agg.summarise([str(src)]))[0].startswith("### Surfaces")

    rescored = agg.render(agg.summarise([str(dst)]))
    banner = chr(10).join(rescored[: rescored.index("### Surfaces")])
    assert "RE-SCORES of stored replays, not new samples" in banner
    assert "REPLACES its source" in banner
    assert src.name in banner, "the banner must name the source it replaces"
    assert "a second judge" in banner, "judge changed, so the banner must say which kind"


def test_an_arm_nobody_bought_says_so_instead_of_blaming_the_operators_key(monkeypatch, tmp_path):
    """`[M] 2026-09-07` the first real `--rejudge --arm fp` run printed, for the arm it had
    deliberately not purchased: *"Provider errors (never reached a verdict): 12"*, then
    *"THE DETECTION ARM REACHED NO VERDICT ... This is a connectivity/credentials problem."*
    The run was clean and the key was fine. An artifact of record must not accuse its operator
    of a fault that is actually a flag they passed on purpose."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record_rounds(monkeypatch, src, 2)
    text = _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "openai/gpt-oss-120b",
            "--judge-provider",
            "groq",
            "--arm",
            "fp",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    assert "NOT MEASURED under this judge: --arm fp excluded the detection arm" in text
    assert "connectivity/credentials" not in text
    assert "Provider errors" not in text.split("INJECTED")[1]

    # ...and an offline re-read of that artifact must reach the same conclusion, which it can
    # only do if the artifact recorded which arm was bought.
    monkeypatch.setattr(sys, "argv", ["fp_measurement.py", "--rescore", str(dst)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fp.main()
    assert "NOT MEASURED under this judge" in buf.getvalue()
    assert "connectivity/credentials" not in buf.getvalue()


def test_a_rejudge_does_not_report_replay_tokens_as_if_it_spent_them(monkeypatch, tmp_path):
    """A rejudge makes no replay call, so its footer must not print the source run's replay
    tokens beside it unqualified - that invites a reader to price a free re-score as though it
    had bought the replay a second time, which is the whole cost claim inverted."""
    src, dst = tmp_path / "src.jsonl", tmp_path / "dst.jsonl"
    _record(monkeypatch, src, True)
    text = _main(
        monkeypatch,
        [
            "--rejudge",
            str(src),
            "--out",
            str(dst),
            "--judge",
            "openai/gpt-oss-120b",
            "--judge-provider",
            "groq",
        ],  # fmt: skip
        judge=_Judge(True),
    )
    assert "NO replay call was made" in text
    assert "re-scored (judge calls only)" in text
    assert "run live" not in text
