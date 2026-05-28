"""Tests for smart replay — LLM re-targets recorded actions when UI shifts.

Covers:
  D3: agent_loop.retarget_action  (this file)
  D4: record_replay.replay_smart  (added later, kept in same file)
  D6: MacroStepFailed             (added later)
"""
import json
import types
from pathlib import Path

import pytest

from mobile_use import agent_loop, record_replay

# ---------- helpers ----------

def _mk_llm(reply):
    """Return a callable(prompt) -> str that ignores prompt and returns reply.

    Pass a string for canned reply, or a list for one-per-call replies.
    """
    if isinstance(reply, list):
        i = {"n": 0}
        def call(prompt):
            r = reply[i["n"]]
            i["n"] += 1
            return r
        return call
    return lambda prompt: reply


def _fake_helpers():
    mod = types.ModuleType("fake_smart_helpers")
    calls = []
    def tap_at_xy(x, y):
        calls.append(("tap_at_xy", (x, y), {}))
    def tap(el):
        calls.append(("tap", (el,), {}))
    def type_text(text):
        calls.append(("type_text", (text,), {}))
    def find(label=None, text=None):
        if label or text:
            return {"label": label or text, "cx": 100, "cy": 200}
        return None
    mod.tap_at_xy = tap_at_xy
    mod.tap = tap
    mod.type_text = type_text
    mod.find = find
    mod._calls = calls
    return mod


# ---------- D3: retarget_action ----------

def test_retarget_action_exists():
    assert callable(agent_loop.retarget_action)


def test_retarget_requires_callable_llm():
    with pytest.raises(TypeError):
        agent_loop.retarget_action("intent", {}, [], {"fn": "tap"}, llm="not-callable")


def test_retarget_returns_adapted_action_from_llm_json():
    llm = _mk_llm('{"fn": "tap", "args": [], "kwargs": {"el": "ComposeButton"}}')
    recorded_fp = {"app": "x", "labels": ["Old"], "focused": None, "count": 1}
    current_ui = [{"type": "Button", "label": "Compose"}]
    recorded_call = {"fn": "tap_at_xy", "args": [10, 20], "kwargs": {}}

    out = agent_loop.retarget_action(
        "open compose", recorded_fp, current_ui, recorded_call, llm=llm
    )
    assert out == {"fn": "tap", "args": [], "kwargs": {"el": "ComposeButton"}}


def test_retarget_returns_none_on_skip_signal():
    llm = _mk_llm('{"skip": true, "reason": "no matching element"}')
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out is None


def test_retarget_returns_none_on_invalid_json():
    llm = _mk_llm("not valid json at all")
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out is None


def test_retarget_strips_markdown_fences():
    llm = _mk_llm('```json\n{"fn": "tap", "args": [1, 2]}\n```')
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out == {"fn": "tap", "args": [1, 2], "kwargs": {}}


def test_retarget_returns_none_on_llm_exception():
    def boom(prompt):
        raise RuntimeError("rate limited")
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=boom)
    assert out is None


def test_retarget_returns_none_when_fn_missing_from_response():
    llm = _mk_llm('{"args": [1, 2], "kwargs": {}}')
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out is None


def test_retarget_returns_none_on_non_dict_response():
    llm = _mk_llm('["unexpected", "list"]')
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out is None


def test_retarget_normalizes_missing_args_kwargs():
    llm = _mk_llm('{"fn": "press_home"}')
    out = agent_loop.retarget_action("x", {}, [], {"fn": "press_home"}, llm=llm)
    assert out == {"fn": "press_home", "args": [], "kwargs": {}}


def test_retarget_prompt_includes_intent_and_fingerprint():
    captured = {}
    def llm(prompt):
        captured["prompt"] = prompt
        return '{"fn": "tap"}'

    fp = {"app": "com.test", "labels": ["Old", "Btn"], "focused": "Old", "count": 2}
    agent_loop.retarget_action(
        "tap the new thing",
        fp,
        [{"type": "Button", "label": "New"}],
        {"fn": "tap_at_xy", "args": [10, 20]},
        llm=llm,
        current_app="com.test",
        current_focused="New",
    )

    assert "tap the new thing" in captured["prompt"]
    assert "com.test" in captured["prompt"]
    assert "Old" in captured["prompt"]
    assert "New" in captured["prompt"]
    assert "tap_at_xy" in captured["prompt"]


def test_retarget_handles_non_string_llm_response():
    llm = lambda p: 12345  # ints, not strings
    out = agent_loop.retarget_action("x", {}, [], {"fn": "tap"}, llm=llm)
    assert out is None


