"""MP-195 — `mp scan` was OpenAI/Anthropic/Google-shaped, and cross-vendor is the wedge.

`[M] 2026-09-06`, reproduced with a control that makes it unambiguous. A directory holding one
`app.py` that names `llama-3.3-70b-versatile`, `qwen/qwen3-32b` and `openai/gpt-oss-20b`, plus a
`.env.example` naming `llama-3.1-8b-instant`:

    $ mp scan .
    No model identifiers found.        EXIT=0

Appending a single line ``M = "gpt-4o-mini"`` to **that same file** produced a populated table.
The file was visible or invisible depending only on whose ids it held.

Why this is more than a missing feature:

- `README.md:96` makes `modelpin scan` the FIRST command of *"The real flow, on your own app"*.
  A Groq or Together shop meets a confident empty result, not a hint that we do not cover them.
- `CLAUDE.md` names **cross-vendor** as wedge item 3, and README advertises `groq`, `openrouter`,
  `together` and `cerebras` as live providers -- using `llama-3.3-70b-versatile` and
  `qwen/qwen3.8-27b` as its own copy-pasteable examples. `scan` could not see the ids the
  README tells people to use.
- It is the exact twin of MP-134/MP-135, both `done`, which fixed scan's false POSITIVES. This
  is the false-NEGATIVE half of the same command.

The false-positive controls below are not optional politeness. `detector/__init__.py` states the
tradeoff in terms: *"a MISSED model in `scan` costs the user a line in a table they can add by
hand, while a FABRICATED one is the north-star failure showing up in the first command a
stranger runs."* Widening the patterns is the risky direction, so it is measured: over the same
6,228 third-party files MP-135 used, the only false positive these patterns produced was
`deepseek-ai` -- a HuggingFace org, not a model -- and the shipped `deepseek-` pattern
enumerates its real families to exclude it.
"""

from __future__ import annotations

import pytest

from modelpin.detector import models_used, scan_repo

# --- ids these patterns MUST find -------------------------------------------------------

#: Real, currently-servable ids. Each is here because something concrete points at it: the
#: README's own cross-vendor examples, the ids in the 9router registry reconciliation, and the
#: HuggingFace-cased form, which differs from the Groq-cased form of the same family.
REAL_IDS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/Llama-3.3-70B-Instruct",
    "qwen/qwen3-32b",
    "qwen/qwen3.8-27b",
    "qwen3-32b",
    "openai/gpt-oss-20b",
    "gpt-oss-120b",
    "mistral-large-latest",
    "mixtral-8x7b-32768",
    "deepseek-r1-0528",
    "deepseek-v3.1",
    "deepseek-chat",
]


@pytest.mark.parametrize("model_id", REAL_IDS)
def test_a_cross_vendor_id_is_found(tmp_path, model_id: str) -> None:
    (tmp_path / "app.py").write_text(f'MODEL = "{model_id}"\n', encoding="utf-8")
    found = models_used(tmp_path)
    assert model_id in found, f"{model_id!r} was invisible to scan; found {found}"


def test_the_reproduction_from_the_row(tmp_path) -> None:
    """The exact shape that scanned to `No model identifiers found.`, with its control."""
    (tmp_path / "app.py").write_text(
        'MODEL = "llama-3.3-70b-versatile"\n'
        'FALLBACK = "qwen/qwen3-32b"\n'
        'OSS = "openai/gpt-oss-20b"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "GROQ_API_KEY=\nMODEL_NAME=llama-3.1-8b-instant\n", encoding="utf-8"
    )
    found = models_used(tmp_path)
    assert {
        "llama-3.3-70b-versatile",
        "qwen/qwen3-32b",
        "openai/gpt-oss-20b",
        "llama-3.1-8b-instant",
    } <= found, found


# --- ids these patterns MUST NOT invent -------------------------------------------------

#: `[M]` `deepseek-ai` is the one real false positive the measurement over site-packages found:
#: it is the HuggingFace ORGANISATION, and it appears in dependency metadata. The rest are the
#: shapes a widened pattern would plausibly start claiming.
NOT_MODELS = [
    "deepseek-ai",
    "llama-index",
    "llama_index",
    "llamaindex",
    "qwen",
    "mistralai",
    "gpt-ossify",
    "meta-llama-guard",
]


