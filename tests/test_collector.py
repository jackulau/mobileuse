"""Tests for training data collector."""
import json
import os
import tempfile

import pytest

from mobile_use.collector import Collector, export_training_data, list_sessions, training_stats


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("mobile_use.collector.DATA_DIR", tmp_path)
    return tmp_path


def test_collector_creates_session_dir(tmp_data_dir):
    c = Collector(session_name="s1")
    assert (tmp_data_dir / "s1").is_dir()
    assert (tmp_data_dir / "s1" / "screenshots").is_dir()


def test_record_basic(tmp_data_dir):
    c = Collector(session_name="s1", platform="ios")
    event = c.record(action="tap(find(text='OK'))", success=True)
    assert event["action"] == "tap(find(text='OK'))"
    assert event["success"] is True
    assert event["platform"] == "ios"
    assert event["session"] == "s1"
    assert c.count == 1

    lines = open(c.data_path).read().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action"] == "tap(find(text='OK'))"


def test_record_with_ui_tree(tmp_data_dir):
    c = Collector(session_name="s1")
    tree = [{"type": "Button", "label": "OK", "x": 100, "y": 200}]
    event = c.record(ui_tree=tree, active_app={"bundleId": "com.test"})
    assert event["ui_tree"] == tree
    assert event["ui_tree_size"] == 1
    assert event["active_app"]["bundleId"] == "com.test"


def test_record_with_screenshot(tmp_data_dir, tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG fake")
    c = Collector(session_name="s1")
    event = c.record(screenshot_path=str(img))
    assert "screenshot" in event
    assert "screenshot_hash" in event
    assert os.path.exists(event["screenshot"])


def test_record_perception(tmp_data_dir):
    c = Collector(session_name="s1")
    state = {
        "ui_tree": [{"type": "Label", "label": "Hi"}],
        "active_app": {"bundleId": "com.test"},
        "window_size": {"width": 390, "height": 844},
    }
    event = c.record_perception(state, action="swipe_up()", success=True)
    assert event["action"] == "swipe_up()"
    assert event["window_size"] == {"width": 390, "height": 844}
    assert c.count == 1


def test_record_multiple_events(tmp_data_dir):
    c = Collector(session_name="s1")
    c.record(action="tap1")
    c.record(action="tap2")
    c.record(action="tap3")
    assert c.count == 3
    lines = open(c.data_path).read().splitlines()
    assert len(lines) == 3


def test_list_sessions(tmp_data_dir):
    Collector(session_name="alpha")
    Collector(session_name="beta")
    sessions = list_sessions()
    assert "alpha" in sessions
    assert "beta" in sessions


def test_export_training_data(tmp_data_dir, tmp_path):
    c = Collector(session_name="s1")
    c.record(action="tap1", active_app={"bundleId": "com.a"})
    c.record(action="tap2", active_app={"bundleId": "com.b"})

    out = tmp_path / "export.jsonl"
    count = export_training_data(str(out))
    assert count == 2
    lines = out.read_text().splitlines()
    assert len(lines) == 2


def test_export_without_tree(tmp_data_dir, tmp_path):
    c = Collector(session_name="s1")
    c.record(ui_tree=[{"type": "X"}], action="tap")
    out = tmp_path / "export.jsonl"
    export_training_data(str(out), include_tree=False)
    parsed = json.loads(out.read_text().strip())
    assert "ui_tree" not in parsed


def test_export_specific_sessions(tmp_data_dir, tmp_path):
    c1 = Collector(session_name="keep")
    c1.record(action="a")
    c2 = Collector(session_name="skip")
    c2.record(action="b")
    out = tmp_path / "export.jsonl"
    count = export_training_data(str(out), sessions=["keep"])
    assert count == 1


def test_training_stats(tmp_data_dir):
    c = Collector(session_name="s1")
    c.record(active_app={"bundleId": "com.test"})
    c.record(active_app={"bundleId": "com.other"})
    stats = training_stats()
    assert stats["sessions"] == 1
    assert stats["events"] == 2
    assert "com.test" in stats["apps"]
    assert "com.other" in stats["apps"]


def test_training_stats_empty(tmp_data_dir):
    stats = training_stats()
    assert stats["sessions"] == 0
    assert stats["events"] == 0


def test_metadata_field(tmp_data_dir):
    c = Collector(session_name="s1")
    event = c.record(action="tap", metadata={"model": "gpt-4", "confidence": 0.95})
    assert event["metadata"]["model"] == "gpt-4"
    assert event["metadata"]["confidence"] == 0.95
