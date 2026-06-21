"""iOS daemon session options — UDID is required for the USB path but NOT for a
wireless attach (IPH_WDA_URL).

Cable-free multibox pools (`DevicePool.from_remembered`) remember an iPhone by
its `wda_url` alone — `wifi_store` accepts wda_url as the sole iOS identity — and
spawn the daemon with `IPH_UDID=""`. Before this, `_build_options` raised
"IPH_UDID not set" before ever looking at IPH_WDA_URL, so such a device could
never start in a pool. These tests pin the corrected contract:

  - wireless (valid IPH_WDA_URL) + no UDID  -> builds, attaches via webDriverAgentUrl
  - USB (no IPH_WDA_URL) + no UDID           -> still raises (unchanged)
  - invalid IPH_WDA_URL + no UDID            -> still raises (can't attach, no udid)
  - UDID present                             -> udid set in caps (USB path unaffected)
"""
import importlib

import pytest

daemon = importlib.import_module("iphone_harness.daemon")


@pytest.fixture(autouse=True)
def _quiet_log(monkeypatch):
    # _build_options' wireless branch calls log(), which appends to a file.
    monkeypatch.setattr(daemon, "log", lambda *a, **k: None)


def test_wireless_no_udid_builds_and_attaches(monkeypatch):
    monkeypatch.setattr(daemon, "UDID", None)
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    caps = daemon._build_options().to_capabilities()
    assert caps.get("appium:webDriverAgentUrl") == "http://192.168.1.50:8100"
    # No UDID was set — wireless attach doesn't need one.
    assert not caps.get("appium:udid")


def test_usb_no_udid_still_raises(monkeypatch):
    monkeypatch.setattr(daemon, "UDID", None)
    monkeypatch.delenv("IPH_WDA_URL", raising=False)
    with pytest.raises(RuntimeError, match="IPH_UDID not set"):
        daemon._build_options()


def test_invalid_wda_url_no_udid_still_raises(monkeypatch):
    # A bare port / non-URL can't be attached to, so without a UDID there is
    # genuinely no way to reach the device — must still raise.
    monkeypatch.setattr(daemon, "UDID", None)
    monkeypatch.setenv("IPH_WDA_URL", "8100")
    with pytest.raises(RuntimeError, match="IPH_UDID not set"):
        daemon._build_options()


def test_udid_present_sets_udid(monkeypatch):
    # USB path is unaffected: a real UDID still lands in the caps.
    monkeypatch.setattr(daemon, "UDID", "00008140-AABBCCDDEEFF0011")
    monkeypatch.delenv("IPH_WDA_URL", raising=False)
    caps = daemon._build_options().to_capabilities()
    assert caps.get("appium:udid") == "00008140-AABBCCDDEEFF0011"


def test_udid_and_wireless_both_set(monkeypatch):
    # A USB-discovered device that was also remembered wirelessly keeps both.
    monkeypatch.setattr(daemon, "UDID", "00008140-AABBCCDDEEFF0011")
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    caps = daemon._build_options().to_capabilities()
    assert caps.get("appium:udid") == "00008140-AABBCCDDEEFF0011"
    assert caps.get("appium:webDriverAgentUrl") == "http://192.168.1.50:8100"
