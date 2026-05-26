"""Tests for record_replay — captures helper calls and replays them."""
import json
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


# ---------- UI fingerprint helper ----------

def _fake_helpers_with_ui(tree, app="com.example.app"):
    mod = types.ModuleType("fake_with_ui")
    mod.active_app = lambda: app
    mod.ui_tree = lambda visible_only=False, compact=False: list(tree)
    return mod


def test_fingerprint_empty_when_helpers_lack_ui():
    """Helpers without ui_tree/active_app yield empty fingerprint, no exception."""
    h = _make_fake_helpers()
    fp = record_replay._ui_fingerprint(h)
    assert fp == {"app": "", "labels": [], "focused": None, "count": 0}


def test_fingerprint_collects_visible_labels_ios_shape():
    tree = [
        {"type": "Button", "label": "Send", "name": "sendBtn", "traits": ""},
        {"type": "Button", "label": "Cancel", "name": "cancelBtn", "traits": ""},
        {"type": "TextField", "label": "", "name": "composeField", "traits": "Focused"},
    ]
    h = _make_fake_helpers()
    h.active_app = lambda: "com.apple.mobilesms"
    h.ui_tree = lambda visible_only=False, compact=False: tree
    fp = record_replay._ui_fingerprint(h)
    assert fp["app"] == "com.apple.mobilesms"
    assert fp["labels"] == ["Cancel", "Send", "composeField"]
    assert fp["focused"] == "composeField"
    assert fp["count"] == 3


def test_fingerprint_collects_text_android_shape():
    tree = [
        {"type": "Button", "text": "Reply", "content_desc": "", "focused": False},
        {"type": "EditText", "text": "", "content_desc": "Message box", "focused": True},
    ]
    h = _make_fake_helpers()
    h.active_app = lambda: "com.android.messaging"
    h.ui_tree = lambda visible_only=False, compact=False: tree
    fp = record_replay._ui_fingerprint(h)
    assert fp["app"] == "com.android.messaging"
    assert "Reply" in fp["labels"]
    assert "Message box" in fp["labels"]
    assert fp["focused"] == "Message box"


def test_fingerprint_caps_labels_at_top_n():
    tree = [{"type": "X", "label": f"item-{i:03d}"} for i in range(50)]
    h = _make_fake_helpers()
    h.ui_tree = lambda visible_only=False, compact=False: tree
    fp = record_replay._ui_fingerprint(h)
    assert len(fp["labels"]) == record_replay._FP_TOP_N
    # Sorted-stable cap: top of sorted set
    assert fp["labels"] == sorted([f"item-{i:03d}" for i in range(50)])[: record_replay._FP_TOP_N]


def test_fingerprint_dedupes_repeated_labels():
    tree = [
        {"type": "Cell", "label": "Row"},
        {"type": "Cell", "label": "Row"},
        {"type": "Cell", "label": "Row"},
        {"type": "Cell", "label": "Other"},
    ]
    h = _make_fake_helpers()
    h.ui_tree = lambda visible_only=False, compact=False: tree
    fp = record_replay._ui_fingerprint(h)
    assert fp["labels"] == ["Other", "Row"]
    assert fp["count"] == 4  # count counts all visible, not unique


def test_fingerprint_swallows_active_app_errors():
    h = _make_fake_helpers()
    def boom():
        raise RuntimeError("WDA disconnected")
    h.active_app = boom
    h.ui_tree = lambda visible_only=False, compact=False: []
    fp = record_replay._ui_fingerprint(h)
    assert fp["app"] == ""  # gracefully empty


def test_fingerprint_swallows_ui_tree_errors():
    h = _make_fake_helpers()
    h.active_app = lambda: "x"
    def boom(visible_only=False, compact=False):
        raise RuntimeError("appium gone")
    h.ui_tree = boom
    fp = record_replay._ui_fingerprint(h)
    assert fp["labels"] == []
    assert fp["count"] == 0
    assert fp["app"] == "x"


def test_fingerprint_handles_ui_tree_without_kwargs():
    """Some mock helpers may not accept visible_only=. Fall back to no-arg call."""
    h = _make_fake_helpers()
    h.ui_tree = lambda: [{"type": "X", "label": "Hello"}]
    fp = record_replay._ui_fingerprint(h)
    assert fp["labels"] == ["Hello"]