@pytest.mark.parametrize("token", NOT_MODELS)
def test_a_non_model_token_is_not_reported(tmp_path, token: str) -> None:
    """A FABRICATED id is the north-star failure in the first command a stranger runs."""
    (tmp_path / "app.py").write_text(f'X = "{token}"\n', encoding="utf-8")
    assert token not in models_used(tmp_path), f"scan invented {token!r} as a model"


def test_deepseek_ai_is_excluded_while_real_deepseek_models_are_found(tmp_path) -> None:
    """Both halves in one file, because the risk is fixing one by losing the other."""
    (tmp_path / "app.py").write_text(
        'ORG = "deepseek-ai"\nMODEL = "deepseek-r1-0528"\nALT = "deepseek-v3.1"\n',
        encoding="utf-8",
    )
    found = models_used(tmp_path)
    assert "deepseek-ai" not in found
    assert {"deepseek-r1-0528", "deepseek-v3.1"} <= found


# --- the noise this fix could have introduced -------------------------------------------


def test_a_vendor_prefixed_id_is_not_reported_twice(tmp_path) -> None:
    """`[M] 2026-09-06` The first version of this fix DID ship this defect and it was caught
    here: `qwen/qwen3-32b` was reported alongside the bare `qwen3-32b` it contains, so a file
    naming four models produced five rows. Fixing scan's blindness must not create scan's
    noise (MP-10) -- a substring of a longer match on the same span is dropped."""
    (tmp_path / "app.py").write_text('M = "qwen/qwen3-32b"\n', encoding="utf-8")
    found = models_used(tmp_path)
    assert found == {"qwen/qwen3-32b"}, found


def test_two_genuinely_different_ids_on_one_line_both_survive(tmp_path) -> None:
    """Control for the de-duplication: it drops CONTAINED spans, not equal-looking strings."""
    (tmp_path / "app.py").write_text(
        'PAIR = ("llama-3.3-70b-versatile", "qwen/qwen3-32b")\n', encoding="utf-8"
    )
    assert models_used(tmp_path) == {"llama-3.3-70b-versatile", "qwen/qwen3-32b"}


# --- the file-matching half --------------------------------------------------------------


@pytest.mark.parametrize("name", [".env", ".env.example", ".env.local", ".env.sample"])
def test_env_variants_are_scanned(tmp_path, name: str) -> None:
    """`.env` was matched by exact NAME, so `.env.example` -- the file a repo commits precisely
    because it is the readable record of its configuration -- was invisible. Its `suffix` is
    `.example`, so the extension test could not see it either."""
    (tmp_path / name).write_text("MODEL_NAME=llama-3.1-8b-instant\n", encoding="utf-8")
    assert "llama-3.1-8b-instant" in models_used(tmp_path)


def test_a_utf8_source_file_is_decoded_as_utf8(tmp_path) -> None:
    """MP-190's class in the site its sweep missed: `read_text(errors="ignore")` carried no
    `encoding=`, so on a cp1252 machine a UTF-8 file decoded to mojibake and its ids stopped
    matching -- a silent MISS rather than a wrong verdict, but silent either way."""
    (tmp_path / "app.py").write_text(
        '# comentário sobre o modelo — em português\nMODEL = "llama-3.3-70b-versatile"\n',
        encoding="utf-8",
    )
    assert "llama-3.3-70b-versatile" in models_used(tmp_path)


def test_dependencies_are_still_not_reported_as_the_users_models(tmp_path) -> None:
    """Control for MP-134/MP-135: widening the patterns must not re-open scan's worst noise."""
    pkg = tmp_path / ".venv" / "Lib" / "site-packages" / "somedep"
    pkg.mkdir(parents=True)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (pkg / "consts.py").write_text('M = "llama-3.3-70b-versatile"\n', encoding="utf-8")
    (tmp_path / "app.py").write_text('MINE = "qwen/qwen3-32b"\n', encoding="utf-8")
    assert models_used(tmp_path) == {"qwen/qwen3-32b"}


def test_scan_reports_a_line_number_that_points_at_the_id(tmp_path) -> None:
    (tmp_path / "app.py").write_text('a = 1\nb = 2\nM = "qwen/qwen3-32b"\n', encoding="utf-8")
    hits = [h for h in scan_repo(tmp_path) if h["model"] == "qwen/qwen3-32b"]
    assert hits and hits[0]["line"] == 3, hits


