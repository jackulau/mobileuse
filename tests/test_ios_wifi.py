"""D3 — first-class iOS Wi-Fi: IPH_WDA_URL -> appium:webDriverAgentUrl + tunnel note.

Device-free: builds XCUITestOptions and inspects the resulting capabilities,
plus captures daemon.log() messages for the tunnel-reminder behaviour. Mirrors
tests/test_extra_caps.py (monkeypatch UDID + to_capabilities()).
"""
import importlib

import pytest

WDA_CAP = "appium:webDriverAgentUrl"


@pytest.fixture
def daemon(monkeypatch):
    d = importlib.import_module("iphone_harness.daemon")
    monkeypatch.setattr(d, "UDID", "x")
    return d


@pytest.mark.parametrize("url", [
    "http://192.168.1.50:8100",
    "https://my-iphone.local:8100",
    "http://10.0.0.7:8100/wd/hub",
])
def test_valid_wda_url_sets_capability(daemon, monkeypatch, url):
    monkeypatch.setenv("IPH_WDA_URL", url)
    caps = daemon._build_options().to_capabilities()
    assert caps.get(WDA_CAP) == url


def test_wda_url_absent_means_no_capability(daemon, monkeypatch):
    monkeypatch.delenv("IPH_WDA_URL", raising=False)
    caps = daemon._build_options().to_capabilities()
    assert WDA_CAP not in caps


@pytest.mark.parametrize("bad", [
    "not a url",
    "192.168.1.50:8100",   # missing scheme
    "ftp://192.168.1.50",  # wrong scheme
    "http://",             # no host
    "8100",
])
def test_malformed_wda_url_is_ignored_not_fatal(daemon, monkeypatch, bad):
    monkeypatch.setenv("IPH_WDA_URL", bad)
    caps = daemon._build_options().to_capabilities()  # must not raise
    assert WDA_CAP not in caps


def test_iph_caps_can_override_wda_url(daemon, monkeypatch):
    # IPH_CAPS is merged last, so an explicit override wins over IPH_WDA_URL.
    import json
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    monkeypatch.setenv("IPH_CAPS", json.dumps({WDA_CAP: "http://10.9.9.9:8100"}))
    caps = daemon._build_options().to_capabilities()
    assert caps.get(WDA_CAP) == "http://10.9.9.9:8100"


@pytest.mark.parametrize("url, valid", [
    ("http://h:8100", True),
    ("https://h", True),
    ("http://", False),
    ("h:8100", False),
    ("", False),
    (None, False),
])
def test_valid_http_url_helper(daemon, url, valid):
    assert daemon._valid_http_url(url) is valid


def _capture_log(daemon, monkeypatch):
    msgs = []
    monkeypatch.setattr(daemon, "log", lambda m: msgs.append(m))
    return msgs


def test_tunnel_note_logged_for_ios17_plus(daemon, monkeypatch):
    msgs = _capture_log(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "PLATFORM_VERSION", "18.3.2")
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    daemon._build_options()
    assert any("tunnel" in m.lower() for m in msgs)
    assert any("remotexpc" in m.lower() for m in msgs)


def test_no_tunnel_note_for_ios16(daemon, monkeypatch):
    msgs = _capture_log(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "PLATFORM_VERSION", "16.7.1")
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    daemon._build_options()
    # iOS 16 is pre-RemoteXPC — no tunnel reminder.
    assert not any("tunnel" in m.lower() for m in msgs)
    # ...but the wireless-attach line is still logged.
    assert any("webdriveragent" in m.lower() for m in msgs)


def test_soft_tunnel_hint_when_version_unknown(daemon, monkeypatch):
    msgs = _capture_log(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "PLATFORM_VERSION", None)
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    daemon._build_options()
    assert any("17+" in m or "tunnel" in m.lower() for m in msgs)


def test_ios_tunnel_note_helper_direct(daemon):
    assert daemon._ios_tunnel_note("18.0") is not None
    assert daemon._ios_tunnel_note("16.0") is None
    assert daemon._ios_tunnel_note(None) is not None  # soft hint
