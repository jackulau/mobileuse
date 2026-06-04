"""B2 — self-labeling bbox dataset capture (the accessibility tree is the labeler).

Device-free. The UI tree supplies box + label, so acting yields a labeled
object-detection sample at zero extra VLM cost.
"""
import json

import pytest

import mobile_use.collector as collector_mod
from mobile_use.collector import Collector, load_detection_samples


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(collector_mod, "DATA_DIR", tmp_path / "training-data")
    return tmp_path


def test_record_detection_writes_jsonl_row(sandbox):
    c = Collector(session_name="cap", platform="ios")
    ev = c.record_detection_sample(
        screenshot_path=None, bbox=(10, 20, 30, 40), label="Search",
        screen_sig="abc123", action="tap_at_xy",
    )
    assert ev["type"] == "detection"
    assert ev["label"] == "Search"
    assert ev["bbox_logical"] == [10.0, 20.0, 30.0, 40.0]
    assert ev["bbox"] == [10.0, 20.0, 30.0, 40.0]  # no image -> scale 1.0
    assert ev["scale"] == 1.0
    assert c.detection_count == 1
    rows = [json.loads(x) for x in open(c.detections_path) if x.strip()]
    assert rows[0]["label"] == "Search"


def test_scales_logical_box_to_pixels_and_crops(sandbox):
    from PIL import Image
    png = sandbox / "shot.png"
    Image.new("RGB", (200, 100), (123, 222, 64)).save(png)  # 2x of a 100pt-wide window

    c = Collector(session_name="cap2", platform="ios")
    ev = c.record_detection_sample(
        screenshot_path=str(png), bbox=(10, 10, 20, 10), label="Btn",
        window_size={"width": 100, "height": 50},
    )
    assert ev["scale"] == 2.0
    assert ev["bbox"] == [20.0, 20.0, 40.0, 20.0]   # logical * 2
    assert "crop" in ev
    from pathlib import Path
    assert Path(ev["crop"]).exists()


def test_match_element_picks_smallest_containing(monkeypatch):
    from mobile_use.agent_loop import AgentLoop
    tree = [
        {"type": "Cell", "x": 0, "y": 0, "w": 300, "h": 100, "label": "row"},
        {"type": "Button", "x": 90, "y": 40, "w": 40, "h": 20, "label": "Go"},
    ]
    el = AgentLoop._match_element(tree, 100, 50)
    assert el["label"] == "Go"  # smallest box containing the point


def test_no_match_returns_none():
    from mobile_use.agent_loop import AgentLoop
    tree = [{"type": "Button", "x": 0, "y": 0, "w": 10, "h": 10}]
    assert AgentLoop._match_element(tree, 500, 500) is None


def test_agent_loop_hook_captures_on_tap(sandbox, monkeypatch):
    monkeypatch.setenv("HOME", str(sandbox))
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="hook", collect=True)
    state = {
        "screenshot_path": None,
        "ui_tree": [{"type": "Button", "x": 90, "y": 40, "w": 40, "h": 20, "label": "Send"}],
        "marks": [{"i": 0, "type": "Button", "label": "Send", "cx": 110, "cy": 50}],
        "active_app": {"bundleId": "com.example"},
        "window_size": {"width": 390, "height": 844},
    }
    loop._maybe_capture_detection(state, "tap_at_xy", {"x": 110, "y": 50})
    samples = load_detection_samples(["hook"])
    assert len(samples) == 1
    assert samples[0]["label"] == "Send"
    assert "screen_sig" in samples[0]


def test_hook_skips_non_xy_verbs(sandbox, monkeypatch):
    monkeypatch.setenv("HOME", str(sandbox))
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="skip", collect=True)
    state = {"ui_tree": [{"x": 0, "y": 0, "w": 9, "h": 9}], "marks": []}
    loop._maybe_capture_detection(state, "type_text", {"text": "hi"})
    assert load_detection_samples(["skip"]) == []
