"""`actions/watch_pr.py`: one idempotent pull request per retiring model, from `mp watch --json`.

MP-277. Driven entirely through a recording fake runner: no `git`, no `gh`, no network. The
properties pinned here are the ones a consumer would be hurt by if they drifted: one pull
request per (model, successor) that is updated rather than duplicated; nothing spent inside the
re-check window; no pull request without a baseline; a title that never says "regression" for
exit 3 or 4; the consumer's own files never staged.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "watch_pr", Path(__file__).resolve().parents[1] / "actions" / "watch_pr.py"
)
watch_pr = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
# Registered before execution: `@dataclass` under `from __future__ import annotations` resolves
# field types through `sys.modules[cls.__module__]`, which is None for an unregistered module.
sys.modules[_SPEC.name] = watch_pr
_SPEC.loader.exec_module(watch_pr)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _row(model="gpt-4o-2024-05-13", successor="gpt-5.6-sol", **over):
    row = {
        "id": model,
        "sources": ["config"],
        "role": "app",
        "decides_exit": True,
        "known": True,
        "status": "deprecated",
        "effective_status": "deprecated",
        "retired_at": "2026-10-23",
        "days_remaining": 35,
        "affected": True,
        "replacement_id": successor,
        "replacement_provider": "openai",
        "source_url": "https://developers.openai.com/api/docs/deprecations",
        "fetched_at": "2026-09-17",
        "notes": "Section '2026-04-22: Legacy GPT model snapshots'.",
        "has_baseline": True,
        "baseline_path": f".modelpin/baseline-{model}.json",
        "check_command": f"modelpin check --from {model} --to {successor}",
        "branch": f"modelpin/migrate-{model}-to-{successor}",
    }
    row.update(over)
    return row


def _doc(*rows):
    return {
        "modelpin_version": "0.5.0",
        "today": "2026-09-18",
        "exit_code": 1,
        "models": list(rows),
    }


class FakeRunner:
    """Records every command; answers `gh pr list` from a script and `mp check` by writing a report."""

    def __init__(
        self,
        cwd: Path,
        *,
        open_prs=None,
        check_code=0,
        report="## report\n\nunchanged\n",
    ):
        self.cwd = cwd
        self.calls: list[list[str]] = []
        self.open_prs = dict(open_prs or {})  # branch -> dict(number, updatedAt, body)
        self.check_code = check_code
        self.report = report

    def __call__(self, cmd, *, cwd):
        assert cwd == self.cwd
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            branch = cmd[cmd.index("--head") + 1]
            pr = self.open_prs.get(branch)
            return watch_pr.Result(0, json.dumps([pr] if pr else []))
        if cmd[:2] == ["mp", "check"]:
            (cwd / ".modelpin").mkdir(exist_ok=True)
            (cwd / watch_pr.REPORT_PATH).write_text(self.report, encoding="utf-8")
            return watch_pr.Result(self.check_code, "")
        if cmd[:3] == ["gh", "pr", "create"]:
            return watch_pr.Result(0, "https://github.com/o/r/pull/42\n")
        return watch_pr.Result(0, "")

    def commands(self, *prefix):
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def _run(runner, doc, **kw):
    return watch_pr.run(
        doc,
        runner=runner,
        cwd=runner.cwd,
        config="modelpin.yaml",
        now=NOW,
        warn=kw.pop("warn", lambda m: None),
        **kw,
    )


def test_a_retiring_model_with_a_baseline_gets_one_pull_request(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path)
    out = _run(runner, _doc(_row()))
    assert [o.action for o in out] == ["opened"] and out[0].number == 42
    assert runner.commands("mp", "check") == [
        [
            "mp",
            "check",
            "--from",
            "gpt-4o-2024-05-13",
            "--to",
            "gpt-5.6-sol",
            "--config",
            "modelpin.yaml",
        ]
    ]
    assert len(runner.commands("gh", "pr", "create")) == 1
    create = runner.commands("gh", "pr", "create")[0]
    assert create[create.index("--head") + 1] == "modelpin/migrate-gpt-4o-2024-05-13-to-gpt-5.6-sol"
    assert create[create.index("--base") + 1] == "main"


def test_the_migration_file_is_the_only_thing_staged(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path)
    _run(runner, _doc(_row()))
    adds = runner.commands("git", "add")
    assert adds == [
        [
            "git",
            "add",
            "-f",
            str(Path(".modelpin/migrations/gpt-4o-2024-05-13-to-gpt-5.6-sol.md")),
        ]
    ]
    assert (tmp_path / ".modelpin/migrations/gpt-4o-2024-05-13-to-gpt-5.6-sol.md").exists()


def test_the_branch_is_rebased_on_the_base_branch_each_run(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path)
    _run(runner, _doc(_row()), base="develop")
    assert ["git", "fetch", "--quiet", "origin", "develop"] in runner.calls
    assert [
        "git",
        "checkout",
        "--quiet",
        "-B",
        "modelpin/migrate-gpt-4o-2024-05-13-to-gpt-5.6-sol",
        "origin/develop",
    ] in runner.calls


def test_an_open_pull_request_is_updated_not_duplicated(tmp_path: Path) -> None:
    stale = {
        "number": 7,
        "updatedAt": "2026-09-01T00:00:00Z",
        "body": "<!-- modelpin-watch: x | sha256:old -->",
    }
    runner = FakeRunner(tmp_path, open_prs={_row()["branch"]: stale})
    out = _run(runner, _doc(_row()))
    assert out[0].action == "updated" and out[0].number == 7
    assert runner.commands("gh", "pr", "create") == []
    assert runner.commands("gh", "pr", "edit")[0][:4] == ["gh", "pr", "edit", "7"]


def test_a_pull_request_updated_within_the_recheck_window_spends_nothing(
    tmp_path: Path,
) -> None:
    fresh = {"number": 7, "updatedAt": "2026-09-16T00:00:00Z", "body": ""}
    runner = FakeRunner(tmp_path, open_prs={_row()["branch"]: fresh})
    out = _run(runner, _doc(_row()), recheck_days=7)
    assert out[0].action == "fresh" and out[0].number == 7
    assert runner.commands("mp", "check") == [] and runner.commands("git", "push") == []


def test_an_unchanged_report_is_neither_pushed_nor_edited(tmp_path: Path) -> None:
    report = "## report\n\nunchanged\n"
    digest = watch_pr.hashlib.sha256(report.encode()).hexdigest()[:16]
    stale = {"number": 7, "updatedAt": "2026-09-01T00:00:00Z", "body": f"x {digest} y"}
    runner = FakeRunner(tmp_path, open_prs={_row()["branch"]: stale}, report=report)
    out = _run(runner, _doc(_row()))
    assert out[0].action == "unchanged"
    assert runner.commands("git", "push") == [] and runner.commands("gh", "pr", "edit") == []


def test_no_baseline_yields_a_warning_and_no_pull_request(tmp_path: Path) -> None:
    warnings: list[str] = []
    runner = FakeRunner(tmp_path)
    out = _run(runner, _doc(_row(has_baseline=False, baseline_path=None)), warn=warnings.append)
    assert out[0].action == "no-baseline"
    assert runner.commands("mp", "check") == [] and runner.commands("gh", "pr", "create") == []
    assert (
        warnings
        and "mp baseline --model gpt-4o-2024-05-13" in warnings[0]
        and "2026-10-23" in warnings[0]
    )


def test_no_successor_yields_a_warning_and_no_pull_request(tmp_path: Path) -> None:
    warnings: list[str] = []
    runner = FakeRunner(tmp_path)
    out = _run(
        runner,
        _doc(_row(replacement_id=None, branch=None, check_command=None)),
        warn=warnings.append,
    )
    assert out[0].action == "no-successor" and runner.calls == []
    assert "names no successor" in warnings[0]


def test_the_cap_stops_further_checks(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path)
    rows = [
        _row(model=f"old-{i}", successor="new", branch=f"modelpin/migrate-old-{i}-to-new")
        for i in range(3)
    ]
    out = _run(runner, _doc(*rows), max_prs=2)
    assert [o.action for o in out] == ["opened", "opened", "capped"]
    assert len(runner.commands("mp", "check")) == 2


def test_rows_that_do_not_decide_the_exit_are_never_acted_on(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path)
    out = _run(runner, _doc(_row(decides_exit=False), _row(affected=False), _row(role="judge")))
    assert out == [] and runner.calls == []


@pytest.mark.parametrize(("code", "word"), [(0, "clean"), (1, "regression")])
def test_a_measured_verdict_opens_a_pull_request_whose_title_carries_the_date(
    tmp_path: Path, code: int, word: str
) -> None:
    runner = FakeRunner(tmp_path, check_code=code)
    _run(runner, _doc(_row()))
    create = runner.commands("gh", "pr", "create")[0]
    title = create[create.index("--title") + 1]
    assert title == f"modelpin: gpt-4o-2024-05-13 retires 2026-10-23; gpt-5.6-sol verdict: {word}"


@pytest.mark.parametrize("code", [3, 4, 9])
def test_no_pull_request_is_opened_without_a_measured_verdict(tmp_path: Path, code: int) -> None:
    """Exit 3 measured too little and exit 4 never ran: the job goes red, no PR is opened."""
    warnings: list[str] = []
    runner = FakeRunner(tmp_path, check_code=code)
    out = _run(runner, _doc(_row()), warn=warnings.append)
    assert out[0].action == "no-verdict" and out[0].code == code
    assert runner.commands("gh", "pr", "create") == []
    assert runner.commands("git", "push") == []
    assert warnings and "no pull request is opened without a measured verdict" in warnings[0]
    assert "regression" not in warnings[0]


def test_verdict_words_never_call_3_or_4_a_regression() -> None:
    assert watch_pr.verdict_word(3) == "could not measure"
    assert watch_pr.verdict_word(4) == "could not run"
    assert watch_pr.verdict_word(9) == "exit 9"


def test_the_body_carries_the_source_the_fetch_date_and_the_token_note(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(tmp_path)
    _run(runner, _doc(_row()))
    body = (tmp_path / ".modelpin/migrations/gpt-4o-2024-05-13-to-gpt-5.6-sol.md").read_text(
        encoding="utf-8"
    )
    assert body.startswith("<!-- modelpin-watch: gpt-4o-2024-05-13 -> gpt-5.6-sol | sha256:")
    assert "https://developers.openai.com/api/docs/deprecations (fetched 2026-09-17)" in body
    assert "Vendor note:" in body and "## report" in body
    assert "GITHUB_TOKEN" in body and "do not trigger your other workflows" in body
    assert "mp check --from gpt-4o-2024-05-13 --to gpt-5.6-sol" in body


def test_the_provider_flag_is_added_only_across_vendors_or_when_the_consumer_set_one(
    tmp_path: Path,
) -> None:
    same = watch_pr.check_command(_row(), config="c.yaml", provider="", runs="", match="")
    assert "--provider" not in same
    cross = watch_pr.check_command(
        _row(
            replacement_provider="google",
            check_command="modelpin check --from a --to b --provider google",
        ),
        config="c.yaml",
        provider="",
        runs="",
        match="",
    )
    assert cross[-2:] == ["--provider", "google"]
    forced = watch_pr.check_command(
        _row(), config="c.yaml", provider="openrouter", runs="7", match="subset"
    )
    assert forced[-6:] == [
        "--provider",
        "openrouter",
        "--runs",
        "7",
        "--match",
        "subset",
    ]


def test_a_verdict_with_a_missing_report_still_produces_a_body_that_says_so(
    tmp_path: Path,
) -> None:
    class NoReport(FakeRunner):
        def __call__(self, cmd, *, cwd):
            if cmd[:2] == ["mp", "check"]:
                self.calls.append(cmd)
                return watch_pr.Result(1, "")  # a verdict, but the report file is absent
            return super().__call__(cmd, cwd=cwd)

    runner = NoReport(tmp_path)
    out = _run(runner, _doc(_row()))
    assert out[0].code == 1 and out[0].action == "opened"
    body = (tmp_path / ".modelpin/migrations/gpt-4o-2024-05-13-to-gpt-5.6-sol.md").read_text(
        encoding="utf-8"
    )
    assert "produced no report" in body
    assert "**Verdict:** regression (exit code 1)" in body
