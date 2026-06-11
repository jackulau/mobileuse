"""Agent action-dispatch hardening: curated allowlist + destructive gate.

act() must dispatch ONLY ACTION_VERBS (plus an explicit allow_extra), and
destructive verbs are refused unless MU_ALLOW_DESTRUCTIVE=1 — a hallucinated
action name can no longer reach arbitrary module attributes.
"""
import sys
import types

import pytest


def _make_fake_helpers():
    m = types.ModuleType("fake_helpers")
    calls = []

    def screenshot(path=None):
        calls.append(("screenshot", path))
        return "/tmp/fake.png"

    def ui_tree():
        return []

    def active_app():
        return {"bundleId": "com.fake.app"}

    def window_size():
        return {"width": 390, "height": 844}

    def alert():
        return None

    def auto_dismiss_dialog():
        return False

    def tap(x=None, y=None, **kw):
        calls.append(("tap", {"x": x, "y": y}))
        return True

    def uninstall_app(bundle_id):
        calls.append(("uninstall_app", bundle_id))
        return True

    def secret_internal_helper():
        calls.append(("secret_internal_helper", None))
        return "should never be reachable by hallucinated dispatch"

    for fn in (screenshot, ui_tree, active_app, window_size, alert,
               auto_dismiss_dialog, tap, uninstall_app, secret_internal_helper):
        setattr(m, fn.__name__, fn)
    m._calls = calls
    return m


def _install_fakes(monkeypatch):
    fake_h = _make_fake_helpers()
    fake_a = types.ModuleType("fake_admin")
    fake_a.ensure_daemon = lambda *a, **kw: True
    import iphone_harness
    monkeypatch.setitem(sys.modules, "iphone_harness.helpers", fake_h)
    monkeypatch.setitem(sys.modules, "iphone_harness.admin", fake_a)
    monkeypatch.setattr(iphone_harness, "helpers", fake_h, raising=False)
    monkeypatch.setattr(iphone_harness, "admin", fake_a, raising=False)
    return fake_h


def _loop(monkeypatch, tmp_path, **kw):
    monkeypatch.setenv("HOME", str(tmp_path))
    from mobile_use.agent_loop import AgentLoop
    return AgentLoop(platform="ios", session_name="safety", collect=False, **kw)


def test_hallucinated_verb_refused_structured(tmp_path, monkeypatch):
    fake_h = _install_fakes(monkeypatch)
    loop = _loop(monkeypatch, tmp_path)
    result = loop.act("secret_internal_helper")
    assert result.get("refused") == "uncurated"
    assert "Unknown action" in result["error"]
    assert "allow_extra" in result["error"]
    assert ("secret_internal_helper", None) not in fake_h._calls


def test_injected_helper_dispatchable_only_with_allow_extra(tmp_path, monkeypatch):
    fake_h = _install_fakes(monkeypatch)
    loop = _loop(monkeypatch, tmp_path, allow_extra={"secret_internal_helper"})
    result = loop.act("secret_internal_helper")
    assert "error" not in result
    assert ("secret_internal_helper", None) in fake_h._calls


def test_uninstall_app_refused_by_default(tmp_path, monkeypatch):
    fake_h = _install_fakes(monkeypatch)
    monkeypatch.delenv("MU_ALLOW_DESTRUCTIVE", raising=False)
    loop = _loop(monkeypatch, tmp_path, allow_extra={"uninstall_app"})
    result = loop.act("uninstall_app", bundle_id="com.victim.app")
    assert result.get("refused") == "destructive"
    assert "MU_ALLOW_DESTRUCTIVE" in result["error"]
    assert ("uninstall_app", "com.victim.app") not in fake_h._calls


def test_uninstall_app_allowed_with_env_opt_in(tmp_path, monkeypatch):
    fake_h = _install_fakes(monkeypatch)
    monkeypatch.setenv("MU_ALLOW_DESTRUCTIVE", "1")
    loop = _loop(monkeypatch, tmp_path, allow_extra={"uninstall_app"})
    result = loop.act("uninstall_app", bundle_id="com.victim.app")
    assert "error" not in result
    assert ("uninstall_app", "com.victim.app") in fake_h._calls


def test_destructive_gate_beats_allow_extra(tmp_path, monkeypatch):
    """allow_extra cannot bypass the destructive refusal — env opt-in only."""
    _install_fakes(monkeypatch)
    monkeypatch.delenv("MU_ALLOW_DESTRUCTIVE", raising=False)
    loop = _loop(monkeypatch, tmp_path, allow_extra={"uninstall_app"})
    assert loop.act("uninstall_app", bundle_id="x").get("refused") == "destructive"


def test_routine_taps_unaffected(tmp_path, monkeypatch):
    fake_h = _install_fakes(monkeypatch)
    loop = _loop(monkeypatch, tmp_path)
    result = loop.act("tap", x=50, y=100)
    assert result == {"result": True}
    assert ("tap", {"x": 50, "y": 100}) in fake_h._calls


def test_destructive_verbs_constant_pins_uninstall():
    from mobile_use.agent_loop import DESTRUCTIVE_VERBS
    assert "uninstall_app" in DESTRUCTIVE_VERBS


def test_refusal_does_not_raise(tmp_path, monkeypatch):
    """Structured refusal, never an exception — macro replay and demos keep
    running on a refused step instead of crashing."""
    _install_fakes(monkeypatch)
    loop = _loop(monkeypatch, tmp_path)
    out = loop.act("definitely_not_a_verb")
    assert isinstance(out, dict)
    assert "error" in out
