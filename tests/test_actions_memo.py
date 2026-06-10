"""goal/022 D5 — get_available_actions() memoized per helpers module.

_build_agent_prompt re-ran inspect.signature + getdoc over ~40 verbs on every
LLM step. The schema is pure introspection over a module whose functions never
change at runtime — now memoized in _ACTIONS_MEMO keyed (weakly) by the module
object, so swapping in a different helpers module (tests do this constantly)
still yields a fresh schema.
"""
import sys
import types

import pytest


def _helpers_module(verbs):
    m = types.ModuleType(f"fake_helpers_{id(verbs)}")
    for verb in verbs:
        def fn(x=1, _v=verb):
            """One-line doc."""
            return _v
        fn.__name__ = verb
        setattr(m, verb, fn)
    return m


def _install(monkeypatch, helpers):
    a = types.ModuleType("fake_admin")
    a.ensure_daemon = lambda *args, **kw: True
    import importlib
    parent = importlib.import_module("iphone_harness")
    monkeypatch.setitem(sys.modules, "iphone_harness.helpers", helpers)
    monkeypatch.setitem(sys.modules, "iphone_harness.admin", a)
    monkeypatch.setattr(parent, "helpers", helpers, raising=False)
    monkeypatch.setattr(parent, "admin", a, raising=False)


def _loop(monkeypatch, tmp_path, helpers):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path / "sessions")
    _install(monkeypatch, helpers)
    from mobile_use.agent_loop import AgentLoop
    return AgentLoop(platform="ios", session_name="memo-test", collect=False)


def test_repeat_calls_skip_introspection(monkeypatch, tmp_path):
    import mobile_use.agent_loop as al
    h = _helpers_module(["tap", "swipe"])
    loop = _loop(monkeypatch, tmp_path, h)

    sig_calls = {"n": 0}
    orig_signature = al.inspect.signature

    def counting_signature(fn):
        sig_calls["n"] += 1
        return orig_signature(fn)

    monkeypatch.setattr(al.inspect, "signature", counting_signature)

    first = loop.get_available_actions()
    after_first = sig_calls["n"]
    assert after_first >= 2 and set(first) == {"tap", "swipe"}

    second = loop.get_available_actions()
    assert sig_calls["n"] == after_first, "second call must be a memo hit"
    assert second == first


def test_module_swap_refreshes_schema(monkeypatch, tmp_path):
    h_a = _helpers_module(["tap"])
    loop_a = _loop(monkeypatch, tmp_path, h_a)
    assert set(loop_a.get_available_actions()) == {"tap"}

    h_b = _helpers_module(["tap", "swipe", "type_text"])
    loop_b = _loop(monkeypatch, tmp_path, h_b)
    assert set(loop_b.get_available_actions()) == {"tap", "swipe", "type_text"}


def test_memo_result_is_mutation_safe(monkeypatch, tmp_path):
    h = _helpers_module(["tap"])
    loop = _loop(monkeypatch, tmp_path, h)
    first = loop.get_available_actions()
    first.pop("tap")
    assert "tap" in loop.get_available_actions(), \
        "caller mutation must not poison the memo"


def test_schema_content_unchanged_by_memo(monkeypatch, tmp_path):
    h = _helpers_module(["tap"])
    loop = _loop(monkeypatch, tmp_path, h)
    schema = loop.get_available_actions()
    assert schema["tap"]["signature"].startswith("(")
    assert schema["tap"]["doc"] == "One-line doc."
