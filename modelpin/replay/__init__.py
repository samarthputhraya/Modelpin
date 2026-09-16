"""Replay — run a Scenario on a model N times via an adapter. See spec section 4.4."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from modelpin.models import Scenario, Trace
from modelpin.providers.base import ProviderAdapter

#: Runs of one scenario sent to a live provider at the same time.
#:
#: `[M] 2026-09-15` replays were strictly sequential, so a 12-scenario suite at `runs: 5` on
#: `gemini-2.5-pro` (22 s per call) spent ~22 minutes on ONE side of one check, and a stranger's
#: first CI job looked hung. The N runs of a scenario are independent samples by design -- the
#: statistics assume exactly that -- so sending them together changes no measurement, only the
#: wall clock. Bounded so a suite does not burst a provider's per-minute quota; the SDKs retry
#: a 429 either way.
DEFAULT_WORKERS = 5


def replay(
    scenario: Scenario,
    model_id: str,
    adapter: ProviderAdapter,
    runs: int = 3,
    workers: int | None = None,
) -> list[Trace]:
    """Return N Traces of the scenario on the model, in run order. N>1 is essential to handle
    model nondeterminism downstream (see diff/stats.py).

    Runs execute concurrently only when the adapter declares ``parallel_safe`` (the live
    providers, whose SDK clients are thread-safe). Anything else -- the offline fake, a test's
    scripted adapter -- keeps the sequential order it may depend on.
    """
    limit = DEFAULT_WORKERS if workers is None else workers
    if runs <= 1 or limit <= 1 or not getattr(adapter, "parallel_safe", False):
        return [adapter.run(scenario, model_id, run_idx=i) for i in range(runs)]
    # The FIRST run goes alone. A request the provider rejects (a 400 on an unsupported
    # parameter) then costs one call instead of five in flight. `[M] 2026-09-15` one live job
    # also failed on its first scenario with a client-side pydantic `ValidationError` inside
    # google-genai while five calls started at once; 60 further concurrent calls did not
    # reproduce it. `[A]` a cold-start race in the SDK's request models, which one warm-up
    # call would avoid -- falsified if the error recurs with this ordering in place.
    first = adapter.run(scenario, model_id, run_idx=0)
    with ThreadPoolExecutor(max_workers=min(limit, runs - 1)) as pool:
        futures = [pool.submit(adapter.run, scenario, model_id, i) for i in range(1, runs)]
        # `.result()` in submission order: traces come back in run order, and the first error
        # (a provider rejection) propagates exactly as it did from the sequential loop.
        return [first, *(f.result() for f in futures)]
