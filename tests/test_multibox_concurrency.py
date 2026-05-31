"""Concurrency regression for multibox by-name daemon addressing (goal 016).

The old `Device._load()` mutated global os.environ and `importlib.reload()`d the
shared `iphone_harness.helpers` singleton to rebind its module-global NAME. Because
every Device shared that one module object, two iOS devices clobbered each other —
and `DevicePool.broadcast()` / `ensure_all_ready()` reload concurrently under a
ThreadPoolExecutor, so commands cross-routed to whichever device reloaded last.

The fix addresses daemons by name (like viewer.NamedStreamClient): the active name
is a contextvar bound per call, with a per-name socket cache. These tests prove a
process can drive two named daemons concurrently with zero cross-routing. They FAIL
on the old reload approach (both names collapse to one daemon) and PASS on by-name.

No real device/Appium: two mock iphone daemons stand in, each with a distinct pid
and a distinct MOCK_WIDTH so a response is attributable to exactly one daemon.
"""
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from iphone_harness import _ipc as iph_ipc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spawn_mock(name, width):
    return subprocess.Popen(
        [sys.executable, "-m", "tests._mock_iphone_daemon"],
        env={**os.environ, "IPH_NAME": name, "MOCK_WIDTH": str(width)},
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


@contextmanager
def _mock_daemons(specs):
    """specs: list of (name, width). Spawn, wait until live, yield {name: pid},
    then shut down + clean sockets/pid/log and drop any cached client sockets."""
    from iphone_harness import helpers
    procs = {}
    pids = {}
    try:
        for name, width in specs:
            procs[name] = _spawn_mock(name, width)
        for name, _ in specs:
            assert _wait_alive(name, timeout=5.0), f"mock daemon {name} never came up"
            pid = iph_ipc.identify(name, timeout=1.0)
            assert isinstance(pid, int), f"no pid for {name}"
            pids[name] = pid
        # distinct processes => distinct pids (sanity for the identity assertions)
        assert len(set(pids.values())) == len(pids), f"pids collided: {pids}"
        yield pids
    finally:
        for name, _ in specs:
            # close + forget this name's cached client socket
            try:
                tok = helpers._use_name(name)
                try:
                    helpers._drop_conn()
                finally:
                    helpers._reset_name(tok)
                helpers._conns.pop(name, None)
            except Exception:
                pass
            try:
                s, _ = iph_ipc.connect(name, timeout=1.0)
                iph_ipc.request(s, None, {"meta": "shutdown"})
                s.close()
            except Exception:
                pass
        for name, p in procs.items():
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


def test_concurrent_named_sends_route_to_own_daemon(monkeypatch):
    """Helpers layer: many threads, two bound names, zero cross-routing.

    Each thread binds a daemon name via helpers._use_name and pings repeatedly.
    Every ping under name A must return daemon A's pid (never B's), and vice versa.
    Under the old single-NAME module this collapses to one daemon and fails.
    """
    from iphone_harness import admin, helpers
    a = f"mbca{uuid.uuid4().hex[:10]}"
    b = f"mbcb{uuid.uuid4().hex[:10]}"
    # Never spawn a real daemon if a transient connect hiccups — the mocks are up.
    monkeypatch.setattr(admin, "ensure_daemon", lambda *args, **kw: None)

    with _mock_daemons([(a, 111), (b, 222)]) as pids:
        leaks = []
        seen = {a: set(), b: set()}
        seen_lock = threading.Lock()

        def worker(name):
            for _ in range(30):
                tok = helpers._use_name(name)
                try:
                    r = helpers._send({"meta": "ping"})
                finally:
                    helpers._reset_name(tok)
                pid = r.get("pid") if isinstance(r, dict) else None
                with seen_lock:
                    seen[name].add(pid)
                    if pid != pids[name]:
                        leaks.append((name, pid, pids[name]))

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in (a, b) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not leaks, f"cross-routed sends (name, got_pid, want_pid): {leaks[:5]}"
        assert seen[a] == {pids[a]}, f"name {a} saw foreign pids: {seen[a]}"
        assert seen[b] == {pids[b]}, f"name {b} saw foreign pids: {seen[b]}"


def test_devicepool_broadcast_routes_each_device(monkeypatch):
    """Device proxy end-to-end: DevicePool.broadcast routes each device to its own
    daemon. Two devices over two mock daemons with distinct MOCK_WIDTH; broadcast
    runs window_size() on both in parallel and each must see its own width.
    """
    from iphone_harness import admin
    from mobile_use.multibox import DevicePool
    a = f"mbcp{uuid.uuid4().hex[:10]}"
    b = f"mbcq{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(admin, "ensure_daemon", lambda *args, **kw: None)

    with _mock_daemons([(a, 111), (b, 222)]):
        pool = DevicePool()
        pool.add_ios(a, udid="UDID-A", appium_url="http://127.0.0.1:4723")
        pool.add_ios(b, udid="UDID-B", appium_url="http://127.0.0.1:4723")

        # Repeat to stress the ThreadPoolExecutor — a routing race would surface
        # as one device's width leaking into the other on some iteration.
        for _ in range(10):
            out = pool.broadcast(lambda d: d.window_size(), max_workers=2)
            assert out[a]["result"]["width"] == 111, f"device {a} mis-routed: {out[a]}"
            assert out[b]["result"]["width"] == 222, f"device {b} mis-routed: {out[b]}"


def test_devices_share_one_helpers_module_but_isolate_state():
    """White-box: the two Devices proxy to the SAME helpers module object (that's the
    bug surface the old code reloaded to work around) — yet bound calls stay isolated.
    Proves the fix doesn't rely on per-device module copies."""
    from mobile_use.multibox import DevicePool
    pool = DevicePool()
    da = pool.add_ios("alpha", udid="U1", appium_url="http://127.0.0.1:4723")
    db = pool.add_ios("beta", udid="U2", appium_url="http://127.0.0.1:4723")
    da._load()
    db._load()
    assert da._helpers is db._helpers, "expected one shared helpers module (no reload)"
    # Binding name is per-context and restores cleanly to the default afterwards.
    helpers = da._helpers
    assert helpers._active_name() == helpers.NAME  # nothing bound at rest
    tok = helpers._use_name("alpha")
    try:
        assert helpers._active_name() == "alpha"
    finally:
        helpers._reset_name(tok)
    assert helpers._active_name() == helpers.NAME  # restored
