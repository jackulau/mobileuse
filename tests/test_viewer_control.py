"""Interactive viewer control: POST /control(/name) tap/type/key routing.

Device-free: the helpers layer is faked (recording taps + window size), so
tests assert exact coordinate scaling without any daemon or device.
"""
import json
import time
import types
import urllib.error
import urllib.request

import pytest

from mobile_use.viewer.server import ViewerServer, _control_point, _dispatch_control

MOCK_W, MOCK_H = 1000, 2000


def _fake_helpers(width=MOCK_W, height=MOCK_H):
    m = types.ModuleType("fake_viewer_helpers")
    m.calls = []
    m.bound = []

    def _use_name(name):
        m.bound.append(name)
        return ("token", name)

    def _reset_name(token):
        m.bound.append(("reset", token[1]))

    def window_size():
        return {"width": width, "height": height}

    def tap_at_xy(x, y):
        m.calls.append(("tap", x, y))
        return True

    def type_text(text):
        m.calls.append(("type", text))
        return True

    def press_enter():
        m.calls.append(("key", "enter"))
        return True

    def press_home():
        m.calls.append(("key", "home"))
        return True

    def screen_stream_start(**kw):
        return {"running": True}

    def screen_stream_stop():
        return {"running": False}

    def screen_stream_frame():
        return {"ready": False, "frame_no": 0, "fps": 0.0}

    for fn in (window_size, tap_at_xy, type_text, press_enter, press_home,
               screen_stream_start, screen_stream_stop, screen_stream_frame):
        setattr(m, fn.__name__, fn)
    m._use_name = _use_name
    m._reset_name = _reset_name
    return m


# ---- _control_point ------------------------------------------------------------

def test_point_from_fractions():
    size = {"width": MOCK_W, "height": MOCK_H}
    assert _control_point({"fx": 0.5, "fy": 0.25}, size) == (500, 500)


def test_point_from_frame_pixels():
    size = {"width": MOCK_W, "height": MOCK_H}
    body = {"x": 200, "y": 100, "frame_w": 400, "frame_h": 800}
    assert _control_point(body, size) == (500, 250)


@pytest.mark.parametrize("body", [
    {}, {"fx": 0.5}, {"fx": 1.5, "fy": 0.5}, {"fx": -0.1, "fy": 0.5},
    {"x": 1, "y": 1, "frame_w": 0, "frame_h": 10},
])
def test_point_malformed_raises(body):
    with pytest.raises(ValueError):
        _control_point(body, {"width": MOCK_W, "height": MOCK_H})


# ---- _dispatch_control ------------------------------------------------------------

def test_dispatch_tap_scales_to_device_points():
    h = _fake_helpers()
    out = _dispatch_control(h, {"action": "tap", "fx": 0.5, "fy": 0.5})
    assert out["ok"] is True
    assert ("tap", 500, 1000) in h.calls


def test_dispatch_type_routes_text():
    h = _fake_helpers()
    out = _dispatch_control(h, {"action": "type", "text": "hello"})
    assert out == {"ok": True, "action": "type", "chars": 5}
    assert ("type", "hello") in h.calls


def test_dispatch_keys():
    h = _fake_helpers()
    _dispatch_control(h, {"action": "key", "key": "enter"})
    _dispatch_control(h, {"action": "key", "key": "home"})
    assert ("key", "enter") in h.calls
    assert ("key", "home") in h.calls


def test_dispatch_unknown_action_raises():
    h = _fake_helpers()
    with pytest.raises(ValueError, match="unknown control action"):
        _dispatch_control(h, {"action": "explode"})


def test_dispatch_unsupported_key_raises():
    h = _fake_helpers()
    with pytest.raises(ValueError, match="back"):
        _dispatch_control(h, {"action": "key", "key": "back"})  # fake lacks press_back


def test_dispatch_binds_and_resets_name():
    h = _fake_helpers()
    _dispatch_control(h, {"action": "tap", "fx": 0.1, "fy": 0.1}, bind_name="px1")
    assert h.bound[0] == "px1"
    assert h.bound[-1] == ("reset", "px1")


# ---- single ViewerServer HTTP ---------------------------------------------------------

def _post(url, body, timeout=3.0):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture
def single_viewer(monkeypatch):
    h = _fake_helpers()
    monkeypatch.setattr("mobile_use.viewer.server._load_helpers", lambda p: h)
    v = ViewerServer(platform="ios")
    v.start()
    time.sleep(0.05)
    yield v, h
    v.stop()


