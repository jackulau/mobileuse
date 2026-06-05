"""D9 — REAL end-to-end proof: synthetic dataset -> trained yolov8n weights -> grounding.

This is the deliverable that proves the whole pipeline produces an ACTUAL detector that
grounds elements on HELD-OUT screens (not just that the code paths don't raise). It does
real training, so it is double-gated:

  * ``pytest.importorskip("ultralytics")`` — CI without the [yolo] extra skips cleanly,
    keeping the 1043-test baseline green.
  * ``MU_RUN_TRAIN_E2E=1`` opt-in — the ~1-2 min CPU training never runs in the normal
    unit suite (and so can never SIGILL it on a box whose default ``polars`` wheel is
    incompatible with an older CPU; use ``polars-lts-cpu`` there).

Run it explicitly:  MU_RUN_TRAIN_E2E=1 pytest tests/test_train_end_to_end.py -q
"""
import os
from pathlib import Path

import pytest

pytest.importorskip("ultralytics")

if os.environ.get("MU_RUN_TRAIN_E2E") != "1":
    pytest.skip("set MU_RUN_TRAIN_E2E=1 to run the real (~1-2 min) training e2e",
                allow_module_level=True)

from mobile_use.synthetic_ui import generate_seed_dataset  # noqa: E402
from mobile_use.train_detector import (  # noqa: E402
    YoloDetector,
    build_yolo_dataset,
    train,
)

_LABELS = ("Search", "Send")
# Augmentation OFF + enough epochs is what lets a nano model memorise the simple,
# high-contrast synthetic UI and ground it on held-out screens with high confidence.
_AUG_OFF = dict(mosaic=0.0, close_mosaic=0, hsv_h=0.0, hsv_s=0.0, hsv_v=0.0,
                translate=0.0, scale=0.0, fliplr=0.0, erasing=0.0,
                degrees=0.0, shear=0.0, perspective=0.0)


def _gt_boxes(yolo_dir, img_path, classes):
    """Ground-truth (label, (cx,cy,x1,y1,x2,y2)) pixel boxes for one image."""
    from PIL import Image
    stem = Path(img_path).stem
    lbl = Path(yolo_dir) / "labels" / f"{stem}.txt"
    if not lbl.exists():
        return []
    with Image.open(img_path) as im:
        W, H = im.size
    out = []
    for line in lbl.read_text().splitlines():
        if not line.strip():
            continue
        c, xc, yc, w, h = line.split()
        cx, cy, w, h = float(xc) * W, float(yc) * H, float(w) * W, float(h) * H
        out.append((classes[int(c)], (cx, cy, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return out


def test_train_and_ground_on_held_out(tmp_path):
    work = tmp_path / "e2e"
    samples = generate_seed_dataset(work / "seed", n=16, seed=5,
                                    labels=_LABELS, per_screen=4)
    yolo_dir = work / "yolo"
    stats = build_yolo_dataset(samples, yolo_dir)
    assert stats["val_images"] >= 1 and stats["train_images"] >= 1   # real held-out split

    res = train(str(yolo_dir), epochs=80, imgsz=320,
                project=str(work / "runs"), seed=0, deterministic=True,
                batch=8, patience=0, plots=False, val=True, workers=0, **_AUG_OFF)
    assert res["status"] == "trained"
    weights = res["weights"]
    assert weights.endswith("best.pt") or weights.endswith("last.pt")
    assert os.path.exists(weights)                                   # D1 path bug would fail here

    classes = stats["classes"]
    det = YoloDetector(weights, min_confidence=0.25)
    val_imgs = (yolo_dir / "val.txt").read_text().split()
    assert val_imgs

    grounded = 0          # predictions that land on a same-label GT box (true grounding)
    any_pred = 0
    for vi in val_imgs:
        preds = det.predict(vi)
        any_pred += len(preds)
        for p in preds:
            assert p["label"] in classes                            # never an unknown class
            assert p["method"] == "yolo"
            for gname, (_cx, _cy, x1, y1, x2, y2) in _gt_boxes(yolo_dir, vi, classes):
                if p["label"] == gname and x1 <= p["cx"] <= x2 and y1 <= p["cy"] <= y2:
                    grounded += 1
                    break

    assert any_pred > 0, "trained detector produced no detections on held-out screens"
    assert grounded > 0, "no prediction grounded onto a correct-label held-out element"
