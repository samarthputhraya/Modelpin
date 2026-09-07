"""MP-205: the false-positive harness can be CUT and RESUMED, RE-SCORED without a key, and run
concurrently - and none of that changes a published number.

Why these exist. `[M] 2026-09-07` the only prior live `arg_*` run lost 10 of its 70 trials to a
mid-run network outage; the verdicts it did reach survive only as a stdout transcript, and the
document quoting it admits *"closing it against the run itself needs a committed artifact of the
run, which the repo does not have"* (MP-83). The run of record for the north-star metric must
be (a) restartable at the trial it stopped on, (b) re-derivable offline from what it wrote, and
(c) identical at any worker count. Each of those is a way a rate could silently change, so each
is pinned here. Every provider below is a stub: ADR-0006, no live call from the suite, ever.
"""

from __future__ import annotations

import io
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modelpin.models import DiffResult, DiffVerdict, Scenario, ToolCall, Trace  # noqa: E402
from modelpin.providers.base import ProviderError  # noqa: E402
from scripts import fp_measurement as fp  # noqa: E402
from scripts import fp_aggregate as agg  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "examples" / "suite"


def _scn(sid: str, tools=()) -> Scenario:
    return Scenario(
        id=sid,
        name=sid,
        input={"messages": [{"role": "user", "content": "hi"}], "tools": list(tools)},
    )


def _trace(i: int, out: str = "ok", tools=(), refused=False) -> Trace:
    return Trace(
        scenario_id="s",
        model_id="m",
        run_idx=i,
        tool_calls=[ToolCall(name=n, arguments={"k": i}) for n in tools],
        final_output=out,
        refused=refused,
        tokens_in=10,
        tokens_out=3,
        latency_ms=1.0,
    )


def _result(sid: str, verdict=DiffVerdict.unchanged, conf=0.7) -> DiffResult:
    return DiffResult(
        scenario_id=sid, from_model="m", to_model="m", verdict=verdict, confidence=conf
    )


# --- the plan the arms will ask for -----------------------------------------------------


class TestPlan:
    def test_labels_follow_the_arms_own_rule(self):
        """The FP arm labels a row `id` for one round and `id#i` under --repeats. The planner
        must use the identical rule or a resumed run re-buys every trial under a key nothing
        in the artifact matches."""
        assert fp.fp_label("a", 0, 1) == "a"
        assert fp.fp_label("a", 0, 3) == "a#1"
        assert fp.fp_label("a", 2, 3) == "a#3"

    def test_fp_rows_come_repeat_major_then_the_recall_rows(self, monkeypatch):
        monkeypatch.setattr(fp, "PERTURBATIONS", {"b": "do it differently"})
        plan = fp.plan_trials([_scn("a"), _scn("b")], 2)
        assert [k for k, *_ in plan] == ["fp:a#1", "fp:b#1", "fp:a#2", "fp:b#2", "recall:b"]
        key, sid, base, cand = plan[-1]
        assert base is not cand and cand.input["messages"][0]["role"] == "system"

    def test_the_two_arms_never_share_a_key_even_when_their_labels_collide(self, monkeypatch):
        """With --repeats 1 both arms label a row by the bare id. The key must still tell them
        apart, or the recall arm reads the FP arm's verdict (always `unchanged` on a clean
        run) and scores every perturbation a MISS."""
        monkeypatch.setattr(fp, "PERTURBATIONS", {"a": "x"})
        plan = fp.plan_trials([_scn("a")], 1)
        assert [k for k, *_ in plan] == ["fp:a", "recall:a"]
        s = _scn("a")
        assert fp.trial_key(s, s, "a") != fp.trial_key(s, fp._perturb(s, "x"), "a")


# --- running the plan ---------------------------------------------------------------


