"""IPC layer tests — exercise the AF_UNIX JSON-line protocol without a real daemon.

Spawns the mock daemon subprocesses (tests/_mock_iphone_daemon.py and
tests/_mock_android_daemon.py) which mirror the real daemon's IPC contract
minus Appium/device dependencies. Each test gets a unique IPH_NAME/ANH_NAME so
socket files at /tmp/iph-<name>.sock and /tmp/anh-<name>.sock don't collide.
"""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from iphone_harness import _ipc as iph_ipc
from android_harness import _ipc as anh_ipc


REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_alive(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _spawn_mock(platform, name):
    """Spawn mock iphone/android daemon. Returns Popen handle."""
    if platform == "iphone":
        module = "tests._mock_iphone_daemon"
        env_var = "IPH_NAME"
    else:
        module = "tests._mock_android_daemon"
        env_var = "ANH_NAME"
    env = {**os.environ, env_var: name}
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


def _cleanup_files(platform, name):
    prefix = "iph" if platform == "iphone" else "anh"
    for ext in ("sock", "pid", "log"):
        try:
            (Path("/tmp") / f"{prefix}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def iph_name():
    n = f"tst{uuid.uuid4().hex[:10]}"
    os.environ["IPH_NAME"] = n
    yield n
    _cleanup_files("iphone", n)
    os.environ.pop("IPH_NAME", None)


@pytest.fixture
def anh_name():
    n = f"tst{uuid.uuid4().hex[:10]}"
    os.environ["ANH_NAME"] = n
    yield n
    _cleanup_files("android", n)
    os.environ.pop("ANH_NAME", None)


@pytest.fixture
def iph_daemon(iph_name):
    p = _spawn_mock("iphone", iph_name)
    if not _wait_alive(iph_ipc, iph_name, timeout=5.0):
        p.kill()
        p.wait(timeout=2.0)
        pytest.fail("mock iphone daemon never came up")
    yield iph_name, p
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


@pytest.fixture
def anh_daemon(anh_name):
    p = _spawn_mock("android", anh_name)
    if not _wait_alive(anh_ipc, anh_name, timeout=5.0):
        p.kill()
        p.wait(timeout=2.0)
        pytest.fail("mock android daemon never came up")
    yield anh_name, p
    try:
        s, _ = anh_ipc.connect(anh_name, timeout=1.0)
        anh_ipc.request(s, None, {"meta": "shutdown"})
        s.close()
    except Exception:
        pass
    try:
        p.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=2.0)


# ---- iphone --------------------------------------------------------------

def test_ping_returns_false_when_no_daemon(iph_name):
    assert iph_ipc.ping(iph_name, timeout=0.3) is False


def test_identify_returns_none_when_no_daemon(iph_name):
    assert iph_ipc.identify(iph_name, timeout=0.3) is None


def test_ping_returns_true_when_daemon_alive(iph_daemon):
    name, _ = iph_daemon
    assert iph_ipc.ping(name, timeout=1.0) is True


def test_identify_returns_pid_when_daemon_alive(iph_daemon):
    name, p = iph_daemon
    pid = iph_ipc.identify(name, timeout=1.0)
    assert pid == p.pid


def test_request_roundtrip_basic(iph_daemon):
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        resp = iph_ipc.request(s, token, {"method": "window_size", "params": {}})
        assert "result" in resp
        assert resp["result"]["width"] == 390
    finally:
        s.close()


def test_request_appium_passthrough(iph_daemon):
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        resp = iph_ipc.request(s, token, {
            "method": "appium",
            "params": {"script": "mobile: activeAppInfo", "args": {}},
        })
        assert resp["result"]["bundleId"] == "com.apple.springboard"
    finally:
        s.close()


def test_request_unknown_method_returns_error(iph_daemon):
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        resp = iph_ipc.request(s, token, {"method": "no_such_method", "params": {}})
        assert "error" in resp
        assert "unknown method" in resp["error"]
    finally:
        s.close()


def test_request_missing_method_returns_error(iph_daemon):
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        resp = iph_ipc.request(s, token, {"params": {}})
        assert "error" in resp
        assert "missing method" in resp["error"]
    finally:
        s.close()


def test_request_server_side_raise_returns_error(iph_daemon):
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        resp = iph_ipc.request(s, token, {"method": "raise", "params": {}})
        assert "error" in resp
        assert "intentional crash" in resp["error"]
    finally:
        s.close()


def test_malformed_json_request_does_not_crash_daemon(iph_daemon):
    name, _ = iph_daemon
    s, _ = iph_ipc.connect(name, timeout=1.0)
    try:
        s.sendall(b"this is not json\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        resp = json.loads(data)
        assert "error" in resp
        assert "bad json" in resp["error"]
    finally:
        s.close()
    # Daemon still alive after.
    assert iph_ipc.ping(name, timeout=1.0) is True


def test_shutdown_via_ipc_stops_daemon(iph_name):
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name, timeout=5.0)
        s, _ = iph_ipc.connect(iph_name, timeout=1.0)
        resp = iph_ipc.request(s, None, {"meta": "shutdown"})
        s.close()
        assert resp == {"ok": True}
        # Daemon should exit within a few seconds.
        try:
            p.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            p.kill()
            pytest.fail("daemon didn't exit after shutdown")
        assert iph_ipc.ping(iph_name, timeout=0.5) is False
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


def test_invalid_name_rejected_iphone():
    with pytest.raises(ValueError):
        iph_ipc.sock_addr("../etc/passwd")
    with pytest.raises(ValueError):
        iph_ipc.sock_addr("name with spaces")
    with pytest.raises(ValueError):
        iph_ipc.sock_addr("")


def test_socket_file_removed_after_shutdown(iph_name):
    p = _spawn_mock("iphone", iph_name)
    try:
        assert _wait_alive(iph_ipc, iph_name, timeout=5.0)
        sock_path = Path(iph_ipc.sock_addr(iph_name))
        assert sock_path.exists()
        s, _ = iph_ipc.connect(iph_name, timeout=1.0)
        iph_ipc.request(s, None, {"meta": "shutdown"})
        s.close()
        p.wait(timeout=5.0)
        # Mock daemon's serve() calls cleanup_endpoint in its finally.
        assert not sock_path.exists()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)


