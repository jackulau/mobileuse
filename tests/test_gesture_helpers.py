"""D17 — device-free unit tests for core gesture/text helper math.

These pure-logic paths (swipe speed, scroll_by clamping, tap None-guards,
tap_safe nav-bar avoidance) had zero coverage despite being device-free
testable by monkeypatching appium/window_size/tap_at_xy.
"""
import pytest

import android_harness.helpers as ah
import iphone_harness.helpers as ih


def _patch(monkeypatch, mod, w, h):
    calls = {"appium": [], "tap_at_xy": []}
    monkeypatch.setattr(mod, "appium", lambda script, **kw: calls["appium"].append((script, kw)) or True)
    monkeypatch.setattr(mod, "window_size", lambda: {"width": w, "height": h})
    monkeypatch.setattr(mod, "tap_at_xy", lambda x, y: calls["tap_at_xy"].append((x, y)))
    monkeypatch.setattr(mod, "wait", lambda *a, **k: None)
    return calls


# ---- tap None-guards (both platforms) -------------------------------------

@pytest.mark.parametrize("mod", [ah, ih])
def test_tap_none_raises(mod, monkeypatch):
    _patch(monkeypatch, mod, 1080, 1920)
    with pytest.raises(RuntimeError):
        mod.tap(None)
    with pytest.raises(RuntimeError):
        mod.tap_safe(None)


@pytest.mark.parametrize("mod", [ah, ih])
def test_tap_uses_element_center(mod, monkeypatch):
    calls = _patch(monkeypatch, mod, 1080, 1920)
    mod.tap({"cx": 123, "cy": 456})
    assert calls["tap_at_xy"] == [(123, 456)]


# ---- scroll_by clamp (both platforms share the formula) -------------------

@pytest.mark.parametrize("mod, w, h", [(ah, 1080, 1920), (ih, 390, 844)])
def test_scroll_by_clamps_target_y(mod, w, h, monkeypatch):
    calls = _patch(monkeypatch, mod, w, h)
    # A huge downward dy must clamp the target to >= 50, never negative/off-screen.
    mod.scroll_by(dy=-100000, x=10, y=h // 2)
    mod.scroll_by(dy=100000, x=10, y=h // 2)
    for script, kw in calls["appium"]:
        ty = kw.get("toY", kw.get("endY"))
        if ty is not None:
            assert 50 <= ty <= h - 50, f"target_y {ty} not clamped into [50, {h - 50}]"


# ---- android swipe speed floor --------------------------------------------

def test_android_swipe_speed_is_positive(monkeypatch):
    calls = _patch(monkeypatch, ah, 1080, 1920)
    ah.swipe(500, 800, 500, 400)            # normal swipe
    ah.swipe(100, 200, 100, 200)            # degenerate zero-distance swipe
    speeds = [kw["speed"] for _, kw in calls["appium"] if "speed" in kw]
    assert speeds, "swipe should emit a speed"
    assert all(s >= 1 for s in speeds), f"swipe speed must be a positive int, got {speeds}"


# ---- android tap_safe nav-bar avoidance -----------------------------------

def test_android_tap_safe_taps_directly_when_above_nav_bar(monkeypatch):
    calls = _patch(monkeypatch, ah, 1080, 1920)
    # Element well above the bottom nav bar zone → tapped at its own center.
    el = {"cx": 540, "cy": 800, "x": 400, "y": 760, "h": 80}
    ah.tap_safe(el)
    assert calls["tap_at_xy"] == [(540, 800)]


def test_android_tap_safe_lifts_tap_out_of_nav_bar_without_refind(monkeypatch):
    calls = _patch(monkeypatch, ah, 1080, 1920)
    # Element overlapping the nav-bar danger zone (y+h > height-48), no refind:
    # taps at a lifted safe_y = min(cy, y+20), never the raw cy in the danger zone.
    el = {"cx": 540, "cy": 1900, "x": 500, "y": 1890, "h": 60}
    ah.tap_safe(el)
    assert len(calls["tap_at_xy"]) == 1
    tapped_x, tapped_y = calls["tap_at_xy"][0]
    assert tapped_x == 540
    assert tapped_y == min(el["cy"], el["y"] + 20)
