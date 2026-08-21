import json
from pathlib import Path

from modelpin.watcher import deprecations, get_model, load_registry


def test_registry_includes_current_anthropic_ids():
    ids = {m.id for m in load_registry()}
    assert {"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"} <= ids


def test_get_model_hit_and_miss():
    assert get_model("claude-opus-4-8") is not None
    assert get_model("totally-made-up-model") is None


def test_deprecations_filters_to_deprecated_and_retired(tmp_path):
    p = tmp_path / "models.json"
    p.write_text(
        json.dumps(
            {
                "models": [
                    {"id": "a", "provider": "x", "status": "active"},
                    {"id": "b", "provider": "x", "status": "deprecated"},
                    {"id": "c", "provider": "x", "status": "retired"},
                ]
            }
        )
    )
    reg = load_registry(p)
    assert {m.id for m in deprecations(reg)} == {"b", "c"}


def test_seed_matches_repo_json():
    """The embedded seed and the repo's data/models.json must never drift.

    The wheel is code only (ADR-0011), so the registry that ships is SEED_MODELS. But
    community PRs are far more comfortable editing JSON than a Python literal, so
    data/models.json stays in the repo as the contributor-facing surface. This equality is
    the only thing keeping the file people edit and the data that ships in step.
    """
    from modelpin.watcher.registry import SEED_MODELS

    root = Path(__file__).resolve().parents[1]
    on_disk = json.loads((root / "data" / "models.json").read_text(encoding="utf-8"))["models"]
    assert SEED_MODELS == on_disk, (
        "modelpin/watcher/registry.py::SEED_MODELS has drifted from data/models.json. "
        "Edit the JSON, then mirror it into the Python literal (or vice versa)."
    )


def test_registry_needs_no_files_on_disk(tmp_path, monkeypatch):
    """load_registry() must not depend on the caller's cwd.

    The previous implementation searched `Path.cwd()/"data"/"models.json"`, so an unrelated
    file in a user's working directory could redefine which models Modelpin believes exist.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "models.json").write_text(
        json.dumps({"models": [{"id": "impostor", "provider": "x", "status": "active"}]}),
        encoding="utf-8",
    )
    ids = {m.id for m in load_registry()}
    assert "impostor" not in ids, "cwd hijacked the registry"
    assert "claude-opus-4-8" in ids
