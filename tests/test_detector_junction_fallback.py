"""`_is_junction` answers the same on every Python the wheel admits.

MP-281. `os.path.isjunction` arrived in Python 3.12, and `requires-python` now admits 3.11. The
scan boundary (MP-241) uses it to refuse to follow an NTFS junction out of the repo, so on 3.11
the detector needs its own reading of the reparse tag -- and that reading must agree with the
standard library's wherever both exist.

These tests never skip. `tests/test_recall_arm.py::test_the_readmes_test_count_is_the_real_one`
pins passed + xfailed against collected, so a platform skip here would turn a published number
into one that depends on which machine ran the suite. Each test builds whatever link the
platform allows without privilege -- a junction on Windows, a symlink elsewhere -- exactly as
`tests/test_scan_boundary.py` does, and asserts the answer both with and without the 3.12 helper.
"""

from __future__ import annotations

import os
from pathlib import Path

from modelpin.detector import _is_junction


def _link_dir(target: Path, link: Path) -> str:
    """Make `link` point at the directory `target`, unprivileged. Returns what was made."""
    try:
        import _winapi  # Windows only; CreateJunction needs no privilege

        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    except ImportError:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"


def _pretend_python_311(monkeypatch) -> None:
    # 3.11 has no `os.path.isjunction`; deleting it forces the fallback path on any interpreter.
    monkeypatch.delattr(os.path, "isjunction", raising=False)


def test_a_plain_directory_and_a_plain_file_are_not_junctions(tmp_path: Path, monkeypatch) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    assert _is_junction(d) is False
    assert _is_junction(f) is False
    _pretend_python_311(monkeypatch)
    assert _is_junction(d) is False
    assert _is_junction(f) is False


def test_a_missing_path_is_not_a_junction_and_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "does-not-exist"
    assert _is_junction(missing) is False
    _pretend_python_311(monkeypatch)
    assert _is_junction(missing) is False


def test_the_fallback_agrees_with_the_stdlib_on_a_real_link(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    kind = _link_dir(target, link)
    # What 3.12+ says, or -- where the helper is absent -- what the platform built.
    stdlib = getattr(os.path, "isjunction", None)
    expected = bool(stdlib(link)) if stdlib is not None else kind == "junction"
    # A junction is a junction; a symlink is not. The expectation must carry that fact.
    assert expected is (kind == "junction")
    assert _is_junction(link) is expected
    _pretend_python_311(monkeypatch)
    assert _is_junction(link) is expected


def test_the_fallback_never_calls_lstat_off_windows(tmp_path: Path, monkeypatch) -> None:
    _pretend_python_311(monkeypatch)
    monkeypatch.setattr(os, "name", "posix")

    def _boom(*_args, **_kwargs):  # pragma: no cover - the assertion is that it is never reached
        raise AssertionError("lstat was called off Windows, where no junction can exist")

    monkeypatch.setattr(os, "lstat", _boom)
    assert _is_junction(tmp_path) is False


def test_the_fallback_reads_the_mount_point_reparse_tag(tmp_path: Path, monkeypatch) -> None:
    """Below 3.12 on Windows the answer is `lstat().st_reparse_tag == IO_REPARSE_TAG_MOUNT_POINT`.

    Driven through a stand-in `lstat` so the rule is pinned on every platform, not only where a
    junction can be created.
    """
    _pretend_python_311(monkeypatch)
    monkeypatch.setattr(os, "name", "nt")

    class _Stat:
        def __init__(self, tag: int) -> None:
            self.st_reparse_tag = tag

    mount_point = 0xA0000003  # stat.IO_REPARSE_TAG_MOUNT_POINT, the value CPython uses
    symlink_tag = 0xA000000C  # IO_REPARSE_TAG_SYMLINK: a reparse point that is NOT a junction
    monkeypatch.setattr(os, "lstat", lambda _p: _Stat(mount_point))
    assert _is_junction(tmp_path) is True
    monkeypatch.setattr(os, "lstat", lambda _p: _Stat(symlink_tag))
    assert _is_junction(tmp_path) is False
    monkeypatch.setattr(os, "lstat", lambda _p: _Stat(0))
    assert _is_junction(tmp_path) is False
