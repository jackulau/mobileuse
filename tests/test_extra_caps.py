"""D11 — WDA-reuse caps + arbitrary-capability passthrough (IPH_CAPS / ANH_CAPS).

There was no way to pass arbitrary Appium caps (attach to a pre-running WDA,
override automationName for a new-OS quirk, set systemPort/snapshotMaxDepth)
without editing source. IPH_CAPS / ANH_CAPS are JSON env vars merged last over
the defaults. iOS also reuses a prebuilt WebDriverAgent when present, skipping
the slow per-session WDA reinstall.
"""
import importlib

import pytest


@pytest.mark.parametrize("mod_name, env, key, val", [
    ("iphone_harness.daemon", "IPH_CAPS", "appium:webDriverAgentUrl", "http://h:8100"),
    ("android_harness.daemon", "ANH_CAPS", "appium:systemPort", 8201),
])
def test_caps_passthrough(mod_name, env, key, val, monkeypatch):
    daemon = importlib.import_module(mod_name)
    monkeypatch.setattr(daemon, "UDID", "x")
    import json
    monkeypatch.setenv(env, json.dumps({key: val}))
    caps = daemon._build_options().to_capabilities()
    assert caps.get(key) == val, f"{env} override not applied: {caps.get(key)!r}"


@pytest.mark.parametrize("mod_name, env", [
    ("iphone_harness.daemon", "IPH_CAPS"),
    ("android_harness.daemon", "ANH_CAPS"),
])
def test_invalid_caps_json_is_ignored_not_fatal(mod_name, env, monkeypatch):
    daemon = importlib.import_module(mod_name)
    monkeypatch.setattr(daemon, "UDID", "x")
    monkeypatch.setenv(env, "this is not json")
    # Must not raise — invalid JSON is logged and skipped.
    caps = daemon._build_options().to_capabilities()
    assert caps.get("appium:newCommandTimeout")  # options still built


def test_android_unicode_caps_present(monkeypatch):
    daemon = importlib.import_module("android_harness.daemon")
    monkeypatch.setattr(daemon, "UDID", "x")
    caps = daemon._build_options().to_capabilities()
    assert caps.get("appium:unicodeKeyboard") is True
    assert caps.get("appium:resetKeyboard") is True


def test_ios_prebuilt_wda_helper_returns_path_or_none():
    daemon = importlib.import_module("iphone_harness.daemon")
    # Device-free: just exercise the discovery helper — returns a str path or None.
    result = daemon._default_wda_derived_data()
    assert result is None or isinstance(result, str)