class TestRunTrials:
    @staticmethod
    def _plan(n: int):
        scns = [_scn(f"s{i}") for i in range(n)]
        return [(f"fp:s{i}", f"s{i}", s, s) for i, s in enumerate(scns)]

    def test_memoised_trials_are_not_re_run_and_errors_are_not_memoised(self):
        calls: list[str] = []

        def live(base, cand, sid):
            calls.append(sid)
            if sid == "s2":
                return None  # a provider error
            return _result(sid), [_trace(0)], [_trace(0)]

        plan = self._plan(4)
        memo = {"fp:s0": (_result("s0"), {}, {})}
        seen = []
        fp.run_trials(plan, live, memo, 1, lambda *a: seen.append(a))
        assert calls == ["s1", "s2", "s3"], "s0 was in the memo and must not be bought again"
        assert set(memo) == {"fp:s0", "fp:s1", "fp:s3"}, "the error must stay OUT of the memo"
        errors = [a for a in seen if a[2] is None]
        assert [a[1] for a in errors] == ["s2"] and errors[0][5] == "provider error"

    def test_the_memo_is_identical_at_every_worker_count(self):
        """The report is built from the memo in plan order, so as long as the memo is the
        same the report is the same. Completion order under a pool is not."""

        def live(base, cand, sid):
            time.sleep(0.001 * (hash(sid) % 5))
            return _result(sid, conf=0.5), [_trace(0)], [_trace(0)]

        plan = self._plan(12)
        memos = []
        for workers in (1, 4):
            memo: dict = {}
            fp.run_trials(plan, live, memo, workers, lambda *a: None)
            memos.append({k: v[0].model_dump() for k, v in memo.items()})
        assert memos[0] == memos[1]

    def test_on_row_runs_on_the_calling_thread(self):
        """The artifact writer and the progress print both live in `on_row`; neither is
        written to be re-entrant, so the pool must hand results back, not call out."""
        main_thread = threading.current_thread()
        threads = set()

        def live(base, cand, sid):
            return _result(sid), [_trace(0)], [_trace(0)]

        fp.run_trials(
            self._plan(6), live, {}, 3, lambda *a: threads.add(threading.current_thread())
        )
        assert threads == {main_thread}

    def test_offline_mode_reports_every_missing_key_as_an_error_and_calls_nothing(self):
        seen = []
        memo = {"fp:s0": (_result("s0"), {}, {})}
        fp.run_trials(self._plan(3), None, memo, 4, lambda *a: seen.append(a))
        assert [(a[1], a[5]) for a in seen] == [
            ("s1", "not in artifact"),
            ("s2", "not in artifact"),
        ]


# --- the artifact -----------------------------------------------------------------------


