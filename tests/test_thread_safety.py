"""Thread-safety regression for the shared daemon socket + driver executor.

(1) The `mobile-use --headed` ViewerServer is a ThreadingMixIn server whose
handlers call helpers.screen_stream_frame() (→ _send) from many threads, all
sharing the one module-global _cached_sock. Without a lock, two threads
interleave sendall/recv on that socket and corrupt each other's framing. The
fix wraps _send's socket round-trip in a reentrant _conn_lock.

(2) The real Daemon must run blocking driver calls on a SINGLE worker so the
screen-stream task and IPC handlers can't hit the non-thread-safe selenium
session concurrently.
"""
import inspect
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from iphone_harness import _ipc as iph_ipc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spawn_mock_iphone(name):
    return subprocess.Popen(
        [sys.executable, "-m", "tests._mock_iphone_daemon"],
        env={**os.environ, "IPH_NAME": name},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


def _wait_alive(name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if iph_ipc.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def test_concurrent_send_does_not_corrupt_shared_socket():
    from iphone_harness import admin, helpers
    name = f"ts{uuid.uuid4().hex[:10]}"
    os.environ["IPH_NAME"] = name
    p = _spawn_mock_iphone(name)
    orig_name = helpers.NAME
    orig_ensure = admin.ensure_daemon
    try:
        assert _wait_alive(name, timeout=5.0), "mock daemon never came up"
        helpers.NAME = name
        helpers._drop_conn()
        # A reconnect would paper over a framing corruption with a fresh socket;
        # force the shared cached socket to be the only path so the lock is what
        # keeps concurrent callers correct.
        admin.ensure_daemon = lambda *a, **k: None

        errors = []
        results = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(25):
                    r = helpers._send({"method": "window_size", "params": {}})
                    with lock:
                        results.append(r)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"concurrent _send raised: {errors[:3]}"
        assert len(results) == 8 * 25
        bad = [r for r in results if not (isinstance(r, dict) and r.get("result", {}).get("width") == 390)]
        assert not bad, f"{len(bad)} corrupted/cross-talked responses, e.g. {bad[:2]}"
    finally:
        helpers.NAME = orig_name
        admin.ensure_daemon = orig_ensure
        helpers._drop_conn()
        try:
            s, _ = iph_ipc.connect(name, timeout=1.0)
            iph_ipc.request(s, None, {"meta": "shutdown"})
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
                (Path("/tmp") / f"iph-{name}.{ext}").unlink()
            except FileNotFoundError:
                pass
        os.environ.pop("IPH_NAME", None)


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_daemon_uses_single_driver_worker(mod_name):
    import importlib
    mod = importlib.import_module(mod_name)
    d = mod.Daemon()
    try:
        assert d._exec._max_workers == 1, "driver executor must be single-worker (serialized)"
        assert "_exec" in inspect.getsource(mod.Daemon._drive), "_drive must use the dedicated executor"
    finally:
        d._exec.shutdown(wait=False)
