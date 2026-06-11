"""Multimodal agent loop: screenshot bytes flow to images-capable callables.

No network calls anywhere — llm callables are fakes; anthropic is never hit.
"""
import sys
import types

import pytest

from mobile_use.agent_loop import _llm_accepts_images, _state_screenshot_bytes

# ---- signature inspection -------------------------------------------------------

def test_accepts_images_kwarg():
    def llm(prompt, images=None):
        return "{}"
    assert _llm_accepts_images(llm) is True


def test_plain_prompt_callable_not_images():
    def llm(prompt):
        return "{}"
    assert _llm_accepts_images(llm) is False


def test_bare_var_kwargs_does_not_count():
    def llm(prompt, **kwargs):
        return "{}"
    assert _llm_accepts_images(llm) is False


def test_uninspectable_callable_safe():
    assert _llm_accepts_images(len) in (True, False)  # must not raise


# ---- screenshot extraction --------------------------------------------------------

def test_screenshot_bytes_from_path_key(tmp_path):
    p = tmp_path / "s.png"
    p.write_bytes(b"\x89PNGdata")
    assert _state_screenshot_bytes({"screenshot_path": str(p)}) == b"\x89PNGdata"


def test_screenshot_bytes_from_dict_key(tmp_path):
    p = tmp_path / "s.png"
    p.write_bytes(b"\x89PNGdict")
    assert _state_screenshot_bytes({"screenshot": {"path": str(p)}}) == b"\x89PNGdict"


def test_screenshot_bytes_missing_returns_none(tmp_path):
    assert _state_screenshot_bytes({}) is None
    assert _state_screenshot_bytes({"screenshot_path": str(tmp_path / "ghost.png")}) is None


# ---- run() passes images to capable llms only ---------------------------------------

def _install_fakes(monkeypatch, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNGscreenshot-bytes")
    m = types.ModuleType("fake_helpers")

    def snapshot(visible_only=True):
        return {"screenshot_path": str(shot),
                "ui_tree": [], "active_app": {"bundleId": "com.x"},
                "window_size": {"width": 390, "height": 844}, "alert": None}

    def screenshot(path=None):
        return str(shot)

    def ui_tree():
        return []

    def active_app():
        return {"bundleId": "com.x"}

    def window_size():
        return {"width": 390, "height": 844}

    def alert():
        return None

    def auto_dismiss_dialog():
        return False

    def tap(x=None, y=None, **kw):
        return True

    for fn in (snapshot, screenshot, ui_tree, active_app, window_size, alert,
               auto_dismiss_dialog, tap):
        setattr(m, fn.__name__, fn)
    fake_a = types.ModuleType("fake_admin")
    fake_a.ensure_daemon = lambda *a, **kw: True
    import iphone_harness
    monkeypatch.setitem(sys.modules, "iphone_harness.helpers", m)
    monkeypatch.setitem(sys.modules, "iphone_harness.admin", fake_a)
    monkeypatch.setattr(iphone_harness, "helpers", m, raising=False)
    monkeypatch.setattr(iphone_harness, "admin", fake_a, raising=False)


def _loop(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "0")
    from mobile_use.agent_loop import AgentLoop
    return AgentLoop(platform="ios", session_name="mm", collect=False)


def test_capturing_llm_receives_screenshot_bytes(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, tmp_path)
    loop = _loop(monkeypatch, tmp_path)
    seen = {}

    def llm(prompt, images=None):
        seen["images"] = images
        return '{"done": true, "reason": "test"}'

    result = loop.run("do nothing", llm, max_steps=1)
    assert result["status"] == "done"
    assert seen["images"] is not None
    assert seen["images"][0] == b"\x89PNGscreenshot-bytes"


def test_legacy_single_arg_llm_unaffected(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, tmp_path)
    loop = _loop(monkeypatch, tmp_path)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"done": true, "reason": "test"}'

    result = loop.run("do nothing", llm, max_steps=1)
    assert result["status"] == "done"
    assert len(calls) == 1


# ---- _default_llm multimodal + install hint ------------------------------------------

def test_default_llm_builds_image_blocks(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            block = types.SimpleNamespace(type="text", text='{"done": true}')
            return types.SimpleNamespace(content=[block])

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from mobile_use.agent_loop import _default_llm
    llm = _default_llm()
    assert llm is not None
    out = llm("what do you see?", images=[b"\x89PNGxyz"])
    assert out == '{"done": true}'
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[-1] == {"type": "text", "text": "what do you see?"}


def test_default_llm_plain_prompt_without_images(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            block = types.SimpleNamespace(type="text", text="ok")
            return types.SimpleNamespace(content=[block])

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = lambda api_key=None: types.SimpleNamespace(
        messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from mobile_use.agent_loop import _default_llm
    llm = _default_llm()
    llm("plain")
    assert captured["messages"][0]["content"] == "plain"


def test_missing_anthropic_hint_names_agent_extra():
    import inspect as _inspect

    import mobile_use.agent_loop as al
    src = _inspect.getsource(al.run_agent)
    assert "mobile-use[agent]" in src


def test_pyproject_has_agent_extra():
    import tomllib
    from pathlib import Path
    pj = Path(__file__).resolve().parents[1] / "pyproject.toml"
    d = tomllib.load(open(pj, "rb"))
    assert "agent" in d["project"]["optional-dependencies"]
    assert any("anthropic" in dep for dep in d["project"]["optional-dependencies"]["agent"])
