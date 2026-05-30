"""D7 — Enter/keycode + hide_keyboard helpers on both platforms.

The 'type a query then submit' flow is one of the most common mobile tasks but
had no first-class helper: iOS type_text sent a literal '\\n' (never the Return
key) and Android wired only home/back/recents. These helpers unblock search /
URL / login submit. Because the agent's action surface is dir(helpers), adding
them as named functions also makes them visible to the LLM agent.
"""
import android_harness.helpers as ah
import iphone_harness.helpers as ih


def test_both_platforms_expose_enter_and_hide_keyboard():
    for mod in (ih, ah):
        for name in ("press_enter", "hide_keyboard"):
            assert hasattr(mod, name) and callable(getattr(mod, name)), f"{mod.__name__} missing {name}"
    assert callable(ih.press_return)
    assert callable(ah.key_event) and callable(ah.press_search)


def test_ios_type_text_sends_return_on_newline(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ih.type_text("coffee\n")
    # First a keys call for the literal text, then a Return key press.
    assert calls[0][0] == "mobile: keys"
    assert calls[0][1]["keys"] == list("coffee")
    assert any(
        c[0] == "mobile: keys" and c[1].get("keys") == [{"key": "XCUIKeyboardKeyReturn"}]
        for c in calls
    ), "newline must emit a Return key press, not a literal '\\n'"


def test_ios_type_text_plain_text_no_return(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ih.type_text("hello")
    assert len(calls) == 1 and calls[0][1]["keys"] == list("hello")


def test_ios_press_return_uses_keyboard_return_key(monkeypatch):
    calls = []
    monkeypatch.setattr(ih, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ih.press_return()
    assert calls == [("mobile: keys", {"keys": [{"key": "XCUIKeyboardKeyReturn"}]})]


def test_android_press_enter_is_keycode_66(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ah.press_enter()
    assert calls == [("mobile: pressKey", {"keycode": 66})]


def test_android_key_event_passes_keycode(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "appium", lambda script, **kw: calls.append((script, kw)) or {})
    ah.key_event(61)
    assert calls == [("mobile: pressKey", {"keycode": 61})]
