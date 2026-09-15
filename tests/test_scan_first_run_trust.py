"""`modelpin scan` must be trustworthy for a stranger: no silent wrong answer, no invented ids.

`[M] 2026-09-15` A first-run audit ran `scan` the way a new user does and found, verbatim:

    $ modelpin scan does-not-exist-dir
    No model identifiers found.                          EXIT=0

    src/bundle.min.js:  var o1=1,o3=3        ->  reported models `o1` and `o3`
    docs prose:  gpt-4.1-mini-adr0040.jsonl  ->  reported model `gpt-4.1-mini-adr0040.jsonl`
    docs prose:  gemini-2.5-flash.txt        ->  reported model `gemini-2.5-flash.txt`
    changelog:   gemini-3.x                  ->  reported model `gemini-3.x`

and, the blind half, `claude-haiku-4-5@20251001` (Vertex) and `us.anthropic.claude-...-v1:0`
(Bedrock) reported as truncated ids, and OpenRouter's `anthropic/claude-sonnet-4.5` without the
vendor prefix that `--to` needs. A typo'd path answering "nothing found" is the worst of these:
it is indistinguishable from a real clean result.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelpin.cli import EXIT_SETUP_FAILED, app
from modelpin.detector import models_used, scan_repo

runner = CliRunner()


def _flat(text: str) -> str:
    """Collapse whitespace: rich wraps to a terminal width `CliRunner` does not share."""
    return " ".join(text.split())


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# --- 1. a path that is not there is an error, not an empty result ---------------------------


def test_a_missing_path_fails_naming_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist-dir"
    result = runner.invoke(app, ["scan", str(missing)])
    out = _flat(result.output)
    assert result.exit_code == EXIT_SETUP_FAILED, out
    assert "does-not-exist-dir" in out, out
    assert (
        "No model identifiers found" not in out
    ), f"a typo'd path must not read like a clean repo.\n\n{out}"


def test_scan_repo_raises_on_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_repo(tmp_path / "nope")


def test_a_single_file_can_be_scanned(tmp_path: Path) -> None:
    app_py = _write(tmp_path, "src/app.py", 'MODEL = "gpt-4o-mini"\n')
    hits = scan_repo(app_py)
    assert [(h["model"], h["file"], h["line"]) for h in hits] == [("gpt-4o-mini", "app.py", 1)]
    result = runner.invoke(app, ["scan", str(app_py)])
    assert result.exit_code == 0, result.output
    assert "gpt-4o-mini" in result.output


def test_a_named_file_is_read_whatever_its_extension(tmp_path: Path) -> None:
    """A walk skips `.txt` (logs, scraped dumps). A user who NAMES the file has chosen it."""
    notes = _write(tmp_path, "notes.txt", 'model = "claude-opus-4-1"\n')
    assert models_used(notes) == {"claude-opus-4-1"}


def test_a_named_secrets_env_file_is_refused(tmp_path: Path) -> None:
    env = _write(tmp_path, ".env", "OPENAI_API_KEY=sk-live\nMODEL=gpt-4o\n")
    result = runner.invoke(app, ["scan", str(env)])
    out = _flat(result.output)
    assert result.exit_code == EXIT_SETUP_FAILED, out
    assert ".env.example" in out, out


# --- 2. minifier names are not models --------------------------------------------------------


def test_a_minified_bundle_reports_no_models(tmp_path: Path) -> None:
    """The audit's reproduction, with a control that the real file is still read."""
    _write(tmp_path, "src/app.py", 'MODEL = "claude-sonnet-4-5"\n')
    _write(tmp_path, "src/bundle.min.js", "var o1=1,o3=3;function f(){return o1+o3}\n")
    assert models_used(tmp_path) == {"claude-sonnet-4-5"}


@pytest.mark.parametrize(
    "name", ["app.min.js", "vendor-min.js", "main.bundle.js", "package-lock.json", "pnpm-lock.yaml"]
)
def test_generated_files_are_not_read(tmp_path: Path, name: str) -> None:
    """`gpt-3-encoder` is an npm package, and a lockfile lists every package by name."""
    _write(tmp_path, name, '{"gpt-3-encoder": "1.1.4", "m": "o4-mini"}\n')
    assert models_used(tmp_path) == set()


