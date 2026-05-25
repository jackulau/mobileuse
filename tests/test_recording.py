"""Tests for screen recording helpers (iOS + Android).

Mocks the appium() boundary so tests run without a real device.
"""
import base64

import pytest

from iphone_harness import helpers as iph
from android_harness import helpers as anh


# ---- iOS ------------------------------------------------------------------

def test_iph_record_screen_writes_decoded_mp4(monkeypatch, tmp_path):
    fake_video = b"\x00\x01\x02" * 100  # fake mp4 bytes
    encoded = base64.b64encode(fake_video).decode()

    calls = []
    def fake_appium(script, **kw):
        calls.append((script, kw))
        if script == "mobile: stopRecordingScreen":
            return encoded
        return None

    monkeypatch.setattr(iph, "appium", fake_appium)
    monkeypatch.setattr(iph.time, "sleep", lambda *a: None)

    out = iph.record_screen(duration=1, path=str(tmp_path / "out.mp4"))
    assert out.endswith("out.mp4")
    assert (tmp_path / "out.mp4").read_bytes() == fake_video

    scripts = [c[0] for c in calls]
    assert "mobile: startRecordingScreen" in scripts
    assert "mobile: stopRecordingScreen" in scripts


def test_iph_record_screen_default_path(monkeypatch):
    encoded = base64.b64encode(b"").decode()
    monkeypatch.setattr(iph, "appium", lambda script, **kw: encoded if "stop" in script else None)
    monkeypatch.setattr(iph.time, "sleep", lambda *a: None)
    out = iph.record_screen(duration=1)
    assert "iph-record-" in out
    assert out.endswith(".mp4")


def test_iph_record_screen_raises_on_start_failure(monkeypatch):
    def fake_appium(script, **kw):
        if script == "mobile: startRecordingScreen":
            raise RuntimeError("xcuitest doesn't support")
        return None
    monkeypatch.setattr(iph, "appium", fake_appium)
    with pytest.raises(RuntimeError, match="start screen recording failed"):
        iph.record_screen(duration=1)


def test_iph_has_stop_screen_recording():
    """Existing iOS stop_screen_recording stays (UI-driven path)."""
    assert callable(iph.stop_screen_recording)


# ---- Android --------------------------------------------------------------

def test_anh_record_screen_writes_decoded_mp4(monkeypatch, tmp_path):
    fake_video = b"\xff" * 256
    encoded = base64.b64encode(fake_video).decode()

    calls = []
    def fake_appium(script, **kw):
        calls.append((script, kw))
        if script == "mobile: stopRecordingScreen":
            return encoded
        return None

    monkeypatch.setattr(anh, "appium", fake_appium)
    monkeypatch.setattr(anh.time, "sleep", lambda *a: None)

    out = anh.record_screen(duration=1, path=str(tmp_path / "v.mp4"))
    assert (tmp_path / "v.mp4").read_bytes() == fake_video


def test_anh_record_screen_rejects_over_180s(monkeypatch):
    monkeypatch.setattr(anh, "appium", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="180s per segment"):
        anh.record_screen(duration=200)


def test_anh_record_screen_passes_bit_rate(monkeypatch):
    captured = {}
    def fake_appium(script, **kw):
        if script == "mobile: startRecordingScreen":
            captured.update(kw)
        return base64.b64encode(b"").decode() if "stop" in script else None
    monkeypatch.setattr(anh, "appium", fake_appium)
    monkeypatch.setattr(anh.time, "sleep", lambda *a: None)
    anh.record_screen(duration=1, bit_rate="8M")
    assert captured["bitRate"] == "8M"


def test_anh_record_screen_passes_size(monkeypatch):
    captured = {}
    def fake_appium(script, **kw):
        if script == "mobile: startRecordingScreen":
            captured.update(kw)
        return base64.b64encode(b"").decode() if "stop" in script else None
    monkeypatch.setattr(anh, "appium", fake_appium)
    monkeypatch.setattr(anh.time, "sleep", lambda *a: None)
    anh.record_screen(duration=1, size="720x1280")
    assert captured["videoSize"] == "720x1280"


def test_anh_start_stop_separate(monkeypatch, tmp_path):
    fake_video = b"data"
    encoded = base64.b64encode(fake_video).decode()
    def fake_appium(script, **kw):
        return encoded if "stop" in script else None
    monkeypatch.setattr(anh, "appium", fake_appium)
    anh.start_screen_recording()
    out = anh.stop_screen_recording(path=str(tmp_path / "split.mp4"))
    assert (tmp_path / "split.mp4").read_bytes() == fake_video


def test_anh_has_record_screen():
    assert callable(anh.record_screen)
    assert callable(anh.start_screen_recording)
    assert callable(anh.stop_screen_recording)


def test_both_platforms_export_record_screen():
    assert callable(iph.record_screen)
    assert callable(anh.record_screen)
