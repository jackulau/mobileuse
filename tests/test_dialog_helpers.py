"""Regression tests for dialog/alert button targeting (device-free).

Android: alert helpers used to hard-require type == 'android.widget.Button', so
they silently missed MaterialButton / AppCompatButton / Jetpack Compose dialogs
and the Android 13+ Material permission sheet. Now they match any clickable
element whose text/content-desc is in the label set.

iOS: auto_dismiss_dialog used to call mobile:alert action='dismiss' regardless
of which button matched, and listed 'OK' (an accept word) as a dismiss label,
and blindly accepted as a final fallback — so on a [Don't Allow, Allow]
permission sheet it could grant a permission it meant to deny. Now it taps the
specific matched label via buttonLabel and never silently grants.
"""
import android_harness.helpers as ah
import iphone_harness.helpers as ih

# ---- Android -------------------------------------------------------------

def _android_tree(monkeypatch):
    taps = []
    monkeypatch.setattr(ah, "tap", lambda el: taps.append(el))
    monkeypatch.setattr(ah, "wait", lambda *a, **k: None)
    monkeypatch.setattr(ah, "press_back", lambda: taps.append("BACK"))
    return taps


def test_android_alert_accept_matches_material_button(monkeypatch):
    taps = _android_tree(monkeypatch)
    monkeypatch.setattr(ah, "ui_tree", lambda visible_only=False, compact=False: [
        {"type": "com.google.android.material.button.MaterialButton", "text": "Allow",
         "content_desc": "", "enabled": True, "clickable": True, "cx": 100, "cy": 200},
    ])
    ah.alert_accept()
    assert len(taps) == 1 and taps[0]["text"] == "Allow"


def test_android_alert_accept_matches_compose_node(monkeypatch):
    """Compose dialog buttons have no Button class at all — must still match."""
    taps = _android_tree(monkeypatch)
    monkeypatch.setattr(ah, "ui_tree", lambda visible_only=False, compact=False: [
        {"type": "android.view.View", "text": "OK", "content_desc": "",
         "enabled": True, "clickable": True, "cx": 50, "cy": 60},
    ])
    ah.alert_accept()
    assert len(taps) == 1 and taps[0]["text"] == "OK"


def test_android_auto_dismiss_prefers_deny_on_permission_sheet(monkeypatch):
    taps = _android_tree(monkeypatch)
    monkeypatch.setattr(ah, "ui_tree", lambda visible_only=False, compact=False: [
        {"type": "com.google.android.material.button.MaterialButton", "text": "Allow",
         "content_desc": "", "enabled": True, "clickable": True, "cx": 300, "cy": 400},
        {"type": "com.google.android.material.button.MaterialButton", "text": "Deny",
         "content_desc": "", "enabled": True, "clickable": True, "cx": 100, "cy": 400},
    ])
    assert ah.auto_dismiss_dialog() is True
    assert taps[0]["text"] == "Deny", "auto_dismiss must prefer the deny/dismiss button"


def test_android_alert_accept_raises_when_no_button(monkeypatch):
    _android_tree(monkeypatch)
    monkeypatch.setattr(ah, "ui_tree", lambda visible_only=False, compact=False: [
        {"type": "android.widget.TextView", "text": "Some label", "content_desc": "",
         "enabled": True, "clickable": False, "cx": 0, "cy": 0},
    ])
    try:
        ah.alert_accept()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


# ---- iOS -----------------------------------------------------------------

def _ios_capture(monkeypatch, buttons):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    monkeypatch.setattr(ih, "alert", lambda: {"buttons": buttons, "label": "Permission"})
    monkeypatch.setattr(ih, "wait", lambda *a, **k: None)
    return calls


def test_ios_auto_dismiss_taps_matched_deny_label(monkeypatch):
    calls = _ios_capture(monkeypatch, ["Don't Allow", "Allow"])
    assert ih.auto_dismiss_dialog() is True
    # Must tap the specific matched dismiss label, not blindly accept/allow.
    alert_calls = [c for c in calls if c[0] == "mobile: alert"]
    assert alert_calls, "should have tapped a button"
    assert alert_calls[0][1].get("buttonLabel") == "Don't Allow"


def test_ios_auto_dismiss_does_not_grant_all_grant_alert(monkeypatch):
    calls = _ios_capture(monkeypatch, ["Allow", "Allow Once"])
    # Every button is a grant — must NOT silently accept.
    assert ih.auto_dismiss_dialog() is False
    accept_calls = [c for c in calls if c[0] == "mobile: alert" and c[1].get("action") == "accept"]
    assert not accept_calls, "must not grant a permission it meant to dismiss"


def test_ios_auto_dismiss_single_button_informational(monkeypatch):
    calls = _ios_capture(monkeypatch, ["OK"])
    assert ih.auto_dismiss_dialog() is True
    alert_calls = [c for c in calls if c[0] == "mobile: alert"]
    assert alert_calls[0][1].get("buttonLabel") == "OK"


def test_ios_ok_not_treated_as_dismiss_on_confirm(monkeypatch):
    # [Cancel, OK] confirmation — auto_dismiss should back out via Cancel, not OK.
    calls = _ios_capture(monkeypatch, ["Cancel", "OK"])
    assert ih.auto_dismiss_dialog() is True
    alert_calls = [c for c in calls if c[0] == "mobile: alert"]
    assert alert_calls[0][1].get("buttonLabel") == "Cancel"
