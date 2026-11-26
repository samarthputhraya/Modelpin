"""`SECURITY.md` is served from the default branch to anyone deciding whether to trust Modelpin,
so every promise in it is pinned here against the code that has to keep it.

MP-320 / MP-08. `[M] 2026-11-26` the file listed `0.1.x` as the supported line while PyPI's
latest was 0.5.7, and it said secrets were "scrubbed from all output ... before anything is
printed, logged, or written to a report", while `scrub_secrets` ran only on provider error
messages and `mp draft` input: model outputs and tool-call arguments reached baselines, run
files, reports and the PR comment unscrubbed.
"""

from __future__ import annotations

import re
from pathlib import Path

from modelpin.providers._common import scrub_secrets

POLICY = (Path(__file__).resolve().parents[1] / "SECURITY.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", POLICY, re.M | re.S)
    assert match, f"SECURITY.md has no '## {title}' section"
    return match.group(1)


def test_supported_versions_name_no_release_line_that_can_go_stale():
    """The table said `0.1.x` for five months of 0.2-0.5 releases. Naming the latest release,
    rather than a number, is what keeps it true at the next release without an edit."""
    table = _section("Supported versions")
    assert "latest release" in table.lower(), table
    stale = re.findall(r"\b\d+\.\d+\.x\b", table)
    assert not stale, f"a version line in the table goes stale at the next release: {stale}"


def test_the_policy_does_not_promise_that_all_output_is_scrubbed():
    """MP-08. Model outputs are recorded and quoted as produced; only error text is scrubbed."""
    lowered = POLICY.lower()
    assert "scrubbed from all output" not in lowered
    assert "before anything is printed" not in lowered
    assert "model outputs and tool-call arguments are not scrubbed" in lowered


#: One made-up sample per shape SECURITY.md lists, keyed by the exact backticked text it uses.
#: A shape added to the file without a sample here fails the test below, so the list cannot
#: grow past what the scrubber is shown to redact.
_SAMPLES = {
    "sk-": "sk-" + "a1B2c3D4e5F6g7H8i9J0",
    "sk-proj-": "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0",
    "sk-ant-": "sk-ant-" + "api03-a1B2c3D4e5F6g7H8i9J0",
    "gsk_": "gsk_" + "a1B2c3D4e5F6g7H8i9J0",
    "AIza": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2",
    "ya29.": "ya29." + "a1B2c3D4e5F6g7H8i9J0",
    "AQ.": "AQ." + "a1B2c3D4e5F6g7H8i9J0",
    "AKIA": "AKIA" + "IOSFODNN7EXAMPLE",
    "ASIA": "ASIA" + "IOSFODNN7EXAMPLE",
    "ghp_": "ghp_" + "a1B2c3D4e5F6g7H8i9J0a1B2c3D4e5F6g7H8",
    "github_pat_": "github_pat_" + "a1B2c3D4e5F6g7H8i9J0a1B2",
    "AccountKey=": "AccountKey=" + "a1B2c3D4e5F6g7H8i9J0a1B2==",
    "Bearer": "Bearer " + "a1B2c3D4e5F6g7H8i9J0",
}


def test_every_key_shape_the_policy_lists_is_redacted():
    bullet = re.search(r"Provider error messages are scrubbed.*?(?=\n- \*\*)", POLICY, re.S)
    assert bullet, "the scrubbing bullet moved; update this test with it"
    # Every backticked token in the bullet is a key shape, except the two command names.
    listed = {t for t in re.findall(r"`([^`]+)`", bullet.group(0)) if not t.startswith("modelpin")}
    unsampled = listed - _SAMPLES.keys()
    assert not unsampled, f"SECURITY.md lists shapes with no sample here: {sorted(unsampled)}"
    assert len(listed) >= 10, sorted(listed)
    for shape in sorted(listed):
        sample = _SAMPLES[shape]
        text = f"request failed: key={sample} rejected"
        assert sample not in scrub_secrets(text), f"{shape!r} is listed but not redacted"


def test_a_complete_pem_block_is_redacted_whole():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEbody\n-----END RSA PRIVATE KEY-----"
    assert scrub_secrets(f"x {block} y") == "x [redacted] y"
