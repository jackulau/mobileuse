"""Training data collector — captures screenshot + UI tree + action triples.

Each perception event is a training sample:
  {screenshot, ui_tree, active_app, action, target_element, success, timestamp}

Stored as JSONL in .claude-workspace/training-data/<session>/<date>.jsonl.
Use `mobile-use export-training` to merge and export.
"""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get(
    "MU_TRAINING_DIR",
    REPO_ROOT / ".claude-workspace" / "training-data"
)).expanduser()

# Per-element fields kept by the default compact ui_tree dump (MU_COLLECT_TREE).
_TREE_FIELDS = ("type", "label", "name", "text", "content_desc",
                "x", "y", "w", "h", "cx", "cy",
                "clickable", "accessible", "enabled", "focused", "visible")


def _compact_tree(ui_tree):
    """Shrink the per-row ui_tree dump (the per-step hot path writes this JSON).

    MU_COLLECT_TREE=full keeps the raw tree; default 'compact' keeps only the
    grounding-relevant fields per element and caps the element count at
    MU_COLLECT_TREE_MAX (true size is always recorded as ui_tree_size).
    """
    if os.environ.get("MU_COLLECT_TREE", "compact") == "full":
        return ui_tree
    try:
        cap = int(os.environ.get("MU_COLLECT_TREE_MAX", "150"))
    except ValueError:
        cap = 150
    out = []
    for el in ui_tree[:cap]:
        if not isinstance(el, dict):
            out.append(el)
            continue
        out.append({k: el[k] for k in _TREE_FIELDS if k in el})
    return out


def _valid_bbox(bbox):
    """True iff ``bbox`` is a 4-tuple of (x, y, w, h) with x,y >= 0 and w,h > 0.

    Capture-time guard: a degenerate or negative box would be persisted and only
    dropped much later in ``build_yolo_dataset`` (silent data loss). Reject it here.
    """
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return False
    return x >= 0 and y >= 0 and w > 0 and h > 0


