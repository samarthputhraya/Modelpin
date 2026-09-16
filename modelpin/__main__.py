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
"""

from __future__ import annotations

from modelpin.cli import app

if __name__ == "__main__":
    app()
