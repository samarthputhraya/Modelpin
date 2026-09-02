"""Every `check` run must leave an artifact that the NEXT run cannot delete (MP-150).

`[M] 2026-08-31` `cli.py` wrote exactly one path, `<store>/last-report.md`, on every run.
Two consequences, both measured:

  * Run a check, get a regression, run it again after a fix -- the evidence of the first run
    is gone. Nothing survives to be cited, quoted in an issue, or diffed against.
  * The project's own dogfood runs (`ops/launch/dogfood-kavach.md`,
    `dogfood-aegis.md`) had to be transcribed BY HAND into `ops/` because the file they came
    from had already been overwritten by the next run.

`last-report.md` itself must not move: `action.yml:135` publishes that exact path as an
output, both CI workflows glob for it, and the README documents it. So the stable path stays
and an ARCHIVE is written beside it -- one file per run, named for the models and the UTC
instant, which is the thing a Report can actually cite.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from typer.testing import CliRunner

import modelpin.cli as cli
from modelpin.demo import DEMO_DIRNAME, DEMO_FIXTURES, DEMO_FROM, DEMO_TO, write_demo

_ROOT = Path(tempfile.mkdtemp(prefix="modelpin-archive-"))
write_demo(_ROOT)
_DEMO = _ROOT / DEMO_DIRNAME
FIXTURES = str(_DEMO / DEMO_FIXTURES)
SCEN = str(_DEMO / "scenarios")
CONFIG = str(_DEMO / "modelpin.yaml")

runner = CliRunner()


def _common(store: str) -> list[str]:
    return [
        "--provider", "fake", "--fixtures", FIXTURES, "--scenarios-dir", SCEN,
        "--config", CONFIG, "--store-dir", store, "--runs", "5",
    ]  # fmt: skip


def _baseline(tmp_path) -> str:
    store = str(tmp_path / ".modelpin")
    r = runner.invoke(cli.app, ["baseline", "--model", DEMO_FROM, *_common(store)])
    assert r.exit_code == 0, r.output
    return store


def _check(store: str, to: str = DEMO_TO):
    return runner.invoke(cli.app, ["check", "--to", to, "--from", DEMO_FROM, *_common(store)])


def _archives(store: str) -> list[Path]:
    return sorted((Path(store) / cli.REPORT_ARCHIVE_DIRNAME).glob("*.md"))


def test_a_run_writes_an_archived_report_beside_the_stable_one(tmp_path):
    store = _baseline(tmp_path)
    _check(store)
    assert (Path(store) / "last-report.md").exists(), "the documented path must not move"
    assert len(_archives(store)) == 1


def test_a_second_run_does_not_delete_the_first(tmp_path):
    """The defect, stated as an assertion."""
    store = _baseline(tmp_path)
    _check(store)
    first = _archives(store)[0]
    first_text = first.read_text(encoding="utf-8")

    _check(store, to=DEMO_FROM)  # a DIFFERENT comparison, same store
    assert first.exists(), "the first run's artifact was overwritten"
    assert first.read_text(encoding="utf-8") == first_text
    assert len(_archives(store)) == 2


def test_the_archive_name_carries_both_models_and_the_utc_instant(tmp_path):
    """A filename that cannot be read back to a run is not a citation."""
    store = _baseline(tmp_path)
    _check(store)
    name = _archives(store)[0].name
    assert DEMO_FROM in name and DEMO_TO in name, name
    assert re.search(r"\d{8}T\d{6}Z", name), name


def test_two_runs_in_the_same_second_both_survive(tmp_path):
    """Timestamps are one second wide and CI runs are fast. A collision must disambiguate,
    never clobber -- which is the whole point of the row."""
    store = _baseline(tmp_path)
    stamp = "20260831T101500Z"
    cli._archive_path(Path(store), DEMO_FROM, DEMO_TO, stamp).write_text("first", "utf-8")
    second = cli._archive_path(Path(store), DEMO_FROM, DEMO_TO, stamp)
    second.write_text("second", encoding="utf-8")
    assert second.name != f"{stamp}.md"
    assert len(_archives(store)) == 2


def test_the_archive_is_byte_identical_to_the_stable_path(tmp_path):
    """One render, two destinations. If they could differ, a reader would have to know which
    one the Action posted -- and the archive would stop being evidence of that run."""
    store = _baseline(tmp_path)
    _check(store)
    stable = (Path(store) / "last-report.md").read_text(encoding="utf-8")
    assert _archives(store)[0].read_text(encoding="utf-8") == stable


def test_both_paths_are_printed(tmp_path):
    """An artifact the user cannot find is not an artifact.

    `[M] 2026-09-02` Compared with ALL whitespace stripped, because the raw substring form of
    this test passed on Windows and FAILED on both Linux CI jobs -- blocking two PRs. Rich
    wraps at the console width and will break a path mid-token; whether `last-report.md`
    survives intact depends only on how long the temp directory happens to be, so the
    assertion was measuring the terminal rather than the product. CI's
    `/tmp/pytest-of-runner/...` broke it as `last-repor` + `t.md`; the local
    `C:/Users/.../Temp/...` happened to break one character earlier and passed.
    """
    store = _baseline(tmp_path)
    r = _check(store)
    printed = "".join(r.output.split())
    assert "last-report.md" in printed, r.output
    assert cli.REPORT_ARCHIVE_DIRNAME in printed, r.output


def test_a_failed_archive_write_never_costs_the_stable_report(tmp_path, monkeypatch):
    """LOAD-BEARING ordering. `action.yml` publishes `last-report.md`; the archive is a
    convenience. If only one can be written, it must be the one CI reads."""
    store = _baseline(tmp_path)
    real = Path.write_text

    def explode(self, *a, **kw):
        if cli.REPORT_ARCHIVE_DIRNAME in str(self):
            raise OSError("disk full")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", explode)
    r = _check(store)
    assert r.exit_code == 1, r.output  # the regression verdict still stands
    assert (Path(store) / "last-report.md").exists()
    assert "warning" in r.output.lower()