def test_fingerprint_size_is_small():
    """Realistic-size fingerprint should serialize to ≤ 1KB JSON (target ~200 bytes)."""
    import json
    tree = [{"type": "Button", "label": f"Btn {i}"} for i in range(record_replay._FP_TOP_N)]
    h = _make_fake_helpers()
    h.active_app = lambda: "com.test.app"
    h.ui_tree = lambda visible_only=False, compact=False: tree
    fp = record_replay._ui_fingerprint(h)
    serialized = json.dumps(fp)
    assert len(serialized) < 1024, f"fingerprint too large: {len(serialized)} bytes"


# ---------- annotate / intent ----------

def test_annotate_exists():
    assert hasattr(record_replay, "annotate")


def test_annotate_rejects_empty_intent():
    with pytest.raises(ValueError):
        record_replay.annotate("")
    with pytest.raises(ValueError):
        record_replay.annotate("   ")
    with pytest.raises(ValueError):
        record_replay.annotate(None)  # type: ignore


def test_annotate_outside_recording_is_noop():
    """annotate() block outside start_recording does not raise and does not journal."""
    with record_replay.annotate("noop intent"):
        pass  # no recording active → just sets/restores thread state


def test_annotate_tags_recorded_calls(tmp_path):
    """Calls inside annotate block carry intent in the journal."""
    h = _make_fake_helpers()
    h.active_app = lambda: "com.test.app"
    h.ui_tree = lambda visible_only=False, compact=False: [
        {"type": "Btn", "label": "Compose"},
    ]
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    try:
        with record_replay.annotate("open compose"):
            h.tap_at_xy(10, 20)
            h.type_text("hi")
        # Outside the block → no intent tagged
        h.tap_at_xy(30, 40)
    finally:
        record_replay.stop_recording()

    sidecar = out.with_suffix(".py.jsonl")
    assert sidecar.exists(), "sidecar .jsonl should be written when any entry has intent"
    lines = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    assert lines[0]["intent"] == "open compose"
    assert lines[1]["intent"] == "open compose"
    assert "intent" not in lines[2]
    # Fingerprint captured at annotate __enter__
    assert "fingerprint" in lines[0]
    assert lines[0]["fingerprint"]["app"] == "com.test.app"
    assert "Compose" in lines[0]["fingerprint"]["labels"]


def test_annotate_no_sidecar_when_no_intents(tmp_path):
    """No annotate used → no .jsonl sidecar written (backward compat)."""
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    h.tap_at_xy(1, 2)
    record_replay.stop_recording()
    assert not out.with_suffix(".py.jsonl").exists()


def test_annotate_restores_prev_intent_on_exit(tmp_path):
    """After annotate block exits, recording state returns to prior intent."""
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    try:
        assert record_replay._state["current_intent"] is None
        with record_replay.annotate("outer"):
            assert record_replay._state["current_intent"] == "outer"
            with record_replay.annotate("inner"):
                assert record_replay._state["current_intent"] == "inner"
            assert record_replay._state["current_intent"] == "outer"
        assert record_replay._state["current_intent"] is None
    finally:
        record_replay.stop_recording()


def test_annotate_clears_state_on_exception(tmp_path):
    """If body raises inside annotate, state still restored."""
    h = _make_fake_helpers()
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    try:
        with pytest.raises(RuntimeError):
            with record_replay.annotate("crashy"):
                raise RuntimeError("boom")
        assert record_replay._state["current_intent"] is None
    finally:
        record_replay.stop_recording()


def test_annotate_fingerprint_captured_at_entry(tmp_path):
    """Fingerprint is snapshotted once at __enter__, not re-fetched per call."""
    h = _make_fake_helpers()
    tree_state = [[{"type": "B", "label": "First"}]]  # mutable, so we can mutate between calls
    h.active_app = lambda: "app"
    h.ui_tree = lambda visible_only=False, compact=False: tree_state[0]
    out = tmp_path / "rec.py"
    record_replay.start_recording(str(out), helpers=h)
    try:
        with record_replay.annotate("step"):
            h.tap_at_xy(1, 2)
            # Change UI mid-block — the journal entries should still all reference
            # the fingerprint captured at __enter__.
            tree_state[0] = [{"type": "B", "label": "AfterChange"}]
            h.tap_at_xy(3, 4)
    finally:
        record_replay.stop_recording()

    lines = [json.loads(l) for l in out.with_suffix(".py.jsonl").read_text().splitlines() if l.strip()]
    assert lines[0]["fingerprint"]["labels"] == ["First"]
    assert lines[1]["fingerprint"]["labels"] == ["First"]  # not "AfterChange"
