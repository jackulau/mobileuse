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
import sys
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
    "current_intent": None,
    "current_fingerprint": None,
}


# Max distinct labels kept in a fingerprint. Keeps the snapshot ~200 bytes.
_FP_TOP_N = 20


def _ui_fingerprint(helpers) -> dict:
    """Capture a lightweight snapshot of the current UI for replay-time diffing.

    Returns a dict with stable keys regardless of platform / helper availability:
        {
          "app":     str       current app bundle id ('' if unknown)
          "labels":  list[str] sorted unique visible labels (top _FP_TOP_N)
          "focused": str|None  label/text of focused element (None if unknown)
          "count":   int       total visible elements (0 if tree unavailable)
        }

    Designed to be safe to call even when helpers don't expose ui_tree/active_app
    (e.g. mock helpers in tests, or a stripped-down replay env) — degrades to an
    empty fingerprint instead of raising.

    Works cross-platform: reads `label`/`name` (iOS), `text`/`content_desc`
    (Android). Focused detection uses both the boolean `focused` field (Android)
    and the `traits` string containing 'Focused' (iOS).
    """
    fp: dict[str, Any] = {"app": "", "labels": [], "focused": None, "count": 0}

    if hasattr(helpers, "active_app"):
        try:
            app = helpers.active_app()
            if isinstance(app, str):
                fp["app"] = app
        except Exception:
            pass

    if hasattr(helpers, "ui_tree"):
        try:
            tree = helpers.ui_tree(visible_only=True)
        except TypeError:
            try:
                tree = helpers.ui_tree()
            except Exception:
                tree = None
        except Exception:
            tree = None

        if isinstance(tree, list) and tree:
            seen: set[str] = set()
            labels: list[str] = []
            for el in tree:
                if not isinstance(el, dict):
                    continue
                lbl = (
                    el.get("label")
                    or el.get("name")
                    or el.get("text")
                    or el.get("content_desc")
                    or ""
                )
                lbl = lbl.strip() if isinstance(lbl, str) else ""
                if lbl and lbl not in seen:
                    seen.add(lbl)
                    labels.append(lbl)
                if fp["focused"] is None:
                    if el.get("focused") is True or "Focused" in str(el.get("traits") or ""):
                        fp["focused"] = lbl or None
            fp["labels"] = sorted(labels)[:_FP_TOP_N]
            fp["count"] = len(tree)

    return fp


def is_recording() -> bool:
    return _state["recording"]


def _make_recorder(name: str, original):
    @functools.wraps(original)
    def recorded(*args, **kwargs):
        entry = {
            "t": round(time.time() - _state["started_at"], 3),
            "fn": name,
            "args": list(args),
            "kwargs": dict(kwargs),
        }
        if _state["current_intent"] is not None:
            entry["intent"] = _state["current_intent"]
            if _state["current_fingerprint"] is not None:
                entry["fingerprint"] = _state["current_fingerprint"]
        _state["journal"].append(entry)
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

    if not hasattr(helpers, "__name__"):
        raise TypeError(
            f"record_replay: helpers must be a module-like object with __name__, "
            f"got {type(helpers).__name__}"
        )

    _state["recording"] = True
    _state["journal"] = []
    _state["output_path"] = output_path
    _state["helpers_module"] = helpers
    _state["originals"] = {}
    _state["started_at"] = time.time()

    names = fn_names or RECORDED_HELPERS
    try:
        for name in names:
            original = getattr(helpers, name, None)
            if original is None or not callable(original):
                continue
            _state["originals"][name] = original
            setattr(helpers, name, _make_recorder(name, original))
    except Exception:
        # Partial wrap → restore everything and clear state
        for n, orig in _state["originals"].items():
            try:
                setattr(helpers, n, orig)
            except Exception:
                pass
        _state["recording"] = False
        _state["journal"] = []
        _state["originals"] = {}
        raise


def stop_recording() -> str:
    """End recording, restore helpers, write the script. Returns the path.

    Writes a sidecar ``<output_path>.jsonl`` with structured journal entries
    when any entry carries an intent (added via ``annotate()``). Smart-replay
    reads the sidecar to recover intent + fingerprint metadata that won't fit
    in the runnable .py script.
    """
    if not _state["recording"]:
        raise RuntimeError("record_replay: not recording.")

    helpers = _state["helpers_module"]
    for name, original in _state["originals"].items():
        setattr(helpers, name, original)

    journal = _state["journal"]
    script = _generate_script(journal, helpers.__name__)
    out = _state["output_path"]
    Path(out).write_text(script)

    if any("intent" in e for e in journal):
        sidecar = Path(out).with_suffix(Path(out).suffix + ".jsonl")
        with sidecar.open("w") as f:
            for entry in journal:
                f.write(json.dumps(entry) + "\n")

    _state["recording"] = False
    _state["journal"] = []
    _state["originals"] = {}
    _state["current_intent"] = None
    _state["current_fingerprint"] = None
    return out


