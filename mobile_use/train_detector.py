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


def _render_data_yaml(dataset_dir, classes, train="train.txt", val="val.txt"):
    """Render a minimal YOLO data.yaml (index: name form tolerates spaces in labels).

    ``train``/``val`` are image-list files (relative to ``path``) so validation runs
    on a HELD-OUT split, not the training images. Images stay in a flat ``images/``
    dir; ultralytics finds each label by swapping ``/images/``->``/labels/`` + ``.txt``.
    """
    lines = [
        f"path: {Path(dataset_dir).resolve()}",
        f"train: {train}",
        f"val: {val}",
        f"nc: {len(classes)}",
        "names:",
    ]
    for i, name in enumerate(classes):
        lines.append(f"  {i}: {name}")
    return "\n".join(lines) + "\n"


def _split_train_val(stems, val_fraction=0.2):
    """Deterministic held-out split of sorted image stems -> (train, val) lists.

    val gets the trailing ``max(1, round(n*val_fraction))`` of the SORTED stems when
    there are >=2 images (so val != train); with a single image it cannot be held out,
    so both splits get it (a degenerate but non-crashing fallback).
    """
    ordered = sorted(stems)
    n = len(ordered)
    if n < 2:
        return list(ordered), list(ordered)
    n_val = max(1, round(n * val_fraction))
    n_val = min(n_val, n - 1)  # always leave >=1 in train
    return ordered[:-n_val], ordered[-n_val:]


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
    used_stems = {}          # base-stem -> count, to dedupe colliding basenames
    written_stems = []       # the actual (unique) stems written, for the split
    for shot, grp in by_image.items():
        try:
            with Image.open(shot) as im:
                width, height = im.size
        except Exception:
            continue
        if width <= 0 or height <= 0:
            continue
        # Dedupe stem collisions: two different-path screenshots sharing a basename
        # would otherwise overwrite last-writer-wins, silently dropping training data.
        base = Path(shot).stem
        if base in used_stems:
            used_stems[base] += 1
            stem = f"{base}-{used_stems[base]}"
        else:
            used_stems[base] = 0
            stem = base
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
        written_stems.append(stem)
        n_img += 1

    # Held-out split: write image-list files so val != train.
    train_stems, val_stems = _split_train_val(written_stems)
    (out / "train.txt").write_text(
        "".join(f"{(img_dir / f'{s}.png').resolve()}\n" for s in train_stems), encoding="utf-8")
    (out / "val.txt").write_text(
        "".join(f"{(img_dir / f'{s}.png').resolve()}\n" for s in val_stems), encoding="utf-8")

    data_yaml = out / "data.yaml"
    data_yaml.write_text(_render_data_yaml(out, classes), encoding="utf-8")
    return {"images": n_img, "boxes": n_box, "classes": classes,
            "data_yaml": str(data_yaml), "dataset_dir": str(out),
            "train_images": len(train_stems), "val_images": len(val_stems)}


def _dataset_counts(dataset_dir):
    """Count built (images, boxes) on disk — the train() empty-dataset preflight.

    Reads the materialized ``images/*.png`` + ``labels/*.txt`` so the check reflects
    what ultralytics would actually see, not the pre-filter sample list.
    """
    img_dir, lbl_dir = Path(dataset_dir) / "images", Path(dataset_dir) / "labels"
    n_images = len(list(img_dir.glob("*.png"))) if img_dir.is_dir() else 0
    n_boxes = 0
    if lbl_dir.is_dir():
        for txt in lbl_dir.glob("*.txt"):
            try:
                n_boxes += sum(1 for ln in txt.read_text(encoding="utf-8").splitlines()
                               if ln.strip())
            except OSError:
                continue
    return {"images": n_images, "boxes": n_boxes}