def test_connect_fails_fast_when_no_daemon(iph_name):
    """connect() should raise quickly, not hang."""
    t0 = time.time()
    with pytest.raises((FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError)):
        iph_ipc.connect(iph_name, timeout=0.5)
    elapsed = time.time() - t0
    assert elapsed < 2.0


# ---- android --------------------------------------------------------------

def test_android_ping_no_daemon(anh_name):
    assert anh_ipc.ping(anh_name, timeout=0.3) is False


def test_android_daemon_alive(anh_daemon):
    name, _ = anh_daemon
    assert anh_ipc.ping(name, timeout=1.0) is True


def test_android_identify_returns_pid(anh_daemon):
    name, p = anh_daemon
    pid = anh_ipc.identify(name, timeout=1.0)
    assert pid == p.pid


def test_android_request_roundtrip(anh_daemon):
    name, _ = anh_daemon
    s, token = anh_ipc.connect(name, timeout=1.0)
    try:
        resp = anh_ipc.request(s, token, {"method": "window_size", "params": {}})
        assert resp["result"]["width"] == 1080
    finally:
        s.close()


def test_android_invalid_name_rejected():
    with pytest.raises(ValueError):
        anh_ipc.sock_addr("../etc/passwd")
    with pytest.raises(ValueError):
        anh_ipc.sock_addr("")


def test_iph_ipc_caps_oversized_response(iph_daemon, monkeypatch):
    """IPC client should reject responses larger than _MAX_MSG (defense in depth)."""
    name, _ = iph_daemon
    s, token = iph_ipc.connect(name, timeout=1.0)
    try:
        # Temporarily lower the cap so we can trigger it without 64MB of data
        monkeypatch.setattr(iph_ipc, "_MAX_MSG", 100)
        # Ask the mock daemon for something whose JSON-encoded result exceeds 100 bytes.
        # `page_source` returns an XML string from the mock — small but request adds overhead.
        # Send a request that would yield a >100-byte JSON response.
        with pytest.raises(RuntimeError, match="exceeded.*MB cap"):
            iph_ipc.request(s, token, {
                "method": "appium",
                "params": {"script": "x" * 200, "args": {}},
            })
    finally:
        try:
            s.close()
        except Exception:
            pass


def test_anh_ipc_caps_oversized_response(anh_daemon, monkeypatch):
    name, _ = anh_daemon
    s, token = anh_ipc.connect(name, timeout=1.0)
    try:
        # Drop the cap below any plausible response — even a 60-byte appium reply trips it.
        monkeypatch.setattr(anh_ipc, "_MAX_MSG", 10)
        with pytest.raises(RuntimeError, match="exceeded.*MB cap"):
            anh_ipc.request(s, token, {"method": "appium", "params": {}})
    finally:
        try:
            s.close()
        except Exception:
            pass


def test_android_iphone_namespaces_separate(iph_name, anh_name):
    """iOS and Android socket namespaces must not collide for the same name."""
    # Same name but different prefixes — should produce different paths.
    assert iph_ipc.sock_addr(iph_name) != anh_ipc.sock_addr(anh_name)
    # Even with the SAME name argument, prefixes differ:
    same_name = "shared"
    assert iph_ipc.sock_addr(same_name).endswith(f"iph-{same_name}.sock")
    assert anh_ipc.sock_addr(same_name).endswith(f"anh-{same_name}.sock")
