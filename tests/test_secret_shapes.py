"""Which credential shapes Modelpin redacts -- and, as importantly, which text it leaves alone.

MP-243. `[M] 2026-09-09` security review: `_SECRET_RE` covered OpenAI (`sk-`, which also
catches Anthropic's `sk-ant-`), Groq (`gsk_`), three Google shapes and raw `Bearer` headers,
and missed every other common credential a developer's environment holds -- AWS access key
ids, GitHub tokens, PEM private keys, Azure storage connection strings. Meanwhile
`README.md` said a failed call "never leaks your key", and the 0.1.0 CHANGELOG entry said
scrubbing covered "all output". It covers provider error messages; recorded evidence is
deliberately warned about rather than rewritten.

Every pattern added here has a DISTINCTIVE PREFIX. That is the design constraint, not an
oversight: many credentials -- Azure OpenAI keys, several hosted-inference keys -- are bare
hex or base64 with no prefix at all, and a pattern for "32 hex characters" would redact git
SHAs, content hashes, UUIDs and request ids out of every error message. A scrubber that
destroys the diagnostic it sits inside gets turned off. The negative cases below pin that.

THE MISTAKE THIS MODULE NOW GUARDS AGAINST. The first draft of MP-243 put a word boundary
`\\b` before every prefix, to stop two cosmetic false positives. The FP review measured the
cost: a key that follows a WORD character has no boundary before it -- and an escape sequence
leaves exactly that. `repr()` turns a newline into the two characters `\\n`, so the key after
it starts right after an `n`; URL-encoding turns `=` into `%3D`. Keys in those positions were
redacted on `main` and LEAKED under the draft. `openai` 3.3.1 builds its error text from a
Python repr of the response body, so this was the ordinary path, not an exotic one.

For a secret scrubber, a false negative is a leaked key and a false positive is an ugly word.
So the escaped-context cases below are the guard, and the two cosmetic false positives are
kept -- as accepted, documented, and pinned -- rather than traded for a leak.
"""

from __future__ import annotations

import time

import pytest

from modelpin.providers._common import contains_secret, scrub_secrets

_OPENAI = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
_GOOGLE = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4"
_AWS = "AKIAIOSFODNN7EXAMPLE"
_GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

#: Shaped like real credentials, but not real. Each is a format published by its vendor.
REDACTED = {
    "AWS access key id": _AWS,
    "AWS temporary key id": "ASIAIOSFODNN7EXAMPLE",
    "GitHub personal token": _GITHUB,
    "GitHub OAuth token": "gho_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "GitHub fine-grained PAT": "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
    "PEM private key": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn\n"
        "-----END RSA PRIVATE KEY-----"
    ),
    "Azure storage connection string": (
        "DefaultEndpointsProtocol=https;AccountName=acct;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq==;"
    ),
    # Already covered before MP-243; kept so a regression in the OLD shapes is caught too.
    "OpenAI project key": _OPENAI,
    "Anthropic key": "sk-ant-" + "api03-A1b2C3d4E5f6G7h8",
    "Groq key": "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "Google API key": _GOOGLE,
}

#: A key immediately after an escape sequence -- i.e. after a WORD character, where a leading
#: `\\b` cannot match. `[M] 2026-09-09` each of these was redacted on `main` and leaked under
#: the first draft of MP-243. The body is the key; everything before it is the escape.
ESCAPED = {
    "after repr() of a newline": (repr("token:\n" + _OPENAI), _OPENAI),
    "after URL-encoded '='": ("api_key%3D" + _OPENAI, _OPENAI),
    "after unicode-escaped '='": ("key\\u003d" + _OPENAI, _OPENAI),
    "Google key after URL-encoded '='": ("key%3D" + _GOOGLE, _GOOGLE),
    "AWS id after URL-encoded '='": ("aws_key%3D" + _AWS, _AWS),
    "GitHub token after URL-encoded '='": ("token%3D" + _GITHUB, _GITHUB),
}

