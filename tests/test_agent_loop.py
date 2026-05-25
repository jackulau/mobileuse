"""Smoke tests for mobile_use.agent_loop.AgentLoop.

Mocks iphone_harness/android_harness helpers + admin so AgentLoop can be
exercised without a real device. Closes the coverage gap identified in
the goal-007 audit.
"""
import sys
import types
from unittest import mock

import pytest


def _make_fake_helpers(name="ios"):
    """Return a fake helpers module that records every call."""
    calls = []
    m = types.ModuleType(f"fake_{name}_helpers")

    def screenshot(path=None):
        calls.append(("screenshot", path))
        return "/tmp/fake.png"

    def ui_tree(visible_only=False, compact=False):
        calls.append(("ui_tree", visible_only, compact))
        return [{"type": "Button", "label": "Send", "visible": True}]

    def active_app():
        calls.append(("active_app",))
        return {"bundleId": "com.example.app", "name": "Example"}

    def window_size():
        calls.append(("window_size",))
        return {"width": 390, "height": 844}

    def alert():
        calls.append(("alert",))
        return None

    def auto_dismiss_dialog():
        calls.append(("auto_dismiss_dialog",))
        return False

    def tap(el):
        calls.append(("tap", el))
        return True

    def press_back():
        calls.append(("press_back",))
        return True

    def appium(script, **kw):
        calls.append(("appium", script, kw))
        return True

    def find(**kw):
        calls.append(("find", kw))
        return {"x": 100, "y": 200}

    for fn in (screenshot, ui_tree, active_app, window_size, alert,
               auto_dismiss_dialog, tap, press_back, appium, find):
        setattr(m, fn.__name__, fn)

    m._calls = calls
    return m


def _make_fake_admin():
    m = types.ModuleType("fake_admin")
    m._daemon_calls = 0

    def ensure_daemon(*a, **kw):
        m._daemon_calls += 1
        return True

    m.ensure_daemon = ensure_daemon
    return m


def _install_fakes(monkeypatch, platform="ios"):
    fake_h = _make_fake_helpers(platform)
    fake_a = _make_fake_admin()
    if platform == "ios":
        # Pre-import so the parent package exists, then patch both sys.modules
        # AND the parent-package attribute (which is what `import pkg.sub as x` reads).
        import iphone_harness
        monkeypatch.setitem(sys.modules, "iphone_harness.helpers", fake_h)
        monkeypatch.setitem(sys.modules, "iphone_harness.admin", fake_a)
        monkeypatch.setattr(iphone_harness, "helpers", fake_h, raising=False)
        monkeypatch.setattr(iphone_harness, "admin", fake_a, raising=False)
    else:
        import android_harness
        monkeypatch.setitem(sys.modules, "android_harness.helpers", fake_h)
        monkeypatch.setitem(sys.modules, "android_harness.admin", fake_a)
        monkeypatch.setattr(android_harness, "helpers", fake_h, raising=False)
        monkeypatch.setattr(android_harness, "admin", fake_a, raising=False)
    return fake_h, fake_a


# ---- instantiation --------------------------------------------------------

def test_agent_loop_imports():
    from mobile_use.agent_loop import AgentLoop, run_agent
    assert AgentLoop is not None
    assert callable(run_agent)


def test_agent_loop_instantiates_without_device(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="test-session", collect=False)
    assert loop.platform == "ios"
    assert loop._helpers is None
    assert loop._admin is None


def test_agent_loop_unknown_platform_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="windows", session_name="x", collect=False)
    with pytest.raises(RuntimeError, match="Unknown platform"):
        loop._load_platform()


# ---- start / daemon -------------------------------------------------------

def test_agent_loop_start_calls_ensure_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _h, a = _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="test", collect=False)
    loop.start()
    assert a._daemon_calls == 1


# ---- perceive -------------------------------------------------------------

def test_perceive_captures_full_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="perceive-test", collect=False)
    loop.start()
    state = loop.perceive()
    assert state["screenshot_path"] == "/tmp/fake.png"
    assert state["ui_tree"] == [{"type": "Button", "label": "Send", "visible": True}]
    assert state["active_app"]["bundleId"] == "com.example.app"
    assert state["window_size"] == {"width": 390, "height": 844}
    assert state["alert"] is None


def test_perceive_handles_helper_failures(tmp_path, monkeypatch):
    """If a helper raises, perceive captures the error and continues."""
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "ios")
    def boom(*a, **kw):
        raise RuntimeError("device asleep")
    h.screenshot = boom
    h.ui_tree = boom

    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="fail-test", collect=False)
    loop.start()
    state = loop.perceive()
    assert "screenshot_error" in state
    assert "ui_tree_error" in state
    assert state["ui_tree"] == []
    assert state["active_app"]["bundleId"] == "com.example.app"


# ---- act ------------------------------------------------------------------

def test_act_dispatches_to_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="act-test", collect=False)
    loop.start()
    result = loop.act("tap", el={"x": 50, "y": 100})
    assert result == {"result": True}
    assert ("tap", {"x": 50, "y": 100}) in h._calls


def test_act_unknown_action_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="act-bad", collect=False)
    loop.start()
    result = loop.act("nonsense_action")
    assert "error" in result
    assert "Unknown action" in result["error"]


def test_act_captures_helper_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "ios")
    def fail_tap(el):
        raise ValueError("element not visible")
    h.tap = fail_tap

    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="act-fail", collect=False)
    loop.start()
    result = loop.act("tap", el={})
    assert "error" in result
    assert "element not visible" in result["error"]


# ---- undo -----------------------------------------------------------------

def test_undo_last_when_empty_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="undo-empty", collect=False)
    loop.start()
    result = loop.undo_last()
    assert result == {"error": "No actions to undo"}


def test_undo_last_pops_action_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "android")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="android", session_name="undo", collect=False)
    loop.start()
    loop.act("tap", el={"x": 1, "y": 2})
    assert len(loop._action_stack) == 1
    result = loop.undo_last()
    assert "undone" in result
    assert ("press_back",) in h._calls
    assert len(loop._action_stack) == 0


# ---- context + discovery --------------------------------------------------

def test_get_available_actions_lists_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="actions", collect=False)
    actions = loop.get_available_actions()
    assert "tap" in actions
    assert "screenshot" in actions
    assert "ui_tree" in actions
    # private helpers excluded
    assert not any(a.startswith("_") for a in actions)


def test_get_context_returns_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="ctx", collect=False)
    loop.start()
    loop.perceive()
    ctx = loop.get_context()
    assert ctx["platform"] == "ios"
    assert "available_actions" in ctx
    assert "session_summary" in ctx


def test_find_element_delegates_to_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h, _a = _install_fakes(monkeypatch, "ios")
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop(platform="ios", session_name="find", collect=False)
    el = loop.find_element(label="Send")
    assert el == {"x": 100, "y": 200}
    assert ("find", {"label": "Send"}) in h._calls
