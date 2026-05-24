"""Device disconnect / sleep / lock recovery tests.

Exercise the @retry_on_disconnect decorator and wake_device helper via
boundary-mocked _send. No real device or Appium needed.
"""
from unittest import mock

import pytest

from iphone_harness import helpers as iph
from android_harness import helpers as anh


# ---- @retry_on_disconnect (iOS) -------------------------------------------

def test_iph_retry_succeeds_after_transient_disconnect(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("daemon unreachable")
        return "ok"

    # Skip daemon restart/wake side-effects in tests
    monkeypatch.setattr(iph, "wake_device", lambda: False)
    from iphone_harness import admin as iph_admin
    monkeypatch.setattr(iph_admin, "restart_daemon", lambda *a, **kw: None)
    monkeypatch.setattr(iph_admin, "ensure_daemon", lambda *a, **kw: None)

    wrapped = iph.retry_on_disconnect(max_attempts=3, backoff=0.01)(flaky)
    assert wrapped() == "ok"
    assert calls["n"] == 3


def test_iph_retry_raises_disconnect_error_after_max_attempts(monkeypatch):
    monkeypatch.setattr(iph, "wake_device", lambda: False)
    from iphone_harness import admin as iph_admin
    monkeypatch.setattr(iph_admin, "restart_daemon", lambda *a, **kw: None)
    monkeypatch.setattr(iph_admin, "ensure_daemon", lambda *a, **kw: None)

    def always_dies():
        raise RuntimeError("connection lost")

    wrapped = iph.retry_on_disconnect(max_attempts=2, backoff=0.01)(always_dies)
    with pytest.raises(iph.DeviceDisconnectError):
        wrapped()


def test_iph_retry_does_not_swallow_unrelated_errors(monkeypatch):
    monkeypatch.setattr(iph, "wake_device", lambda: False)

    def bad():
        raise RuntimeError("invalid argument")  # NOT a disconnect pattern

    wrapped = iph.retry_on_disconnect(max_attempts=3, backoff=0.01)(bad)
    with pytest.raises(RuntimeError, match="invalid argument"):
        wrapped()


def test_iph_retry_preserves_function_metadata():
    @iph.retry_on_disconnect(max_attempts=2)
    def my_named_function():
        """docstring"""
        return 1

    assert my_named_function.__name__ == "my_named_function"
    assert my_named_function.__doc__ == "docstring"


def test_iph_is_locked_handles_exception(monkeypatch):
    """is_locked() should return False rather than raising if appium call fails."""
    def boom(*a, **kw):
        raise RuntimeError("appium not available")
    monkeypatch.setattr(iph, "appium", boom)
    assert iph.is_locked() is False


def test_iph_wake_device_noop_when_unlocked(monkeypatch):
    """Already-unlocked returns True (post-state confirmed unlocked)."""
    monkeypatch.setattr(iph, "is_locked", lambda: False)
    assert iph.wake_device() is True


def test_iph_wake_device_calls_unlock_when_locked(monkeypatch):
    """Locked → unlock called → post-state checked → True iff unlocked after."""
    states = iter([True, False])  # locked at start, unlocked after unlock()
    monkeypatch.setattr(iph, "is_locked", lambda: next(states))
    called = {}

    def fake_appium(script, **kw):
        called["script"] = script
        return None

    monkeypatch.setattr(iph, "appium", fake_appium)
    assert iph.wake_device() is True
    assert called["script"] == "mobile: unlock"


# ---- @retry_on_disconnect (Android) ---------------------------------------

def test_anh_retry_succeeds_after_transient_disconnect(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("adb session dropped")
        return "ok"

    monkeypatch.setattr(anh, "wake_device", lambda: False)
    from android_harness import admin as anh_admin
    monkeypatch.setattr(anh_admin, "restart_daemon", lambda *a, **kw: None)
    monkeypatch.setattr(anh_admin, "ensure_daemon", lambda *a, **kw: None)

    wrapped = anh.retry_on_disconnect(max_attempts=3, backoff=0.01)(flaky)
    assert wrapped() == "ok"
    assert calls["n"] == 3


def test_anh_retry_raises_disconnect_error_after_max_attempts(monkeypatch):
    monkeypatch.setattr(anh, "wake_device", lambda: False)
    from android_harness import admin as anh_admin
    monkeypatch.setattr(anh_admin, "restart_daemon", lambda *a, **kw: None)
    monkeypatch.setattr(anh_admin, "ensure_daemon", lambda *a, **kw: None)

    def always_dies():
        raise RuntimeError("uiautomator2 timed out")

    wrapped = anh.retry_on_disconnect(max_attempts=2, backoff=0.01)(always_dies)
    with pytest.raises(anh.DeviceDisconnectError):
        wrapped()


def test_anh_retry_does_not_swallow_unrelated_errors(monkeypatch):
    monkeypatch.setattr(anh, "wake_device", lambda: False)

    def bad():
        raise RuntimeError("permission denied")

    wrapped = anh.retry_on_disconnect(max_attempts=3, backoff=0.01)(bad)
    with pytest.raises(RuntimeError, match="permission denied"):
        wrapped()


def test_anh_is_locked_handles_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("appium not available")
    monkeypatch.setattr(anh, "appium", boom)
    assert anh.is_locked() is False


def test_anh_wake_device_noop_when_unlocked(monkeypatch):
    """Already-unlocked → True (no work needed; post-state is unlocked)."""
    monkeypatch.setattr(anh, "is_locked", lambda: False)
    assert anh.wake_device() is True


def test_anh_wake_device_calls_unlock_when_locked(monkeypatch):
    states = iter([True, False])  # locked → unlock → unlocked
    monkeypatch.setattr(anh, "is_locked", lambda: next(states))
    called = {}

    def fake_appium(script, **kw):
        called["script"] = script
        return None

    monkeypatch.setattr(anh, "appium", fake_appium)
    assert anh.wake_device() is True
    assert called["script"] == "mobile: unlock"


def test_both_platforms_export_recovery_api():
    """Public API is consistent across iOS and Android."""
    for mod in (iph, anh):
        assert callable(getattr(mod, "retry_on_disconnect"))
        assert callable(getattr(mod, "wake_device"))
        assert callable(getattr(mod, "is_locked"))
        assert issubclass(getattr(mod, "DeviceDisconnectError"), RuntimeError)