class TestArtifact:
    def test_a_record_round_trips_verdict_repertoires_and_traces(self, tmp_path):
        base = [_trace(i, "ok", ("lookup",)) for i in range(5)]
        cand = [_trace(i, "ok" if i else "okay", ("lookup",)) for i in range(5)]
        rec = fp.trial_record("fp:a#1", "a#1", _result("a#1"), base, cand, None, 1.5, True)
        assert rec["scenario_id"] == "a" and rec["arm"] == "fp"
        assert rec["tokens_in"] == 100 and rec["tokens_out"] == 30
        assert rec["judge_calls"] == 1, "one candidate run differs from the modal text"
        assert "messages" not in rec["base_traces"][0], "the prompt is not the measurement"
        path = tmp_path / "a.jsonl"
        w = fp.ArtifactWriter(str(path))
        w.write({"kind": "header", "model": "m"})
        w.write(rec)
        header, rows = fp.load_artifact(str(path))
        assert header["model"] == "m" and len(rows) == 1
        memo = fp.memo_from_rows(rows)
        res, brep, crep = memo["fp:a#1"]
        assert res == _result("a#1") and brep == fp.repertoire(base) and crep == fp.repertoire(cand)
        hydrated = fp.traces_from_json(rows[0]["cand_traces"])
        assert [t.final_output for t in hydrated] == [t.final_output for t in cand]
        assert hydrated[0].tool_calls[0].name == "lookup"

    def test_a_verdict_supersedes_an_error_but_never_the_reverse(self, tmp_path):
        """A resumed run re-attempts errors and appends the verdict AFTER the error line; the
        loader must keep the verdict. And if a later line is an error for a key that already
        has a verdict, the verdict stays: nothing asked for that re-run."""
        path = tmp_path / "a.jsonl"
        w = fp.ArtifactWriter(str(path))
        w.write({"kind": "header"})
        w.write(fp.trial_record("fp:a", "a", None, None, None, "boom", 0.1, False))
        w.write(
            fp.trial_record("fp:a", "a", _result("a"), [_trace(0)], [_trace(0)], None, 1, False)
        )
        w.write(fp.trial_record("fp:a", "a", None, None, None, "boom again", 0.1, False))
        w.write({"kind": "resume", "ts": "x"})
        w.write({"kind": "summary"})
        _, rows = fp.load_artifact(str(path))
        assert len(rows) == 1 and rows[0]["result"] is not None

    def test_resume_refuses_an_artifact_recorded_under_another_configuration(self):
        header = {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "runs": 5,
            "judge": "j",
            "scenarios_dir": "suite",
            "role": None,
            "repeats": 3,
            "only": None,
        }
        fp.check_resume_header(header, dict(header))
        with pytest.raises(SystemExit, match="runs: artifact=5 now=8"):
            fp.check_resume_header(header, {**header, "runs": 8})
        with pytest.raises(SystemExit, match="model"):
            fp.check_resume_header(header, {**header, "model": "gpt-4.1-mini"})
        with pytest.raises(SystemExit, match="no header"):
            fp.check_resume_header(None, header)

    def test_only_refuses_an_id_that_is_not_in_the_set(self):
        assert fp.parse_only(None) is None
        assert fp.parse_only(" a, b ,") == {"a", "b"}
        with pytest.raises(SystemExit, match="at least one"):
            fp.parse_only(" , ")
        with pytest.raises(SystemExit, match="zzz"):
            fp.select_only([_scn("a")], {"a", "zzz"})
        assert [s.id for s in fp.select_only([_scn("a"), _scn("b")], {"b"})] == ["b"]


# --- main() end to end, offline -----------------------------------------------------------
#
# `main()` needs a provider, and ADR-0006 forbids a live one. A STUB adapter and judge are
# injected through the same seams the real ones use (`get_adapter`, `build_judge`), so the
# whole path - header, per-trial lines, both arms, footer, summary - runs for real against
# nothing. This is the one place the harness's wiring is executed rather than grepped.


class _StubAdapter:
    """Answers every scenario identically, except a perturbed candidate, which drops its tool
    call - so the FP arm is clean by construction and the recall arm has something to catch.
    `fail_recall` turns the perturbed trial into a provider error instead."""

    def __init__(self, fail_recall: bool = False) -> None:
        self.calls = 0
        self.fail_recall = fail_recall

    def preflight(self) -> None:
        pass

    def run(self, scenario, model_id, run_idx=0):
        self.calls += 1
        perturbed = scenario.input["messages"][0].get("role") == "system" and "Policy change" in (
            scenario.input["messages"][0].get("content") or ""
        )
        if perturbed and self.fail_recall:
            raise ProviderError("stub outage")
        tools = () if perturbed else tuple(str(t) for t in scenario.input.get("tools") or ())[:1]
        return _trace(run_idx, "Your order has shipped.", tools)


class _StubJudge:
    def preflight(self) -> None:
        pass

    def equivalent(self, reference, candidate, task=None) -> bool:
        return True


def _run_main(monkeypatch, argv: list[str], adapter=None):
    adapter = adapter or _StubAdapter()
    monkeypatch.setattr(fp, "get_adapter", lambda name: adapter)
    monkeypatch.setattr(fp, "build_judge", lambda model: _StubJudge())
    monkeypatch.setattr(sys, "argv", ["fp_measurement.py", *argv])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fp.main()
    return buf.getvalue(), adapter