#: Text that looks technical but is not a credential, and must survive untouched. Each of these
#: routinely appears INSIDE the error messages this scrubber runs over.
PRESERVED = {
    "git commit SHA": "e578589922ee68497f85d7157ba6fd4d68a5ab6a",
    "sha256 digest": "sha256:5cba1dc8b691f3e2a4c7d9b0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0",
    "UUID request id": "req_7f3e9a2c-1b4d-4e8f-9a6c-2d5b8e1f4a7c",
    "model id": "gpt-4o-mini-2024-07-18",
    "ordinary word": "ASKING",
    "public key header": "-----BEGIN PUBLIC KEY-----",
    # Case-sensitivity, NOT a word boundary, is what keeps this from reading as an AWS id.
    "a region-shaped word": "asiapacificregion1234",
}

#: Over-redactions we KNOW about and accept, because removing them cost key coverage. They
#: were on `main` before MP-243 and are unchanged by it. Pinned so that nobody "fixes" them
#: with a word boundary again without first reading the module docstring.
ACCEPTED_OVER_REDACTION = ("risk-free-retry", "FAQ.documentation-page")


@pytest.mark.parametrize("label", sorted(REDACTED))
def test_a_credential_shape_is_redacted(label: str) -> None:
    secret = REDACTED[label]
    text = f"provider error: request failed with credential {secret} attached"
    scrubbed = scrub_secrets(text)
    assert secret not in scrubbed, f"{label} survived scrubbing: {scrubbed!r}"
    assert "[redacted]" in scrubbed
    assert contains_secret(text), f"contains_secret disagrees with scrub_secrets on {label}"


@pytest.mark.parametrize("label", sorted(ESCAPED))
def test_a_key_right_after_an_escape_sequence_is_still_redacted(label: str) -> None:
    """The regression a leading word boundary causes. This is the guard against re-adding it."""
    text, secret = ESCAPED[label]
    scrubbed = scrub_secrets(text)
    assert secret not in scrubbed, (
        f"a key {label} LEAKED through the scrubber: {scrubbed!r}. A leading `\\b` cannot match "
        "after a word character, and an escape sequence leaves one. Read this module's "
        "docstring before adding a word boundary to any prefix."
    )


@pytest.mark.parametrize("label", sorted(PRESERVED))
def test_technical_text_that_is_not_a_credential_is_left_alone(label: str) -> None:
    value = PRESERVED[label]
    text = f"provider error: see {value} for details"
    assert scrub_secrets(text) == text, (
        f"the scrubber redacted a {label}, which is not a secret. Over-redaction destroys the "
        "diagnostic the scrubber sits inside, and a scrubber that does that gets turned off."
    )
    assert not contains_secret(text), f"contains_secret false-positive on a {label}"


@pytest.mark.parametrize("word", ACCEPTED_OVER_REDACTION)
def test_the_known_over_redactions_are_the_accepted_price_of_coverage(word: str) -> None:
    """Pinned ON PURPOSE. If this starts passing through untouched, check coverage first.

    These two strings are redacted because their prefixes are not preceded by a word boundary
    check -- and that absence is exactly what keeps an escaped key redacted. If a change makes
    them survive, it very likely re-introduced the leak the escaped-context tests guard.
    """
    assert scrub_secrets(f"see {word}") != f"see {word}", (
        f"{word!r} is no longer over-redacted. That is only safe if every escaped-context case "
        "still passes -- if it came from a word boundary, it re-opened the key leak."
    )


def test_a_multi_line_pem_is_redacted_whole_not_just_its_header() -> None:
    """Redacting only the BEGIN line would leave the key body -- the actual secret -- in place."""
    pem = REDACTED["PEM private key"]
    scrubbed = scrub_secrets(f"config dump:\n{pem}\nend")
    assert "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn" not in scrubbed, scrubbed
    assert scrubbed.startswith("config dump:") and scrubbed.endswith("end")


def test_many_unterminated_pem_headers_do_not_go_quadratic() -> None:
    """`[M] 2026-09-09` FP review: 1,000 unterminated BEGIN headers took 0.354 s with an
    unbounded lazy body, against 0.002 s before -- quadrupling per doubling. The body is now
    capped. The budget is generous so a slow CI runner cannot flake it into being muted."""
    text = "-----BEGIN RSA PRIVATE KEY-----\n" * 4000
    t0 = time.perf_counter()
    scrub_secrets(text)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, (
        f"scrubbing 4,000 unterminated PEM headers took {elapsed:.2f} s -- the PEM body scan "
        "is unbounded again, and it runs on every provider error message."
    )
