"""D3 — synthetic seed UI dataset generator (Pillow-only, deterministic, device-free).

Proves the rows it emits are consumable UNCHANGED by both downstream consumers:
build_yolo_dataset (YOLO training) and LocalElementMatcher.from_samples (templates).
"""
from mobile_use.synthetic_ui import generate_seed_dataset
from mobile_use.train_detector import build_yolo_dataset


def test_rows_have_canonical_shape(tmp_path):
    s = generate_seed_dataset(tmp_path / "ds", n=4, seed=7)
    assert len(s) > 0
    for r in s:
        assert "bbox" in r and "label" in r and "screenshot" in r
        assert len(r["bbox"]) == 4 and all(isinstance(v, float) for v in r["bbox"])
    # *_detections.jsonl layout written next to the images
    assert (tmp_path / "ds" / "seed_detections.jsonl").exists()


def test_is_deterministic(tmp_path):
    a = generate_seed_dataset(tmp_path / "a", n=4, seed=7)
    b = generate_seed_dataset(tmp_path / "b", n=4, seed=7)
    assert [r["label"] for r in a] == [r["label"] for r in b]
    assert [r["bbox"] for r in a] == [r["bbox"] for r in b]
    # different seed -> different layout
    c = generate_seed_dataset(tmp_path / "c", n=4, seed=99)
    assert [r["bbox"] for r in a] != [r["bbox"] for r in c]


def test_feeds_build_yolo_dataset(tmp_path):
    s = generate_seed_dataset(tmp_path / "ds", n=6, seed=1)
    stats = build_yolo_dataset(s, tmp_path / "yolo")
    assert stats["images"] == 6
    assert stats["boxes"] == len(s)
    assert stats["train_images"] >= 1 and stats["val_images"] >= 1


def test_feeds_template_matcher(tmp_path):
    import pytest
    pytest.importorskip("cv2")
    from mobile_use.local_detector import LocalElementMatcher
    s = generate_seed_dataset(tmp_path / "ds", n=4, seed=3)
    m = LocalElementMatcher.from_samples(s)
    assert m.template_count > 0          # crops were readable as templates
