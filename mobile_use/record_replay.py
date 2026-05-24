"""Record-replay — capture a sequence of taps/swipes/typing as a replayable script.

Usage:

    from mobile_use import record_replay
    import iphone_harness.helpers as h

    record_replay.start_recording('test.py', helpers=h)
    h.tap_at_xy(100, 200)
    h.type_text('hello')
    h.swipe(0, 500, 0, 100)
    record_replay.stop_recording()
    # → test.py is a runnable Python file that replays the same calls.

    record_replay.replay('test.py', helpers=h)
    # → re-executes the recorded sequence.

Implementation: at start_recording, we wrap each helper function in the supplied
module with a recording trampoline that journals the call and forwards to the
original. stop_recording restores originals and writes the journal to a Python
script.
"""
from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from typing import Any


# Default set of helpers to record. Anything not listed is left alone.
RECORDED_HELPERS = (
    "tap_at_xy", "tap",
    "double_tap", "long_press",
    "swipe", "scroll", "scroll_by",
    "type_text",
    "press_back", "press_home", "press_recents",
    "open_notifications", "open_control_center", "close_control_center",
)


_state: dict[str, Any] = {
    "recording": False,
    "journal": [],
    "output_path": None,
    "helpers_module": None,
    "originals": {},
    "started_at": None,
}


def is_recording() -> bool:
    return _state["recording"]


def _make_recorder(name: str, original):
    @functools.wraps(original)
    def recorded(*args, **kwargs):
        _state["journal"].append({
            "t": round(time.time() - _state["started_at"], 3),
            "fn": name,
            "args": list(args),
            "kwargs": dict(kwargs),
        })
        return original(*args, **kwargs)
    return recorded


def start_recording(output_path: str, helpers, fn_names: tuple[str, ...] | None = None):
    """Begin recording calls to the named helpers.

    Args:
        output_path: where to write the .py script when stop_recording is called.
        helpers: the helpers module to wrap (iphone_harness.helpers or android_harness.helpers).
        fn_names: which helpers to record; defaults to RECORDED_HELPERS.
    """
    if _state["recording"]:
        raise RuntimeError("record_replay: already recording — call stop_recording() first.")

    _state["recording"] = True
    _state["journal"] = []
    _state["output_path"] = output_path
    _state["helpers_module"] = helpers
    _state["originals"] = {}
    _state["started_at"] = time.time()

    names = fn_names or RECORDED_HELPERS
    for name in names:
        original = getattr(helpers, name, None)
        if original is None or not callable(original):
            continue
        _state["originals"][name] = original
        setattr(helpers, name, _make_recorder(name, original))


def stop_recording() -> str:
    """End recording, restore helpers, write the script. Returns the path."""
    if not _state["recording"]:
        raise RuntimeError("record_replay: not recording.")

    helpers = _state["helpers_module"]
    for name, original in _state["originals"].items():
        setattr(helpers, name, original)

    script = _generate_script(_state["journal"], helpers.__name__)
    out = _state["output_path"]
    Path(out).write_text(script)

    _state["recording"] = False
    _state["journal"] = []
    _state["originals"] = {}
    return out


def _format_arg(v) -> str:
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (int, float, bool, type(None))):
        return repr(v)
    if isinstance(v, (list, tuple, dict)):
        try:
            return json.dumps(v)
        except (TypeError, ValueError):
            return repr(v)
    return repr(v)


def _format_call(entry: dict) -> str:
    args = ", ".join(_format_arg(a) for a in entry["args"])
    kwargs = ", ".join(f"{k}={_format_arg(v)}" for k, v in entry["kwargs"].items())
    body = ", ".join(p for p in (args, kwargs) if p)
    return f"h.{entry['fn']}({body})"


def _generate_script(journal: list[dict], helpers_module: str) -> str:
    lines = [
        '"""Recorded with mobile_use.record_replay. Edit freely.',
        '',
        f'Source module: {helpers_module}',
        f'Calls captured: {len(journal)}',
        '"""',
        f'import {helpers_module} as h',
        'import time',
        '',
    ]
    prev_t = 0.0
    for entry in journal:
        gap = entry["t"] - prev_t
        if gap > 0.05:  # only inject sleeps for noticeable pauses
            lines.append(f"time.sleep({round(gap, 2)})")
        lines.append(_format_call(entry))
        prev_t = entry["t"]
    lines.append('')
    return "\n".join(lines)


def replay(script_path: str, helpers=None):
    """Execute a previously-recorded script.

    Just runs the .py file with the helpers module in scope. The script imports
    its own helpers, so `helpers=` is optional unless you want to inject mocks.
    """
    path = Path(script_path)
    if not path.exists():
        raise FileNotFoundError(f"replay script not found: {script_path}")
    code = path.read_text()
    ns: dict[str, Any] = {"__name__": "__replay__"}
    if helpers is not None:
        ns["h"] = helpers
    exec(compile(code, str(path), "exec"), ns)
