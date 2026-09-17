"""Open one pull request per retiring model, from `mp watch --json`, with `gh` and `git` alone.

MP-277: the Dependabot half. This runs inside the consumer's GitHub Actions job when
``action.yml`` is used with ``mode: watch``. No third-party action runs with the consumer's
token (MP-244's standard): everything here is ``gh`` and ``git``, both preinstalled on hosted
runners, driven through one injectable runner so the whole decision path is tested offline.

    python actions/watch_pr.py --watch-json "$RUNNER_TEMP/watch.json" --config modelpin.yaml
        [--provider P] [--runs N] [--match M] [--max-prs 3] [--recheck-days 7] [--base main]

For every row that decides the exit code, is affected, and names a successor:

* an open pull request on the row's branch updated inside ``--recheck-days`` is left alone
  and nothing is spent;
* otherwise ``mp check --from <model> --to <successor>`` runs (``--provider`` added only when
  the successor's vendor differs), and the report becomes
  ``.modelpin/migrations/<model>-to-<successor>.md`` on the branch
  ``modelpin/migrate-<model>-to-<successor>``, rebased on the base branch, committed as
  ``github-actions[bot]`` and force-pushed (the branch is Modelpin's own);
* the pull request is created, or updated when the report changed, or left untouched when the
  body hash is the same. The title never says "regression" for exit 3 or 4 (ADR-0035).

A row with no stored baseline gets a warning and no pull request: a verdict cannot be invented.
The run stops after ``--max-prs`` pull requests. Nothing here edits the consumer's own files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
DEFAULT_STORE = ".modelpin"
MIGRATIONS_DIR = Path(DEFAULT_STORE) / "migrations"
REPORT_PATH = Path(DEFAULT_STORE) / "last-report.md"


def _report_path(store_dir: str) -> Path:
    return Path(store_dir) / "last-report.md"


def _migrations_dir(store_dir: str) -> Path:
    return Path(store_dir) / "migrations"


MARKER = "<!-- modelpin-watch: {model} -> {successor} | sha256:{digest} -->"

VERDICT_WORDS = {
    0: "clean",
    1: "regression",
    3: "could not measure",
    4: "could not run",
}


class Completed(Protocol):
    returncode: int
    stdout: str


class Runner(Protocol):
    """Run one command in ``cwd``; never raises on a non-zero exit."""

    def __call__(self, cmd: list[str], *, cwd: Path) -> Completed: ...


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str


def subprocess_runner(cmd: list[str], *, cwd: Path) -> Result:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 and proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    return Result(proc.returncode, proc.stdout)


@dataclass(frozen=True)
class Outcome:
    model: str
    successor: Optional[str]
    action: str  # "opened" | "updated" | "unchanged" | "fresh" | "no-baseline" | "no-successor" | "capped"
    number: Optional[int] = None
    code: Optional[int] = None


def verdict_word(code: int) -> str:
    """0/1/3/4 in words; anything else is reported as a code, never guessed at (ADR-0035)."""
    return VERDICT_WORDS.get(code, f"exit {code}")


def candidates(doc: dict) -> list[dict]:
    """The rows this script may act on, in the order `mp watch` printed them."""
    return [
        r
        for r in doc.get("models", [])
        if r.get("decides_exit") and r.get("affected") and r.get("role") == "app"
    ]


def check_command(
    row: dict,
    *,
    config: str,
    provider: str,
    runs: str,
    match: str,
    store_dir: str = DEFAULT_STORE,
) -> list[str]:
    cmd = [
        "mp",
        "check",
        "--from",
        row["id"],
        "--to",
        row["replacement_id"],
        "--config",
        config,
    ]
    # The consumer's explicit provider wins; otherwise `mp watch` says whether the successor's
    # vendor differs from the retiring model's, and only then is `--provider` needed.
    if provider:
        cmd += ["--provider", provider]
    elif row.get("replacement_provider") and row.get("check_command", "").find("--provider") >= 0:
        cmd += ["--provider", row["replacement_provider"]]
    if runs:
        cmd += ["--runs", runs]
    if match:
        cmd += ["--match", match]
    if store_dir != DEFAULT_STORE:
        cmd += ["--store-dir", store_dir]
    return cmd


def render(row: dict, code: int, report: str, *, version: str) -> tuple[str, str, str]:
    """Title, body and the digest that decides whether an existing pull request changes."""
    model, successor = row["id"], row["replacement_id"]
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()[:16]
    when = row.get("retired_at") or "date not announced"
    title = f"modelpin: {model} retires {when}; {successor} verdict: {verdict_word(code)}"
    source = row.get("source_url") or "no source recorded"
    fetched = f" (fetched {row['fetched_at']})" if row.get("fetched_at") else ""
    lines = [
        MARKER.format(model=model, successor=successor, digest=digest),
        f"# {model} retires {when}",
        "",
        f"Modelpin replayed this repository's scenarios on **{successor}**, the successor the "
        f"vendor names, and compared them with the committed baseline for **{model}**.",
        "",
        f"- **Verdict:** {verdict_word(code)} (exit code {code})",
        f"- **Retirement:** {when}; source: {source}{fetched}",
    ]
    if row.get("notes"):
        lines.append(f"- **Vendor note:** {row['notes']}")
    lines += [
        f"- **Reproduce:** `{' '.join(check_command(row, config='modelpin.yaml', provider='', runs='', match=''))}`",
        f"- **Opened by:** modelpin {version}, `mode: watch`, on this repository's own schedule.",
        "",
        "Exit 3 means Modelpin could not measure at least one scenario; exit 4 means the check "
        "could not run at all (often a missing API-key secret). Neither is a verdict on the model.",
        "",
        "---",
        "",
        report.rstrip(),
        "",
        "---",
        "",
        "Pull requests opened with the default `GITHUB_TOKEN` do not trigger your other workflows. "
        "To run CI on this branch, pass a personal-access or GitHub App token as the Action's "
        "`github-token` input, or push an empty commit to it.",
        "",
    ]
    return title, "\n".join(lines), digest


def existing_pr(runner: Runner, cwd: Path, branch: str) -> Optional[dict]:
    """The open pull request on ``branch``, with number, updatedAt and body; None when there is none."""
    res = runner(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,updatedAt,body",
            "--limit",
            "1",
        ],
        cwd=cwd,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return None
    prs = json.loads(res.stdout)
    return prs[0] if prs else None


def is_fresh(pr: dict, *, recheck_days: int, now: datetime) -> bool:
    updated = datetime.fromisoformat(pr["updatedAt"].replace("Z", "+00:00"))
    return now - updated < timedelta(days=recheck_days)


def _run_check(
    runner: Runner, cwd: Path, cmd: list[str], store_dir: str = DEFAULT_STORE
) -> tuple[int, str]:
    res = runner(cmd, cwd=cwd)
    report_file = cwd / _report_path(store_dir)
    report = report_file.read_text(encoding="utf-8") if report_file.exists() else ""
    if not report:
        report = "Modelpin produced no report for this check; see the job log."
    return res.returncode, report


def publish(
    runner: Runner,
    cwd: Path,
    *,
    branch: str,
    base: str,
    file: Path,
    title: str,
    body: str,
    digest: str,
    existing: Optional[dict],
    store_dir: str = DEFAULT_STORE,
) -> tuple[str, Optional[int]]:
    """Put ``body`` on ``branch`` and create or update its pull request. Returns (action, number)."""
    if existing is not None and digest in (existing.get("body") or ""):
        return "unchanged", existing.get("number")
    git = ["git", "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
    for cmd in (
        ["git", "fetch", "--quiet", "origin", base],
        ["git", "checkout", "--quiet", "-B", branch, f"origin/{base}"],
    ):
        res = runner(cmd, cwd=cwd)
        if res.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed with exit {res.returncode}")
    (cwd / file).parent.mkdir(parents=True, exist_ok=True)
    (cwd / file).write_text(body, encoding="utf-8")
    for cmd in (
        # `-f`: the store's own .gitignore ignores everything but baselines, and must keep
        # doing so for the consumer's ordinary runs; this one file is the pull request's diff.
        ["git", "add", "-f", str(file)],
        git + ["commit", "--quiet", "-m", title],
        ["git", "push", "--quiet", "--force", "origin", branch],
    ):
        res = runner(cmd, cwd=cwd)
        if res.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed with exit {res.returncode}")
    body_file = cwd / store_dir / "watch-pr-body.md"
    body_file.write_text(body, encoding="utf-8")
    if existing is not None:
        res = runner(
            [
                "gh",
                "pr",
                "edit",
                str(existing["number"]),
                "--title",
                title,
                "--body-file",
                str(body_file),
            ],
            cwd=cwd,
        )
        if res.returncode != 0:
            raise RuntimeError(f"gh pr edit failed with exit {res.returncode}")
        return "updated", existing["number"]
    res = runner(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        cwd=cwd,
    )
    if res.returncode != 0:
        raise RuntimeError(f"gh pr create failed with exit {res.returncode}")
    number = None
    tail = res.stdout.strip().rsplit("/", 1)[-1]
    if tail.isdigit():
        number = int(tail)
    return "opened", number


def run(
    doc: dict,
    *,
    runner: Runner,
    cwd: Path,
    config: str,
    provider: str = "",
    runs: str = "",
    match: str = "",
    max_prs: int = 3,
    recheck_days: int = 7,
    base: str = "main",
    store_dir: str = DEFAULT_STORE,
    now: Optional[datetime] = None,
    warn: Callable[[str], None] = lambda m: print(f"::warning::{m}"),
) -> list[Outcome]:
    now = now or datetime.now(timezone.utc)
    version = doc.get("modelpin_version", "unknown")
    outcomes: list[Outcome] = []
    opened = 0
    for row in candidates(doc):
        model, successor = row["id"], row.get("replacement_id")
        if not successor:
            warn(
                f"{model} is retiring ({row.get('retired_at') or 'date not announced'}) and the "
                "vendor names no successor; choose one and run `mp check --from "
                f"{model} --to <model>`"
            )
            outcomes.append(Outcome(model, None, "no-successor"))
            continue
        if not row.get("has_baseline"):
            warn(
                f"no baseline for {model}; record one with `mp baseline --model {model}` before "
                f"{row.get('retired_at') or 'it retires'} or its successor {successor} cannot be checked"
            )
            outcomes.append(Outcome(model, successor, "no-baseline"))
            continue
        if opened >= max_prs:
            outcomes.append(Outcome(model, successor, "capped"))
            continue
        branch = row["branch"]
        existing = existing_pr(runner, cwd, branch)
        if existing is not None and is_fresh(existing, recheck_days=recheck_days, now=now):
            outcomes.append(Outcome(model, successor, "fresh", existing.get("number")))
            continue
        code, report = _run_check(
            runner,
            cwd,
            check_command(
                row,
                config=config,
                provider=provider,
                runs=runs,
                match=match,
                store_dir=store_dir,
            ),
            store_dir,
        )
        if code not in (0, 1):
            # No pull request without a measured verdict (wedge-warden C5/C6 on MP-277):
            # exit 3 measured too little, exit 4 never ran. Both are the job's red step, not
            # a migration pull request that would carry prose where a verdict belongs.
            warn(
                f"mp check --from {model} --to {successor} exited {code} "
                f"({verdict_word(code)}); no pull request is opened without a measured "
                "verdict. See the job log."
            )
            outcomes.append(Outcome(model, successor, "no-verdict", None, code))
            continue
        title, body, digest = render(row, code, report, version=version)
        file = _migrations_dir(store_dir) / f"{_safe(model)}-to-{_safe(successor)}.md"
        action, number = publish(
            runner,
            cwd,
            branch=branch,
            base=base,
            file=file,
            title=title,
            body=body,
            digest=digest,
            existing=existing,
            store_dir=store_dir,
        )
        if action == "opened":
            opened += 1
        outcomes.append(Outcome(model, successor, action, number, code))
    return outcomes


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in text)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--watch-json", required=True)
    parser.add_argument("--config", default="modelpin.yaml")
    parser.add_argument("--provider", default="")
    parser.add_argument("--runs", default="")
    parser.add_argument("--match", default="")
    parser.add_argument("--max-prs", type=int, default=3)
    parser.add_argument("--recheck-days", type=int, default=7)
    parser.add_argument("--base", default=os.environ.get("GITHUB_REF_NAME") or "main")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--store-dir", default=DEFAULT_STORE)
    args = parser.parse_args(argv)

    cwd = Path(args.cwd).resolve()
    doc = json.loads(Path(args.watch_json).read_text(encoding="utf-8"))
    outcomes = run(
        doc,
        runner=subprocess_runner,
        cwd=cwd,
        config=args.config,
        provider=args.provider,
        runs=args.runs,
        match=args.match,
        max_prs=args.max_prs,
        recheck_days=args.recheck_days,
        base=args.base,
        store_dir=args.store_dir,
    )
    numbers = [
        str(o.number)
        for o in outcomes
        if o.number is not None and o.action in ("opened", "updated")
    ]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [
        "| model | successor | action | PR | check exit |",
        "|---|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(
            f"| {o.model} | {o.successor or '-'} | {o.action} | "
            f"{('#' + str(o.number)) if o.number else '-'} | {o.code if o.code is not None else '-'} |"
        )
    text = "\n".join(lines) + "\n"
    print(text)
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Modelpin watch\n\n" + text)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"prs={','.join(numbers)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