def test_retarget_handles_empty_recorded_fp():
    llm = _mk_llm('{"fn": "tap"}')
    out = agent_loop.retarget_action("x", None, None, {"fn": "tap"}, llm=llm)
    assert out == {"fn": "tap", "args": [], "kwargs": {}}


# ---------- D4: replay_smart engine ----------

def _record_smart_macro(tmp_path, intent_blocks):
    """Build a recorded macro with annotated blocks. Returns script_path.

    intent_blocks: list of (intent_str, ui_tree, calls_fn) tuples.
        intent_str  : str — annotate intent label, or None for unannotated
        ui_tree     : list[dict] — what helpers.ui_tree should return at record time
        calls_fn    : callable(helpers) → makes helper calls under the annotate block
    """
    h = _fake_helpers()
    # active_app fixed throughout to keep test stable
    h.active_app = lambda: "com.test.smart"
    out = tmp_path / "macro.py"
    record_replay.start_recording(str(out), helpers=h)
    try:
        for intent, ui_tree, calls_fn in intent_blocks:
            h.ui_tree = lambda visible_only=False, compact=False, _t=ui_tree: _t
            if intent is None:
                calls_fn(h)
            else:
                with record_replay.annotate(intent):
                    calls_fn(h)
    finally:
        record_replay.stop_recording()
    return str(out)


def test_replay_smart_exists():
    assert callable(record_replay.replay_smart)


def test_replay_smart_rejects_unknown_on_failure(tmp_path):
    h = _fake_helpers()
    out = tmp_path / "x.py"
    out.write_text("")
    with pytest.raises(ValueError):
        record_replay.replay_smart(str(out), h, on_failure="explode")


def test_replay_smart_missing_script_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        record_replay.replay_smart(str(tmp_path / "missing.py"), _fake_helpers())


def test_replay_smart_no_sidecar_falls_back_to_literal(tmp_path):
    """When no .jsonl sidecar exists, smart replay degrades to dumb replay."""
    h = _fake_helpers()
    out = tmp_path / "rec.py"
    h.__name__ = "fake_smart_helpers"
    record_replay.start_recording(str(out), helpers=h)
    h.tap_at_xy(5, 6)
    record_replay.stop_recording()
    assert not out.with_suffix(".py.jsonl").exists()  # confirm no sidecar

    fresh = _fake_helpers()
    fresh.__name__ = "fake_smart_helpers"
    import sys as _sys
    _sys.modules["fake_smart_helpers"] = fresh
    try:
        results = record_replay.replay_smart(str(out), fresh)
        assert results == []  # signal: dumb-replay fallback
        assert fresh._calls == [("tap_at_xy", (5, 6), {})]
    finally:
        del _sys.modules["fake_smart_helpers"]


def test_replay_smart_literal_when_fingerprint_matches(tmp_path):
    """Annotated step where current UI matches recorded → run literal call."""
    tree = [{"type": "Button", "label": "Compose"}]
    out = _record_smart_macro(tmp_path, [
        ("open compose", tree, lambda h: h.tap_at_xy(10, 20)),
    ])

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: tree
    # No llm provided — should be fine because fingerprint matches
    results = record_replay.replay_smart(out, fresh, llm=None)
    assert len(results) == 1
    assert results[0]["outcome"] == "literal"
    assert fresh._calls == [("tap_at_xy", (10, 20), {})]


def test_replay_smart_warns_when_mismatch_without_llm(tmp_path, capsys):
    """Fingerprint diverged + no llm → warn to stderr, run literal fallback."""
    out = _record_smart_macro(tmp_path, [
        ("open compose", [{"type": "Button", "label": "Compose"}],
         lambda h: h.tap_at_xy(10, 20)),
    ])

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: [
        {"type": "Button", "label": "Completely Different"},
    ]
    results = record_replay.replay_smart(out, fresh, llm=None)
    captured = capsys.readouterr()
    assert "diverged" in captured.err
    assert len(results) == 1
    assert results[0]["outcome"] == "literal"


def test_replay_smart_retargets_via_llm_on_mismatch(tmp_path):
    """Fingerprint diverged + llm → calls retarget_action, executes adapted call."""
    out = _record_smart_macro(tmp_path, [
        ("tap compose", [{"type": "Button", "label": "Compose"}],
         lambda h: h.tap_at_xy(50, 60)),
    ])

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: [
        {"type": "Button", "label": "NewCompose"},
    ]
    llm = _mk_llm('{"fn": "tap_at_xy", "args": [99, 88]}')

    results = record_replay.replay_smart(out, fresh, llm=llm)
    assert results[0]["outcome"] == "retargeted"
    assert fresh._calls == [("tap_at_xy", (99, 88), {})]