class Collector:
    """Collects perception events for model training."""

    def __init__(self, session_name="default", platform=None):
        self.session_name = session_name
        self.platform = platform
        self._dir = DATA_DIR / session_name
        self._dir.mkdir(parents=True, exist_ok=True)
        self._date = time.strftime("%Y-%m-%d")
        self._path = self._dir / f"{self._date}.jsonl"
        self._count = 0
        self._screenshots_dir = self._dir / "screenshots"
        self._screenshots_dir.mkdir(exist_ok=True)
        # Self-labeling detection dataset (B2): one JSONL row per grounded action,
        # plus a cropped PNG of the labeled element region.
        self._detections_path = self._dir / f"{self._date}_detections.jsonl"
        self._crops_dir = self._dir / "crops"
        self._detection_count = 0
        self._detection_invalid_count = 0   # degenerate boxes rejected at capture time

    def record(self, screenshot_path=None, ui_tree=None, active_app=None,
               window_size=None, action=None, target_element=None,
               success=True, metadata=None):
        """Record one perception+action event.

        Args:
            screenshot_path: path to PNG on host
            ui_tree: list of element dicts from ui_tree()
            active_app: dict from active_app()
            window_size: dict from window_size()
            action: string describing the action taken (e.g. "tap(find(text='Send'))")
            target_element: the element dict that was targeted (if any)
            success: whether the action succeeded
            metadata: extra context dict
        """
        ts = time.time()
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "epoch": ts,
            "session": self.session_name,
            "platform": self.platform,
        }

        if screenshot_path and os.path.exists(screenshot_path):
            event["screenshot"], event["screenshot_hash"] = \
                self._store_screenshot(screenshot_path)

        if ui_tree is not None:
            event["ui_tree_size"] = len(ui_tree)
            event["ui_tree"] = _compact_tree(ui_tree)

        if active_app is not None:
            event["active_app"] = active_app

        if window_size is not None:
            event["window_size"] = window_size

        if action is not None:
            event["action"] = action

        if target_element is not None:
            event["target_element"] = target_element

        event["success"] = success

        if metadata:
            event["metadata"] = metadata

        with open(self._path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        self._count += 1
        return event

    def _store_screenshot(self, screenshot_path):
        """Content-addressed screenshot store: hash first, copy only when new.

        The hash (already needed for the row's ``screenshot_hash``) doubles as
        the stored basename, so a repeated identical screen — the common case in
        a multi-step run — costs one stat instead of a full PNG copy per step,
        and the dataset never accumulates byte-identical duplicates.
        Returns ``(stored_path_str, digest)``.
        """
        digest = _file_hash(screenshot_path)
        dest = self._screenshots_dir / f"{digest[:16]}.png"
        if not dest.exists():
            self._screenshots_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(screenshot_path, dest)
        return str(dest), digest

    def record_perception(self, state, action=None, target=None, success=True):
        """Record from a perceive() state dict (convenience wrapper)."""
        return self.record(
            screenshot_path=state.get("screenshot_path"),
            ui_tree=state.get("ui_tree"),
            active_app=state.get("active_app"),
            window_size=state.get("window_size"),
            action=action,
            target_element=target,
            success=success,
        )

    def record_detection_sample(self, screenshot_path, bbox, label, screen_sig=None,
                                window_size=None, action=None, active_app=None,
                                source="ui_tree", save_crop=True):
        """Record one self-labeled object-detection sample for local-detector training.

        The accessibility tree already gives the element's box + label, so this is
        a free byproduct of acting — no extra VLM call. ``bbox`` is ``(x, y, w, h)``
        in the UI tree's *logical-point* space; iOS screenshots come back at the
        device's *physical-pixel* resolution, so when ``window_size`` is known the
        box is scaled to pixel space (``scale = image_width / window_width``) and
        stored as ``bbox`` (the canonical training box), with the original kept as
        ``bbox_logical``. A cropped PNG of the region is saved when PIL is available.

        A degenerate box (wrong shape, negative origin, or non-positive size) is
        rejected here and counted in ``_detection_invalid_count`` — returning None —
        so it never pollutes the dataset.
        """
        if not _valid_bbox(bbox):
            self._detection_invalid_count += 1
            return None
        ts = time.time()
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "epoch": ts,
            "session": self.session_name,
            "platform": self.platform,
            "type": "detection",
            "label": label or "",
            "source": source,
            "bbox_logical": [float(v) for v in bbox],
        }
        if screen_sig:
            event["screen_sig"] = screen_sig
        if action is not None:
            event["action"] = action
        if active_app is not None:
            event["active_app"] = active_app

        px_bbox = list(event["bbox_logical"])
        scale = 1.0
        if screenshot_path and os.path.exists(screenshot_path):
            event["screenshot"], event["screenshot_hash"] = \
                self._store_screenshot(screenshot_path)
            scale = _image_scale(screenshot_path, window_size)
            if scale != 1.0:
                px_bbox = [v * scale for v in px_bbox]
            if save_crop:
                # ms timestamp + per-collector ordinal: unique even for several
                # samples recorded within the same millisecond.
                crop = self._save_crop(
                    screenshot_path, px_bbox,
                    stem=f"det-{int(ts * 1000)}-{self._detection_count}")
                if crop:
                    event["crop"] = str(crop)
        event["bbox"] = px_bbox          # pixel-space box, consistent with the image
        event["scale"] = scale

        with open(self._detections_path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        self._detection_count += 1
        return event

    def _save_crop(self, src_png, bbox, stem=None):
        """Crop ``bbox`` (pixel space) out of ``src_png`` into the crops dir. None on failure.

        ``stem`` names the crop file uniquely per SAMPLE. The old behavior named
        it after the source screenshot's basename — but the source is a fixed
        device temp path (e.g. iph-shot.png), so every crop overwrote the same
        file and earlier samples' ``crop`` rows silently pointed at the wrong
        pixels (corrupting the template matcher's training set).
        """
        try:
            from PIL import Image
        except Exception:
            return None
        try:
            x, y, w, h = bbox
            with Image.open(src_png) as im:
                x0, y0 = max(0, int(x)), max(0, int(y))
                x1, y1 = min(im.width, int(x + w)), min(im.height, int(y + h))
                if x1 <= x0 or y1 <= y0:
                    return None
                self._crops_dir.mkdir(exist_ok=True)
                base = stem or Path(src_png).name.removesuffix(".png")
                dest = self._crops_dir / f"{base}-crop.png"
                im.crop((x0, y0, x1, y1)).save(dest)
                return dest
        except Exception:
            return None

    @property
    def detection_count(self):
        return self._detection_count

    @property
    def detection_invalid_count(self):
        """How many detection samples were rejected at capture for a degenerate box."""
        return self._detection_invalid_count

    @property
    def detections_path(self):
        return str(self._detections_path)

    @property
    def count(self):
        return self._count

    @property
    def data_path(self):
        return str(self._path)


def _file_hash(path, algo="md5"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _image_scale(png_path, window_size):
    """Pixels-per-logical-point ratio: image_width / window_width. 1.0 if unknown.

    iOS screenshots are physical pixels while the UI tree is logical points; on a
    Retina device this is 2.0 or 3.0. Falls back to 1.0 when PIL is missing or the
    window size is unavailable (Android coords are already pixel-space -> 1.0).
    """
    try:
        w = (window_size or {}).get("width")
        if not w:
            return 1.0
        from PIL import Image
        with Image.open(png_path) as im:
            return im.width / float(w) if w else 1.0
    except Exception:
        return 1.0


def load_detection_samples(sessions=None):
    """Load self-labeled detection samples (``*_detections.jsonl``) across sessions.

    Returns a list of event dicts (each with ``bbox``, ``label``, ``screenshot``).
    Used by the local-detector matcher (B4) and the YOLO trainer (B5).
    """
    if not DATA_DIR.exists():
        return []
    if sessions:
        dirs = [DATA_DIR / s for s in sessions if (DATA_DIR / s).exists()]
    else:
        dirs = [d for d in DATA_DIR.iterdir() if d.is_dir()]
    samples = []
    for d in sorted(dirs):
        for jsonl_file in sorted(d.glob("*_detections.jsonl")):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return samples


def list_sessions():
    """List all sessions with training data."""
    if not DATA_DIR.exists():
        return []
    return sorted(d.name for d in DATA_DIR.iterdir() if d.is_dir())


def export_training_data(output_path, sessions=None, include_tree=True,
                         include_screenshots=False):
    """Export training data from all (or specified) sessions to a single JSONL file.

    Args:
        output_path: where to write the merged JSONL
        sessions: list of session names (None = all)
        include_tree: include full ui_tree in output (can be large)
        include_screenshots: copy screenshots to output dir
    """
    if not DATA_DIR.exists():
        return 0

    dirs = []
    if sessions:
        dirs = [DATA_DIR / s for s in sessions if (DATA_DIR / s).exists()]
    else:
        dirs = [d for d in DATA_DIR.iterdir() if d.is_dir()]

    output = Path(output_path)
    if include_screenshots:
        ss_dir = output.parent / "screenshots"
        ss_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(output, "w", encoding="utf-8") as out:
        for d in sorted(dirs):
            for jsonl_file in sorted(d.glob("*.jsonl")):
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not include_tree:
                        event.pop("ui_tree", None)

                    if include_screenshots and "screenshot" in event:
                        src = Path(event["screenshot"])
                        if src.exists():
                            dest = ss_dir / src.name
                            shutil.copy2(src, dest)
                            event["screenshot"] = str(dest)

                    out.write(json.dumps(event, default=str) + "\n")
                    count += 1
    return count


def training_stats():
    """Summary stats across all training data."""
    if not DATA_DIR.exists():
        return {"sessions": 0, "events": 0, "apps": set()}

    sessions = 0
    events = 0
    apps = set()

    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        sessions += 1
        for jsonl_file in d.glob("*.jsonl"):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    events += 1
                    app = event.get("active_app")
                    if isinstance(app, dict):
                        apps.add(app.get("bundleId") or app.get("package") or "unknown")
                except json.JSONDecodeError:
                    continue

    return {"sessions": sessions, "events": events, "apps": sorted(apps)}
