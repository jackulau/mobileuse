"""HTTP MJPEG viewer sidecar tests.

Spawns the mock iphone (or android) daemon, points the ViewerServer at it,
then hits the HTTP endpoints with urllib. Verifies:
  - index.html served on /
  - /healthz returns JSON with platform + frame_no
  - /still returns a JPEG (synthetic stub from mock)
  - /stream returns multipart/x-mixed-replace with at least one frame
  - start/stop lifecycle releases the port
"""
import json
import os
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


def _wait_first_frame(url, timeout=5.0):
    """Bounded condition poll: block until the frame loop has produced a frame
    (healthz frame_no >= 1), replacing the old blind 0.3s sleeps. Returns the
    healthz payload. Asserts (never hangs) at the deadline."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "healthz", timeout=2.0) as r:
                last = json.loads(r.read())
                if last.get("frame_no", 0) >= 1:
                    return last
        except Exception:
            pass
        time.sleep(0.02)
    raise AssertionError(f"viewer produced no frame within {timeout}s: {last}")


def _spawn_mock(platform, name):
    if platform == "iphone":
        module = "tests._mock_iphone_daemon"
        env_var = "IPH_NAME"
    else:
        module = "tests._mock_android_daemon"
        env_var = "ANH_NAME"
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env={**os.environ, env_var: name},
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
def iph_viewer(monkeypatch):
    """Mock iphone daemon + ViewerServer wired together, ready to hit via HTTP."""
    import iphone_harness.helpers as ih
    from iphone_harness import _ipc as ipc
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    if not _wait_alive(ipc, name, timeout=5.0):
        p.kill(); p.wait(timeout=2.0); _cleanup("iphone", name)
        pytest.fail("mock iphone daemon never came up")
    v = ViewerServer(platform="ios", fps=8, quality=70)
    v.start()
    try:
        yield v
    finally:
        v.stop()
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


@pytest.fixture
def anh_viewer(monkeypatch):
    import android_harness.helpers as ah
    from android_harness import _ipc as ipc
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("ANH_NAME", name)
    monkeypatch.setattr(ah, "NAME", name)
    p = _spawn_mock("android", name)
    if not _wait_alive(ipc, name, timeout=5.0):
        p.kill(); p.wait(timeout=2.0); _cleanup("android", name)
        pytest.fail("mock android daemon never came up")
    v = ViewerServer(platform="android", fps=8)
    v.start()
    try:
        yield v
    finally:
        v.stop()
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


# ---- import sanity --------------------------------------------------------

def test_viewer_import_ok():
    from mobile_use.viewer.server import ViewerServer
    assert ViewerServer is not None


def test_viewer_init_picks_free_port():
    from mobile_use.viewer.server import ViewerServer
    v = ViewerServer(platform="ios")
    assert 0 < v.port < 65536
    assert v.url == f"http://127.0.0.1:{v.port}/"


def test_viewer_rejects_unknown_platform():
    from mobile_use.viewer.server import ViewerServer
    with pytest.raises(ValueError):
        ViewerServer(platform="windows-phone")


def test_single_viewer_stream_degrades_when_daemon_down(monkeypatch):
    """Single-device viewer: if screen_stream_frame() raises (daemon/Appium down),
    the stream handler stops cleanly instead of dumping a traceback into the HTTP
    server thread. No real daemon — the helpers module is stubbed to raise."""
    import socketserver

    import mobile_use.viewer.server as vs

    class _DeadHelpers:
        def screen_stream_start(self, **kw):
            return {"running": True}

        def screen_stream_frame(self):
            raise RuntimeError("iphone-harness daemon didn't come up")

        def screen_stream_stop(self):
            return {"running": False}

    monkeypatch.setattr(vs, "_load_helpers", lambda platform: _DeadHelpers())
    errors = []
    monkeypatch.setattr(
        socketserver.BaseServer, "handle_error",
        lambda self, request, client_address: errors.append(True),
    )

    v = vs.ViewerServer(platform="ios")
    v.start()
    try:
        with urllib.request.urlopen(v.url + "stream", timeout=3.0) as r:
            assert r.status == 200      # headers sent before the frame loop
            r.read(2048)                # ends cleanly once the frame fetch fails
    finally:
        v.stop()
    assert not errors, "single-viewer stream raised into the HTTP server thread (traceback spew)"


def test_viewer_client_disconnect_does_not_spew(monkeypatch):
    """do_GET disconnect guard: if a non-stream route raises BrokenPipe mid-write
    (the client vanished — closed tab / timed-out poll), handle_one_request
    swallows it instead of dumping a traceback into the server thread. Covers the
    health/still/index paths that the _serve_stream guard alone does not."""
    import socketserver

    import iphone_harness.helpers as ih
    from iphone_harness import _ipc as ipc
    from mobile_use.viewer.server import ViewerServer

    errors = []
    monkeypatch.setattr(
        socketserver.BaseServer, "handle_error",
        lambda self, request, client_address: errors.append(True),
    )

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    try:
        assert _wait_alive(ipc, name, timeout=5.0)
        v = ViewerServer(platform="ios")
        v.start()
        try:
            # Simulate the client vanishing mid-write on a normal route.
            def _boom(self):
                raise BrokenPipeError("client went away")
            monkeypatch.setattr(v._server.RequestHandlerClass, "_serve_health", _boom)
            try:
                urllib.request.urlopen(v.url + "healthz", timeout=3.0).read()
            except Exception:
                pass  # client side may see a reset — that's fine
        finally:
            v.stop()
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
    assert not errors, "client disconnect (BrokenPipe) escaped into the HTTP server thread"


# ---- HTTP routes ---------------------------------------------------------

def test_viewer_mjpeg_index_served(iph_viewer):
    with urllib.request.urlopen(iph_viewer.url, timeout=2.0) as r:
        assert r.status == 200
        body = r.read()
        assert b"mobile-use" in body
        assert b"<img" in body  # the live screen element
        assert r.headers.get_content_type() == "text/html"


def test_viewer_mjpeg_healthz(iph_viewer):
    data = _wait_first_frame(iph_viewer.url)
    assert data["platform"] == "ios"
    assert "frame_no" in data
    assert "fps" in data


def test_viewer_mjpeg_still_returns_jpeg(iph_viewer):
    _wait_first_frame(iph_viewer.url)
    with urllib.request.urlopen(iph_viewer.url + "still", timeout=2.0) as r:
        assert r.status == 200
        assert r.headers.get_content_type() == "image/jpeg"
        jpeg = r.read()
        assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_viewer_mjpeg_stream_multipart(iph_viewer):
    _wait_first_frame(iph_viewer.url)
    with urllib.request.urlopen(iph_viewer.url + "stream", timeout=3.0) as r:
        assert r.status == 200
        ctype = r.headers["Content-Type"]
        assert "multipart/x-mixed-replace" in ctype
        assert "boundary=mobile-use-frame" in ctype
        # Read enough bytes to capture at least one frame.
        chunk = r.read(4096)
        assert b"--mobile-use-frame" in chunk
        assert b"Content-Type: image/jpeg" in chunk
        assert b"\xff\xd8" in chunk  # JPEG SOI in payload


def test_viewer_mjpeg_404_for_unknown_route(iph_viewer):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(iph_viewer.url + "nope", timeout=2.0)
    assert exc.value.code == 404


# ---- Android parity ------------------------------------------------------

def test_viewer_mjpeg_healthz_android(anh_viewer):
    data = _wait_first_frame(anh_viewer.url)
    assert data["platform"] == "android"


def test_viewer_mjpeg_still_android(anh_viewer):
    _wait_first_frame(anh_viewer.url)
    with urllib.request.urlopen(anh_viewer.url + "still", timeout=2.0) as r:
        assert r.status == 200
        assert r.headers.get_content_type() == "image/jpeg"


# ---- Lifecycle -----------------------------------------------------------

def test_viewer_stop_releases_port(monkeypatch):
    """After stop(), the port should be reusable. ViewerServer is the only
    holder; no zombie threads should keep it bound."""
    import socket as _socket

    import iphone_harness.helpers as ih
    from iphone_harness import _ipc as ipc
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    try:
        if not _wait_alive(ipc, name, timeout=5.0):
            pytest.fail("mock daemon never came up")
        v = ViewerServer(platform="ios")
        port = v.port
        v.start()
        v.stop()
        # Port should now be free — rebind without conflict.
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        finally:
            s.close()
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


def test_viewer_context_manager(monkeypatch):
    """Confirm `with ViewerServer(...) as v:` start/stop pair works."""
    import iphone_harness.helpers as ih
    from iphone_harness import _ipc as ipc
    from mobile_use.viewer.server import ViewerServer

    name = f"tst{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    p = _spawn_mock("iphone", name)
    try:
        assert _wait_alive(ipc, name, timeout=5.0)
        with ViewerServer(platform="ios") as v:
            url = v.url
            with urllib.request.urlopen(url, timeout=2.0) as r:
                assert r.status == 200
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
