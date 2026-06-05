"""Distill the self-labeled dataset into a local YOLO-nano element detector.

The self-labeling capture (B2) accumulates ``(screenshot, bbox, label)`` samples
for free from the accessibility tree. This module turns them into a trained
pixel detector so tree-less screens (games, canvas, web views) — where the tree
gives nothing — can still be grounded locally, without a VLM round-trip.

A YOLOv8-nano (CNN object detector) is the right tool here — NOT an RNN, which
models sequences, not spatial layout. Two stages:

  * ``build_yolo_dataset`` — convert samples -> YOLO layout (images/, labels/,
    data.yaml). Pure + dependency-light (only Pillow, already required), so it is
    always testable.
  * ``train`` / ``load_detector`` — import-guarded ultralytics. With the optional
    ``[yolo]`` extra absent, ``train`` returns ``{"status": "skipped"}`` instead
    of raising — the harness degrades cleanly to the tree/template/VLM paths.
"""
import os
import shutil
from pathlib import Path


def available():
    """True iff the optional YOLO training dep (ultralytics) is importable."""
    try:
        import ultralytics  # noqa: F401
        return True
    except Exception:
        return False


def _render_data_yaml(dataset_dir, classes):
    """Render a minimal YOLO data.yaml (index: name form tolerates spaces in labels)."""
    lines = [
        f"path: {Path(dataset_dir).resolve()}",
        "train: images",
        "val: images",
        f"nc: {len(classes)}",
        "names:",
    ]
    for i, name in enumerate(classes):
        lines.append(f"  {i}: {name}")
    return "\n".join(lines) + "\n"