def _live_argv(out: Path, *extra: str) -> list[str]:
    return [
        "--provider",
        "stub",
        "--model",
        "m",
        "--runs",
        "4",
        "--judge",
        "j",
        "--scenarios-dir",
        str(SUITE),
        "--only",
        "refund_request,order_status",
        "--repeats",
        "2",
        "--out",
        str(out),
        *extra,
    ]


def test_a_live_run_writes_a_header_then_one_line_per_trial(monkeypatch, tmp_path):
    out = tmp_path / "run.jsonl"
    text, adapter = _run_main(monkeypatch, _live_argv(out, "--workers", "2"))
    header, rows = fp.load_artifact(str(out))
    assert header["model"] == "m" and header["runs"] == 4 and header["repeats"] == 2
    assert header["only"] == ["order_status", "refund_request"]
    assert header["constants"]["ALPHA"] == 0.05 and header["git_sha"]
    assert sorted(r["key"] for r in rows) == sorted(
        [
            "fp:order_status#1",
            "fp:refund_request#1",
            "fp:order_status#2",
            "fp:refund_request#2",
            "recall:refund_request",
        ]
    )
    assert adapter.calls == 5 * 2 * 4, "5 trials x 2 sides x 4 runs"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "header"
    assert json.loads(lines[-1])["kind"] == "summary"
    assert "False-positive rate: 0/" in text and "Detection: 1/1" in text
    assert "This invocation: 5 trial(s) run live, 0 reused" in text


def test_a_cut_run_resumes_from_the_trial_it_stopped_on(monkeypatch, tmp_path):
    """Round 1: the perturbed trial hits a provider error and is recorded as one. Round 2
    with --resume: the four clean trials are reused (no adapter call), the error is
    re-attempted, and the artifact's loader shows the verdict superseding the error."""
    out = tmp_path / "run.jsonl"
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)  # the retry back-off, not the suite
    text1, a1 = _run_main(monkeypatch, _live_argv(out), adapter=_StubAdapter(fail_recall=True))
    assert "Provider errors (never reached a verdict): 1" in text1
    # 4 clean trials x 2 sides x 4 runs, then the recall trial: its baseline side ran (4),
    # and its candidate side raised on the first call of each of _replay_resilient's 4 attempts.
    assert a1.calls == 4 * 2 * 4 + 4 + 4, "the recall trial's baseline side ran before the outage"
    text2, a2 = _run_main(monkeypatch, _live_argv(out, "--resume"))
    assert "4 reused from the artifact" in text2 and "1 trial(s) run live" in text2
    assert a2.calls == 2 * 4, "only the failed trial was bought again"
    assert "Detection: 1/1" in text2 and "Provider errors" not in text2.split("INJECTED")[1]
    _, rows = fp.load_artifact(str(out))
    assert len(rows) == 5 and all(r["result"] is not None for r in rows)
    kinds = [json.loads(ln)["kind"] for ln in out.read_text(encoding="utf-8").splitlines()]
    assert kinds.count("header") == 1 and kinds.count("resume") == 1


def test_resume_needs_out_and_out_refuses_to_overwrite_silently(monkeypatch, tmp_path):
    out = tmp_path / "run.jsonl"
    _run_main(monkeypatch, _live_argv(out))
    with pytest.raises(SystemExit, match="exists"):
        _run_main(monkeypatch, _live_argv(out))
    with pytest.raises(SystemExit, match="--resume needs --out"):
        _run_main(monkeypatch, [*_live_argv(out)[:-2], "--resume"])


def test_resume_refuses_a_different_configuration_on_the_command_line(monkeypatch, tmp_path):
    out = tmp_path / "run.jsonl"
    _run_main(monkeypatch, _live_argv(out))
    argv = _live_argv(out, "--resume")
    argv[argv.index("--runs") + 1] = "5"
    with pytest.raises(SystemExit, match="different configuration"):
        _run_main(monkeypatch, argv)