@pytest.mark.parametrize("dirname", [".next", ".nuxt", ".svelte-kit", ".turbo", ".mypy_cache"])
def test_build_output_and_cache_directories_are_not_walked(tmp_path: Path, dirname: str) -> None:
    _write(tmp_path, f"{dirname}/chunks/1a2b.js", 'const m="gpt-4o-mini";\n')
    assert models_used(tmp_path) == set()


def test_an_o_series_name_as_a_bare_identifier_is_not_a_model(tmp_path: Path) -> None:
    """Not every bundle is named `.min.js`. `o4-e` is `o4 minus e` in minified code."""
    _write(tmp_path, "static/app.js", "var o1=a,o3=b;const x=o4-e;function g(o1){return o1}\n")
    assert models_used(tmp_path) == set()


@pytest.mark.parametrize(
    "rel, body, model",
    [
        ("app.py", 'client.responses.create(model="o3-mini")\n', "o3-mini"),
        ("app.js", "const MODELS = ['o1', 'o3'];\n", "o1"),
        (
            "app.ts",
            "await openai.chat.completions.create({ model: o4Mini ?? 'o4-mini' })\n",
            "o4-mini",
        ),
        (".env.example", "OPENAI_MODEL=o4-mini\n", "o4-mini"),
        ("cfg.yaml", "model: o3\n", "o3"),
        ("modelpin.yaml", "models:\n  - o3-mini\n", "o3-mini"),
        ("cfg.json", '{"model": "o1"}\n', "o1"),
        ("README.md", "We moved to `o3-mini` last month.\n", "o3-mini"),
        ("README.md", "Run `modelpin check --to o4-mini`.\n", "o4-mini"),
    ],
)
def test_an_o_series_id_in_model_context_is_still_found(
    tmp_path: Path, rel: str, body: str, model: str
) -> None:
    _write(tmp_path, rel, body)
    assert model in models_used(tmp_path)


# --- 3. a filename or path is not a dependency -----------------------------------------------


def test_a_model_id_used_as_a_filename_is_not_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/notes.md",
        "Raw traces are in gpt-4.1-mini-adr0040.jsonl, and the judge output in "
        "gemini-2.5-flash.txt; see also results/gpt-4o-mini.csv and runs/gpt-4o/summary.\n",
    )
    assert models_used(tmp_path) == set()


def test_a_path_separator_after_an_id_is_a_path_on_windows_too(tmp_path: Path) -> None:
    _write(tmp_path, "tool.py", 'OUT = r"runs\\claude-opus-4-6\\trace.json"\n')
    assert models_used(tmp_path) == set()


def test_a_string_escape_after_an_id_is_not_a_path(tmp_path: Path) -> None:
    """`[M] 2026-09-15` The first cut treated any trailing backslash as a separator and dropped
    `"gpt-6-astra-its-latest-ai-model\\n"` from an existing test fixture."""
    _write(tmp_path, "app.py", 'f.write("model: gpt-4o-mini\\n")\n')
    assert models_used(tmp_path) == {"gpt-4o-mini"}


def test_a_real_id_ending_a_sentence_or_vendor_prefixed_is_still_found(tmp_path: Path) -> None:
    """Controls: a full stop is not an extension, and `vendor/` BEFORE an id is not a path."""
    _write(
        tmp_path,
        "app.py",
        'PRIMARY = "claude-sonnet-4.5"\nROUTED = "openai/gpt-4o"\n# we used gemini-2.5-flash.\n',
    )
    assert models_used(tmp_path) == {"claude-sonnet-4.5", "openai/gpt-4o", "gemini-2.5-flash"}


# --- 4. a version wildcard is not an id ------------------------------------------------------


@pytest.mark.parametrize("prose", ["gemini-3.x", "gpt-5.x", "claude-4.X", "gemini-2.5-*", "gpt-4*"])
def test_a_wildcard_family_is_not_a_model(tmp_path: Path, prose: str) -> None:
    _write(tmp_path, "CHANGELOG.md", f"Support for the {prose} family.\n")
    assert models_used(tmp_path) == set()


def test_an_x_inside_a_real_id_is_not_a_wildcard(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'M = "mixtral-8x7b-32768"\n')
    assert models_used(tmp_path) == {"mixtral-8x7b-32768"}


# --- 5. current cross-vendor and hosted-platform ids -----------------------------------------