def validate_weights(weights, sample_image=None):
    """True iff ``weights`` exists AND loads as a model AND runs one inference clean.

    This is the post-train self-check: a path that ``os.path.exists`` but is a
    truncated/incompatible checkpoint, or a model that cannot run a forward pass,
    is NOT a usable detector — so we actually load it and predict on a tiny dummy
    image. Returns False (never raises) when ultralytics is absent, the file is
    missing, or anything fails, so callers can gate on a real, loadable model.
    """
    try:
        if not weights or not os.path.exists(weights):
            return False
        model = load_detector(weights)
        if model is None:
            return False
        img, tmp = sample_image, None
        if img is None:
            import tempfile

            from PIL import Image
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            Image.new("RGB", (64, 64), (0, 0, 0)).save(tmp.name)
            img = tmp.name
        try:
            model.predict(source=img, verbose=False)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def train(dataset_dir, epochs=10, imgsz=640, model="yolov8n.pt", project=None,
          **train_kwargs):
    """Train a YOLOv8-nano on a built dataset. Import-guarded + self-validating.

    Preflights the built dataset and returns ``{"status": "empty_dataset", ...}``
    when there are no images/boxes (never hands ultralytics an empty run that would
    error mid-train). Returns ``{"status": "skipped", ...}`` when ultralytics is
    absent so callers never crash on installs without the ``[yolo]`` extra. After a
    real run it loads + runs one inference on the produced checkpoint and only
    reports ``{"status": "trained"}`` when that succeeds — otherwise
    ``{"status": "trained_unverified", "reason": ...}`` with the real weights path,
    so a missing/corrupt checkpoint is never silently reported as success. Extra
    ``train_kwargs`` (e.g. ``seed``, ``deterministic``, ``batch``, ``patience``)
    pass through to ``ultralytics``'s trainer for reproducible / bounded runs.
    """
    counts = _dataset_counts(dataset_dir)
    if counts["images"] == 0 or counts["boxes"] == 0:
        return {"status": "empty_dataset",
                "reason": "dataset has no images/boxes — capture self-labeled samples "
                          "first (run the agent) before training",
                "images": counts["images"], "boxes": counts["boxes"]}
    if not available():
        return {"status": "skipped",
                "reason": "ultralytics not installed — `pip install 'mobile-use[yolo]'`"}
    from ultralytics import YOLO

    data = str(Path(dataset_dir) / "data.yaml")
    project = project or str(Path(dataset_dir) / "runs")
    yolo = YOLO(_resolve_base_model(model))
    result = yolo.train(data=data, epochs=epochs, imgsz=imgsz,
                        project=project, verbose=False, **train_kwargs)
    # ultralytics writes the actual checkpoints to <save_dir>/weights/{best,last}.pt;
    # result.save_dir is only the RUN directory, so resolve the real weights file.
    save_dir = getattr(result, "save_dir", None) or project
    weights = _resolve_weights(save_dir, yolo)
    verified = validate_weights(weights)
    out = {"status": "trained" if verified else "trained_unverified",
           "data": data, "epochs": epochs, "save_dir": str(save_dir),
           "weights": weights, "verified": verified}
    if not verified:
        out["reason"] = ("training finished but the checkpoint at the reported path is "
                         "missing or failed to load + run one inference")
    return out


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


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_base_model(model):
    """Resolve a bare base-model filename to the committed repo-root copy if present.

    ultralytics treats a bare ``"yolov8n.pt"`` as relative-to-CWD and, when absent,
    silently downloads it from the network. The repo ships ``yolov8n.pt`` at its root,
    so prefer that local copy: offline training never triggers an implicit download.
    A path with a directory component, or one that already exists as given, is left
    untouched (caller knows where their weights are).
    """
    if not model:
        return model
    p = Path(model)
    if p.exists() or len(p.parts) > 1:
        return str(model)
    local = _REPO_ROOT / p.name
    return str(local) if local.exists() else str(model)


def load_detector(weights):
    """Load a trained detector for serving, or None if ultralytics is absent."""
    if not available():
        return None
    from ultralytics import YOLO
    return YOLO(weights)


