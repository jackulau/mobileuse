"""goal/022 D7 — gesture-settle tunables (IPH_GESTURE_SETTLE / ANH_GESTURE_SETTLE).

The act path carried fixed sleeps (iOS scroll_by 1.2s + tap_safe 0.6s/iter +
app-switcher 0.6s; Android scroll_by 0.8s + dialog-dismiss 0.5s). They are now
scaled by a per-platform multiplier env — default 1.0 keeps stock real-device
timing exactly; 0 disables (emulators/CI with animations off). Tests capture
the REQUESTED sleep durations via monkeypatch — wall-clock is never measured.
"""
import pytest

import android_harness.helpers as ah
import iphone_harness.helpers as ih


@pytest.mark.parametrize("mod,env", [(ih, "IPH_GESTURE_SETTLE"),
                                     (ah, "ANH_GESTURE_SETTLE")])
def test_settle_scale_parses_and_clamps(monkeypatch, mod, env):
    monkeypatch.delenv(env, raising=False)
    assert mod._settle_scale() == 1.0          # default = stock timing
    monkeypatch.setenv(env, "0.25")
    assert mod._settle_scale() == 0.25
    monkeypatch.setenv(env, "0")
    assert mod._settle_scale() == 0.0          # 0 allowed: no settle at all
    monkeypatch.setenv(env, "-3")
    assert mod._settle_scale() == 1.0          # negative -> stock
    monkeypatch.setenv(env, "garbage")
    assert mod._settle_scale() == 1.0          # unparseable -> stock


@pytest.mark.parametrize("mod,env", [(ih, "IPH_GESTURE_SETTLE"),
                                     (ah, "ANH_GESTURE_SETTLE")])
def test_settle_requests_scaled_duration(monkeypatch, mod, env):
    slept = []
    monkeypatch.setattr(mod, "wait", lambda s=1.0: slept.append(s))
    monkeypatch.setenv(env, "0.5")
    mod._settle(1.2)
    assert slept == [pytest.approx(0.6)]


@pytest.mark.parametrize("mod,env", [(ih, "IPH_GESTURE_SETTLE"),
                                     (ah, "ANH_GESTURE_SETTLE")])
def test_settle_zero_skips_sleep_entirely(monkeypatch, mod, env):
    slept = []
    monkeypatch.setattr(mod, "wait", lambda s=1.0: slept.append(s))
    monkeypatch.setenv(env, "0")
    mod._settle(1.2)
    assert slept == [], "scale 0 must not sleep at all"


def test_android_scroll_by_settles_scaled(monkeypatch):
    slept = []
    monkeypatch.setattr(ah, "wait", lambda s=1.0: slept.append(s))
    monkeypatch.setattr(ah, "appium", lambda *a, **kw: True)
    monkeypatch.setattr(ah, "window_size", lambda: {"width": 400, "height": 800})
    monkeypatch.setenv("ANH_GESTURE_SETTLE", "0.25")
    ah.scroll_by(dy=-200)
    assert slept == [pytest.approx(0.2)]       # 0.8 * 0.25


def test_ios_scroll_by_settles_scaled(monkeypatch):
    slept = []
    monkeypatch.setattr(ih, "wait", lambda s=1.0: slept.append(s))
    monkeypatch.setattr(ih, "appium", lambda *a, **kw: True)
    monkeypatch.setattr(ih, "window_size", lambda: {"width": 390, "height": 844})
    monkeypatch.setenv("IPH_GESTURE_SETTLE", "0.5")
    ih.scroll_by(dy=-200)
    assert slept == [pytest.approx(0.6)]       # 1.2 * 0.5


def test_default_scale_keeps_stock_durations(monkeypatch):
    """No env set -> the exact pre-022 sleep durations are requested."""
    slept = []
    monkeypatch.setattr(ah, "wait", lambda s=1.0: slept.append(s))
    monkeypatch.setattr(ah, "appium", lambda *a, **kw: True)
    monkeypatch.setattr(ah, "window_size", lambda: {"width": 400, "height": 800})
    monkeypatch.delenv("ANH_GESTURE_SETTLE", raising=False)
    ah.scroll_by(dy=-100)
    assert slept == [pytest.approx(0.8)]