#: Each must be reported EXACTLY as written: the full string is what `--to` accepts, and a
#: truncated `claude-haiku-4-5` for a Vertex id is a different model string on that platform.
FULL_IDS = [
    "claude-opus-4-1",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5@20251001",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.5-flash",
    "gemini-2.5-pro",
    "gpt-5",
    "gpt-4.1-mini",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "mistral-large-latest",
    "codestral-latest",
    "deepseek-chat",
    "deepseek-r1",
    "deepseek-ai/DeepSeek-R1",
    "moonshotai/kimi-k2-instruct",
    "x-ai/grok-4",
    "grok-3-mini",
    "gemma2-9b-it",
    "llama3.2",
]


@pytest.mark.parametrize("model_id", FULL_IDS)
def test_a_current_model_id_is_reported_in_full(tmp_path: Path, model_id: str) -> None:
    _write(tmp_path, "app.py", f'MODEL = "{model_id}"\n')
    assert models_used(tmp_path) == {model_id}


#: Words and paths that share a stem with a model family but are not models.
NOT_MODELS = [
    "llama",
    "ollama",
    "mistral",
    "qwen",
    "kimi",
    "grok",
    "gemma",
    "llama/qwen/mistral",
    "google/protobuf",
    "openai/openai-python",
    "anthropic/anthropic-sdk-python",
]


@pytest.mark.parametrize("token", NOT_MODELS)
def test_a_family_word_or_org_path_is_not_a_model(tmp_path: Path, token: str) -> None:
    _write(tmp_path, "app.py", f'X = "{token}"\n')
    assert models_used(tmp_path) == set(), f"scan invented a model from {token!r}"


# --- 6. env templates are config; real env files are never read ------------------------------


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_an_env_template_is_read_as_config(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name, "# pick one\nMODEL=gpt-4o\n")
    hits = scan_repo(tmp_path)
    assert [(h["model"], h["context"]) for h in hits] == [("gpt-4o", "code")], hits


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", "prod.env"])
def test_a_real_env_file_is_never_read(tmp_path: Path, name: str) -> None:
    """These hold live secrets. `scan` reports only ids, but it has no business opening them."""
    _write(tmp_path, name, "OPENAI_API_KEY=sk-live\nMODEL=gpt-4o\n")
    scanned: list[str] = []
    assert scan_repo(tmp_path, scanned=scanned) == []
    assert scanned == [], scanned


# --- 7. "nothing found" says how much was looked at -------------------------------------------


def test_no_hits_says_how_many_files_were_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "print('hello')\n")
    _write(tmp_path, "README.md", "# hello\n")
    result = runner.invoke(app, ["scan", str(tmp_path)])
    out = _flat(result.output)
    assert result.exit_code == 0, out
    assert "No model identifiers found" in out and "2 files" in out, out


def test_scanning_nothing_is_distinguishable_from_finding_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "main.zig", 'const model = "gpt-4o"\n')
    result = runner.invoke(app, ["scan", str(tmp_path)])
    out = _flat(result.output)
    assert result.exit_code == 0, out
    assert "0 files" in out, out


def test_scan_repo_reports_what_it_read(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "b.txt", "not read\n")
    scanned: list[str] = []
    scan_repo(tmp_path, scanned=scanned)
    assert scanned == ["a.py"]


@pytest.mark.parametrize("name", ["app/page.tsx", "src/App.jsx", "server.mjs", "api.cjs", "main.go"])
def test_javascript_family_and_server_languages_are_scanned(tmp_path: Path, name: str) -> None:
    """A Next.js or React app keeps its model calls in `.tsx`/`.jsx`/`.mjs`; they were never read."""
    _write(tmp_path, name, 'const model = "gpt-4o"; // or gpt-5.5 later\n')
    hits = scan_repo(tmp_path)
    assert {(h["model"], h["context"]) for h in hits} == {("gpt-4o", "code"), ("gpt-5.5", "comment")}


def test_the_judge_modelpin_init_writes_is_not_reported_as_an_app_dependency(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", 'MODEL = "gpt-4o"\n')
    _write(tmp_path, "modelpin.yaml", "models:\n  - gpt-4o\njudge_model: gpt-4.1-mini\n")
    assert {h["model"] for h in scan_repo(tmp_path)} == {"gpt-4o"}
