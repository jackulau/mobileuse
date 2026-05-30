"""D16 — autonomous perceive→reason→act loop with set-of-marks, step
verification, a curated action schema, and the iOS undo fix.

Before this, the 'agent' was REPL-only: no autonomous loop, perceive shipped the
raw uncapped tree with no set-of-marks indices, act() never verified (a silent
no-op tap was logged as success), and the action surface was all 63 public
helpers including observation/plumbing functions. undo_last() pressed Home on
iOS (abandoning the app) instead of an in-app back swipe.
"""
import sys
import types

import pytest


def _fake_helpers():
    m = types.ModuleType("fake_helpers")
    calls = []

    tree = [
        {"type": "Button", "label": "Search", "cx": 100, "cy": 50, "clickable": True, "visible": True},
        {"type": "Field", "label": "", "cx": 200, "cy": 80, "accessible": True, "visible": True},
        {"type": "Static", "label": "noisy", "cx": 0, "cy": 0, "visible": True},  # no cx? has cx; kept if labelled
    ]

    def snapshot(visible_only=True):
        return {"screenshot_path": "/x.png", "ui_tree": list(tree),
                "active_app": {"bundleId": "com.example"}, "window_size": {"width": 390, "height": 844},
                "alert": None}

    def auto_dismiss_dialog():
        return False

    def tap_at_xy(x, y):
        calls.append(("tap_at_xy", x, y))
        return True

    def type_text(text):
        calls.append(("type_text", text))
        return True

    def press_enter():
        calls.append(("press_enter",))
        return True

    def swipe_back():
        calls.append(("swipe_back",))
        return True

    def screenshot(path=None):
        return "/x.png"

    def ui_tree(visible_only=False, compact=False):
        return list(tree)

    for fn in (snapshot, auto_dismiss_dialog, tap_at_xy, type_text, press_enter,
               swipe_back, screenshot, ui_tree):
        setattr(m, fn.__name__, fn)
    m._calls = calls
    return m


def _install(monkeypatch, platform="ios"):
    h = _fake_helpers()
    a = types.ModuleType("fake_admin")
    a.ensure_daemon = lambda *args, **kw: True
    pkg = "iphone_harness" if platform == "ios" else "android_harness"
    import importlib
    parent = importlib.import_module(pkg)
    monkeypatch.setitem(sys.modules, f"{pkg}.helpers", h)
    monkeypatch.setitem(sys.modules, f"{pkg}.admin", a)
    monkeypatch.setattr(parent, "helpers", h, raising=False)
    monkeypatch.setattr(parent, "admin", a, raising=False)
    return h


def _loop(monkeypatch, tmp_path, platform="ios"):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install(monkeypatch, platform)
    from mobile_use.agent_loop import AgentLoop
    return AgentLoop(platform=platform, session_name="run-test", collect=False)


def test_parse_json_block_tolerates_fences():
    from mobile_use.agent_loop import _parse_json_block
    assert _parse_json_block('{"fn":"tap"}') == {"fn": "tap"}
    assert _parse_json_block('```json\n{"done": true}\n```') == {"done": True}
    assert _parse_json_block("not json") is None
    assert _parse_json_block("[1,2]") is None  # not an object


def test_perceive_marks_indexes_interactables(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    state = loop.perceive(marks=True)
    assert "marks" in state
    marks = state["marks"]
    assert all("i" in mk and "cx" in mk and "cy" in mk for mk in marks)
    # The labelled Button and the accessible Field both qualify.
    assert any(mk["label"] == "Search" for mk in marks)
    # Indices are sequential.
    assert [mk["i"] for mk in marks] == list(range(len(marks)))


def test_act_with_expect_verifies_and_flags(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    loop.start()
    out = loop.act("tap_at_xy", x=100, y=50, expect=lambda s: True)
    assert out["verified"] is True

    out2 = loop.act("tap_at_xy", x=1, y=1, expect=lambda s: False)
    assert out2["verified"] is False  # retried once, still unverified


def test_act_without_expect_has_no_verified_key(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    loop.start()
    out = loop.act("tap_at_xy", x=5, y=5)
    assert "verified" not in out and out["result"] is True


def test_undo_last_ios_uses_swipe_back_not_home(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path, "ios")
    loop.start()
    loop.act("tap_at_xy", x=1, y=2)
    loop.undo_last()
    h = sys.modules["iphone_harness.helpers"]
    assert ("swipe_back",) in h._calls
    assert not any(c[0] == "appium" for c in h._calls if isinstance(c, tuple))


def test_run_drives_until_done(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    scripted = [
        '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}',
        '{"fn": "type_text", "kwargs": {"text": "coffee"}}',
        '{"done": true, "reason": "searched"}',
    ]
    step = {"n": 0}

    def llm(prompt):
        r = scripted[step["n"]]
        step["n"] += 1
        return r

    result = loop.run("search for coffee", llm, max_steps=10)
    assert result["status"] == "done"
    assert result["reason"] == "searched"
    h = sys.modules["iphone_harness.helpers"]
    assert ("tap_at_xy", 100, 50) in h._calls
    assert ("type_text", "coffee") in h._calls


def test_run_stops_at_max_steps(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    result = loop.run("never ends", lambda p: '{"fn": "tap_at_xy", "kwargs": {"x": 1, "y": 1}}', max_steps=3)
    assert result["status"] == "max_steps"
    assert result["steps"] == 3


def test_run_requires_callable_llm(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    with pytest.raises(TypeError):
        loop.run("x", llm="not callable")
