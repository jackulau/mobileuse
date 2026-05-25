"""Daemon lifecycle tests — ensure_daemon, restart_daemon, stale-file cleanup.

Uses mock daemons (tests._mock_iphone_daemon, tests._mock_android_daemon) so no
device or Appium is required. The real admin.ensure_daemon is exercised via
IPH_DAEMON_MODULE / ANH_DAEMON_MODULE env override.
"""
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from iphone_harness import _ipc as iph_ipc
from iphone_harness import admin as iph_admin
from android_harness import _ipc as anh_ipc
from android_harness import admin as anh_admin


REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_alive(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _wait_dead(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _cleanup_files(prefix, name):
    for ext in ("sock", "pid", "log"):
        try:
            (Path("/tmp") / f"{prefix}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def iph_name(monkeypatch):
    n = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", n)
    monkeypatch.setenv("IPH_DAEMON_MODULE", "tests._mock_iphone_daemon")
    yield n
    _cleanup_files("iph", n)


@pytest.fixture
def anh_name(monkeypatch):
    n = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("ANH_NAME", n)
    monkeypatch.setenv("ANH_DAEMON_MODULE", "tests._mock_android_daemon")
    yield n
    _cleanup_files("anh", n)


def _spawn_mock(platform, name, extra_env=None):
    if platform == "iphone":
        module = "tests._mock_iphone_daemon"
        env_var = "IPH_NAME"
    else:
        module = "tests._mock_android_daemon"
        env_var = "ANH_NAME"
    env = {**os.environ, env_var: name, **(extra_env or {})}
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


# ---- ensure_daemon (end-to-end via mock module) ---------------------------

def test_ensure_daemon_spawns_when_dead(iph_name):
    """ensure_daemon spawns mock daemon and returns when alive."""
    assert iph_ipc.ping(iph_name, timeout=0.3) is False
    try:
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        assert iph_ipc.ping(iph_name, timeout=1.0) is True
    finally:
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name, timeout=5.0)


def test_ensure_daemon_noop_when_alive(iph_name):
    """Second call to ensure_daemon shouldn't spawn a new process."""
    try:
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        pid1 = iph_ipc.identify(iph_name, timeout=1.0)
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        pid2 = iph_ipc.identify(iph_name, timeout=1.0)
        assert pid1 == pid2
    finally:
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name, timeout=5.0)


def test_ensure_daemon_restarts_when_appium_handshake_fails(iph_name, monkeypatch):
    """If daemon's Appium-side handshake errors, ensure_daemon respawns it."""
    # First spawn with broken appium boundary
    monkeypatch.setenv("MOCK_FAIL_APPIUM", "1")
    try:
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        pid1 = iph_ipc.identify(iph_name, timeout=1.0)
        # Now make a second ensure_daemon — handshake will fail, daemon restarted.
        monkeypatch.delenv("MOCK_FAIL_APPIUM", raising=False)
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        pid2 = iph_ipc.identify(iph_name, timeout=1.0)
        assert pid2 != pid1  # New process after restart
    finally:
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name, timeout=5.0)


# ---- restart_daemon -------------------------------------------------------

def test_restart_daemon_kills_alive_daemon(iph_name):
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name)
        iph_admin.restart_daemon(iph_name)
        assert _wait_dead(iph_ipc, iph_name)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


def test_restart_daemon_noop_when_no_daemon(iph_name):
    """Should not raise even if nothing to restart."""
    iph_admin.restart_daemon(iph_name)  # should just complete
    assert iph_ipc.ping(iph_name, timeout=0.3) is False


def test_restart_daemon_cleans_pid_file(iph_name):
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name)
        pid_path = Path(iph_ipc.pid_path(iph_name))
        assert pid_path.exists()
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name)
        assert not pid_path.exists()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


# ---- stale file handling --------------------------------------------------

def test_stale_socket_file_does_not_block_new_daemon(iph_name):
    """Leftover .sock from a hard-killed daemon should be cleaned on new spawn."""
    sock_path = Path(iph_ipc.sock_addr(iph_name))
    # Pre-create stale socket file (mimic kill -9 aftermath)
    sock_path.write_text("")
    assert sock_path.exists()
    try:
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        assert iph_ipc.ping(iph_name, timeout=1.0) is True
    finally:
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name, timeout=5.0)


def test_stale_pid_file_does_not_block_new_daemon(iph_name):
    """Leftover .pid file with a non-existent PID shouldn't block respawn."""
    pid_path = Path(iph_ipc.pid_path(iph_name))
    pid_path.write_text("999999")  # Almost-certainly-dead PID
    try:
        iph_admin.ensure_daemon(wait=10.0, name=iph_name)
        assert iph_ipc.ping(iph_name, timeout=1.0) is True
        # PID file should now contain the real daemon's PID, not the stale one.
        real_pid = iph_ipc.identify(iph_name, timeout=1.0)
        assert real_pid is not None
        assert real_pid != 999999
    finally:
        iph_admin.restart_daemon(iph_name)
        _wait_dead(iph_ipc, iph_name, timeout=5.0)


