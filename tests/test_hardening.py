"""Hardening tests — regression coverage for bugs found in goal/005 audit pass.

Each test below corresponds to an issue found by edge-case / coverage-audit /
approach-critic agents and must stay green to prevent regression.
"""
import pytest

from iphone_harness import admin as iph_admin
from iphone_harness import helpers as iph_helpers
from android_harness import admin as anh_admin
from android_harness import helpers as anh_helpers


# ---- _pid_alive rejects bool (isinstance(True, int) is True) --------------

def test_iph_pid_alive_rejects_True():
    """_pid_alive(True) must NOT delegate to os.kill(1, 0) (which would say PID 1 exists)."""
    assert iph_admin._pid_alive(True) is False


def test_iph_pid_alive_rejects_False():
    assert iph_admin._pid_alive(False) is False


def test_anh_pid_alive_rejects_True():
    assert anh_admin._pid_alive(True) is False


def test_anh_pid_alive_rejects_False():
    assert anh_admin._pid_alive(False) is False


def test_pid_alive_rejects_None():
    assert iph_admin._pid_alive(None) is False
    assert anh_admin._pid_alive(None) is False


def test_pid_alive_rejects_string():
    assert iph_admin._pid_alive("123") is False
    assert anh_admin._pid_alive("123") is False


def test_pid_alive_accepts_self_pid():
    """Sanity: real os.getpid() must return True."""
    import os
    assert iph_admin._pid_alive(os.getpid()) is True
    assert anh_admin._pid_alive(os.getpid()) is True


# ---- cleanup_stale tolerates corrupt PID files ----------------------------