class annotate:
    """Tag recorded calls inside this block with an intent + captured fingerprint.

    Only meaningful while record_replay is actively recording. Outside of
    ``start_recording``/``stop_recording``, the annotate block is a no-op
    (recording is the source of truth; annotate is metadata for whatever's
    already being captured).

    Example::

        with recording("flow.py", helpers=h):
            with annotate("open compose screen"):
                h.tap(find(label="Compose"))
            with annotate("type message body"):
                h.type_text("hi")

    Each annotate block snapshots the UI fingerprint at ``__enter__`` — that
    fingerprint is the "expected state" smart-replay diffs against when
    replaying the segment. Nested annotate blocks restore the outer intent +
    fingerprint on inner exit.
    """

    def __init__(self, intent: str):
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("annotate(intent=...) requires a non-empty string")
        self.intent = intent
        self._prev_intent = None
        self._prev_fp = None

    def __enter__(self):
        self._prev_intent = _state["current_intent"]
        self._prev_fp = _state["current_fingerprint"]
        _state["current_intent"] = self.intent
        if _state["recording"] and _state["helpers_module"] is not None:
            try:
                _state["current_fingerprint"] = _ui_fingerprint(_state["helpers_module"])
            except Exception:
                _state["current_fingerprint"] = None
        else:
            _state["current_fingerprint"] = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _state["current_intent"] = self._prev_intent
        _state["current_fingerprint"] = self._prev_fp
        return False


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


class recording:
    """Context manager around start_recording/stop_recording.

    Guarantees stop_recording is called even if the body raises, so helpers
    are always restored. Returns the output path on __exit__.

        with recording("flow.py", helpers=h):
            h.tap_at_xy(100, 200)
            h.type_text("hello")
    """
    def __init__(self, output_path, helpers, fn_names=None):
        self.output_path = output_path
        self.helpers = helpers
        self.fn_names = fn_names

    def __enter__(self):
        start_recording(self.output_path, self.helpers, self.fn_names)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if _state["recording"]:
            try:
                stop_recording()
            except Exception:
                # Best-effort: ensure helpers restored even if write fails
                for n, orig in _state["originals"].items():
                    try:
                        setattr(_state["helpers_module"], n, orig)
                    except Exception:
                        pass
                _state["recording"] = False
                _state["journal"] = []
                _state["originals"] = {}
        return False  # don't swallow exceptions


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


# ---------- Smart replay ----------

# Fingerprint match threshold: fraction of recorded labels still present in
# current UI. Below this → mismatch → escalate to LLM (or warn if no LLM).
_FP_MATCH_THRESHOLD = 0.5


class MacroStepFailed(Exception):
    """Raised by replay_smart when a step cannot be replayed.

    Attributes set on the instance:
        step_index   int          0-based index of failing entry in the journal
        intent       str | None   annotated intent (if any) of the failing step
        recorded_fn  str          recorded helper function name
        reason       str          human-readable failure category
        fingerprint  dict | None  fingerprint captured at record time (if any)
    """

    def __init__(self, step_index, intent, recorded_fn, reason, fingerprint=None):
        self.step_index = step_index
        self.intent = intent
        self.recorded_fn = recorded_fn
        self.reason = reason
        self.fingerprint = fingerprint
        super().__init__(
            f"macro step {step_index} ({recorded_fn!r}) failed: {reason}"
            + (f" — intent: {intent!r}" if intent else "")
        )


def _fingerprint_matches(recorded_fp, current_fp) -> bool:
    """Heuristic: same app + ≥_FP_MATCH_THRESHOLD label overlap.

    Empty recorded fingerprint → always matches (no expectation to compare).
    """
    if not recorded_fp:
        return True
    recorded_labels = set(recorded_fp.get("labels") or [])
    if not recorded_labels:
        return True
    if recorded_fp.get("app") and current_fp.get("app") \
            and recorded_fp["app"] != current_fp["app"]:
        return False
    current_labels = set(current_fp.get("labels") or [])
    overlap = recorded_labels & current_labels
    return (len(overlap) / len(recorded_labels)) >= _FP_MATCH_THRESHOLD


