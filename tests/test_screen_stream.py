"""Screen stream RPC tests — exercises screen_stream_{start,frame,stop} via the
mock iphone + android daemons. The mock returns a synthetic 67-byte JPEG stub
so tests don't need PIL/a real device.
"""
import base64
import os
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
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT), start_new_session=True,
    )


def _cleanup(platform, name):
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
    _cleanup("iphone", n)
    os.environ.pop("IPH_NAME", None)


@pytest.fixture
def anh_name():
    n = f"tst{uuid.uuid4().hex[:10]}"
    os.environ["ANH_NAME"] = n
    yield n
    _cleanup("android", n)
    os.environ.pop("ANH_NAME", None)


@pytest.fixture
def iph_daemon(iph_name):
    p = _spawn_mock("iphone", iph_name)
    if not _wait_alive(iph_ipc, iph_name, timeout=5.0):
        p.kill(); p.wait(timeout=2.0)
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
        p.kill(); p.wait(timeout=2.0)


@pytest.fixture
def anh_daemon(anh_name):
    p = _spawn_mock("android", anh_name)
    if not _wait_alive(anh_ipc, anh_name, timeout=5.0):
        p.kill(); p.wait(timeout=2.0)
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
        p.kill(); p.wait(timeout=2.0)


def _call(ipc_mod, name, method, params=None):
    s, t = ipc_mod.connect(name, timeout=1.0)
    try:
        return ipc_mod.request(s, t, {"method": method, "params": params or {}})
    finally:
        s.close()


# ---- iPhone screen_stream RPC --------------------------------------------

def test_screen_stream_start_iphone(iph_daemon):
    name, _ = iph_daemon
    r = _call(iph_ipc, name, "screen_stream_start", {"fps": 4, "quality": 70})
    assert r["result"]["running"] is True
    assert r["result"]["started"] is True
    assert r["result"]["fps"] == 4
    assert r["result"]["quality"] == 70


def test_screen_stream_frame_not_ready_until_start_iphone(iph_daemon):
    name, _ = iph_daemon
    r = _call(iph_ipc, name, "screen_stream_frame", {})
    assert r["result"]["ready"] is False
    assert r["result"]["frame_no"] == 0


def test_screen_stream_frame_after_start_iphone(iph_daemon):
    name, _ = iph_daemon
    _call(iph_ipc, name, "screen_stream_start", {})
    r = _call(iph_ipc, name, "screen_stream_frame", {})
    assert r["result"]["ready"] is True
    assert r["result"]["frame_no"] >= 1
    jpeg = base64.b64decode(r["result"]["jpeg_b64"])
    # JPEG SOI marker — 0xff 0xd8 first two bytes.
    assert jpeg[:2] == b"\xff\xd8"


def test_screen_stream_frame_no_increments_iphone(iph_daemon):
    name, _ = iph_daemon
    _call(iph_ipc, name, "screen_stream_start", {})
    a = _call(iph_ipc, name, "screen_stream_frame", {})["result"]["frame_no"]
    b = _call(iph_ipc, name, "screen_stream_frame", {})["result"]["frame_no"]
    assert b > a


def test_screen_stream_reconfigure_iphone(iph_daemon):
    """Calling start again returns updated=True, not started=True."""
    name, _ = iph_daemon
    _call(iph_ipc, name, "screen_stream_start", {"fps": 4})
    r = _call(iph_ipc, name, "screen_stream_start", {"fps": 10, "quality": 80})
    assert r["result"]["running"] is True
    assert r["result"]["updated"] is True
    assert r["result"]["started"] is False
    assert r["result"]["fps"] == 10


def test_screen_stream_stop_iphone(iph_daemon):
    name, _ = iph_daemon
    _call(iph_ipc, name, "screen_stream_start", {})
    r = _call(iph_ipc, name, "screen_stream_stop", {})
    assert r["result"]["running"] is False
    assert r["result"]["stopped"] is True
    # After stop, frame is no longer ready.
    r2 = _call(iph_ipc, name, "screen_stream_frame", {})
    assert r2["result"]["ready"] is False


def test_screen_stream_stop_idempotent_iphone(iph_daemon):
    name, _ = iph_daemon
    r = _call(iph_ipc, name, "screen_stream_stop", {})
    assert r["result"]["running"] is False


# ---- Android screen_stream RPC ------------------------------------------

def test_screen_stream_start_android(anh_daemon):
    name, _ = anh_daemon
    r = _call(anh_ipc, name, "screen_stream_start", {"fps": 6})
    assert r["result"]["running"] is True
    assert r["result"]["fps"] == 6


def test_screen_stream_frame_after_start_android(anh_daemon):
    name, _ = anh_daemon
    _call(anh_ipc, name, "screen_stream_start", {})
    r = _call(anh_ipc, name, "screen_stream_frame", {})
    assert r["result"]["ready"] is True
    jpeg = base64.b64decode(r["result"]["jpeg_b64"])
    assert jpeg[:2] == b"\xff\xd8"


def test_screen_stream_stop_android(anh_daemon):
    name, _ = anh_daemon
    _call(anh_ipc, name, "screen_stream_start", {})
    r = _call(anh_ipc, name, "screen_stream_stop", {})
    assert r["result"]["running"] is False


# ---- helpers wrappers ----------------------------------------------------

def test_stream_rpc_helpers_iphone(iph_daemon, monkeypatch):
    """iphone_harness.helpers exposes screen_stream_start/frame/stop wrappers."""
    name, _ = iph_daemon
    import iphone_harness.helpers as h
    # helpers.NAME is captured at import time; tests use unique names per run.
    monkeypatch.setattr(h, "NAME", name)
    h._drop_conn()
    started = h.screen_stream_start(fps=4, quality=50)
    assert started["running"] is True
    frame = h.screen_stream_frame()
    assert frame["ready"] is True
    assert frame["frame_no"] >= 1
    stopped = h.screen_stream_stop()
    assert stopped["running"] is False


def test_stream_rpc_helpers_android(anh_daemon, monkeypatch):
    name, _ = anh_daemon
    import android_harness.helpers as h
    monkeypatch.setattr(h, "NAME", name)
    h._drop_conn()
    started = h.screen_stream_start(fps=4)
    assert started["running"] is True
    frame = h.screen_stream_frame()
    assert frame["ready"] is True
    stopped = h.screen_stream_stop()
    assert stopped["running"] is False
