"""goal/022 D4 — pre-act dismiss gating (MU_PREACT_DISMISS).

act() used to spend a real device round-trip on auto_dismiss_dialog() before
EVERY action, discarding the alert state the same-step snapshot had already
carried. Default 'snapshot' mode skips it when the fresh perceive showed no
alert; 'always' restores the old behavior; 'off' never pre-dismisses.
"""
import sys
import time
import types

import pytest


def _fake_helpers(alert=None):
    m = types.ModuleType("fake_helpers")
    dismissals = []
    taps = []

    def snapshot(visible_only=True):
        return {"screenshot_path": "/x.png", "ui_tree": [],
                "active_app": {"bundleId": "com.example"},
                "window_size": {"width": 390, "height": 844}, "alert": alert}

    def auto_dismiss_dialog():
        dismissals.append(1)
        return alert is not None

    def tap_at_xy(x, y):
        taps.append((x, y))
        return True

    for fn in (snapshot, auto_dismiss_dialog, tap_at_xy):
        setattr(m, fn.__name__, fn)
    m._dismissals = dismissals
    m._taps = taps
    return m


def _loop(monkeypatch, tmp_path, alert=None):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path / "sessions")
    h = _fake_helpers(alert=alert)
    a = types.ModuleType("fake_admin")
    a.ensure_daemon = lambda *args, **kw: True
    import importlib
    parent = importlib.import_module("iphone_harness")
    monkeypatch.setitem(sys.modules, "iphone_harness.helpers", h)
    monkeypatch.setitem(sys.modules, "iphone_harness.admin", a)
    monkeypatch.setattr(parent, "helpers", h, raising=False)
    monkeypatch.setattr(parent, "admin", a, raising=False)
    from mobile_use.agent_loop import AgentLoop
    return AgentLoop(platform="ios", session_name="dismiss-test",
                     collect=False), h


def test_snapshot_mode_skips_dismiss_when_clean(monkeypatch, tmp_path):
    monkeypatch.delenv("MU_PREACT_DISMISS", raising=False)
    loop, h = _loop(monkeypatch, tmp_path, alert=None)
    loop.perceive()
    loop.act("tap_at_xy", x=1, y=2)
    assert h._dismissals == [], "clean fresh snapshot must skip the round-trip"
    assert h._taps == [(1, 2)]


def test_snapshot_mode_dismisses_when_alert_present(monkeypatch, tmp_path):
    monkeypatch.delenv("MU_PREACT_DISMISS", raising=False)
    loop, h = _loop(monkeypatch, tmp_path,
                    alert={"label": "Allow notifications?"})
    loop.perceive()
    loop.act("tap_at_xy", x=1, y=2)
    assert len(h._dismissals) == 1


def test_snapshot_mode_dismisses_without_prior_perceive(monkeypatch, tmp_path):
    """Direct act() with no perception this run -> no knowledge -> fail safe."""
    monkeypatch.delenv("MU_PREACT_DISMISS", raising=False)
    loop, h = _loop(monkeypatch, tmp_path, alert=None)
    loop.act("tap_at_xy", x=1, y=2)
    assert len(h._dismissals) == 1


def test_snapshot_mode_dismisses_when_stale(monkeypatch, tmp_path):
    monkeypatch.delenv("MU_PREACT_DISMISS", raising=False)
    loop, h = _loop(monkeypatch, tmp_path, alert=None)
    loop.perceive()
    loop._last_perceive_t = time.monotonic() - 120.0   # simulate old snapshot
    loop.act("tap_at_xy", x=1, y=2)
    assert len(h._dismissals) == 1, "stale snapshot knowledge must fail safe"


def test_always_mode_restores_old_behavior(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_PREACT_DISMISS", "always")
    loop, h = _loop(monkeypatch, tmp_path, alert=None)
    loop.perceive()
    loop.act("tap_at_xy", x=1, y=2)
    loop.act("tap_at_xy", x=3, y=4)
    assert len(h._dismissals) == 2


def test_off_mode_never_dismisses(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_PREACT_DISMISS", "off")
    loop, h = _loop(monkeypatch, tmp_path, alert={"label": "Alert!"})
    loop.act("tap_at_xy", x=1, y=2)
    assert h._dismissals == []


def test_unknown_mode_falls_back_to_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_PREACT_DISMISS", "bogus")
    loop, h = _loop(monkeypatch, tmp_path, alert=None)
    loop.perceive()
    loop.act("tap_at_xy", x=1, y=2)
    assert h._dismissals == []
    loop2, h2 = _loop(monkeypatch, tmp_path, alert=None)
    loop2.act("tap_at_xy", x=1, y=2)   # no perceive -> safe path
    assert len(h2._dismissals) == 1
