"""B5 — YOLO-nano distillation: dataset conversion (pure) + import-guarded training.

The conversion is exercised directly (Pillow only). ultralytics is absent in CI,
so the training path is asserted to skip cleanly rather than raise.
"""
from pathlib import Path

import pytest

import mobile_use.train_detector as td
from mobile_use.train_detector import available, build_yolo_dataset, train


def _make_png(path, size=(200, 100)):
    from PIL import Image
    Image.new("RGB", size, (10, 20, 30)).save(path)
    return str(path)


def _samples(tmp_path):
    a = _make_png(tmp_path / "a.png", (200, 100))
    b = _make_png(tmp_path / "b.png", (200, 100))
    return [
        # image a: two boxes, two labels
        {"screenshot": a, "bbox": [10, 10, 20, 10], "label": "Search"},
        {"screenshot": a, "bbox": [100, 50, 40, 20], "label": "Send"},
        # image b: one box
        {"screenshot": b, "bbox": [0, 0, 50, 25], "label": "Search"},
        # broken sample (no image) -> skipped
        {"screenshot": str(tmp_path / "missing.png"), "bbox": [1, 1, 2, 2], "label": "X"},
    ]


def test_build_dataset_layout_and_normalization(tmp_path):
    out = tmp_path / "ds"
    stats = build_yolo_dataset(_samples(tmp_path), out)
    assert stats["images"] == 2          # a, b (missing skipped)
    assert stats["boxes"] == 3
    assert stats["classes"] == ["Search", "Send"]  # sorted
    assert (out / "images" / "a.png").exists()
    assert (out / "labels" / "a.txt").exists()
    assert (out / "data.yaml").exists()

    # a.txt: box (10,10,20,10) on 200x100 -> xc=(10+10)/200=0.1, yc=(10+5)/100=0.15, w=0.1, h=0.1
    a_lines = (out / "labels" / "a.txt").read_text().strip().splitlines()
    assert len(a_lines) == 2
    cls, xc, yc, w, h = a_lines[0].split()
    assert cls == "0"  # "Search"
    assert abs(float(xc) - 0.1) < 1e-3
    assert abs(float(yc) - 0.15) < 1e-3
    assert abs(float(w) - 0.1) < 1e-3
    assert abs(float(h) - 0.1) < 1e-3

    yaml = (out / "data.yaml").read_text()
    assert "nc: 2" in yaml
    assert "0: Search" in yaml and "1: Send" in yaml


def test_single_class_collapses_labels(tmp_path):
    stats = build_yolo_dataset(_samples(tmp_path), tmp_path / "ds1", single_class=True)
    assert stats["classes"] == ["ui_element"]
    yaml = (Path(stats["data_yaml"])).read_text()
    assert "nc: 1" in yaml
    # every label line uses class 0
    for txt in (Path(stats["dataset_dir"]) / "labels").glob("*.txt"):
        for line in txt.read_text().splitlines():
            if line.strip():
                assert line.split()[0] == "0"


def test_empty_samples_yield_empty_dataset(tmp_path):
    stats = build_yolo_dataset([], tmp_path / "empty")
    assert stats["images"] == 0
    assert stats["boxes"] == 0
    assert (tmp_path / "empty" / "data.yaml").exists()


def test_train_skips_cleanly_without_ultralytics(tmp_path, monkeypatch):
    # Force the absent path regardless of the host (ultralytics may or may not exist).
    monkeypatch.setattr(td, "available", lambda: False)
    out = tmp_path / "ds"
    build_yolo_dataset(_samples(tmp_path), out)
    res = train(str(out), epochs=1)
    assert res["status"] == "skipped"
    assert "ultralytics" in res["reason"]


def test_available_is_bool():
    assert isinstance(available(), bool)


def test_cli_help_exits_zero(capsys):
    assert td.train_main(["--help"]) == 0
    assert "train-detector" in capsys.readouterr().out
