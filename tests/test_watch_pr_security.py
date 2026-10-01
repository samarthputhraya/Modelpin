"""`actions/watch_pr.py` acts only on what Modelpin made, and never destroys anyone else's work.

Two findings from the security review of MP-277 (2026-09-18), each reproduced here before it was
fixed:

1. **A fork pull request could borrow Modelpin's identity.** `gh pr list --head <name>` matches by
   head branch NAME, so an open pull request from ANY fork whose branch is named
   `modelpin/migrate-<model>-to-<successor>` (predictable from a public `modelpin.yaml` and the
   public registry) was adopted as Modelpin's own: the scheduled run edited the attacker's pull
   request to carry Modelpin's title, verdict and report over the attacker's diff, and the genuine
   migration pull request was never opened.

2. **A force-push could discard a consumer's own commits.** When the report changed or the pull
   request went stale, the run reset the branch to the base tip and `git push --force`d it, with
   no look at what was on the branch. A consumer who had pushed their migration onto Modelpin's
   branch (the ordinary way to land it) lost those commits from the branch head.

The fake below answers the way GitHub does: `gh pr list --head` returns every open pull request
with that head name, forks included; `git ls-remote --exit-code` exits 2 for a missing ref; the
compare API lists the commits a branch carries beyond its base, with their author emails.
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
sys.modules[_SPEC.name] = watch_pr
_SPEC.loader.exec_module(watch_pr)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
MODEL, SUCCESSOR = "gpt-4o-2024-05-13", "gpt-5.6-sol"
BRANCH = f"modelpin/migrate-{MODEL}-to-{SUCCESSOR}"
TIP = "a" * 40
STALE = "2026-09-01T00:00:00Z"
FRESH = "2026-09-17T00:00:00Z"
OURS_MARKER = f"<!-- modelpin-watch: {MODEL} -> {SUCCESSOR} | sha256:0123456789abcdef -->"
HUMAN = "dev@example.com"


def _row(**over):
    row = {
        "id": MODEL,
        "role": "app",
        "decides_exit": True,
        "affected": True,
        "retired_at": "2026-10-23",
        "replacement_id": SUCCESSOR,
        "replacement_provider": "openai",
        "source_url": "https://developers.openai.com/api/docs/deprecations",
        "fetched_at": "2026-09-17",
        "has_baseline": True,
        "check_command": f"modelpin check --from {MODEL} --to {SUCCESSOR}",
        "branch": BRANCH,
    }
    row.update(over)
    return row


def _doc(*rows):
    return {"modelpin_version": "0.5.0", "models": list(rows)}


def _pr(number, *, updated=STALE, body=OURS_MARKER, fork=False):
    return {"number": number, "updatedAt": updated, "body": body, "isCrossRepository": fork}


class GitHub:
    """A recording fake of `gh` and `git` that answers as GitHub does."""

    def __init__(self, cwd, *, prs=(), tip=None, ahead=(), ahead_by=None, ls_remote_code=None):
        self.cwd = cwd
        self.calls: list[list[str]] = []
        self.prs = list(prs)  # every open PR whose head branch is named BRANCH, forks included
        self.tip = tip  # the sha origin's BRANCH points at, or None when it does not exist
        self.ahead = list(ahead)  # author emails of the commits BRANCH carries beyond the base
        self.ahead_by = len(self.ahead) if ahead_by is None else ahead_by
        self.ls_remote_code = ls_remote_code

    def __call__(self, cmd, *, cwd):
        assert cwd == self.cwd
        self.calls.append(list(cmd))
        if cmd[:3] == ["gh", "pr", "list"]:
            assert cmd[cmd.index("--head") + 1] == BRANCH
            return watch_pr.Result(0, json.dumps(self.prs))
        if cmd[:2] == ["git", "ls-remote"]:
            if self.ls_remote_code is not None:
                return watch_pr.Result(self.ls_remote_code, "")
            if self.tip is None:
                return watch_pr.Result(2, "")
            return watch_pr.Result(0, f"{self.tip}\trefs/heads/{BRANCH}\n")
        if cmd[:2] == ["gh", "api"]:
            assert cmd[2].endswith(f"/compare/main...{self.tip}"), cmd
            commits = [{"commit": {"author": {"email": e}}} for e in self.ahead]
            return watch_pr.Result(0, json.dumps({"ahead_by": self.ahead_by, "commits": commits}))
        if cmd[:2] == ["mp", "check"]:
            (cwd / ".modelpin").mkdir(exist_ok=True)
            (cwd / watch_pr.REPORT_PATH).write_text("## report\n\nchanged\n", encoding="utf-8")
            return watch_pr.Result(0, "")
        if cmd[:3] == ["gh", "pr", "create"]:
            return watch_pr.Result(0, "https://github.com/o/r/pull/42\n")
        return watch_pr.Result(0, "")

    def commands(self, *prefix):
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]

    def edited(self):
        return [c[3] for c in self.commands("gh", "pr", "edit")]


def _run(gh, warnings=None):
    return watch_pr.run(
        _doc(_row()),
        runner=gh,
        cwd=gh.cwd,
        config="modelpin.yaml",
        now=NOW,
        warn=(warnings.append if warnings is not None else (lambda m: None)),
    )


# ------------------------------------------------- finding 1: a fork PR borrowing our identity


def test_a_fork_pull_request_on_the_predictable_branch_name_is_never_edited(tmp_path) -> None:
    warnings: list[str] = []
    gh = GitHub(tmp_path, prs=[_pr(66, body="arbitrary diff, arbitrary text", fork=True)])
    out = _run(gh, warnings)
    assert "66" not in gh.edited(), "Modelpin's verdict was written onto a fork's pull request"
    assert out[0].action == "opened" and out[0].number == 42
    assert len(gh.commands("gh", "pr", "create")) == 1
    assert any("66" in w and "ignored" in w for w in warnings)


def test_a_fork_pull_request_that_forges_the_marker_is_still_never_edited(tmp_path) -> None:
    gh = GitHub(tmp_path, prs=[_pr(66, body=OURS_MARKER + "\nforged", fork=True)])
    out = _run(gh)
    assert "66" not in gh.edited()
    assert out[0].action == "opened"


def test_a_fresh_fork_pull_request_cannot_suppress_the_genuine_one(tmp_path) -> None:
    """Kept fresh by its author, it used to read as 'fresh' and stop Modelpin forever."""
    gh = GitHub(tmp_path, prs=[_pr(66, updated=FRESH, fork=True)])
    out = _run(gh)
    assert out[0].action == "opened"
    assert len(gh.commands("mp", "check")) == 1


def test_a_same_repo_pull_request_without_the_marker_is_not_adopted(tmp_path) -> None:
    gh = GitHub(tmp_path, prs=[_pr(70, body="a maintainer's own migration")])
    _run(gh)
    assert "70" not in gh.edited()


def test_the_listing_asks_github_which_pull_requests_come_from_forks(tmp_path) -> None:
    gh = GitHub(tmp_path)
    _run(gh)
    listing = gh.commands("gh", "pr", "list")[0]
    fields = listing[listing.index("--json") + 1].split(",")
    assert "isCrossRepository" in fields and "body" in fields
    assert listing[listing.index("--limit") + 1] != "1", "a fork PR listed first hid ours"


def test_our_own_pull_request_is_still_found_beside_a_fork_one(tmp_path) -> None:
    gh = GitHub(tmp_path, prs=[_pr(66, fork=True), _pr(7)], tip=TIP, ahead=[watch_pr.BOT_EMAIL])
    out = _run(gh)
    assert out[0].action == "updated" and out[0].number == 7
    assert gh.edited() == ["7"]


def test_a_missing_cross_repository_field_is_read_as_not_ours(tmp_path) -> None:
    """Fail closed: a listing that cannot say where a PR came from never authorises an edit."""
    gh = GitHub(tmp_path, prs=[{"number": 66, "updatedAt": STALE, "body": OURS_MARKER}])
    _run(gh)
    assert "66" not in gh.edited()


# ------------------------------------------------- finding 2: a force-push over someone's commits


def test_a_branch_carrying_a_human_commit_is_never_pushed_and_nothing_is_spent(tmp_path) -> None:
    warnings: list[str] = []
    gh = GitHub(tmp_path, prs=[_pr(7)], tip=TIP, ahead=[watch_pr.BOT_EMAIL, HUMAN])
    out = _run(gh, warnings)
    assert gh.commands("git", "push") == [], "a consumer's commits were force-pushed away"
    assert gh.commands("mp", "check") == []
    assert gh.edited() == []
    assert out[0].action == "human-commits" and out[0].number == 7
    assert warnings and "left alone" in warnings[0]
    assert HUMAN not in warnings[0], "the warning names a count, not a collaborator's email"


def test_a_branch_with_no_open_pull_request_but_a_human_commit_is_left_alone(tmp_path) -> None:
    gh = GitHub(tmp_path, tip=TIP, ahead=[HUMAN])
    out = _run(gh)
    assert out[0].action == "human-commits"
    assert gh.commands("git", "push") == [] and gh.commands("gh", "pr", "create") == []


def test_a_branch_longer_than_the_compare_api_lists_is_treated_as_foreign(tmp_path) -> None:
    gh = GitHub(tmp_path, prs=[_pr(7)], tip=TIP, ahead=[watch_pr.BOT_EMAIL], ahead_by=300)
    out = _run(gh)
    assert out[0].action == "human-commits"
    assert gh.commands("git", "push") == []


def test_no_push_is_ever_an_unconditional_force(tmp_path) -> None:
    for gh in (
        GitHub(tmp_path),  # a new branch
        GitHub(tmp_path, prs=[_pr(7)], tip=TIP, ahead=[watch_pr.BOT_EMAIL]),  # ours, bot-only
    ):
        _run(gh)
        for push in gh.commands("git", "push"):
            assert "--force" not in push, push


def test_an_existing_branch_is_pushed_with_a_lease_on_the_sha_that_was_inspected(tmp_path) -> None:
    """Closes the window between the inspection and the push: `mp check` takes minutes, and a
    commit pushed meanwhile makes the push fail instead of vanishing."""
    gh = GitHub(tmp_path, prs=[_pr(7)], tip=TIP, ahead=[watch_pr.BOT_EMAIL])
    _run(gh)
    (push,) = gh.commands("git", "push")
    assert f"--force-with-lease=refs/heads/{BRANCH}:{TIP}" in push


def test_a_new_branch_is_pushed_with_a_lease_that_it_must_not_exist(tmp_path) -> None:
    gh = GitHub(tmp_path)
    _run(gh)
    (push,) = gh.commands("git", "push")
    assert f"--force-with-lease=refs/heads/{BRANCH}:" in push


def test_an_unreadable_remote_branch_fails_closed(tmp_path) -> None:
    gh = GitHub(tmp_path, prs=[_pr(7)], ls_remote_code=128)
    with pytest.raises(RuntimeError, match="ls-remote"):
        _run(gh)
    assert gh.commands("git", "push") == [] and gh.commands("mp", "check") == []


def test_the_pull_request_body_does_not_invite_a_commit_without_saying_what_it_does(
    tmp_path,
) -> None:
    gh = GitHub(tmp_path)
    _run(gh)
    body = (tmp_path / ".modelpin/migrations" / f"{MODEL}-to-{SUCCESSOR}.md").read_text(
        encoding="utf-8"
    )
    assert "push an empty commit" not in body
    assert "leaves the branch alone" in body
