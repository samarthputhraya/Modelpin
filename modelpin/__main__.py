"""``python -m modelpin`` -- the same CLI as the ``modelpin`` and ``mp`` console scripts.

Why this file exists, `[M] 2026-09-16`, first-run walk-through on Windows 11: the console
script is a generated ``.exe`` in the venv's ``Scripts\\``, and Windows Application Control
refused to run a freshly written one. ``python -m modelpin`` is the standard way out of
that, and out of every other reason ``Scripts\\`` is not on ``PATH`` -- a venv that was
never activated, a container that calls the interpreter by absolute path, a CI step that
installed into ``--user``. It answered

    No module named modelpin.__main__; 'modelpin' is a package and cannot be directly executed

which reads like the package is broken.

It is also the one invocation that cannot be shadowed: PowerShell's built-in ``mp`` alias
(``Move-ItemProperty``) wins over ours, which the README already has to warn about.

MP-336. ``python -m`` puts the current directory first on ``sys.path``, and Modelpin is run
inside the user's repository, so a module there named like one of our dependencies (a
``yaml.py``, a ``rich/``) would be imported in its place. The console scripts never had that
entry; this drops it before anything else is imported, so ``python -m modelpin`` imports what
``modelpin`` does. The ``modelpin`` package itself is already loaded by then, which keeps
``python -m modelpin`` from a source checkout working.
"""

from __future__ import annotations

import os
import sys


def _is_cwd(entry: str) -> bool:
    return entry == "" or os.path.normcase(os.path.abspath(entry)) == os.path.normcase(os.getcwd())


if sys.path and _is_cwd(sys.path[0]):
    del sys.path[0]

from modelpin.cli import app  # noqa: E402  (must follow the sys.path change above)

if __name__ == "__main__":
    app()
