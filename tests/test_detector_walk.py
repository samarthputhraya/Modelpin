"""MP-134 -- `mp scan` walked virtualenvs it did not recognise, and blanked on some roots.

`[M] 2026-08-29`, found by dogfooding Modelpin on a real third-party app from a PyPI
install: `modelpin scan c:/dev/kavach` reported **23 distinct model(s)** and only **2** were
the user's own code. The rest were Modelpin's own source inside `site-packages` plus pip's
vendored `rich`. Cause: `SKIP_DIRS` matched by EXACT directory name, so a venv called
`.venv-modelpin` -- or `venv312`, `env`, `.virtualenvs` -- was walked in full, and
`site-packages` was not in the set at all.

Fixed structurally, per the row: a virtualenv is detected by the `pyvenv.cfg` the
interpreter itself writes (PEP 405), not by guessing names, because names are user-chosen
and unbounded.

`[M] 2026-08-31` AND A SECOND DEFECT IN THE SAME WALK, found while reproducing the first
and NOT in the row: the skip test read `any(part in SKIP_DIRS for part in p.parts)` over the
FULL path, which includes the scan root's own ANCESTORS. So a project that merely LIVES
under a directory named `build` -- or `dist`, `venv`, `.git`, `node_modules`, `__pycache__`
-- matched on the ancestor and skipped every file: a tree containing one plain
`MODEL = "gpt-4o-mini"` scanned to 0 hits, silently, exit 0. That is the "measured nothing,
reported nothing" shape this project has six ADRs about, in the FIRST command a stranger
runs. Walking downward from the root cannot express it: only directories at or below the
root are ever considered.
"""

from __future__ import annotations

import pytest

from modelpin.detector import _is_virtualenv, scan_repo

_MODEL_LINE = 'MODEL = "gpt-4o-mini"\n'
_VENV_LINE = 'CFG = "gpt-4.1"\nX = "claude-3-opus"\n'


def _project(tmp_path, venv_name):
    """A project with one real model id, plus a virtualenv holding two decoys."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text(_MODEL_LINE, encoding="utf-8")
    venv = tmp_path / venv_name / "Lib" / "site-packages" / "rich"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "_x.py").write_text(_VENV_LINE, encoding="utf-8")
    (tmp_path / venv_name / "pyvenv.cfg").write_text(
        "home = C:\\Python312\nversion = 3.12.0\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize(
    "venv_name", [".venv", "venv", ".venv-modelpin", "venv312", "env", ".virtualenvs"]
)
def test_a_virtualenv_is_skipped_whatever_it_is_called(tmp_path, venv_name):
    """`[M]` The row forbids the tempting fix -- adding names to `SKIP_DIRS` -- and this is
    why: the last four of these were walked in full before, and no enumeration closes the
    set. `env` in particular is a name a Django/Rails-style app may use for something else,
    so the test is `pyvenv.cfg`, not the name."""
    root = _project(tmp_path, venv_name)
    hits = scan_repo(str(root))
    assert [h["model"] for h in hits] == ["gpt-4o-mini"], hits


def test_site_packages_is_skipped_even_outside_a_virtualenv(tmp_path):
    """`[M]` It was not in `SKIP_DIRS` at all, and on the dogfood it carried most of the
    false answer. A dependency's source is not the user's model choice."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_MODEL_LINE, encoding="utf-8")
    vendored = tmp_path / "lib" / "site-packages" / "dep"
    vendored.mkdir(parents=True)
    (vendored / "d.py").write_text(_VENV_LINE, encoding="utf-8")
    assert [h["model"] for h in scan_repo(str(tmp_path))] == ["gpt-4o-mini"]


def test_a_directory_without_pyvenv_cfg_is_still_scanned(tmp_path):
    """Anti-over-fire: `pyvenv.cfg` is the test, so an ordinary directory that merely looks
    venv-adjacent must not be skipped. Losing a real hit is the worse direction here --
    `scan` exists to FIND the user's models."""
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "prod.py").write_text(_MODEL_LINE, encoding="utf-8")
    assert [h["model"] for h in scan_repo(str(tmp_path))] == ["gpt-4o-mini"]


@pytest.mark.parametrize(
    "ancestor", ["build", "dist", "venv", ".git", "node_modules", "__pycache__"]
)
def test_a_project_under_a_skipped_ancestor_name_still_scans(tmp_path, ancestor):
    """`[M] 2026-08-31` The second defect: every one of these blanked the scan silently when
    it appeared ANYWHERE above the scan root -- `C:/dev/build/myapp` returned 0 hits for a
    file plainly containing `gpt-4o-mini`. The user is not scanning the ancestor; they named
    the root, and what is above it is none of the walk's business."""
    root = tmp_path / ancestor / "myapp"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(_MODEL_LINE, encoding="utf-8")
    hits = scan_repo(str(root))
    assert [h["model"] for h in hits] == ["gpt-4o-mini"], f"blanked under {ancestor!r}: {hits}"


def test_those_names_are_still_skipped_BELOW_the_root(tmp_path):
    """The ancestor fix must not weaken the rule where it was meant to apply."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_MODEL_LINE, encoding="utf-8")
    for name in ("build", "node_modules", "__pycache__"):
        d = tmp_path / name
        d.mkdir()
        (d / "junk.py").write_text(_VENV_LINE, encoding="utf-8")
    assert [h["model"] for h in scan_repo(str(tmp_path))] == ["gpt-4o-mini"]


def test_is_virtualenv_keys_on_the_file_the_interpreter_writes(tmp_path):
    d = tmp_path / "anything"
    d.mkdir()
    assert _is_virtualenv(d) is False
    (d / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    assert _is_virtualenv(d) is True


def test_the_walk_does_not_descend_into_a_pruned_tree(tmp_path):
    """Pruning, not filtering: a venv holding thousands of files must cost nothing. `[M]`
    Scanning this repo (whose own `.venv` holds 6,000+ files) takes 0.28s."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_MODEL_LINE, encoding="utf-8")
    venv = tmp_path / ".venv-big"
    (venv / "Lib" / "site-packages").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    for i in range(200):
        (venv / "Lib" / "site-packages" / f"m{i}.py").write_text(_VENV_LINE, encoding="utf-8")
    assert [h["model"] for h in scan_repo(str(tmp_path))] == ["gpt-4o-mini"]
