"""End-to-end smoke tests for the headed/headless + remote-daemon features.

These hit the full chain in one test each:
  - e2e_headed_ios: mock daemon → ViewerServer → /stream returns JPEG frames
  - e2e_headed_android: same for Android
  - e2e_remote_iphone: TCP daemon endpoint → client connects → RPC works
  - e2e_stream_loop_progresses: poll /stream over time, see frame_no advance

Coverage rationale: per-component tests live in test_screen_stream.py +
test_viewer_mjpeg.py + test_remote_daemon.py. This file is the "the whole
thing works end-to-end" check that the goal's verify hook greps for via
`pytest -k 'e2e_headed or e2e_remote or e2e_stream'`.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_alive(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _spawn_mock(platform, name, extra_env=None):
    if platform == "iphone":
        module = "tests._mock_iphone_daemon"
        env_var = "IPH_NAME"
    else:
        module = "tests._mock_android_daemon"
        env_var = "ANH_NAME"
    env = {**os.environ, env_var: name}
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT), start_new_session=True,
    )


def _cleanup(platform, name):
    prefix = "iph" if platform == "iphone" else "anh"
    for ext in ("sock", "pid", "log"):
        try:
            (Path("/tmp") / f"{prefix}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


def _free_port():
    import socket as _s
    s = _s.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# ---- e2e_headed_ios -----------------------------------------------------

def test_e2e_headed_ios(monkeypatch):
    """Daemon → ViewerServer → HTTP /stream returns multipart JPEG. Tests the
    full data path a Windows user sees when they hit --headed."""
    from iphone_harness import _ipc as ipc
    import iphone_harness.helpers as ih
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    try:
        assert _wait_alive(ipc, name, timeout=5.0)
        with ViewerServer(platform="ios", fps=10) as v:
            time.sleep(0.3)
            with urllib.request.urlopen(v.url + "stream", timeout=3.0) as r:
                ctype = r.headers["Content-Type"]
                assert "multipart/x-mixed-replace" in ctype
                chunk = r.read(8192)
                # At least one JPEG frame in the multipart body.
                assert b"\xff\xd8" in chunk
                # Frame number embedded in MJPEG part header? No — but /healthz has it.
            with urllib.request.urlopen(v.url + "healthz", timeout=2.0) as r:
                data = json.loads(r.read())
                assert data["platform"] == "ios"
                assert data["running"] is True
    finally:
        try:
            s, _ = ipc.connect(name, timeout=1.0)
            ipc.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(timeout=2.0)
        _cleanup("iphone", name)


# ---- e2e_headed_android -------------------------------------------------

def test_e2e_headed_android(monkeypatch):
    from android_harness import _ipc as ipc
    import android_harness.helpers as ah
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("ANH_NAME", name)
    monkeypatch.setattr(ah, "NAME", name)
    p = _spawn_mock("android", name)
    try:
        assert _wait_alive(ipc, name, timeout=5.0)
        with ViewerServer(platform="android", fps=10) as v:
            time.sleep(0.3)
            with urllib.request.urlopen(v.url + "still", timeout=2.0) as r:
                assert r.status == 200
                assert r.headers.get_content_type() == "image/jpeg"
                assert r.read()[:2] == b"\xff\xd8"
    finally:
        try:
            s, _ = ipc.connect(name, timeout=1.0)
            ipc.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(timeout=2.0)
        _cleanup("android", name)


# ---- e2e_remote_iphone (TCP transport, client-only mode) ----------------

def test_e2e_remote_iphone():
    """Full stack: mock daemon binds TCP, client connects via TCP, RPC works."""
    from iphone_harness import _ipc as ipc

    name = f"tst{uuid.uuid4().hex[:10]}"
    port = _free_port()
    bind_uri = f"tcp://127.0.0.1:{port}"
    p = _spawn_mock("iphone", name, extra_env={"IPH_BIND": bind_uri})
    saved = {"IPH_CONNECT": os.environ.get("IPH_CONNECT"),
             "IPH_NAME": os.environ.get("IPH_NAME")}
    os.environ["IPH_CONNECT"] = bind_uri
    os.environ["IPH_NAME"] = name
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if ipc.ping(name, timeout=0.3):
                break
            time.sleep(0.05)
        else:
            pytest.fail("TCP daemon never came up")

        # admin.is_remote_daemon should report True for this configuration.
        from iphone_harness.admin import is_remote_daemon
        assert is_remote_daemon() is True

        # Round-trip RPC over TCP.
        s, _ = ipc.connect(name, timeout=1.0)
        try:
            r = ipc.request(s, None, {"method": "window_size", "params": {}})
            assert r["result"]["width"] == 390
        finally:
            s.close()

        # Verify the unix socket file was NOT created (TCP-only daemon).
        assert not (Path("/tmp") / f"iph-{name}.sock").exists()
    finally:
        try:
            s, _ = ipc.connect(name, timeout=1.0)
            ipc.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(timeout=2.0)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _cleanup("iphone", name)


# ---- e2e_stream_loop_progresses ----------------------------------------

def test_e2e_stream_loop_progresses(monkeypatch):
    """Frame number must advance over a 1-second poll — proves the daemon's
    capture loop is actually running, not just returning the same buffered
    frame forever."""
    from iphone_harness import _ipc as ipc
    import iphone_harness.helpers as ih

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    try:
        assert _wait_alive(ipc, name, timeout=5.0)
        ih.screen_stream_start(fps=20)
        # Pull frames 3 times; frame_no must be strictly increasing.
        seen = []
        for _ in range(3):
            f = ih.screen_stream_frame()
            assert f["ready"] is True
            seen.append(f["frame_no"])
            time.sleep(0.05)
        assert seen == sorted(set(seen)), f"frame_no non-monotonic: {seen}"
        # Stop releases producer state.
        stopped = ih.screen_stream_stop()
        assert stopped["running"] is False
    finally:
        try:
            s, _ = ipc.connect(name, timeout=1.0)
            ipc.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(timeout=2.0)
        _cleanup("iphone", name)
