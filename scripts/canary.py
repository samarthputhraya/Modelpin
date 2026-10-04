"""The public daily canary: each model checked against its own earlier recording.

    python scripts/canary.py --site <gh-pages checkout>            # run every model, rebuild the page
    python scripts/canary.py --site <gh-pages checkout> --render-only

Run once a day by `.github/workflows/canary.yml`. For each model below it replays the open
example suite with `modelpin check --from M --to M` against a baseline of the same model,
recorded by the canary the first time it ran that model, keeps both reports, appends one row
to `canary/results.json`, and rebuilds `canary/index.html` on the GitHub Pages branch.

A flag means the model behaved differently from its own earlier recording: either the host
changed what the model does under the same name, or Modelpin raised a false alarm. The page
publishes both and ranks nothing (ADR-0009). Free tiers only; a model whose key is not set is
skipped, not reported. Stdlib only, so the workflow needs nothing beyond `modelpin` itself.

Exit 0 when at least one model was measured, 1 when none was (a broken key turns the job red),
2 on bad arguments.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

#: (model id, host, the environment variable that holds its key). Every model here must run the
#: whole suite, tool calls included: `allam-2-7b` is Groq's fifth chat model and answers a tool
#: definition with a 400, so it is not listed. A Gemini AI Studio model joins with one row and a
#: `GEMINI_API_KEY` repository secret.
MODELS: tuple[tuple[str, str, str], ...] = (
    ("openai/gpt-oss-120b", "groq", "GROQ_API_KEY"),
    ("openai/gpt-oss-20b", "groq", "GROQ_API_KEY"),
    ("openai/gpt-oss-safeguard-20b", "groq", "GROQ_API_KEY"),
    ("qwen/qwen3.8-27b", "groq", "GROQ_API_KEY"),
)
RUNS = 5
SUITE = "examples/suite"
DEFAULT_REPO = "samarthputhraya/Modelpin"
#: Long enough for a confirmation replay on every scenario; a hung host must not hold the job.
CALL_TIMEOUT_S = 1800
#: How the canary launches modelpin: one call at a time. `[M] 2026-10-04` Groq's free tier
#: allows 8,000 tokens a minute per model (2,000 for gpt-oss-safeguard-20b), and four runs in
#: flight exhausted it on qwen/qwen3.8-27b past the SDK's five `retry-after` retries. Runs are
#: independent samples either way, so pacing changes the wall clock, not the measurement.
PACED = (
    "import modelpin.replay as r; r.DEFAULT_WORKERS = 1; " "from modelpin.cli import main; main()"
)

RESULT_WORDS = {0: "no change", 1: "change flagged", 3: "could not measure"}
COULD_NOT_RUN = "could not run"
_COUNT_WORDS = (
    ("regression", "changed"),
    ("changed_minor", "minor"),
    ("insufficient_evidence", "not measured"),
    ("unchanged", "no change"),
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def slug(model: str) -> str:
    """A file name for a model id: `openai/gpt-oss-120b` -> `openai-gpt-oss-120b`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")


def load_results(path: Path) -> dict[str, Any]:
    if path.exists():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("baselines", {})
        data.setdefault("runs", [])
        return data
    return {"schema": 1, "baselines": {}, "runs": []}


def _config(model: str, provider: str, suite: Path) -> str:
    # No judge: a daily judge pass on the same free tier would double the calls and could
    # exhaust a host's daily token cap. The page says meaning is not compared.
    return (
        f"models:\n  - {model}\n"
        f"scenarios_dir: {suite.as_posix()}\n"
        f"providers:\n  - {provider}\n"
        f"runs: {RUNS}\n"
    )


def _call(runner: Runner, argv: list[str]) -> int:
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"canary: modelpin {_verb(argv)}: {exc}", file=sys.stderr)
        return 4
    if proc.returncode not in (0, 1):
        tail = (proc.stdout or "")[-600:] + (proc.stderr or "")[-600:]
        print(f"canary: modelpin {_verb(argv)} exit {proc.returncode}\n{tail}", file=sys.stderr)
    return proc.returncode


def _verb(argv: Sequence[str]) -> str:
    return next((a for a in argv if a in ("baseline", "check")), "?")


