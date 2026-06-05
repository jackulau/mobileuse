"""D8 — app-lifecycle verbs (launch / activate / terminate / state / installed).

A best-in-class harness (mobile-mcp's mobile_launch_app/terminate, Maestro's
launchApp/stopApp) exposes app switching as first-class verbs. mobile_use had
these only buried in docstrings, so the agent (whose action surface is
dir(helpers)) could not reliably switch or relaunch apps. These are thin
wrappers over the appium() escape hatch.
"""
import android_harness.helpers as ah
import iphone_harness.helpers as ih


def test_both_platforms_expose_lifecycle_verbs():
    for mod in (ih, ah):
        for name in ("launch_app", "activate_app", "terminate_app", "app_state", "is_app_installed"):
            assert hasattr(mod, name) and callable(getattr(mod, name)), f"{mod.__name__} missing {name}"


def test_ios_lifecycle_uses_bundle_id(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or 4)
    ih.launch_app("com.apple.mobilesafari")
    ih.terminate_app("com.apple.mobilesafari")
    ih.activate_app("com.apple.mobilesafari")
    assert calls[0] == ("mobile: launchApp", {"bundleId": "com.apple.mobilesafari"})
    assert calls[1] == ("mobile: terminateApp", {"bundleId": "com.apple.mobilesafari"})
    assert calls[2] == ("mobile: activateApp", {"bundleId": "com.apple.mobilesafari"})


def test_ios_is_app_installed_from_state(monkeypatch):
    monkeypatch.setattr(ih, "appium", lambda script, **kw: 0)
    assert ih.is_app_installed("com.nope.app") is False
    monkeypatch.setattr(ih, "appium", lambda script, **kw: 4)
    assert ih.is_app_installed("com.apple.mobilesafari") is True


def test_android_launch_app_cold_starts(monkeypatch):
    # Parity with iOS launchApp: launch_app must FORCE-STOP then activate (a real cold
    # start), not just foreground like activate_app — otherwise an agent expecting a
    # fresh launch silently gets the app's prior state on Android.
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or True)
    ah.launch_app("com.android.chrome")
    assert calls == [
        ("mobile: terminateApp", {"appId": "com.android.chrome"}),
        ("mobile: activateApp", {"appId": "com.android.chrome"}),
    ]


def test_android_activate_app_does_not_restart(monkeypatch):
    # activate_app stays a pure foreground (no terminate) — the resume path.
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or True)
    ah.activate_app("com.android.chrome")
    assert calls == [("mobile: activateApp", {"appId": "com.android.chrome"})]


def test_android_lifecycle_uses_app_id(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or True)
    ah.terminate_app("com.android.chrome")
    ah.is_app_installed("com.android.chrome")
    assert calls[0] == ("mobile: terminateApp", {"appId": "com.android.chrome"})
    assert calls[1] == ("mobile: isAppInstalled", {"appId": "com.android.chrome"})
