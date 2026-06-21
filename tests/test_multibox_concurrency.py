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

from android_harness import _ipc as anh_ipc
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


# ---- threaded port-allocation uniqueness (TOCTOU race) -----------------------
#
# The old _allocate_appium_port had a check-then-use gap: two concurrent pool
# builds could both see a port as free and both receive it. _port_is_free is
# forced True below so these exercise ONLY the lock+claimed-set logic,
# independent of host port state.

def test_threaded_allocation_never_duplicates(monkeypatch):
    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    monkeypatch.setattr(mb, "_port_is_free", lambda *a, **k: True)

    from concurrent.futures import ThreadPoolExecutor
    names = [f"dev-{i}" for i in range(60)]
    with ThreadPoolExecutor(max_workers=16) as ex:
        ports = list(ex.map(
            lambda n: mb._allocate_port(n, mb._ANDROID_SYSTEM_RANGE), names))
    assert len(ports) == len(set(ports)), "duplicate port handed out under threads"


def test_threaded_same_name_gets_same_port(monkeypatch):
    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    monkeypatch.setattr(mb, "_port_is_free", lambda *a, **k: True)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        ports = list(ex.map(
            lambda _: mb._allocate_port("same-dev", mb._IOS_WDA_LOCAL_RANGE),
            range(32)))
    assert len(set(ports)) == 1, "same name must be idempotent under threads"


def test_threaded_pool_builds_unique_driver_caps(monkeypatch):
    import json as _json

    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    monkeypatch.setattr(mb, "_port_is_free", lambda *a, **k: True)

    from concurrent.futures import ThreadPoolExecutor
    pool = mb.DevicePool()
    names = [f"px-{i}" for i in range(24)]
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda n: pool.add_android(n, udid=f"SER-{n}"), names))

    sys_ports = [_json.loads(pool[n]._env["ANH_CAPS"])["appium:systemPort"]
                 for n in names]
    mjpeg_ports = [_json.loads(pool[n]._env["ANH_CAPS"])["appium:mjpegServerPort"]
                   for n in names]
    assert len(set(sys_ports)) == len(names)
    assert len(set(mjpeg_ports)) == len(names)


# ---- android parity: same by-name routing, separate harness module -----------
#
# The android helpers module (android_harness.helpers) mirrors the iOS one with
# its own contextvar + per-name _conns cache. The iOS tests above prove the iPhone
# path; these prove the android path isolates identically — a mixed pool of phones
# must never cross-route Android-to-Android either. Two mock android daemons stand
# in, each with a distinct pid + MOCK_WIDTH so a response maps to exactly one.

def _spawn_mock_anh(name, width):
    return subprocess.Popen(
        [sys.executable, "-m", "tests._mock_android_daemon"],
        env={**os.environ, "ANH_NAME": name, "MOCK_WIDTH": str(width)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


def _wait_alive_anh(name, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if anh_ipc.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


@contextmanager
def _mock_android_daemons(specs):
    """specs: list of (name, width). Mirror of _mock_daemons for the android harness."""
    from android_harness import helpers
    procs = {}
    pids = {}
    try:
        for name, width in specs:
            procs[name] = _spawn_mock_anh(name, width)
        for name, _ in specs:
            assert _wait_alive_anh(name, timeout=5.0), f"mock android daemon {name} never came up"
            pid = anh_ipc.identify(name, timeout=1.0)
            assert isinstance(pid, int), f"no pid for {name}"
            pids[name] = pid
        assert len(set(pids.values())) == len(pids), f"pids collided: {pids}"
        yield pids
    finally:
        for name, _ in specs:
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
                s, _ = anh_ipc.connect(name, timeout=1.0)
                anh_ipc.request(s, None, {"meta": "shutdown"})
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
                    (Path("/tmp") / f"anh-{name}.{ext}").unlink()
                except FileNotFoundError:
                    pass


def test_concurrent_named_sends_route_to_own_daemon_android(monkeypatch):
    """Android helpers layer: many threads, two bound names, zero cross-routing.
    Mirror of the iOS test — proves the android contextvar/_conns path isolates too."""
    from android_harness import admin, helpers
    a = f"mbaa{uuid.uuid4().hex[:10]}"
    b = f"mbab{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(admin, "ensure_daemon", lambda *args, **kw: None)

    with _mock_android_daemons([(a, 111), (b, 222)]) as pids:
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


def test_devicepool_broadcast_routes_each_android_device(monkeypatch):
    """Android Device proxy end-to-end: DevicePool.broadcast routes each android
    device to its own daemon. Distinct MOCK_WIDTH proves attribution; repeated to
    stress the ThreadPoolExecutor for a routing race."""
    from android_harness import admin
    from mobile_use.multibox import DevicePool
    a = f"mbap{uuid.uuid4().hex[:10]}"
    b = f"mbaq{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(admin, "ensure_daemon", lambda *args, **kw: None)

    with _mock_android_daemons([(a, 111), (b, 222)]):
        pool = DevicePool()
        pool.add_android(a, udid="SER-A", appium_url="http://127.0.0.1:4723")
        pool.add_android(b, udid="SER-B", appium_url="http://127.0.0.1:4723")

        for _ in range(10):
            out = pool.broadcast(lambda d: d.window_size(), max_workers=2)
            assert out[a]["result"]["width"] == 111, f"device {a} mis-routed: {out[a]}"
            assert out[b]["result"]["width"] == 222, f"device {b} mis-routed: {out[b]}"
