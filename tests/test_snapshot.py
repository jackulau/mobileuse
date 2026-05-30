"""D14 — batch perceive() into one daemon snapshot RPC (5 round-trips -> 1).

AgentLoop.perceive() issued 5 independent connect+request round-trips
(screenshot, page_source/ui_tree, active_app, window_size, alert), each blocking
serially through the daemon's single executor. The daemon already holds one live
session, so a single `snapshot` RPC gathers them all server-side. perceive()
uses it when advertised and falls back to per-call perception for older daemons.
"""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from android_harness import _ipc as anh_ipc
from iphone_harness import _ipc as iph_ipc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spawn(platform, name):
    module = "tests._mock_iphone_daemon" if platform == "iphone" else "tests._mock_android_daemon"
    env_var = "IPH_NAME" if platform == "iphone" else "ANH_NAME"
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env={**os.environ, env_var: name},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT), start_new_session=True,
    )


def _wait(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("platform, ipc_mod, helpers_mod, admin_mod", [
    ("iphone", iph_ipc, "iphone_harness.helpers", "iphone_harness.admin"),
    ("android", anh_ipc, "android_harness.helpers", "android_harness.admin"),
])
def test_snapshot_returns_full_state_in_one_rpc(platform, ipc_mod, helpers_mod, admin_mod, monkeypatch):
    import importlib
    helpers = importlib.import_module(helpers_mod)
    admin = importlib.import_module(admin_mod)
    name = f"snap{uuid.uuid4().hex[:8]}"
    os.environ[f"{'IPH' if platform == 'iphone' else 'ANH'}_NAME"] = name
    p = _spawn(platform, name)
    try:
        assert _wait(ipc_mod, name), f"mock {platform} daemon never came up"
        monkeypatch.setattr(helpers, "NAME", name)
        helpers._drop_conn()
        monkeypatch.setattr(admin, "ensure_daemon", lambda *a, **k: None)

        state = helpers.snapshot(visible_only=True)
        assert state["screenshot_path"]
        assert isinstance(state["ui_tree"], list)
        assert state["active_app"]
        assert state["window_size"]["width"] > 0
        assert "alert" in state
    finally:
        helpers._drop_conn()
        try:
            s, _ = ipc_mod.connect(name, timeout=1.0)
            ipc_mod.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=2.0)
        for ext in ("sock", "pid", "log"):
            try:
                (Path("/tmp") / f"{'iph' if platform == 'iphone' else 'anh'}-{name}.{ext}").unlink()
            except FileNotFoundError:
                pass
        os.environ.pop(f"{'IPH' if platform == 'iphone' else 'ANH'}_NAME", None)


def test_perceive_uses_snapshot_when_available(monkeypatch):
    from mobile_use.agent_loop import AgentLoop

    canned = {"screenshot_path": "/x.png", "ui_tree": [{"type": "T"}],
              "active_app": {"bundleId": "a"}, "window_size": {"width": 1, "height": 2}, "alert": None}
    loop = AgentLoop.__new__(AgentLoop)
    loop._load_platform = lambda: None
    loop.collector = None

    class _Sess:
        current_app = None
    loop.session = _Sess()

    calls = []

    class _H:
        def snapshot(self, visible_only=True):
            calls.append("snapshot")
            return dict(canned)

        def screenshot(self):
            calls.append("screenshot")
            return "/should-not-be-called.png"
    loop._helpers = _H()

    out = loop.perceive()
    assert out == canned
    assert calls == ["snapshot"], "perceive must use the batched snapshot, not per-call helpers"
    assert loop.session.current_app == {"bundleId": "a"}


def test_perceive_falls_back_when_snapshot_raises(monkeypatch):
    from mobile_use.agent_loop import AgentLoop
    loop = AgentLoop.__new__(AgentLoop)
    loop._load_platform = lambda: None
    loop.collector = None

    class _Sess:
        current_app = None
    loop.session = _Sess()

    calls = []

    class _H:
        def snapshot(self, visible_only=True):
            raise RuntimeError("old daemon: unknown method snapshot")

        def screenshot(self):
            calls.append("screenshot")
            return "/shot.png"

        def ui_tree(self, visible_only=True):
            calls.append("ui_tree")
            return []

        def active_app(self):
            calls.append("active_app")
            return {"bundleId": "fallback"}

        def window_size(self):
            calls.append("window_size")
            return {"width": 1, "height": 2}

        def alert(self):
            return None
    loop._helpers = _H()

    out = loop.perceive()
    assert out["screenshot_path"] == "/shot.png"
    assert "screenshot" in calls and "ui_tree" in calls, "must fall back to per-call perception"