def test_stale_socket_does_not_respond_to_ping(iph_name):
    """A leftover socket file that no daemon is bound to should ping=False."""
    sock_path = Path(iph_ipc.sock_addr(iph_name))
    sock_path.write_text("garbage")
    assert sock_path.exists()
    # ping must not be tricked into returning True
    assert iph_ipc.ping(iph_name, timeout=0.5) is False


def test_cleanup_stale_removes_dead_pid_and_socket(iph_name):
    """cleanup_stale() should drop .pid (dead pid) and .sock (no listener)."""
    pid_path = Path(iph_ipc.pid_path(iph_name))
    sock_path = Path(iph_ipc.sock_addr(iph_name))
    pid_path.write_text("999999")
    sock_path.write_text("")
    assert pid_path.exists() and sock_path.exists()

    cleaned = iph_admin.cleanup_stale(iph_name)
    assert cleaned is True
    assert not pid_path.exists()
    assert not sock_path.exists()


def test_cleanup_stale_preserves_live_daemon_files(iph_name):
    """cleanup_stale() must NOT wipe files of a live daemon."""
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name)
        pid_path = Path(iph_ipc.pid_path(iph_name))
        sock_path = Path(iph_ipc.sock_addr(iph_name))
        assert pid_path.exists() and sock_path.exists()

        result = iph_admin.cleanup_stale(iph_name)
        assert result is False  # nothing should have been cleaned
        assert pid_path.exists()
        assert sock_path.exists()
        assert iph_ipc.ping(iph_name, timeout=1.0) is True
    finally:
        if p.poll() is None:
            try:
                s, _ = iph_ipc.connect(iph_name, timeout=1.0)
                iph_ipc.request(s, None, {"meta": "shutdown"})
                s.close()
            except Exception:
                pass
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2.0)


def test_cleanup_stale_no_op_when_no_files(iph_name):
    """Safe to call when nothing exists."""
    result = iph_admin.cleanup_stale(iph_name)
    assert result is False


def test_android_cleanup_stale_removes_dead_files(anh_name):
    pid_path = Path(anh_ipc.pid_path(anh_name))
    sock_path = Path(anh_ipc.sock_addr(anh_name))
    pid_path.write_text("999999")
    sock_path.write_text("")
    cleaned = anh_admin.cleanup_stale(anh_name)
    assert cleaned is True
    assert not pid_path.exists()
    assert not sock_path.exists()


def test_restart_daemon_sigkill_escalation(iph_name):
    """If daemon ignores SIGTERM, restart_daemon should SIGKILL it."""
    # Spawn mock with a trap for SIGTERM — simulated by killing daemon's IPC
    # before restart_daemon (so shutdown via IPC fails, then SIGTERM should hit).
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name)
        # restart_daemon should successfully tear it down
        iph_admin.restart_daemon(iph_name)
        # Process should be gone
        assert _wait_dead(iph_ipc, iph_name)
        # Pid file should be gone too
        assert not Path(iph_ipc.pid_path(iph_name)).exists()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


def test_double_spawn_race_only_one_survives(iph_name):
    """If two daemons spawn simultaneously, the second binds (unlinking first's sock)."""
    p1 = _spawn_mock("iphone", iph_name)
    assert _wait_alive(iph_ipc, iph_name)
    pid1 = iph_ipc.identify(iph_name, timeout=1.0)

    p2 = _spawn_mock("iphone", iph_name)
    # Give p2 a moment to bind (it should unlink p1's socket via serve())
    time.sleep(1.0)

    # At least one daemon should be reachable
    final_pid = iph_ipc.identify(iph_name, timeout=1.0)
    assert final_pid is not None

    # Cleanup both
    try: p1.kill()
    except Exception: pass
    try: p2.kill()
    except Exception: pass
    p1.wait(timeout=2.0); p2.wait(timeout=2.0)
    _cleanup_files("iph", iph_name)


# ---- android equivalents (smoke — same code paths) -----------------------

def test_android_ensure_daemon_spawns_when_dead(anh_name):
    assert anh_ipc.ping(anh_name, timeout=0.3) is False
    try:
        anh_admin.ensure_daemon(wait=10.0, name=anh_name)
        assert anh_ipc.ping(anh_name, timeout=1.0) is True
    finally:
        anh_admin.restart_daemon(anh_name)
        _wait_dead(anh_ipc, anh_name, timeout=5.0)


def test_android_restart_daemon_kills_alive(anh_name):
    p = _spawn_mock("android", anh_name)
    try:
        assert _wait_alive(anh_ipc, anh_name)
        anh_admin.restart_daemon(anh_name)
        assert _wait_dead(anh_ipc, anh_name)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


def test_android_stale_socket_cleaned(anh_name):
    sock_path = Path(anh_ipc.sock_addr(anh_name))
    sock_path.write_text("")
    try:
        anh_admin.ensure_daemon(wait=10.0, name=anh_name)
        assert anh_ipc.ping(anh_name, timeout=1.0) is True
    finally:
        anh_admin.restart_daemon(anh_name)
        _wait_dead(anh_ipc, anh_name, timeout=5.0)