def _newest_record(store: Path) -> Optional[dict[str, Any]]:
    records = sorted((store / "runs").glob("check-*.json"))
    if not records:
        return None
    try:
        record: dict[str, Any] = json.loads(records[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record


def _counts(record: Optional[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in (record or {}).get("results") or []:
        verdict = str(r.get("verdict", ""))
        out[verdict] = out.get(verdict, 0) + 1
    return out


def run_model(
    model: str,
    provider: str,
    *,
    site: Path,
    suite: Path,
    today: str,
    results: dict[str, Any],
    exe: Sequence[str],
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Baseline the model if the canary never has, check it against itself, keep the reports."""
    canary = site / "canary"
    store = canary / "store" / slug(model)
    store.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "date": today,
        "model": model,
        "provider": provider,
        "runs": RUNS,
        "suite": SUITE,
        "baseline_recorded_today": False,
        "counts": {},
        "report": None,
        "plain_report": None,
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "modelpin.yaml"
        cfg.write_text(_config(model, provider, suite), encoding="utf-8")
        common = ["--config", str(cfg), "--store-dir", str(store)]
        if not any(store.glob("baseline-*.json")):
            rc = _call(runner, [*exe, "baseline", *common])
            if rc != 0:
                # A partial recording would become the reference for every later day, with
                # the scenarios it missed silently uncompared. Drop it; tomorrow re-records.
                for partial in store.glob("baseline-*.json"):
                    partial.unlink()
                row.update(exit_code=rc, result=COULD_NOT_RUN, baseline_date=None)
                return row
            results["baselines"][model] = today
            row["baseline_recorded_today"] = True
        row["baseline_date"] = results["baselines"].get(model)
        rc = _call(runner, [*exe, "check", "--from", model, "--to", model, *common])
    row["exit_code"] = rc
    row["result"] = RESULT_WORDS.get(rc, COULD_NOT_RUN)
    record = _newest_record(store) if rc in RESULT_WORDS else None
    row["counts"] = _counts(record)
    if record and record.get("modelpin_version"):
        row["modelpin_version"] = record["modelpin_version"]
    reports = canary / "reports" / today
    for name, key, suffix in (
        ("last-report.md", "report", ""),
        ("migration-report.md", "plain_report", "-plain"),
    ):
        src = store / name
        if rc in RESULT_WORDS and src.exists():
            reports.mkdir(parents=True, exist_ok=True)
            dest = reports / f"{slug(model)}{suffix}.md"
            shutil.copyfile(src, dest)
            row[key] = dest.relative_to(site).as_posix()
    return row


def record_row(results: dict[str, Any], row: dict[str, Any]) -> None:
    """Append a row; a second run on the same day replaces that day's row for the model."""
    results["runs"] = [
        r for r in results["runs"] if (r.get("date"), r.get("model")) != (row["date"], row["model"])
    ]
    results["runs"].append(row)


def _scenario_summary(counts: dict[str, int]) -> str:
    parts = [f"{counts[k]} {word}" for k, word in _COUNT_WORDS if counts.get(k)]
    return ", ".join(parts) if parts else "-"


def _link(repo: str, path: Optional[str], label: str) -> str:
    if not path:
        return ""
    url = f"https://github.com/{repo}/blob/gh-pages/{path}"
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


_CSS = """
:root { --bg: #ffffff; --fg: #1d1d1f; --muted: #5f6368; --line: #dadce0; --flag: #b3261e;
  --ok: #1e6b35; --warn: #8a5300; }
@media (prefers-color-scheme: dark) { :root { --bg: #131314; --fg: #e8eaed; --muted: #9aa0a6;
  --line: #3c4043; --flag: #f28b82; --ok: #81c995; --warn: #fdd663; } }
body { background: var(--bg); color: var(--fg); margin: 0;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 8px; }
p { max-width: 70ch; }
.muted { color: var(--muted); }
.table { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin-top: 16px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th, td:nth-child(1), td:nth-child(2), td:nth-child(4) { white-space: nowrap; }
th { font-weight: 600; }
code { font-size: 0.92em; }
.r-no-change { color: var(--ok); }
.r-change-flagged { color: var(--flag); font-weight: 600; }
.r-could-not-measure, .r-could-not-run { color: var(--warn); }
a { color: inherit; }
"""


def render_page(results: dict[str, Any], *, repo: str, generated: str) -> str:
    """The results page: newest day first, models in the canary's order (pure)."""
    order = {m: i for i, (m, _, _) in enumerate(MODELS)}
    rows = sorted(
        results.get("runs", []),
        key=lambda r: (r.get("date", ""), -order.get(r.get("model", ""), len(order))),
        reverse=True,
    )
    body: list[str] = []
    for r in rows:
        result = str(r.get("result", COULD_NOT_RUN))
        css = "r-" + re.sub(r"[^a-z]+", "-", result.lower()).strip("-")
        note = " (baseline recorded this run)" if r.get("baseline_recorded_today") else ""
        links = " &middot; ".join(
            x
            for x in (
                _link(repo, r.get("report"), "report"),
                _link(repo, r.get("plain_report"), "plain English"),
            )
            if x
        )
        body.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('date', '')))}</td>"
            f"<td><code>{html.escape(str(r.get('model', '')))}</code></td>"
            f"<td>{html.escape(str(r.get('provider', '')))}</td>"
            f"<td>{html.escape(str(r.get('baseline_date') or '-'))}</td>"
            f'<td class="{css}">{html.escape(result)}{html.escape(note)}</td>'
            f"<td>{html.escape(_scenario_summary(r.get('counts') or {}))}</td>"
            f"<td>{html.escape(str(r.get('modelpin_version') or '-'))}</td>"
            f"<td>{links or '-'}</td>"
            "</tr>"
        )
    if not body:
        body.append('<tr><td colspan="8" class="muted">No runs yet.</td></tr>')
    repo_url = html.escape(f"https://github.com/{repo}", quote=True)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Modelpin daily canary</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body><main>",
            "<h1>Modelpin daily canary</h1>",
            "<p>Every day, each model below is replayed on Modelpin's open example suite "
            f"(<code>{SUITE}</code>, every scenario {RUNS} times) and compared with a recording "
            "of the <em>same model</em> made on the baseline date shown. A flag means the model "
            "behaved differently from its own earlier recording: either its host changed what "
            "the model does under the same name, or Modelpin raised a false alarm. Both are "
            "published here.</p>",
            "<p>This page measures change, not quality, and ranks no model. Compared: tool "
            "calls, refusals and the suite's text checks. Not compared: meaning, because no "
            "judge model runs here, so an answer that says something different in the same "
            "shape would not be flagged. Free-tier hosts; the reports carry every setting.</p>",
            '<div class="table"><table>',
            "<thead><tr><th>Date (UTC)</th><th>Model</th><th>Host</th><th>Baseline</th>"
            "<th>Result</th><th>Scenarios</th><th>Modelpin</th><th>Reports</th></tr></thead>",
            "<tbody>",
            *body,
            "</tbody></table></div>",
            f'<p class="muted">Generated {html.escape(generated)} by '
            f'<code>scripts/canary.py</code> in <a href="{repo_url}">{html.escape(repo)}</a>. '
            'Raw data: <a href="results.json">results.json</a>.</p>',
            "</main></body></html>",
            "",
        ]
    )


