"""Regression test for the IPC request-framing limit.

asyncio's StreamReader defaults to a 64KB line buffer. A legitimate >64KB
request line (e.g. set_value / paste_text with a long body — both documented
for "long text") used to make the daemon's `await reader.readline()` raise,
breaking the connection so the client saw a BrokenPipe and surfaced a
misleading "daemon unreachable" diagnosis. The fix passes limit=_MAX_MSG to
start_unix_server/start_server in both _ipc modules. Because the real daemons
AND the mock daemons here both route through ipc.serve(), this exercises the
exact code path the fix touches.
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


def _run_large_roundtrip(platform, ipc_mod):
    name = f"big{uuid.uuid4().hex[:10]}"
    os.environ[f"{'IPH' if platform == 'iphone' else 'ANH'}_NAME"] = name
    p = _spawn_mock(platform, name)
    try:
        assert _wait_alive(ipc_mod, name, timeout=5.0), f"mock {platform} daemon never came up"
        big = "A" * 200_000  # ~200KB, well over asyncio's 64KB default line buffer
        s, token = ipc_mod.connect(name, timeout=2.0)
        try:
            resp = ipc_mod.request(s, token, {"method": "set_value", "params": {"value": big}})
        finally:
            s.close()
        # The request must round-trip as a normal result dict, not a broken connection.
        assert isinstance(resp, dict), resp
        assert "result" in resp, resp
        assert resp["result"]["set"] == big
        # Daemon survives and still answers.
        assert ipc_mod.ping(name, timeout=1.0) is True
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


def test_iphone_daemon_accepts_request_over_64kb():
    _run_large_roundtrip("iphone", iph_ipc)


def test_android_daemon_accepts_request_over_64kb():
    _run_large_roundtrip("android", anh_ipc)


@pytest.mark.parametrize("ipc_mod", [iph_ipc, anh_ipc])
def test_serve_passes_limit_to_stream_reader(ipc_mod):
    """Guard the fix itself: serve() must raise the StreamReader limit, not use the 64KB default."""
    import inspect
    src = inspect.getsource(ipc_mod.serve)
    assert "limit=_MAX_MSG" in src, "serve() must pass limit=_MAX_MSG to the asyncio server"
