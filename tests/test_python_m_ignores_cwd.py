"""`python -m modelpin` imports what `modelpin` does, whatever sits in the current directory.

MP-336. `python -m` puts the current directory first on `sys.path`, and Modelpin runs inside the
user's repository, so a `yaml.py` there was imported in place of PyYAML. The console scripts
never had that entry. README recommends `python -m modelpin` when the generated executable is
blocked, so the two must behave the same.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Only what the interpreter needs; never the caller's keys.
_ENV = {
    k: v
    for k, v in os.environ.items()
    if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"}
}
#: The tree this test belongs to, so the subprocess runs this checkout's `modelpin`.
_REPO = Path(__file__).resolve().parents[1]


def test_a_module_in_the_current_directory_is_not_imported(tmp_path: Path) -> None:
    (tmp_path / "yaml.py").write_text("raise SystemExit('SHADOWED')\n", encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-m", "modelpin", "version"],
        cwd=tmp_path,
        env={**_ENV, "PYTHONPATH": str(_REPO)},
        capture_output=True,
        text=True,
    )
    assert "SHADOWED" not in res.stdout + res.stderr, res.stderr
    assert res.returncode == 0 and "modelpin" in res.stdout.lower(), res.stdout + res.stderr