def test_single_post_tap_reaches_helpers_scaled(single_viewer):
    v, h = single_viewer
    status, out = _post(v.url + "control", {"action": "tap", "fx": 0.5, "fy": 0.25})
    assert status == 200
    assert out["ok"] is True
    assert ("tap", 500, 500) in h.calls


def test_single_post_type(single_viewer):
    v, h = single_viewer
    status, out = _post(v.url + "control", {"action": "type", "text": "hi"})
    assert status == 200
    assert ("type", "hi") in h.calls


def test_single_bad_payload_400(single_viewer):
    v, _h = single_viewer
    status, _ = _post(v.url + "control", {"action": "tap", "fx": 9.0, "fy": 0.5})
    assert status == 400


def test_single_index_contains_control_script(single_viewer):
    v, _h = single_viewer
    with urllib.request.urlopen(v.url, timeout=3.0) as r:
        html = r.read().decode()
    assert "/control" in html
    assert "id=ctl" in html          # visible control toggle
    assert "click" in html            # click-to-tap wiring


def test_single_read_only_403(monkeypatch):
    h = _fake_helpers()
    monkeypatch.setattr("mobile_use.viewer.server._load_helpers", lambda p: h)
    v = ViewerServer(platform="ios", read_only=True)
    v.start()
    time.sleep(0.05)
    try:
        status, _ = _post(v.url + "control", {"action": "tap", "fx": 0.5, "fy": 0.5})
        assert status == 403
        assert h.calls == []
    finally:
        v.stop()


# ---- MultiViewerServer HTTP -------------------------------------------------------------

class _FakeClient:
    def __init__(self, platform, name, fps=4, quality=60, max_dim=800):
        self.platform, self.name = platform, name

    def start(self):
        return {"running": True}

    def frame(self):
        return {"ready": False, "frame_no": 0, "fps": 0.0}

    def frame_jpeg(self):
        return None

    def stop(self):
        return {"running": False}


def _multi(monkeypatch, h, **kw):
    from mobile_use.viewer.multi_server import MultiViewerServer
    monkeypatch.setattr("mobile_use.viewer.server._load_helpers", lambda p: h)
    v = MultiViewerServer([("ios", "ipA"), ("android", "pxB")],
                          client_factory=lambda p, n, f, q, m: _FakeClient(p, n),
                          **kw)
    v.start()
    time.sleep(0.05)
    return v


def test_multi_post_routes_to_named_device(monkeypatch):
    h = _fake_helpers()
    v = _multi(monkeypatch, h)
    try:
        status, out = _post(v.url + "control/pxB", {"action": "tap", "fx": 0.5, "fy": 0.5})
        assert status == 200
        assert out["ok"] is True
        assert ("tap", 500, 1000) in h.calls
        assert h.bound[0] == "pxB"     # per-name binding engaged
    finally:
        v.stop()


def test_multi_unknown_device_404(monkeypatch):
    h = _fake_helpers()
    v = _multi(monkeypatch, h)
    try:
        status, _ = _post(v.url + "control/ghost", {"action": "tap", "fx": 0.5, "fy": 0.5})
        assert status == 404
    finally:
        v.stop()


def test_multi_read_only_403(monkeypatch):
    h = _fake_helpers()
    v = _multi(monkeypatch, h, read_only=True)
    try:
        status, _ = _post(v.url + "control/ipA", {"action": "tap", "fx": 0.5, "fy": 0.5})
        assert status == 403
        assert h.calls == []
    finally:
        v.stop()


def test_multi_grid_html_contains_control_script(monkeypatch):
    h = _fake_helpers()
    v = _multi(monkeypatch, h)
    try:
        with urllib.request.urlopen(v.url, timeout=3.0) as r:
            html = r.read().decode()
        assert "/control/" in html
        assert "id=ctl" in html
    finally:
        v.stop()


# ---- devices view flag + help wording ------------------------------------------------------

def test_view_help_drops_read_only_mirror_caveat():
    from mobile_use.devices import VIEW_HELP
    assert "Read-only mirror" not in VIEW_HELP
    assert "--read-only" in VIEW_HELP


def test_view_args_parse_read_only():
    from mobile_use.devices import _parse_view_args
    assert _parse_view_args(["--read-only"])["read_only"] is True
    assert _parse_view_args([])["read_only"] is False