# --- MP-200: our own output is not the user's dependency ---------------------------------


def test_modelpins_own_store_is_not_scanned(tmp_path) -> None:
    """`[M] 2026-09-06`, found by scanning a REAL repo rather than a fixture.

    On `kavach`, `scan` returned **76 hits, 61 of them (80%) from `.modelpin/baseline-*.json`**
    -- the recorded traces of a previous Modelpin run. `actions/README.md` tells users to
    `git add` that directory, so it is present in exactly the repos this command is aimed at,
    and the table a user reads was four-fifths our own artifacts.

    This is MP-134/MP-135's class one directory over: *"scan reported 23 distinct models and
    only 2 were the user's own code"*. Reporting our own output back as their dependencies is
    the same defect wearing our own name.
    """
    from modelpin.storage import STORE_DIRNAME

    store = tmp_path / STORE_DIRNAME
    store.mkdir()
    (store / "baseline-openai_gpt-oss-120b.json").write_text(
        '{"model_id": "openai/gpt-oss-120b", "scenarios": {}}', encoding="utf-8"
    )
    (tmp_path / "app.py").write_text('MINE = "gpt-4o-mini"\n', encoding="utf-8")

    assert models_used(tmp_path) == {"gpt-4o-mini"}


def test_the_skip_is_bound_to_the_store_name_not_typed(tmp_path) -> None:
    """Renaming the store must not silently re-open this."""
    from modelpin.detector import SKIP_DIRS
    from modelpin.storage import STORE_DIRNAME

    assert STORE_DIRNAME in SKIP_DIRS


# --- MP-201: a model id inside a URL is a link, not a dependency --------------------------


def test_a_model_id_inside_a_url_is_not_a_dependency(tmp_path) -> None:
    """`[M] 2026-09-07`, found by scanning a REAL repo (`faceanchor`) rather than a fixture.

    All **28** of its hits were fabricated, and all 28 came from URLs inside scraped SerpAPI
    evidence JSON -- a percent-encoded Thai news slug, an article headline slug, and base64
    PNG data in a filename. Zero of them were models; the repo does not call an LLM at all.
    """
    (tmp_path / "evidence.json").write_text(
        '{"link": "https://example.com/news/gpt-6-astra-its-latest-ai-model-launched"}\n',
        encoding="utf-8",
    )
    assert models_used(tmp_path) == set()


def test_base64_data_in_a_url_is_not_a_model(tmp_path) -> None:
    """`o4-AuaAAAAAElFTkSuQmCC.png` -- MP-135's class (`o3XPaKcS`) through a different door.

    Narrowing the o-series digits cannot help here: `o4` IS a real model and `-Aua...` is a
    legal suffix shape. Only the surrounding context separates them.
    """
    (tmp_path / "raw.json").write_text(
        '{"thumb": "https://serpapi.com/images/o4-AuaAAAAAElFTkSuQmCC.png"}\n', encoding="utf-8"
    )
    assert models_used(tmp_path) == set()


def test_an_asset_filename_is_not_a_model(tmp_path) -> None:
    (tmp_path / "app.py").write_text('ICON = "gpt-4o-mini.png"\n', encoding="utf-8")
    assert models_used(tmp_path) == set()


def test_a_real_id_on_a_line_that_also_holds_a_url_still_counts(tmp_path) -> None:
    """Control, and the one that would catch an over-broad guard: dropping the whole LINE
    rather than the matches inside the URL would lose real ids from ordinary config files."""
    (tmp_path / "app.py").write_text(
        'MODEL = "gpt-4o-mini"  # docs: https://example.com/models/gpt-9-imaginary\n',
        encoding="utf-8",
    )
    assert models_used(tmp_path) == {"gpt-4o-mini"}


def test_a_bare_id_next_to_a_url_on_another_line_still_counts(tmp_path) -> None:
    (tmp_path / "app.py").write_text(
        'DOCS = "https://example.com/gpt-9-imaginary"\nMODEL = "claude-sonnet-4-6"\n',
        encoding="utf-8",
    )
    assert models_used(tmp_path) == {"claude-sonnet-4-6"}