def test_iph_cleanup_stale_handles_binary_garbage_pid(tmp_path, monkeypatch):
    """A PID file with non-UTF8 bytes should be treated as stale, not raise."""
    from iphone_harness import _ipc as iph_ipc
    pid_path = tmp_path / "iph.pid"
    pid_path.write_bytes(b"\xff\xfe\x80\x90")  # invalid UTF-8

    monkeypatch.setattr(iph_ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(iph_ipc, "sock_addr", lambda name: str(tmp_path / "iph.sock"))
    monkeypatch.setattr(iph_ipc, "ping", lambda name, timeout=0.3: False)

    cleaned = iph_admin.cleanup_stale("test")
    # Should remove the bogus PID file rather than raising UnicodeDecodeError
    assert not pid_path.exists()


def test_iph_cleanup_stale_handles_empty_pid_file(tmp_path, monkeypatch):
    """Empty PID file → int('') raises ValueError → must be treated as stale."""
    from iphone_harness import _ipc as iph_ipc
    pid_path = tmp_path / "iph.pid"
    pid_path.write_text("")

    monkeypatch.setattr(iph_ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(iph_ipc, "sock_addr", lambda name: str(tmp_path / "iph.sock"))
    monkeypatch.setattr(iph_ipc, "ping", lambda name, timeout=0.3: False)

    iph_admin.cleanup_stale("test")
    assert not pid_path.exists()


def test_anh_cleanup_stale_handles_binary_garbage(tmp_path, monkeypatch):
    from android_harness import _ipc as anh_ipc
    pid_path = tmp_path / "anh.pid"
    pid_path.write_bytes(b"\xc3\x28")

    monkeypatch.setattr(anh_ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(anh_ipc, "sock_addr", lambda name: str(tmp_path / "anh.sock"))
    monkeypatch.setattr(anh_ipc, "ping", lambda name, timeout=0.3: False)

    anh_admin.cleanup_stale("test")
    assert not pid_path.exists()


# ---- retry_on_disconnect propagates non-RuntimeError immediately ----------

def test_iph_retry_propagates_value_error_no_retry():
    calls = []

    @iph_helpers.retry_on_disconnect(max_attempts=3, backoff=0.01)
    def bad():
        calls.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        bad()
    assert len(calls) == 1, "retry_on_disconnect must not retry non-RuntimeError"


def test_iph_retry_propagates_type_error_no_retry():
    calls = []

    @iph_helpers.retry_on_disconnect(max_attempts=3, backoff=0.01)
    def bad():
        calls.append(1)
        raise TypeError("wrong type")

    with pytest.raises(TypeError):
        bad()
    assert len(calls) == 1


def test_anh_retry_propagates_value_error_no_retry():
    calls = []

    @anh_helpers.retry_on_disconnect(max_attempts=3, backoff=0.01)
    def bad():
        calls.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        bad()
    assert len(calls) == 1


# ---- wake_device returns False when unlock fails --------------------------

def test_iph_wake_device_returns_false_when_unlock_fails(monkeypatch):
    monkeypatch.setattr(iph_helpers, "is_locked", lambda: True)

    def fake_appium(script, **kw):
        raise RuntimeError("unlock failed")

    monkeypatch.setattr(iph_helpers, "appium", fake_appium)
    # Both unlock + pressButton fall paths fail
    assert iph_helpers.wake_device() is False


def test_iph_wake_device_returns_true_when_already_unlocked(monkeypatch):
    monkeypatch.setattr(iph_helpers, "is_locked", lambda: False)
    assert iph_helpers.wake_device() is True


def test_iph_wake_device_confirms_post_state(monkeypatch):
    """After 'mobile: unlock' returns, must re-check is_locked to confirm."""
    locked_states = [True, True]  # is_locked called twice: pre + post

    def fake_is_locked():
        return locked_states.pop(0) if locked_states else False

    monkeypatch.setattr(iph_helpers, "is_locked", fake_is_locked)
    monkeypatch.setattr(iph_helpers, "appium", lambda script, **kw: None)
    # unlock claimed success but device still locked → return False
    assert iph_helpers.wake_device() is False


def test_anh_wake_device_returns_false_when_unlock_fails(monkeypatch):
    monkeypatch.setattr(anh_helpers, "is_locked", lambda: True)
    monkeypatch.setattr(anh_helpers, "appium",
                        lambda script, **kw: (_ for _ in ()).throw(RuntimeError("fail")))
    assert anh_helpers.wake_device() is False


def test_anh_wake_device_returns_true_when_already_unlocked(monkeypatch):
    monkeypatch.setattr(anh_helpers, "is_locked", lambda: False)
    assert anh_helpers.wake_device() is True


# ---- record_screen rejects bad duration -----------------------------------

def test_iph_record_screen_rejects_zero_duration():
    with pytest.raises(ValueError, match="duration"):
        iph_helpers.record_screen(duration=0)


def test_iph_record_screen_rejects_negative_duration():
    with pytest.raises(ValueError, match="duration"):
        iph_helpers.record_screen(duration=-5)


def test_iph_record_screen_rejects_excessive_duration():
    with pytest.raises(ValueError, match="1800"):
        iph_helpers.record_screen(duration=3600)


def test_anh_record_screen_rejects_zero_duration():
    with pytest.raises(ValueError, match="duration"):
        anh_helpers.record_screen(duration=0)


def test_anh_record_screen_rejects_negative_duration():
    with pytest.raises(ValueError, match="duration"):
        anh_helpers.record_screen(duration=-5)


# ---- battery check does NOT fail doctor (regression) ----------------------

def test_iph_battery_low_returns_true_not_false():
    """Low battery should warn, not fail (otherwise doctor refuses to run)."""
    import subprocess
    from unittest.mock import patch
    import os
    with patch.dict(os.environ, {"IPH_UDID": "fake"}):
        with patch("shutil.which", return_value="/usr/local/bin/ideviceinfo"):
            with patch("subprocess.check_output", return_value=b"15\n"):
                ok, info = iph_admin._check_battery()
                assert ok is True, "battery check must not block doctor on low charge"
                assert "WARN" in info or "low" in info.lower()


def test_anh_battery_low_returns_true_not_false():
    from unittest.mock import patch
    with patch("shutil.which", return_value="/usr/local/bin/adb"):
        with patch("subprocess.check_output",
                   return_value=b"  level: 12\n  status: 3\n"):
            ok, info = anh_admin._check_battery()
            assert ok is True
            assert "WARN" in info or "low" in info.lower()


def test_iph_battery_full_returns_true():
    import subprocess
    from unittest.mock import patch
    import os
    with patch.dict(os.environ, {"IPH_UDID": "fake"}):
        with patch("shutil.which", return_value="/usr/local/bin/ideviceinfo"):
            with patch("subprocess.check_output", return_value=b"85\n"):
                ok, info = iph_admin._check_battery()
                assert ok is True
                assert "85" in info


# ---- record_replay restores helpers on exception path --------------------

def test_iph_retry_max_attempts_zero_raises():
    with pytest.raises(ValueError, match="max_attempts"):
        iph_helpers.retry_on_disconnect(max_attempts=0)


def test_iph_retry_negative_backoff_raises():
    with pytest.raises(ValueError, match="backoff"):
        iph_helpers.retry_on_disconnect(max_attempts=3, backoff=-1)


def test_anh_retry_max_attempts_zero_raises():
    with pytest.raises(ValueError, match="max_attempts"):
        anh_helpers.retry_on_disconnect(max_attempts=0)


def test_iph_record_screen_creates_parent_dir(tmp_path, monkeypatch):
    import base64
    encoded = base64.b64encode(b"x" * 32).decode()
    monkeypatch.setattr(iph_helpers, "appium",
                        lambda script, **kw: encoded if "stop" in script else None)
    monkeypatch.setattr(iph_helpers.time, "sleep", lambda *a: None)
    nested = tmp_path / "new" / "subdir" / "out.mp4"
    out = iph_helpers.record_screen(duration=1, path=str(nested))
    assert nested.exists()


def test_iph_record_screen_rejects_directory_path(tmp_path):
    with pytest.raises(IsADirectoryError):
        iph_helpers.record_screen(duration=1, path=str(tmp_path))


def test_anh_battery_handles_None_value(monkeypatch):
    from unittest.mock import patch
    with patch("shutil.which", return_value="/usr/local/bin/adb"):
        with patch("subprocess.check_output",
                   return_value=b"  level: None\n  status: 1\n"):
            ok, info = anh_admin._check_battery()
            assert ok is True
            assert "unreadable" in info or "None" in info


def test_anh_battery_handles_empty_value(monkeypatch):
    from unittest.mock import patch
    with patch("shutil.which", return_value="/usr/local/bin/adb"):
        with patch("subprocess.check_output",
                   return_value=b"  level: \n"):
            ok, info = anh_admin._check_battery()
            assert ok is True


def test_iph_battery_handles_garbage_output(monkeypatch):
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"IPH_UDID": "fake"}):
        with patch("shutil.which", return_value="/usr/local/bin/ideviceinfo"):
            with patch("subprocess.check_output", return_value=b"garbage\n"):
                ok, info = iph_admin._check_battery()
                assert ok is True
                assert "unreadable" in info or "skipped" in info


def test_record_replay_recording_context_manager(tmp_path):
    """recording() context manager guarantees stop_recording on exception."""
    import types
    from mobile_use.record_replay import recording

    mod = types.ModuleType("test_ctx")
    called = []
    mod.tap_at_xy = lambda x, y: called.append((x, y))
    original = mod.tap_at_xy

    with pytest.raises(ValueError):
        with recording(str(tmp_path / "out.py"), helpers=mod,
                       fn_names=("tap_at_xy",)):
            mod.tap_at_xy(1, 2)
            raise ValueError("simulated body failure")

    # Helper restored despite exception
    assert mod.tap_at_xy is original


def test_record_replay_rejects_non_module_helpers(tmp_path):
    """start_recording should reject dict or other non-module objects."""
    from mobile_use import record_replay
    with pytest.raises(TypeError, match="__name__"):
        record_replay.start_recording(str(tmp_path / "x.py"),
                                       helpers={"tap_at_xy": lambda *a: None})


def test_bootstrap_sudo_prefix_handles_missing_sudo(monkeypatch):
    """_sudo_prefix returns None on linux without sudo + not root."""
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda c: None)
    assert bootstrap._sudo_prefix() is None