def test_rescore_rebuilds_both_arms_from_the_artifact_and_never_touches_a_provider(
    monkeypatch, tmp_path
):
    out = tmp_path / "run.jsonl"
    live_text, _ = _run_main(monkeypatch, _live_argv(out))

    def no_provider(*a, **k):
        raise AssertionError("--rescore constructed a provider")

    monkeypatch.setattr(fp, "get_adapter", no_provider)
    monkeypatch.setattr(fp, "build_judge", no_provider)
    monkeypatch.setattr(
        sys, "argv", ["fp_measurement.py", "--rescore", str(out), "--model", "IGNORED"]
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fp.main()
    text = buf.getvalue()
    assert "RESCORED OFFLINE" in text and "model=m " in text, "the artifact's config wins"

    def arms(t: str) -> str:
        return t[t.index("EQUIVALENT PAIRS") : t.index("This invocation")]

    assert arms(text) == arms(live_text), "an offline rescore must print the arms verbatim"
    assert "0 trial(s) run live, 5 reused" in text
    assert "over all 5 trial(s)" in text, "tokens are summed over the artifact, not the live rows"
    assert out.read_text(encoding="utf-8").count('"kind": "summary"') == 1, "--rescore wrote"


def test_rescore_refuses_to_write(monkeypatch, tmp_path):
    out = tmp_path / "run.jsonl"
    _run_main(monkeypatch, _live_argv(out))
    with pytest.raises(SystemExit, match="never writes"):
        _run_main(monkeypatch, ["--rescore", str(out), "--out", str(tmp_path / "b.jsonl")])


def test_the_arms_still_go_through_build_row_and_the_memo(monkeypatch):
    """The concurrency layer fills a memo the arms read; it must not have become a second
    path that computes rows for them. `build_row` is the shape both arm guards pin."""
    src = Path(fp.__file__).read_text(encoding="utf-8")
    body = src[src.index("def main()") :]
    assert body.count("build_row(") == 2, "each arm builds its rows through build_row exactly once"
    assert "memo.get(trial_key(" in body, "_verdict reads the memo by the planner's key"
    assert "run_trials(plan," in body, "the plan is run before either arm prints"


# --- the aggregator ---------------------------------------------------------------------


def test_the_aggregator_pools_surfaces_and_lists_every_flag(monkeypatch, tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _run_main(monkeypatch, _live_argv(a))
    _run_main(monkeypatch, [*_live_argv(b)[:3], "m2", *_live_argv(b)[4:]])
    # Plant one false positive into b by hand: the pooled numbers must carry it.
    rows = b.read_text(encoding="utf-8").splitlines()
    planted = json.loads(rows[1])
    planted["key"], planted["sid"] = "fp:refund_request#9", "refund_request#9"
    planted["result"]["verdict"] = "changed_minor"
    planted["result"]["explanation"] = "planted"
    b.write_text("\n".join([*rows, json.dumps(planted)]) + "\n", encoding="utf-8")

    summary = agg.summarise([str(a), str(b)])
    assert [s["model"] for s in summary["surfaces"]] == ["m", "m2"]
    assert summary["pooled"]["attempted"] == 9 and summary["pooled"]["false_positives"] == 1
    assert [f["sid"] for f in summary["flagged"]] == ["refund_request#9"]
    assert summary["pooled"]["recall"]["detected"] == 2
    scored = summary["pooled"]["scored"]
    if scored:
        assert summary["pooled"]["conditional_ub"] == pytest.approx(fp.upper_bound_95(1, scored))
    md = "\n".join(agg.render(summary))
    assert "**POOLED**" in md and "planted" in md
    block = [ln.strip() for ln in md.split("```")[1].splitlines() if ln.strip()]
    expected = [ln.strip() for ln in fp.recall_summary(summary["pooled"]["recall"]) if ln.strip()]
    assert block == expected, "the detection block is recall_summary's own text, verbatim"
