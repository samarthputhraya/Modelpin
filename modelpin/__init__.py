"""Modelpin — Dependabot for AI models. Know before the model breaks you."""

from importlib.metadata import PackageNotFoundError, version as _dist_version

#: MP-204. Read from the INSTALLED distribution, so `pyproject.toml` is the single source.
#:
#: `[M] 2026-09-07` This was a hardcoded literal and it drifted the moment the version was
#: bumped: `pyproject.toml` and the installed dist metadata said `0.3.0` while
#: `modelpin version` — the command a user runs to tell us what they have, and the string a
#: bug report quotes — printed `0.2.1`. A packaging audit hours earlier had reported all
#: sources agreeing, which was true only because they were all stale together.
#:
#: This is the MP-03 shape: one number with more than one copy. The project has already paid
#: for that with the scaffolded `runs:` default, and the fix there was the same — bind the
#: copies to one origin rather than remember to update both.
#:
#: The literal fallback is for a source tree that was never installed (a plain `git clone` on
#: `sys.path`). It is deliberately NOT kept in sync by hand: `tests/test_packaging_contents.py`
#: asserts the resolved version equals `pyproject.toml`'s, and an uninstalled tree reports
#: `0+unknown` rather than a plausible wrong number — a version that is obviously not a
#: version is safer in a bug report than one that is quietly two releases behind.
try:
    __version__ = _dist_version("modelpin")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0+unknown"