def test_bootstrap_sudo_prefix_empty_when_root(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0)
    assert bootstrap._sudo_prefix() == []


def test_bootstrap_sudo_prefix_has_sudo(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda c: "/usr/bin/sudo")
    assert bootstrap._sudo_prefix() == ["sudo"]


def test_bootstrap_linux_install_returns_none_without_sudo(monkeypatch):
    """If sudo missing + not root, install commands must return None instead of failing later."""
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_sudo_prefix", lambda: None)
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    assert bootstrap._linux_adb_install_cmd() is None
    assert bootstrap._linux_node_install_cmd() is None


def test_bootstrap_linux_install_drops_sudo_when_root(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_sudo_prefix", lambda: [])
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    cmd = bootstrap._linux_adb_install_cmd()
    assert cmd[0] != "sudo"
    assert "apt" in cmd


def test_record_replay_restores_helpers_when_helper_call_raises(tmp_path):
    """If a helper raises during recording, originals must still be restored on stop."""
    import types
    from mobile_use import record_replay

    mod = types.ModuleType("test_helpers")

    def boom(*a, **kw):
        raise RuntimeError("simulated helper failure")

    def normal(*a, **kw):
        return None

    mod.tap_at_xy = boom
    mod.swipe = normal
    original_tap = mod.tap_at_xy
    original_swipe = mod.swipe

    record_replay.start_recording(str(tmp_path / "x.py"), helpers=mod,
                                  fn_names=("tap_at_xy", "swipe"))
    # Calling tap_at_xy raises — recording is still active.
    with pytest.raises(RuntimeError):
        mod.tap_at_xy(1, 2)
    # Stop should still restore both helpers
    record_replay.stop_recording()
    assert mod.tap_at_xy is original_tap
    assert mod.swipe is original_swipe
    assert record_replay.is_recording() is False
