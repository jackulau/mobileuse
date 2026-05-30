"""D9 — device-control verbs: open_url/deep-link, clipboard, set_location, orientation.

These are first-class verbs in mobile-mcp / Maestro / Appium agents but were
absent here. Critically, iOS clipboard MUST use the Pasteboard scripts
(get/setPasteboard) — the Clipboard scripts are Android-only; shipping the
Android names on iOS would silently break the helper.
"""
import base64

import android_harness.helpers as ah
import iphone_harness.helpers as ih


def test_both_platforms_expose_device_verbs():
    for mod in (ih, ah):
        for name in ("open_url", "get_clipboard", "set_clipboard", "set_location",
                     "get_orientation", "set_orientation"):
            assert hasattr(mod, name) and callable(getattr(mod, name)), f"{mod.__name__} missing {name}"


def test_ios_clipboard_uses_pasteboard_not_clipboard(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or "")
    ih.set_clipboard("hello")
    assert calls[0][0] == "mobile: setPasteboard", "iOS must use Pasteboard, not Clipboard"
    # content is base64-encoded
    assert base64.b64decode(calls[0][1]["content"]).decode() == "hello"

    monkeypatch.setattr(ih, "appium",
                        lambda script, **kw: base64.b64encode(b"copied").decode())
    assert ih.get_clipboard() == "copied"


def test_ios_open_url_and_location(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ih.open_url("https://example.com")
    ih.set_location(37.33, -122.03)
    assert calls[0] == ("mobile: deepLink", {"url": "https://example.com", "bundleId": "com.apple.mobilesafari"})
    assert calls[1][0] == "mobile: setSimulatedLocation"
    assert calls[1][1]["latitude"] == 37.33 and calls[1][1]["longitude"] == -122.03


def test_android_clipboard_uses_clipboard_scripts(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or "")
    ah.set_clipboard("hi")
    assert calls[0][0] == "mobile: setClipboard"
    assert base64.b64decode(calls[0][1]["content"]).decode() == "hi"


def test_android_open_url_and_location(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ah.open_url("geo:37.4,-122.0")
    ah.set_location(37.4, -122.0)
    assert calls[0] == ("mobile: deepLink", {"url": "geo:37.4,-122.0"})
    assert calls[1][0] == "mobile: setGeolocation"


def test_orientation_round_trips_through_daemon(monkeypatch):
    for mod in (ih, ah):
        sent = []
        monkeypatch.setattr(mod, "_send", lambda req: sent.append(req) or {"result": "LANDSCAPE"})
        assert mod.set_orientation("LANDSCAPE") == "LANDSCAPE"
        assert sent[-1]["method"] == "set_orientation"
        assert sent[-1]["params"]["orientation"] == "LANDSCAPE"
        assert mod.get_orientation() == "LANDSCAPE"
        monkeypatch.undo()