_ROOT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=canary/">
<title>Modelpin</title></head>
<body><p><a href="canary/">Modelpin daily canary</a></p></body></html>
"""


def write_page(site: Path, results: dict[str, Any], *, repo: str, generated: str) -> None:
    canary = site / "canary"
    canary.mkdir(parents=True, exist_ok=True)
    (canary / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (canary / "index.html").write_text(
        render_page(results, repo=repo, generated=generated), encoding="utf-8"
    )
    (site / ".nojekyll").write_text("", encoding="utf-8")
    if not (site / "index.html").exists():
        (site / "index.html").write_text(_ROOT_PAGE, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--site", required=True, help="checkout of the GitHub Pages branch")
    ap.add_argument("--suite", default=SUITE, help="scenarios directory to replay")
    ap.add_argument("--exe", default=None, help="how to invoke modelpin (shell words)")
    ap.add_argument("--date", default=None, help="override today's UTC date (YYYY-MM-DD)")
    ap.add_argument("--render-only", action="store_true", help="rebuild the page, run nothing")
    args = ap.parse_args(argv)

    site = Path(args.site)
    now = datetime.now(timezone.utc)
    today = args.date or now.date().isoformat()
    repo = os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    results = load_results(site / "canary" / "results.json")
    measured = 0
    if not args.render_only:
        suite = Path(args.suite).resolve()
        if not suite.is_dir():
            print(f"canary: no scenarios directory at {suite}", file=sys.stderr)
            return 2
        exe = shlex.split(args.exe) if args.exe else [sys.executable, "-c", PACED]
        for model, provider, key_env in MODELS:
            if not os.environ.get(key_env):
                print(f"canary: {model} skipped, {key_env} is not set", file=sys.stderr)
                continue
            row = run_model(
                model,
                provider,
                site=site,
                suite=suite,
                today=today,
                results=results,
                exe=exe,
                runner=runner,
            )
            record_row(results, row)
            # By the published word, not the code: a failed baseline also exits 3.
            measured += row["result"] != COULD_NOT_RUN
            print(f"canary: {model}: {row['result']} (exit {row['exit_code']})")
    write_page(site, results, repo=repo, generated=now.strftime("%Y-%m-%d %H:%M UTC"))
    if args.render_only:
        return 0
    return 0 if measured else 1


if __name__ == "__main__":
    sys.exit(main())