def _load_journal(script_path: str) -> list[dict]:
    """Read the sidecar .jsonl if present, else return []."""
    sidecar = Path(script_path).with_suffix(Path(script_path).suffix + ".jsonl")
    if not sidecar.exists():
        return []
    out = []
    for line in sidecar.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def replay_smart(script_path, helpers, llm=None, on_failure="raise"):
    """Replay a recorded macro with intent-aware re-targeting.

    Each entry in the sidecar journal carries (fn, args, kwargs) plus optional
    intent + recorded fingerprint. For each entry:

      1. Probe the current UI fingerprint via helpers.
      2. If the entry has no intent OR the recorded fingerprint still matches
         the current UI → run the recorded call literally.
      3. If the fingerprint diverged AND an llm callable is provided → ask
         retarget_action for an adapted call and execute that instead.
      4. If the fingerprint diverged but no llm was provided → log a warning
         to stderr and fall back to the literal call (best-effort).
      5. Any execution failure routes through `on_failure`:
         - "raise" (default) → raise MacroStepFailed
         - "skip"            → log + continue with next step

    When no sidecar exists, falls back to plain literal replay (the .py script).

    Args:
        script_path:  path to the .py file produced by stop_recording. The
                      sidecar `<script_path>.jsonl` is read alongside.
        helpers:      helpers module (iphone_harness.helpers or
                      android_harness.helpers, or a compatible mock).
        llm:          optional callable(prompt) -> str. Required to actually
                      re-target on UI shifts; without it smart replay is
                      "literal with warnings".
        on_failure:   "raise" | "skip"

    Returns:
        list of dicts, one per step:
            {"step": int, "fn": str, "intent": str|None,
             "outcome": "literal"|"retargeted"|"skipped"|"failed",
             "result": <whatever helper returned, on success>,
             "error":  <str, on failure with on_failure='skip'>}
    """
    if on_failure not in ("raise", "skip"):
        raise ValueError("on_failure must be 'raise' or 'skip'")

    path = Path(script_path)
    if not path.exists():
        raise FileNotFoundError(f"replay script not found: {script_path}")

    journal = _load_journal(script_path)
    if not journal:
        # No sidecar — degrade to dumb replay.
        replay(script_path, helpers=helpers)
        return []

    from . import agent_loop  # lazy import — avoids cycle if user only uses dumb replay

    results = []
    for i, entry in enumerate(journal):
        fn_name = entry.get("fn")
        args = list(entry.get("args") or [])
        kwargs = dict(entry.get("kwargs") or {})
        intent = entry.get("intent")
        recorded_fp = entry.get("fingerprint")

        outcome = "literal"
        adapted = None
        if intent and recorded_fp is not None:
            try:
                current_fp = _ui_fingerprint(helpers)
            except Exception:
                current_fp = {"app": "", "labels": [], "focused": None, "count": 0}

            if not _fingerprint_matches(recorded_fp, current_fp):
                if llm is None:
                    sys.stderr.write(
                        f"[record_replay] step {i} ({fn_name}): UI fingerprint diverged "
                        f"but no llm provided — falling back to literal replay\n"
                    )
                else:
                    try:
                        current_ui = helpers.ui_tree(visible_only=True) \
                            if hasattr(helpers, "ui_tree") else []
                    except Exception:
                        current_ui = []
                    adapted = agent_loop.retarget_action(
                        intent, recorded_fp, current_ui,
                        {"fn": fn_name, "args": args, "kwargs": kwargs},
                        llm=llm,
                        current_app=current_fp.get("app"),
                        current_focused=current_fp.get("focused"),
                    )
                    if adapted is None:
                        err = MacroStepFailed(
                            i, intent, fn_name,
                            "LLM declined to re-target or returned no adapted action",
                            fingerprint=recorded_fp,
                        )
                        if on_failure == "raise":
                            raise err
                        results.append({
                            "step": i, "fn": fn_name, "intent": intent,
                            "outcome": "skipped", "error": str(err),
                        })
                        continue
                    fn_name = adapted["fn"]
                    args = adapted["args"]
                    kwargs = adapted["kwargs"]
                    outcome = "retargeted"

        fn = getattr(helpers, fn_name, None)
        if fn is None or not callable(fn):
            err = MacroStepFailed(
                i, intent, entry.get("fn"),
                f"helper {fn_name!r} not found on helpers module",
                fingerprint=recorded_fp,
            )
            if on_failure == "raise":
                raise err
            results.append({
                "step": i, "fn": fn_name, "intent": intent,
                "outcome": "failed", "error": str(err),
            })
            continue

        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            err = MacroStepFailed(
                i, intent, fn_name, f"helper raised: {e}",
                fingerprint=recorded_fp,
            )
            if on_failure == "raise":
                raise err
            results.append({
                "step": i, "fn": fn_name, "intent": intent,
                "outcome": "failed", "error": str(err),
            })
            continue

        results.append({
            "step": i, "fn": fn_name, "intent": intent,
            "outcome": outcome, "result": result,
        })

    return results
