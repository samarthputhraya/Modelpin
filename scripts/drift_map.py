"""Modelpin Drift Map — replay the open public suite across several REAL model-migration
pairs and measure how often a migration changes app-relevant behavior.

Two purposes:
  1. Thesis experiment: do model migrations break behavior often enough to matter?
  2. Source data for a public "behavioral drift map" report (the distribution asset).

Per-model traces are cached (.modelpin/drift_cache.json) so a re-run, an added pair, or a
crash mid-run does not re-replay. Free-tier providers are rate-paced. BYO-key from the env.

    python scripts/drift_map.py --verify     # 1 call/model: validate ids + keys, then stop
    python scripts/drift_map.py              # full run (replay missing models, diff all pairs)
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

try:  # corporate-proxy: trust the OS cert store (verification stays ON)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - best effort
    pass

from modelpin.diff import diff_scenario
from modelpin.judge import build_judge
from modelpin.models import Trace
from modelpin.providers import get_adapter
from modelpin.providers.base import ProviderError
from modelpin.replay import replay
from modelpin.scenarios import load_scenarios

DEFAULT_SUITE = "examples/report-suite"

#: (model_id, provider, runs, per-call min interval seconds for free-tier RPM caps)
MODELS = [
    ("gpt-3.5-turbo", "openai", 5, 0.0),
    ("gpt-4o-mini", "openai", 5, 0.0),
    ("gpt-4o", "openai", 5, 0.0),
    ("gpt-4.1-mini", "openai", 5, 0.0),
    ("gpt-4.1", "openai", 5, 0.0),
    ("gemini-3.1-flash-lite", "google", 5, 5.0),  # ~15 RPM cap (free tier)
]

#: (from_model, to_model, label) — real migrations a developer actually faces.
PAIRS = [
    ("gpt-3.5-turbo", "gpt-4o-mini", "OpenAI cheap-tier migration (3.5-turbo -> 4o-mini)"),
    ("gpt-4o-mini", "gpt-4o", "OpenAI tier upgrade (4o-mini -> 4o)"),
    ("gpt-4o-mini", "gpt-4.1-mini", "OpenAI cheap-tier version bump (4o-mini -> 4.1-mini)"),
    ("gpt-4o", "gpt-4.1", "OpenAI version bump (4o -> 4.1)"),
    ("gpt-4o-mini", "gemini-3.1-flash-lite", "Cross-vendor migration (OpenAI -> Google)"),
]


class _Paced:
    """Wrap an adapter to enforce a minimum interval between calls (free-tier RPM caps)."""

    def __init__(self, adapter, min_interval: float) -> None:
        self._a = adapter
        self._min = min_interval
        self._last = 0.0

    def preflight(self):
        return self._a.preflight()

    def run(self, scenario, model_id, run_idx: int = 0):
        if self._min:
            dt = time.monotonic() - self._last
            if dt < self._min:
                time.sleep(self._min - dt)
            self._last = time.monotonic()
        return self._a.run(scenario, model_id, run_idx)


def _replay_resilient(scenario, model, adapter, runs, attempts=6):
    """Retry transient provider errors (incl. Google 503 'high demand') with backoff."""
    assert attempts >= 1, "attempts must be >= 1 (else there is no error to re-raise)"
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return replay(scenario, model, adapter, runs=runs)
        except ProviderError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))  # 5,10,15,20,25s
    raise last  # type: ignore[misc]


def _load_cache(path: Path) -> dict[str, dict[str, list[Trace]]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # A run killed mid-write can leave a truncated cache; don't crash the next run.
        print(f"WARNING: cache {path} unreadable ({exc}); starting fresh")
        return {}
    return {
        m: {sid: [Trace(**t) for t in traces] for sid, traces in scn.items()}
        for m, scn in raw.items()
    }


def _save_cache(path: Path, cache: dict[str, dict[str, list[Trace]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        m: {sid: [t.model_dump(mode="json") for t in traces] for sid, traces in scn.items()}
        for m, scn in cache.items()
    }
    # Write to a temp sibling then replace, so a crash mid-write can't corrupt the cache.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(path)


def _verify(scenarios) -> None:
    probe = scenarios[0]
    for model, provider, _runs, _interval in MODELS:
        try:
            adapter = get_adapter(provider)
            adapter.preflight()
            tr = replay(probe, model, adapter, runs=1)
            ok = bool(tr and tr[0].final_output is not None)
            print(f"{'OK  ' if ok else 'WARN'} {provider:<7} {model}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {provider:<7} {model}  -> {str(exc)[:90]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="1 call/model to validate ids, then stop")
    ap.add_argument("--refresh", action="store_true", help="re-replay even cached models")
    ap.add_argument("--suite-dir", default=DEFAULT_SUITE, help="scenario suite to run")
    ap.add_argument("--tag", default=None, help="cache/results filename tag (default: suite name)")
    ap.add_argument("--judge", default="gpt-4o-mini")
    args = ap.parse_args()

    tag = args.tag or Path(args.suite_dir).name
    cache_path = Path(f".modelpin/drift_cache_{tag}.json")
    results_path = Path(f".modelpin/drift_results_{tag}.json")

    scenarios = load_scenarios(args.suite_dir)
    print(f"suite={args.suite_dir} scenarios={len(scenarios)} tag={tag}\n")

    if args.verify:
        _verify(scenarios)
        return

    cache = _load_cache(cache_path)
    for model, provider, runs, interval in MODELS:
        if model in cache and not args.refresh:
            print(f"cached:    {model}")
            continue
        print(f"replaying: {model} ({provider}, runs={runs}, pace={interval}s) ...")
        try:
            adapter = _Paced(get_adapter(provider), interval)
            adapter.preflight()
            cache[model] = {s.id: _replay_resilient(s, model, adapter, runs) for s in scenarios}
            _save_cache(cache_path, cache)
            print(f"  done:    {model}")
        except ProviderError as exc:
            # One model's transient failure (e.g. Google 503) must not sink the whole map;
            # pairs that need it are skipped, the rest still diff + report.
            print(f"  SKIP {model}: {str(exc)[:90]}")

    judge = build_judge(args.judge)
    judge.preflight()
    print(f"\njudge={args.judge}\n")

    pairs_out = []
    for frm, to, label in PAIRS:
        if frm not in cache or to not in cache:
            print(f"SKIP {frm} -> {to} (missing traces)")
            continue
        per = []
        for s in scenarios:
            base, cand = cache[frm].get(s.id), cache[to].get(s.id)
            if not base or not cand:
                continue
            per.append(
                diff_scenario(s.id, frm, to, base, cand, s, "strict", judge=judge).model_dump(
                    mode="json"
                )
            )
        counts = Counter(r["verdict"] for r in per)
        pairs_out.append(
            {"from": frm, "to": to, "label": label, "counts": dict(counts), "results": per}
        )
        changed = [r for r in per if r["verdict"] != "unchanged"]
        print(f"\n### {label}\n  {frm} -> {to}: {dict(counts)}")
        for r in changed:
            print(f"    [{r['verdict']}] {r['scenario_id']}: {r['explanation'][:80]}")

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps({"suite": args.suite_dir, "judge": args.judge, "pairs": pairs_out}, indent=2),
        encoding="utf-8",
    )
    print(f"\nresults -> {results_path}")


if __name__ == "__main__":
    try:
        main()
    except ProviderError as exc:
        print(f"\nerror: {exc}")
        raise SystemExit(1)
