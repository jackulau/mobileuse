"""B5 — YOLO-nano distillation: dataset conversion (pure) + import-guarded training.

The conversion is exercised directly (Pillow only). ultralytics is absent in CI,
so the training path is asserted to skip cleanly rather than raise.
"""
import os
from pathlib import Path

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


def test_held_out_split_is_disjoint(tmp_path):
    # 5 distinct images -> train.txt and val.txt must not overlap, both non-empty.
    samples = [{"screenshot": _make_png(tmp_path / f"s{i}.png"), "bbox": [1, 1, 10, 10],
                "label": "Btn"} for i in range(5)]
    out = tmp_path / "ds"
    stats = build_yolo_dataset(samples, out)
    assert stats["images"] == 5
    assert "train: train.txt" in (out / "data.yaml").read_text()
    assert "val: val.txt" in (out / "data.yaml").read_text()
    train = set((out / "train.txt").read_text().split())
    val = set((out / "val.txt").read_text().split())
    assert train and val
    assert train.isdisjoint(val)                # held-out: never validate on train
    assert stats["train_images"] + stats["val_images"] == 5


def test_stem_collision_is_deduped(tmp_path):
    # Two DIFFERENT screenshots sharing a basename must both survive (no overwrite).
    d1, d2 = tmp_path / "s1", tmp_path / "s2"
    d1.mkdir(); d2.mkdir()
    a1 = _make_png(d1 / "screen.png")
    a2 = _make_png(d2 / "screen.png")
    out = tmp_path / "ds"
    stats = build_yolo_dataset(
        [{"screenshot": a1, "bbox": [1, 1, 10, 10], "label": "A"},
         {"screenshot": a2, "bbox": [2, 2, 10, 10], "label": "B"}], out)
    assert stats["images"] == 2                 # neither silently dropped
    pngs = sorted(p.name for p in (out / "images").glob("*.png"))
    assert len(pngs) == 2 and pngs[0] != pngs[1]


def test_train_skips_cleanly_without_ultralytics(tmp_path, monkeypatch):
    # Force the absent path regardless of the host (ultralytics may or may not exist).
    monkeypatch.setattr(td, "available", lambda: False)
    out = tmp_path / "ds"
    build_yolo_dataset(_samples(tmp_path), out)
    res = train(str(out), epochs=1)
    assert res["status"] == "skipped"
    assert "ultralytics" in res["reason"]


def _fake_ultralytics(write_weights=True):
    """A stand-in ultralytics module so the train() output-validation path is testable
    without the real (heavy, CI-absent) dep. Its YOLO.train writes a stub best.pt and
    its predict() runs clean, so validate_weights() succeeds iff the file was written."""
    import types
    fake = types.ModuleType("ultralytics")

    class _Trainer:
        best = None

    class FakeYOLO:
        def __init__(self, model):
            self.trainer = _Trainer()

        def train(self, **kw):
            sd = Path(kw["project"]) / "train"
            (sd / "weights").mkdir(parents=True, exist_ok=True)
            if write_weights:
                (sd / "weights" / "best.pt").write_bytes(b"stub-checkpoint")
            return types.SimpleNamespace(save_dir=str(sd))

        def predict(self, **kw):
            return []

    fake.YOLO = FakeYOLO
    return fake


def test_validate_weights_false_on_missing_or_absent():
    # Self-validation never raises; a missing/empty path is simply not a usable model.
    assert callable(td.validate_weights)
    assert td.validate_weights("") is False
    assert td.validate_weights("/no/such/checkpoint.pt") is False


def test_train_aborts_on_empty_dataset(tmp_path):
    # Preflight must fire BEFORE ultralytics is consulted, regardless of whether it
    # is installed — an empty run would otherwise error mid-train.
    out = tmp_path / "empty"
    build_yolo_dataset([], out)
    res = train(str(out), epochs=1)
    assert res["status"] == "empty_dataset"
    assert res["images"] == 0 and res["boxes"] == 0


def test_train_reports_trained_only_after_weights_validate(tmp_path, monkeypatch):
    import sys
    out = tmp_path / "ds"
    build_yolo_dataset(_samples(tmp_path), out)
    monkeypatch.setitem(sys.modules, "ultralytics", _fake_ultralytics(write_weights=True))
    monkeypatch.setattr(td, "available", lambda: True)
    res = train(str(out), epochs=1)
    assert res["status"] == "trained"
    assert res["verified"] is True
    assert res["weights"].endswith("best.pt")


def test_train_reports_unverified_when_checkpoint_absent(tmp_path, monkeypatch):
    import sys
    out = tmp_path / "ds"
    build_yolo_dataset(_samples(tmp_path), out)
    # YOLO.train writes NO weights -> resolved best.pt does not exist -> not 'trained'.
    monkeypatch.setitem(sys.modules, "ultralytics", _fake_ultralytics(write_weights=False))
    monkeypatch.setattr(td, "available", lambda: True)
    res = train(str(out), epochs=1)
    assert res["status"] == "trained_unverified"
    assert res["verified"] is False
    assert "reason" in res


def test_resolve_base_model_prefers_repo_root_copy(tmp_path, monkeypatch):
    # A bare filename must resolve to the repo-root copy WHEN one is present
    # (downloaded by a prior train — *.pt is gitignored, so a fresh clone has
    # none), keeping offline training free of implicit ultralytics downloads.
    (tmp_path / "yolov8n.pt").write_bytes(b"fake-weights")
    monkeypatch.setattr(td, "_REPO_ROOT", tmp_path)
    resolved = td._resolve_base_model("yolov8n.pt")
    assert resolved == str(tmp_path / "yolov8n.pt")
    assert os.path.isabs(resolved) and os.path.exists(resolved)
    # A path WITH a directory component is the caller's explicit choice — left untouched.
    assert td._resolve_base_model("some/dir/custom.pt") == "some/dir/custom.pt"
    # An unknown bare name with no repo copy is returned unchanged (let ultralytics decide).
    assert td._resolve_base_model("definitely-not-shipped.pt") == "definitely-not-shipped.pt"


def test_available_is_bool():
    assert isinstance(available(), bool)


def test_cli_help_exits_zero(capsys):
    assert td.train_main(["--help"]) == 0
    assert "train-detector" in capsys.readouterr().out
