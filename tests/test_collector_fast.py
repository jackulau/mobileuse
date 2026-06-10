"""goal/022 D2 — collector cheap record: content-addressed screenshot dedupe,
compact ui_tree dump, and the crop-overwrite fix.

The old record() copied the full PNG then MD5'd it on EVERY perceive; a repeated
identical screen (the common case mid-run) now costs one stat. Crops used to be
named after the SOURCE screenshot basename — a fixed device temp path — so every
crop overwrote the previous one and older rows pointed at the wrong pixels.
"""
import json

import pytest

from mobile_use import collector as collector_mod
from mobile_use.collector import Collector


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "DATA_DIR", tmp_path / "training")
    return tmp_path


def _png(path, content=b"png-bytes-A"):
    path.write_bytes(content)
    return str(path)


def test_identical_screenshot_copied_once(sandbox):
    c = Collector(session_name="s1", platform="ios")
    shot = _png(sandbox / "shot.png")
    events = [c.record(screenshot_path=shot, ui_tree=[]) for _ in range(3)]

    stored = list((collector_mod.DATA_DIR / "s1" / "screenshots").glob("*.png"))
    assert len(stored) == 1, "identical content must be stored once"
    # Every row still points at a real file with the same hash.
    assert len({e["screenshot"] for e in events}) == 1
    assert len({e["screenshot_hash"] for e in events}) == 1


def test_distinct_screenshots_all_stored(sandbox):
    c = Collector(session_name="s2", platform="ios")
    e1 = c.record(screenshot_path=_png(sandbox / "a.png", b"content-A"))
    e2 = c.record(screenshot_path=_png(sandbox / "b.png", b"content-B"))
    assert e1["screenshot"] != e2["screenshot"]
    assert e1["screenshot_hash"] != e2["screenshot_hash"]
    stored = list((collector_mod.DATA_DIR / "s2" / "screenshots").glob("*.png"))
    assert len(stored) == 2


def test_detection_screenshot_shares_store(sandbox):
    """Perception + detection rows for the same content share one stored file."""
    c = Collector(session_name="s3", platform="ios")
    shot = _png(sandbox / "shot.png")
    pe = c.record(screenshot_path=shot)
    de = c.record_detection_sample(screenshot_path=shot, bbox=(1, 1, 5, 5),
                                   label="btn", save_crop=False)
    assert pe["screenshot"] == de["screenshot"]
    stored = list((collector_mod.DATA_DIR / "s3" / "screenshots").glob("*.png"))
    assert len(stored) == 1


def test_crops_no_longer_overwrite(sandbox):
    """Regression: two samples from the SAME source path keep distinct crops."""
    PIL = pytest.importorskip("PIL.Image")
    shot = sandbox / "iph-shot.png"
    PIL.new("RGB", (100, 100), (200, 30, 30)).save(shot)

    c = Collector(session_name="s4", platform="ios")
    e1 = c.record_detection_sample(screenshot_path=str(shot),
                                   bbox=(0, 0, 10, 10), label="a")
    e2 = c.record_detection_sample(screenshot_path=str(shot),
                                   bbox=(20, 20, 30, 30), label="b")
    assert e1["crop"] != e2["crop"], "crop files must be unique per sample"
    crops = list((collector_mod.DATA_DIR / "s4" / "crops").glob("*-crop.png"))
    assert len(crops) == 2


def test_tree_compacted_and_capped_by_default(sandbox, monkeypatch):
    monkeypatch.delenv("MU_COLLECT_TREE", raising=False)
    monkeypatch.setenv("MU_COLLECT_TREE_MAX", "2")
    c = Collector(session_name="s5", platform="ios")
    tree = [{"type": "Button", "label": f"b{i}", "cx": i, "cy": i,
             "raw_attributes": "X" * 500, "xpath": "/very/deep/path" * 20}
            for i in range(5)]
    e = c.record(ui_tree=tree)
    assert e["ui_tree_size"] == 5            # true size always recorded
    assert len(e["ui_tree"]) == 2            # capped
    for el in e["ui_tree"]:                  # noisy fields dropped
        assert "raw_attributes" not in el and "xpath" not in el
        assert el["type"] == "Button" and "label" in el


def test_tree_full_mode_preserves_raw(sandbox, monkeypatch):
    monkeypatch.setenv("MU_COLLECT_TREE", "full")
    c = Collector(session_name="s6", platform="ios")
    tree = [{"type": "Button", "raw_attributes": "kept"}]
    e = c.record(ui_tree=tree)
    assert e["ui_tree"] == tree


def test_rows_round_trip_for_consumers(sandbox):
    """Rows written by the new path still parse + feed load_detection_samples."""
    c = Collector(session_name="s7", platform="ios")
    shot = _png(sandbox / "shot.png")
    c.record_detection_sample(screenshot_path=shot, bbox=(1, 2, 3, 4),
                              label="send", save_crop=False)
    rows = [json.loads(line) for line in
            open(c.detections_path, encoding="utf-8").read().splitlines()]
    assert rows and rows[0]["bbox"] and rows[0]["label"] == "send"
    samples = collector_mod.load_detection_samples(["s7"])
    assert len(samples) == 1 and samples[0]["screenshot"]