# Confidence gate shared with the template matcher (same env, same default).
_DEFAULT_MIN_CONFIDENCE = float(os.environ.get("MU_DETECTOR_MIN_CONF", "0.78"))


class YoloDetector:
    """Confidence-gated serving wrapper around a trained YOLO checkpoint.

    Mirrors the optional-dep guard pattern of ``local_detector``: with ultralytics
    absent (or no weights), it is inert — ``available()`` is False and ``predict`` /
    ``locate`` return ``[]`` / ``None`` rather than raising. Emits the SAME canonical
    match-dict shape as the template matcher (``{label, confidence, cx, cy, bbox:
    [x, y, w, h], method}``) in screenshot *pixel* space, so ``perceive`` can consume
    either grounding source identically. Configure via env: ``MU_DETECTOR_WEIGHTS``
    (checkpoint path) + ``MU_DETECTOR_MIN_CONF`` (gate, default 0.78).
    """

    def __init__(self, weights, min_confidence=None):
        self.weights = weights
        self.min_confidence = float(
            min_confidence if min_confidence is not None else _DEFAULT_MIN_CONFIDENCE)
        self._model = None

    @classmethod
    def from_env(cls, min_confidence=None):
        """Build from ``MU_DETECTOR_WEIGHTS``; None if unset/missing/ultralytics absent."""
        weights = os.environ.get("MU_DETECTOR_WEIGHTS")
        if not weights or not available() or not os.path.exists(weights):
            return None
        return cls(weights, min_confidence=min_confidence)

    def available(self):
        """True iff ultralytics is importable AND the weights file exists."""
        return bool(available() and self.weights and os.path.exists(self.weights))

    def load(self):
        """Lazily load the YOLO model (None when ultralytics absent)."""
        if self._model is None:
            self._model = load_detector(self.weights)
        return self._model

    def _parse_results(self, results):
        """Ultralytics Results -> sorted list of canonical match dicts (pixel space)."""
        out = []
        if not results:
            return out
        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return out
        names = getattr(r, "names", None) or {}
        try:
            xyxy = boxes.xyxy.tolist()
            confs = boxes.conf.tolist()
            clss = boxes.cls.tolist()
        except Exception:
            return out
        for (x1, y1, x2, y2), conf, cls in zip(xyxy, confs, clss):
            if conf < self.min_confidence:
                continue
            idx = int(cls)
            label = names.get(idx, str(idx)) if isinstance(names, dict) else str(idx)
            w, h = float(x2 - x1), float(y2 - y1)
            out.append({
                "label": label, "confidence": float(conf),
                "cx": float((x1 + x2) / 2.0), "cy": float((y1 + y2) / 2.0),
                "bbox": [float(x1), float(y1), w, h], "method": "yolo",
            })
        out.sort(key=lambda d: -d["confidence"])
        return out

    def predict(self, screenshot, max_results=12):
        """All gated detections on ``screenshot`` (sorted by confidence). [] if inert."""
        model = self.load()
        if model is None:
            return []
        try:
            results = model.predict(source=screenshot, conf=self.min_confidence,
                                    verbose=False)
        except Exception:
            return []
        return self._parse_results(results)[:max_results]

    def locate(self, screenshot, label=None):
        """Single best gated detection (optionally label-filtered), or None."""
        for m in self.predict(screenshot):
            if label is None or m["label"] == label:
                return m
        return None


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
        status = res["status"]
        if status == "skipped":
            print(f"Training skipped: {res['reason']}")
            return 0
        if status == "empty_dataset":
            print(f"Training aborted: {res['reason']}")
            return 1
        if status == "trained":
            print(f"Trained + verified -> {res.get('weights')}")
        else:  # trained_unverified — do NOT report a missing/corrupt checkpoint as success
            print(f"WARNING: training finished but the checkpoint is unverified "
                  f"({res.get('reason')}) -> {res.get('weights')}")
            return 1
    else:
        print("Add --train to train a yolov8n on it (needs `pip install 'mobile-use[yolo]'`).")
    return 0