def test_replay_smart_unannotated_steps_run_literal(tmp_path):
    """Unannotated + annotated mixed: unannotated runs literal regardless of UI."""
    tree = [{"type": "Button", "label": "Compose"}]
    out = _record_smart_macro(tmp_path, [
        # mix one annotated step (forces sidecar) with one unannotated step
        ("compose intent", tree, lambda h: h.tap_at_xy(10, 20)),
        (None,             tree, lambda h: h.tap_at_xy(99, 88)),
    ])
    # Sanity: sidecar exists because at least one step is annotated
    assert Path(out).with_suffix(".py.jsonl").exists()

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: tree  # match
    results = record_replay.replay_smart(out, fresh, llm=None)
    assert len(results) == 2
    assert all(r["outcome"] == "literal" for r in results)
    assert fresh._calls == [("tap_at_xy", (10, 20), {}), ("tap_at_xy", (99, 88), {})]


# ---------- D6: MacroStepFailed + recovery ----------

def test_macro_step_failed_exception_class_exists():
    assert issubclass(record_replay.MacroStepFailed, Exception)


def test_macro_step_failed_attributes_set():
    err = record_replay.MacroStepFailed(
        step_index=3, intent="x", recorded_fn="tap",
        reason="testing", fingerprint={"app": "y"},
    )
    assert err.step_index == 3
    assert err.intent == "x"
    assert err.recorded_fn == "tap"
    assert err.reason == "testing"
    assert err.fingerprint == {"app": "y"}
    assert "3" in str(err)
    assert "tap" in str(err)
    assert "testing" in str(err)


def test_recovery_raises_when_llm_declines_and_on_failure_raise(tmp_path):
    out = _record_smart_macro(tmp_path, [
        ("step", [{"type": "B", "label": "X"}], lambda h: h.tap_at_xy(1, 2)),
    ])

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: [
        {"type": "Other", "label": "Z"},
    ]
    llm = _mk_llm('{"skip": true, "reason": "no target"}')

    with pytest.raises(record_replay.MacroStepFailed) as exc:
        record_replay.replay_smart(out, fresh, llm=llm, on_failure="raise")
    assert exc.value.step_index == 0
    assert exc.value.intent == "step"


def test_recovery_skip_continues_to_next_step(tmp_path):
    out = _record_smart_macro(tmp_path, [
        ("a", [{"type": "B", "label": "X"}], lambda h: h.tap_at_xy(1, 2)),
        ("b", [{"type": "B", "label": "X"}], lambda h: h.tap_at_xy(3, 4)),
    ])

    fresh = _fake_helpers()
    fresh.active_app = lambda: "com.test.smart"
    fresh.ui_tree = lambda visible_only=False, compact=False: [
        {"type": "Other", "label": "Z"},
    ]
    llm = _mk_llm(['{"skip": true, "reason": "no a"}',
                   '{"fn": "tap_at_xy", "args": [9, 9]}'])

    results = record_replay.replay_smart(out, fresh, llm=llm, on_failure="skip")
    assert len(results) == 2
    assert results[0]["outcome"] == "skipped"
    assert results[1]["outcome"] == "retargeted"
    assert fresh._calls == [("tap_at_xy", (9, 9), {})]


def test_recovery_failed_helper_routed_per_on_failure(tmp_path):
    out = _record_smart_macro(tmp_path, [
        (None, [], lambda h: h.tap_at_xy(1, 2)),
    ])
    # Tamper with sidecar to reference a nonexistent helper
    sidecar = Path(out).with_suffix(".py.jsonl")
    sidecar.write_text(json.dumps({
        "t": 0.0, "fn": "no_such_helper", "args": [], "kwargs": {},
        "intent": "x",  # force smart path
        "fingerprint": {"app": "", "labels": [], "focused": None, "count": 0},
    }) + "\n")

    fresh = _fake_helpers()
    with pytest.raises(record_replay.MacroStepFailed):
        record_replay.replay_smart(out, fresh, on_failure="raise")

    results = record_replay.replay_smart(out, fresh, on_failure="skip")
    assert results[0]["outcome"] == "failed"
    assert "not found" in results[0]["error"]
