"""Live calls must work behind a corporate TLS-inspecting proxy on Windows and macOS.

The provider SDKs verify TLS against `certifi`, which does not contain a corporate proxy's root
certificate, so every live call failed with CERTIFICATE_VERIFY_FAILED on such a network even
though the machine trusts the proxy. `modelpin.cli.main` now asks the OS trust store instead
(via `truststore`) on the two platforms whose store is always present. Offline tests only.
"""

from __future__ import annotations

import sys
import types

import pytest

from modelpin import cli


@pytest.fixture
def fake_truststore(monkeypatch):
    calls: list[str] = []
    module = types.ModuleType("truststore")
    module.inject_into_ssl = lambda: calls.append("inject")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", module)
    monkeypatch.delenv("MODELPIN_NO_TRUSTSTORE", raising=False)
    return calls


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_the_os_trust_store_is_used_on_windows_and_macos(monkeypatch, fake_truststore, platform):
    monkeypatch.setattr(cli.sys, "platform", platform)
    cli._use_os_trust_store()
    assert fake_truststore == ["inject"]


def test_linux_keeps_certifi(monkeypatch, fake_truststore):
    """A minimal Linux container may have no system CA bundle; certifi is what works there."""
    monkeypatch.setattr(cli.sys, "platform", "linux")
    cli._use_os_trust_store()
    assert fake_truststore == []


def test_it_can_be_turned_off(monkeypatch, fake_truststore):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("MODELPIN_NO_TRUSTSTORE", "1")
    cli._use_os_trust_store()
    assert fake_truststore == []


def test_a_missing_or_broken_truststore_never_stops_the_cli(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.delenv("MODELPIN_NO_TRUSTSTORE", raising=False)
    monkeypatch.setitem(sys.modules, "truststore", None)  # import raises ImportError
    cli._use_os_trust_store()

    broken = types.ModuleType("truststore")

    def _boom():
        raise RuntimeError("unsupported")

    broken.inject_into_ssl = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", broken)
    cli._use_os_trust_store()
