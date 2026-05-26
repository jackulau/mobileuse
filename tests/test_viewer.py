"""Tests for the per-name MJPEG client + multi-viewer shared fixtures."""
import base64
from unittest.mock import MagicMock, patch

import pytest

from mobile_use.viewer.named_client import NamedStreamClient


# A tiny but valid JPEG payload (1x1 black pixel) — 94 bytes.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAcI/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA//9k="
)
TINY_JPEG_B64 = base64.b64encode(TINY_JPEG).decode()


def _mock_ipc_module(reply):
    """Build a fake _ipc module that returns `reply` from `request()`."""
    mod = MagicMock()
    mod.connect.return_value = (MagicMock(), None)
    mod.request.return_value = reply
    return mod


# ---- NamedStreamClient ----------------------------------------------------

def test_named_client_rejects_bad_platform():
    with pytest.raises(ValueError, match="unknown platform"):
        NamedStreamClient("windows", "x")


def test_named_client_has_required_methods():
    with patch("iphone_harness._ipc.connect", MagicMock(return_value=(MagicMock(), None))):
        c = NamedStreamClient("ios", "iphone-A")
    assert hasattr(c, "start")
    assert hasattr(c, "frame")
    assert hasattr(c, "stop")
    assert hasattr(c, "frame_jpeg")


def test_named_client_start_calls_screen_stream_start():
    c = NamedStreamClient("ios", "iphone-A", fps=4, quality=50, max_dim=600)
    c._ipc = _mock_ipc_module({"result": {"running": True, "fps": 4}})
    out = c.start()
    assert out == {"running": True, "fps": 4}
    c._ipc.connect.assert_called_with("iphone-A", timeout=10.0)
    args, _ = c._ipc.request.call_args
    payload = args[2]
    assert payload["method"] == "screen_stream_start"
    assert payload["params"]["fps"] == 4
    assert payload["params"]["quality"] == 50
    assert payload["params"]["max_dim"] == 600


def test_named_client_frame_returns_ready_dict():
    c = NamedStreamClient("ios", "iphone-A")
    c._ipc = _mock_ipc_module({"result": {
        "ready": True, "frame_no": 7, "jpeg_b64": TINY_JPEG_B64, "fps": 6,
    }})
    r = c.frame()
    assert r["ready"] is True
    assert r["frame_no"] == 7
    assert r["jpeg_b64"] == TINY_JPEG_B64


def test_named_client_frame_jpeg_decodes_or_none():
    c = NamedStreamClient("ios", "iphone-A")
    c._ipc = _mock_ipc_module({"result": {
        "ready": True, "frame_no": 1, "jpeg_b64": TINY_JPEG_B64,
    }})
    assert c.frame_jpeg() == TINY_JPEG

    c._ipc = _mock_ipc_module({"result": {"ready": False, "frame_no": 0}})
    assert c.frame_jpeg() is None


def test_named_client_stop_is_idempotent_when_never_started():
    c = NamedStreamClient("ios", "iphone-A")
    c._ipc = _mock_ipc_module({"result": {"running": False}})
    out = c.stop()
    assert out == {"running": False}
    c._ipc.request.assert_not_called()


def test_named_client_start_stop_roundtrip():
    c = NamedStreamClient("android", "pixel-1")
    c._ipc = _mock_ipc_module({"result": {"running": True}})
    c.start()
    out = c.stop()
    assert out["running"] is True
    methods = [call.args[2]["method"] for call in c._ipc.request.call_args_list]
    assert "screen_stream_start" in methods
    assert "screen_stream_stop" in methods


def test_named_client_context_manager_starts_and_stops():
    c = NamedStreamClient("ios", "iphone-A")
    c._ipc = _mock_ipc_module({"result": {"running": True}})
    with c:
        pass
    methods = [call.args[2]["method"] for call in c._ipc.request.call_args_list]
    assert "screen_stream_start" in methods
    assert "screen_stream_stop" in methods


def test_named_client_default_name_passes_none():
    c = NamedStreamClient("ios", name=None)
    c._ipc = _mock_ipc_module({"result": {"running": True}})
    c.start()
    c._ipc.connect.assert_called_with(None, timeout=10.0)


def test_named_client_repr():
    c = NamedStreamClient("ios", "iphone-A")
    s = repr(c)
    assert "iphone-A" in s
    assert "ios" in s
