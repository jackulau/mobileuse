"""iOS navigation parity tests — press_home, swipe_back, press_back, press_recents.

iOS gets first-class equivalents to Android's hardware-button helpers.
press_home uses XCUITest pressButton; swipe_back/press_recents use gestures.
"""


def test_ios_press_home_calls_press_button(monkeypatch):
    import iphone_harness.helpers as iph

    captured = []
    monkeypatch.setattr(iph, "appium", lambda script, **kw: captured.append((script, kw)))

    iph.press_home()

    assert captured == [("mobile: pressButton", {"name": "home"})]


def test_ios_swipe_back_uses_left_edge_gesture(monkeypatch):
    import iphone_harness.helpers as iph

    monkeypatch.setattr(iph, "window_size", lambda: {"width": 390, "height": 844})
    captured = []
    monkeypatch.setattr(iph, "appium", lambda script, **kw: captured.append((script, kw)))

    iph.swipe_back()

    assert len(captured) == 1
    script, kw = captured[0]
    assert script == "mobile: dragFromToForDuration"
    assert kw["fromX"] <= 5
    assert kw["toX"] >= kw["fromX"] + 100
    assert kw["fromY"] == kw["toY"]


def test_ios_press_back_aliases_swipe_back(monkeypatch):
    import iphone_harness.helpers as iph

    monkeypatch.setattr(iph, "window_size", lambda: {"width": 390, "height": 844})
    captured = []
    monkeypatch.setattr(iph, "appium", lambda script, **kw: captured.append((script, kw)))

    iph.press_back()

    assert len(captured) == 1
    assert captured[0][0] == "mobile: dragFromToForDuration"


def test_ios_press_recents_opens_app_switcher(monkeypatch):
    import iphone_harness.helpers as iph

    monkeypatch.setattr(iph, "window_size", lambda: {"width": 390, "height": 844})
    monkeypatch.setattr(iph, "wait", lambda *a, **kw: None)
    captured = []
    monkeypatch.setattr(iph, "appium", lambda script, **kw: captured.append((script, kw)))

    iph.press_recents()

    assert len(captured) == 1
    script, kw = captured[0]
    assert script == "mobile: dragFromToForDuration"
    assert kw["fromY"] > kw["toY"]


def test_ios_open_app_switcher_is_same_as_press_recents(monkeypatch):
    import iphone_harness.helpers as iph

    monkeypatch.setattr(iph, "window_size", lambda: {"width": 390, "height": 844})
    monkeypatch.setattr(iph, "wait", lambda *a, **kw: None)
    captured = []
    monkeypatch.setattr(iph, "appium", lambda script, **kw: captured.append((script, kw)))

    iph.open_app_switcher()

    assert len(captured) == 1
    assert captured[0][0] == "mobile: dragFromToForDuration"


def test_android_button_helpers_unchanged():
    """Sanity: Android helpers we mirror still exist."""
    from android_harness.helpers import press_back, press_home, press_recents
    assert callable(press_back)
    assert callable(press_home)
    assert callable(press_recents)


def test_ios_helpers_match_android_api_surface():
    """Symmetry check: every Android button helper has an iOS equivalent."""
    import android_harness.helpers as anh
    import iphone_harness.helpers as iph

    for name in ("press_home", "press_back", "press_recents"):
        assert hasattr(anh, name), f"missing on android: {name}"
        assert hasattr(iph, name), f"missing on ios: {name}"
