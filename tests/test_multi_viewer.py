"""Tests for MultiViewerServer — grid + per-device routing with mocked IPC."""
import base64
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from mobile_use.viewer.multi_server import MultiViewerServer


TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAcI/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA//9k="
)
TINY_JPEG_B64 = base64.b64encode(TINY_JPEG).decode()


class _FakeClient:
    """Minimal stand-in for NamedStreamClient with controllable frame output."""

    def __init__(self, platform, name, fps=4, quality=60, max_dim=800,
                 ready=True, delay_ms=0):
        self.platform = platform
        self.name = name
        self.fps = fps
        self._ready = ready
        self._delay = delay_ms / 1000.0
        self._frame_no = 0
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return {"running": True}

    def frame(self):
        if self._delay:
            time.sleep(self._delay)
        if not self._ready:
            return {"ready": False, "frame_no": 0, "fps": 0.0}
        self._frame_no += 1
        return {
            "ready": True,
            "frame_no": self._frame_no,
            "jpeg_b64": TINY_JPEG_B64,
            "fps": float(self.fps),
        }

    def frame_jpeg(self):
        r = self.frame()
        if not r.get("ready"):
            return None
        return base64.b64decode(r["jpeg_b64"])

    def stop(self):
        self.stopped = True
        return {"running": False}


def _factory(**overrides):
    def make(platform, name, fps, quality, max_dim):
        return _FakeClient(platform, name, fps, quality, max_dim, **overrides)
    return make


def _start_viewer(pairs, **kwargs):
    factory = kwargs.pop("client_factory", _factory())
    s = MultiViewerServer(pairs, client_factory=factory, **kwargs)
    s.start()
    time.sleep(0.05)
    return s


def _fetch(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


# ---- construction validation ---------------------------------------------

def test_rejects_empty_devices():
    with pytest.raises(ValueError, match="at least one"):
        MultiViewerServer([])


def test_rejects_bad_platform():
    with pytest.raises(ValueError, match="bad platform"):
        MultiViewerServer([("windows", "x")])


def test_rejects_bad_name():
    with pytest.raises(ValueError, match="bad name"):
        MultiViewerServer([("ios", "bad name!")])


def test_rejects_duplicate_name():
    with pytest.raises(ValueError, match="duplicate"):
        MultiViewerServer([("ios", "a"), ("android", "a")])


def test_port_auto_allocated_when_none():
    s = MultiViewerServer([("ios", "a")], client_factory=_factory())
    assert s.port > 0
    assert s.url.startswith("http://127.0.0.1:")


def test_url_property():
    s = MultiViewerServer([("ios", "a")], port=12345, client_factory=_factory())
    assert s.url == "http://127.0.0.1:12345/"


# ---- HTTP surface --------------------------------------------------------

def test_multi_viewer_grid_index_lists_all_devices():
    s = _start_viewer([("ios", "alpha"), ("android", "beta")])
    try:
        status, ctype, body = _fetch(s.url)
        assert status == 200
        assert "text/html" in ctype
        body = body.decode()
        assert 'data-name="alpha"' in body
        assert 'data-name="beta"' in body
        assert '/stream/alpha' in body
        assert '/stream/beta' in body
    finally:
        s.stop()


def test_multi_viewer_devices_endpoint():
    s = _start_viewer([("ios", "alpha"), ("android", "beta")])
    try:
        status, _, body = _fetch(s.url + "devices")
        assert status == 200
        data = json.loads(body)
        names = {d["name"] for d in data}
        assert names == {"alpha", "beta"}
    finally:
        s.stop()


def test_multi_viewer_healthz_lists_all_devices():
    s = _start_viewer([("ios", "alpha"), ("android", "beta")])
    try:
        status, _, body = _fetch(s.url + "healthz")
        assert status == 200
        data = json.loads(body)
        assert len(data["devices"]) == 2
        for d in data["devices"]:
            assert d["running"] is True
            assert d["fps"] > 0
            assert d["name"] in ("alpha", "beta")
    finally:
        s.stop()


def test_multi_viewer_healthz_marks_unready_device():
    s = _start_viewer(
        [("ios", "alive"), ("android", "dead")],
        client_factory=lambda p, n, f, q, m: _FakeClient(p, n, f, q, m, ready=(n == "alive")),
    )
    try:
        _, _, body = _fetch(s.url + "healthz")
        by_name = {d["name"]: d for d in json.loads(body)["devices"]}
        assert by_name["alive"]["running"] is True
        assert by_name["dead"]["running"] is False
    finally:
        s.stop()


def test_multi_viewer_still_returns_jpeg():
    s = _start_viewer([("ios", "alpha")])
    try:
        status, ctype, body = _fetch(s.url + "still/alpha")
        assert status == 200
        assert ctype == "image/jpeg"
        assert body[:3] == b"\xff\xd8\xff"
    finally:
        s.stop()


def test_multi_viewer_still_404_for_unknown_device():
    s = _start_viewer([("ios", "alpha")])
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _fetch(s.url + "still/nope")
        assert exc.value.code == 404
    finally:
        s.stop()


def test_multi_viewer_stream_404_for_unknown_device():
    s = _start_viewer([("ios", "alpha")])
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _fetch(s.url + "stream/nope", timeout=1.0)
        assert exc.value.code == 404
    finally:
        s.stop()


def test_multi_viewer_still_503_when_stream_not_ready():
    s = _start_viewer(
        [("ios", "alpha")],
        client_factory=lambda p, n, f, q, m: _FakeClient(p, n, f, q, m, ready=False),
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _fetch(s.url + "still/alpha")
        assert exc.value.code == 503
    finally:
        s.stop()


def test_multi_viewer_404_for_unknown_path():
    s = _start_viewer([("ios", "alpha")])
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _fetch(s.url + "nope")
        assert exc.value.code == 404
    finally:
        s.stop()


def test_multi_viewer_start_calls_all_clients():
    s = MultiViewerServer(
        [("ios", "a"), ("android", "b")],
        client_factory=_factory(),
    )
    s.start()
    try:
        for c in s.clients.values():
            assert c.started is True
    finally:
        s.stop()


def test_multi_viewer_stop_calls_all_clients():
    s = _start_viewer([("ios", "a"), ("android", "b")])
    s.stop()
    for c in s.clients.values():
        assert c.stopped is True


def test_multi_viewer_one_slow_device_does_not_starve_others():
    s = _start_viewer(
        [("ios", "fast"), ("ios", "slow")],
        client_factory=lambda p, n, f, q, m: _FakeClient(
            p, n, f, q, m, delay_ms=200 if n == "slow" else 0,
        ),
    )
    try:
        results = {}

        def hit(name):
            t0 = time.time()
            _, _, _ = _fetch(s.url + "still/" + name, timeout=3.0)
            results[name] = time.time() - t0

        t_fast = threading.Thread(target=hit, args=("fast",))
        t_slow = threading.Thread(target=hit, args=("slow",))
        t_slow.start()
        time.sleep(0.05)
        t_fast.start()
        t_fast.join(timeout=2.0)
        t_slow.join(timeout=2.0)

        assert "fast" in results
        assert results["fast"] < 0.3
    finally:
        s.stop()


def test_multi_viewer_context_manager_lifecycle():
    with MultiViewerServer([("ios", "a")], client_factory=_factory()) as s:
        assert s._thread is not None
        assert s._thread.is_alive()
    assert s.clients["a"].stopped is True
