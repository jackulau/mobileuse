"""Regression test for daemon connection keep-alive.

The daemon used to serve exactly one request per connection (one readline then
writer.close()), yet the helper layer caches and reuses a module-global socket.
So every steady-state call after the first hit a dead socket — the second
request on a reused connection came back empty ({}) or raised BrokenPipe,
forcing a reconnect + RETRY_DELAY sleep on every agent step. The fix makes the
handler loop per-connection so the cached socket is genuine keep-alive.

This test sends THREE sequential requests on ONE socket at the raw IPC layer
(no helper retry logic to mask the behavior). With the old one-shot handler the
2nd/3rd responses come back as {} (peer closed); with keep-alive all three
return the real result.
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


def _spawn_mock(platform, name):
    module = "tests._mock_iphone_daemon" if platform == "iphone" else "tests._mock_android_daemon"
    env_var = "IPH_NAME" if platform == "iphone" else "ANH_NAME"
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env={**os.environ, env_var: name},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


def _wait_alive(ipc_mod, name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _cleanup(platform, name):
    prefix = "iph" if platform == "iphone" else "anh"
    for ext in ("sock", "pid", "log"):
        try:
            (Path("/tmp") / f"{prefix}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


@pytest.mark.parametrize(
    "platform, ipc_mod, expected_w",
    [("iphone", iph_ipc, 390), ("android", anh_ipc, 1080)],
)
def test_daemon_serves_multiple_requests_on_one_connection(platform, ipc_mod, expected_w):
    name = f"ka{uuid.uuid4().hex[:10]}"
    os.environ[f"{'IPH' if platform == 'iphone' else 'ANH'}_NAME"] = name
    p = _spawn_mock(platform, name)
    try:
        assert _wait_alive(ipc_mod, name, timeout=5.0), f"mock {platform} daemon never came up"
        s, tok = ipc_mod.connect(name, timeout=2.0)
        try:
            responses = [
                ipc_mod.request(s, tok, {"method": "window_size", "params": {}})
                for _ in range(3)
            ]
        finally:
            s.close()
        # Keep-alive: ALL three must be real results. One-shot handler returns {}
        # (empty) for the 2nd and 3rd because the daemon closed the connection.
        for i, r in enumerate(responses):
            assert isinstance(r, dict) and r.get("result", {}).get("width") == expected_w, (
                f"request #{i + 1} on reused connection returned {r!r} — daemon is not keep-alive"
            )
    finally:
        try:
            s2, _ = ipc_mod.connect(name, timeout=1.0)
            ipc_mod.request(s2, None, {"meta": "shutdown"})
            s2.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=2.0)
        _cleanup(platform, name)
        os.environ.pop(f"{'IPH' if platform == 'iphone' else 'ANH'}_NAME", None)


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_serve_handler_is_a_keepalive_loop(mod_name):
    """Guard the fix: the real daemon serve() must loop per connection, not one-shot."""
    import importlib
    import inspect
    mod = importlib.import_module(mod_name)
    src = inspect.getsource(mod.serve)
    assert "while True" in src, f"{mod_name}.serve() handler must loop per connection (keep-alive)"
