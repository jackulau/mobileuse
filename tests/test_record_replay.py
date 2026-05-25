"""Tests for record_replay — captures helper calls and replays them."""
import types
from pathlib import Path

import pytest

from mobile_use import record_replay


def _make_fake_helpers():
    """Build a fake helpers module with the standard surface we record."""
    mod = types.ModuleType("fake_helpers")
    calls = []
    def tap_at_xy(x, y):
        calls.append(("tap_at_xy", (x, y), {}))
    def swipe(x1, y1, x2, y2, duration=0.4):
        calls.append(("swipe", (x1, y1, x2, y2), {"duration": duration}))
    def type_text(text):
        calls.append(("type_text", (text,), {}))
    def scroll(direction="down"):
        calls.append(("scroll", (), {"direction": direction}))
    mod.tap_at_xy = tap_at_xy
    mod.swipe = swipe
    mod.type_text = type_text
    mod.scroll = scroll
    mod._calls = calls
    return mod


def test_module_exports_api():
    assert callable(record_replay.start_recording)
    assert callable(record_replay.stop_recording)
    assert callable(record_replay.replay)


def test_recording_journals_helper_calls(tmp_path):
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"

    record_replay.start_recording(str(out), helpers=h)
    h.tap_at_xy(100, 200)
    h.type_text("hello")
    h.swipe(0, 500, 0, 100)
    script_path = record_replay.stop_recording()

    assert script_path == str(out)
    assert out.exists()
    script = out.read_text()
    assert "h.tap_at_xy(100, 200)" in script
    assert 'h.type_text("hello")' in script
    assert "h.swipe(0, 500, 0, 100" in script


def test_recording_forwards_to_real_helper(tmp_path):
    h = _make_fake_helpers()
    record_replay.start_recording(str(tmp_path / "out.py"), helpers=h)
    h.tap_at_xy(1, 2)
    record_replay.stop_recording()
    # The wrapped call should still have been forwarded to the underlying impl.
    assert h._calls == [("tap_at_xy", (1, 2), {})]


def test_double_start_raises(tmp_path):
    h = _make_fake_helpers()
    record_replay.start_recording(str(tmp_path / "a.py"), helpers=h)
    try:
        with pytest.raises(RuntimeError, match="already recording"):
            record_replay.start_recording(str(tmp_path / "b.py"), helpers=h)
    finally:
        record_replay.stop_recording()


def test_stop_without_start_raises():
    with pytest.raises(RuntimeError, match="not recording"):
        record_replay.stop_recording()


def test_stop_restores_helper_originals(tmp_path):
    h = _make_fake_helpers()
    orig = h.tap_at_xy
    record_replay.start_recording(str(tmp_path / "x.py"), helpers=h)
    assert h.tap_at_xy is not orig
    record_replay.stop_recording()
    assert h.tap_at_xy is orig


def test_replay_runs_recorded_script(tmp_path):
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"

    record_replay.start_recording(str(out), helpers=h)
    h.tap_at_xy(50, 60)
    h.scroll(direction="up")
    record_replay.stop_recording()

    # Replay against fresh helpers — should produce the same call trace.
    fresh = _make_fake_helpers()
    # The recorded script does `import fake_helpers as h`. Inject via helpers= arg
    # which we'll have replay use *before* the script executes. But the script
    # has its own `import` line — to bypass that, we use the helpers= passthrough.
    # In practice, scripts will import the real helpers from the user's module.
    # For our test, we monkey-patch sys.modules so `import fake_helpers` finds our fake.
    import sys
    sys.modules["fake_helpers"] = fresh
    try:
        record_replay.replay(str(out))
        assert fresh._calls == [("tap_at_xy", (50, 60), {}), ("scroll", (), {"direction": "up"})]
    finally:
        del sys.modules["fake_helpers"]


def test_replay_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        record_replay.replay(str(tmp_path / "missing.py"))


def test_script_includes_helpers_import(tmp_path):
    h = _make_fake_helpers()
    h.__name__ = "fake_helpers"
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    h.tap_at_xy(1, 2)
    record_replay.stop_recording()
    script = out.read_text()
    assert "import fake_helpers" in script


def test_quoted_strings_in_script(tmp_path):
    """Strings with special chars must be safely quoted."""
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    h.type_text("hello 'world' \"quoted\"")
    record_replay.stop_recording()
    script = out.read_text()
    # JSON-escaped — script is valid Python
    assert '\\"' in script or "'" in script
    compile(script, str(out), "exec")  # script compiles


def test_is_recording_flag(tmp_path):
    assert record_replay.is_recording() is False
    h = _make_fake_helpers()
    record_replay.start_recording(str(tmp_path / "x.py"), helpers=h)
    try:
        assert record_replay.is_recording() is True
    finally:
        record_replay.stop_recording()
    assert record_replay.is_recording() is False


def test_records_with_iphone_harness_helpers():
    """Importing recorder against the real iphone_harness.helpers module should work."""
    import iphone_harness.helpers as iph
    assert getattr(iph, "tap_at_xy", None) is not None
    # Just check we can wrap and unwrap without side-effects on the daemon.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        path = f.name
    record_replay.start_recording(path, helpers=iph, fn_names=("tap_at_xy",))
    record_replay.stop_recording()
    Path(path).unlink(missing_ok=True)


def test_records_with_android_harness_helpers():
    import android_harness.helpers as anh
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        path = f.name
    record_replay.start_recording(path, helpers=anh, fn_names=("tap_at_xy",))
    record_replay.stop_recording()
    Path(path).unlink(missing_ok=True)