def build_yolo_dataset(samples, out_dir, single_class=False):
    """Convert self-labeled detection samples into a YOLOv8 dataset on disk.

    ``samples`` are B2 detection rows (``screenshot`` path + pixel ``bbox`` +
    ``label``). Boxes are grouped per screenshot, normalized to the image size,
    and written as YOLO ``<cls> <xc> <yc> <w> <h>`` label files alongside copied
    images and a ``data.yaml``. Returns ``{images, boxes, classes, data_yaml,
    dataset_dir}``. ``single_class`` collapses every label to one ``ui_element``
    class (useful when per-label samples are sparse).
    """
    from PIL import Image

    out = Path(out_dir)
    img_dir, lbl_dir = out / "images", out / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    by_image = {}
    for s in samples or []:
        shot, bbox = s.get("screenshot"), s.get("bbox")
        if shot and os.path.exists(shot) and bbox:
            by_image.setdefault(shot, []).append(s)

    if single_class:
        classes = ["ui_element"]

        def cls_of(_s):
            return 0
    else:
        classes = sorted({(s.get("label") or "ui_element")
                          for grp in by_image.values() for s in grp})
        index = {name: i for i, name in enumerate(classes)}

        def cls_of(s):
            return index.get(s.get("label") or "ui_element", 0)

    n_img = n_box = 0
    for shot, grp in by_image.items():
        try:
            with Image.open(shot) as im:
                width, height = im.size
        except Exception:
            continue
        if width <= 0 or height <= 0:
            continue
        stem = Path(shot).stem
        shutil.copy2(shot, img_dir / f"{stem}.png")
        lines = []
        for s in grp:
            x, y, w, h = s["bbox"]
            if w <= 0 or h <= 0:
                continue
            xc = min(max((x + w / 2.0) / width, 0.0), 1.0)
            yc = min(max((y + h / 2.0) / height, 0.0), 1.0)
            wn = min(max(w / width, 0.0), 1.0)
            hn = min(max(h / height, 0.0), 1.0)
            lines.append(f"{cls_of(s)} {xc:.6f} {yc:.6f} {wn:.6f} {hn:.6f}")
            n_box += 1
        (lbl_dir / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        n_img += 1

    data_yaml = out / "data.yaml"
    data_yaml.write_text(_render_data_yaml(out, classes), encoding="utf-8")
    return {"images": n_img, "boxes": n_box, "classes": classes,
            "data_yaml": str(data_yaml), "dataset_dir": str(out)}


def train(dataset_dir, epochs=10, imgsz=640, model="yolov8n.pt", project=None):
    """Train a YOLOv8-nano on a built dataset. Import-guarded.

    Returns ``{"status": "skipped", ...}`` when ultralytics is absent so callers
    never crash on installs without the ``[yolo]`` extra; otherwise trains and
    returns ``{"status": "trained", "weights": <best.pt>}``.
    """
    if not available():
        return {"status": "skipped",
                "reason": "ultralytics not installed — `pip install 'mobile-use[yolo]'`"}
    from ultralytics import YOLO

    data = str(Path(dataset_dir) / "data.yaml")
    project = project or str(Path(dataset_dir) / "runs")
    yolo = YOLO(model)
    result = yolo.train(data=data, epochs=epochs, imgsz=imgsz,
                        project=project, verbose=False)
    # ultralytics writes the actual checkpoints to <save_dir>/weights/{best,last}.pt;
    # result.save_dir is only the RUN directory, so resolve the real weights file.
    save_dir = getattr(result, "save_dir", None) or project
    weights = _resolve_weights(save_dir, yolo)
    return {"status": "trained", "data": data, "epochs": epochs,
            "save_dir": str(save_dir), "weights": weights}


def _resolve_weights(save_dir, yolo=None):
    """Best-effort path to the trained checkpoint: best.pt, then trainer-reported, then last.pt.

    Returns the canonical ``<save_dir>/weights/best.pt`` even when nothing exists yet
    (so callers get a stable, loadable-once-flushed path rather than a run directory).
    """
    wdir = Path(save_dir) / "weights"
    best, last = wdir / "best.pt", wdir / "last.pt"
    reported = getattr(getattr(yolo, "trainer", None), "best", None)
    if best.exists():
        return str(best)
    if reported and Path(reported).exists():
        return str(reported)
    if last.exists():
        return str(last)
    return str(best)


def load_detector(weights):
    """Load a trained detector for serving, or None if ultralytics is absent."""
    if not available():
        return None
    from ultralytics import YOLO
    return YOLO(weights)


def train_main(argv):
    """`mobile-use train-detector [--session N] [--out DIR] [--single-class] [--train] [--epochs N]`."""
    if argv and argv[0] in {"-h", "--help"}:
        print(
            "mobile-use train-detector — distill the self-labeled dataset into a YOLO-nano detector.\n\n"
            "USAGE:\n"
            "  mobile-use train-detector                 build a YOLO dataset from ALL sessions\n"
            "  mobile-use train-detector --session NAME   only this session\n"
            "  mobile-use train-detector --out DIR        dataset output dir\n"
            "  mobile-use train-detector --single-class   collapse labels to one 'ui_element' class\n"
            "  mobile-use train-detector --train [--epochs N]  also train (needs the [yolo] extra)\n\n"
            "Self-labeled samples come free from the accessibility tree during agent runs.\n"
        )
        return 0

    from mobile_use.collector import DATA_DIR, load_detection_samples

    session = out = None
    single_class = do_train = False
    epochs = 10
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--session" and i + 1 < len(argv):
            session = argv[i + 1]; i += 1
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]; i += 1
        elif a == "--epochs" and i + 1 < len(argv):
            try:
                epochs = int(argv[i + 1])
            except ValueError:
                print(f"invalid --epochs {argv[i + 1]!r}"); return 2
            i += 1
        elif a == "--single-class":
            single_class = True
        elif a == "--train":
            do_train = True
        i += 1

    samples = load_detection_samples([session] if session else None)
    if not samples:
        print("No self-labeled detection samples found yet.\n"
              "  Run the agent (mobile-use agent --task ...) to accumulate them — "
              "every grounded tap records one for free.")
        return 1

    out = out or str(Path(DATA_DIR).parent / "detector-dataset")
    stats = build_yolo_dataset(samples, out, single_class=single_class)
    print(f"Built YOLO dataset: {stats['images']} images, {stats['boxes']} boxes, "
          f"{len(stats['classes'])} classes -> {stats['dataset_dir']}")

    if do_train:
        res = train(stats["dataset_dir"], epochs=epochs)
        if res["status"] == "skipped":
            print(f"Training skipped: {res['reason']}")
            return 0
        print(f"Trained -> {res.get('save_dir')}")
    else:
        print("Add --train to train a yolov8n on it (needs `pip install 'mobile-use[yolo]'`).")
    return 0
