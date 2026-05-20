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
            basename = f"{int(ts * 1000)}.png"
            dest = self._screenshots_dir / basename
            shutil.copy2(screenshot_path, dest)
            event["screenshot"] = str(dest)
            event["screenshot_hash"] = _file_hash(screenshot_path)

        if ui_tree is not None:
            event["ui_tree_size"] = len(ui_tree)
            event["ui_tree"] = ui_tree

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
    with open(output, "w") as out:
        for d in sorted(dirs):
            for jsonl_file in sorted(d.glob("*.jsonl")):
                for line in jsonl_file.read_text().splitlines():
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
            for line in jsonl_file.read_text().splitlines():
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
