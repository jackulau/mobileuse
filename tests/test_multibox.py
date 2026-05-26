"""Unit tests for mobile_use.multibox — pool management without devices."""
import pytest
from unittest.mock import patch

from mobile_use.multibox import Device, DevicePool


def test_device_repr():
    d = Device("test", "ios")
    assert "test" in repr(d)
    assert "ios" in repr(d)


def test_pool_add_ios():
    pool = DevicePool()
    dev = pool.add_ios("iphone1", udid="FAKE-UDID-1")
    assert dev.name == "iphone1"
    assert dev.platform == "ios"
    assert "iphone1" in pool
    assert len(pool) == 1


def test_pool_add_android():
    pool = DevicePool()
    dev = pool.add_android("pixel", udid="FAKE-SERIAL")
    assert dev.name == "pixel"
    assert dev.platform == "android"
    assert len(pool) == 1


def test_pool_mixed_devices():
    pool = DevicePool()
    pool.add_ios("ip1", udid="A")
    pool.add_android("px1", udid="B")
    assert len(pool) == 2
    assert len(pool.ios_devices) == 1
    assert len(pool.android_devices) == 1


def test_pool_getitem():
    pool = DevicePool()
    pool.add_ios("ip1", udid="A")
    assert pool["ip1"].name == "ip1"


def test_pool_remove():
    pool = DevicePool()
    pool.add_ios("ip1", udid="A")
    pool.remove("ip1")
    assert len(pool) == 0
    assert "ip1" not in pool


def test_pool_iter():
    pool = DevicePool()
    pool.add_ios("a", udid="1")
    pool.add_android("b", udid="2")
    names = [d.name for d in pool]
    assert "a" in names
    assert "b" in names


def test_pool_summary_empty():
    pool = DevicePool()
    assert "No devices" in pool.summary()


def test_pool_summary_with_devices():
    pool = DevicePool()
    pool.add_ios("ip1", udid="A")
    s = pool.summary()
    assert "1 device" in s
    assert "ip1" in s


def test_pool_devices_property():
    pool = DevicePool()
    pool.add_ios("a", udid="1")
    pool.add_android("b", udid="2")
    devs = pool.devices
    assert isinstance(devs, list)
    assert len(devs) == 2


# ---- from_connected / add_from_udid -----------------------------------

def _fake_discovery(*entries):
    """Patch discover_connected to return canned entries."""
    from mobile_use import devices
    return patch.object(devices, "discover_connected", return_value=list(entries))


def test_from_connected_builds_pool_from_discovery():
    entries = [
        {"platform": "ios", "udid": "AAAA", "name": "iPhone-13"},
        {"platform": "android", "udid": "S1", "name": "Pixel-7"},
    ]
    with _fake_discovery(*entries):
        pool = DevicePool.from_connected()
    assert len(pool) == 2
    assert "iPhone-13" in pool
    assert "Pixel-7" in pool
    assert pool["iPhone-13"].platform == "ios"
    assert pool["Pixel-7"].platform == "android"


def test_from_connected_raises_when_empty():
    with _fake_discovery():
        with pytest.raises(RuntimeError, match="no devices"):
            DevicePool.from_connected()


def test_from_connected_propagates_ios_kwargs():
    entries = [{"platform": "ios", "udid": "U1", "name": "iPhone"}]
    with _fake_discovery(*entries):
        pool = DevicePool.from_connected(xcode_org_id="TEAM123", wda_bundle_id="com.x.wda")
    dev = pool["iPhone"]
    assert dev._env.get("IPH_XCODE_ORG_ID") == "TEAM123"
    assert dev._env.get("IPH_WDA_BUNDLE_ID") == "com.x.wda"


def test_add_from_udid_picks_matching_device():
    entries = [
        {"platform": "ios", "udid": "AAAA", "name": "iPhone-13"},
        {"platform": "android", "udid": "S1", "name": "Pixel-7"},
    ]
    pool = DevicePool()
    with _fake_discovery(*entries):
        dev = pool.add_from_udid("S1")
    assert dev.platform == "android"
    assert dev.name == "Pixel-7"
    assert len(pool) == 1


def test_add_from_udid_unknown_raises():
    with _fake_discovery():
        pool = DevicePool()
        with pytest.raises(ValueError, match="not found"):
            pool.add_from_udid("NOSUCH")


# ---- per-device Appium port allocation (D3) ---------------------------

def test_add_ios_auto_allocates_port_when_no_url():
    pool = DevicePool()
    dev = pool.add_ios("phoneA", udid="U1")
    assert dev._env.get("IPH_APPIUM_URL", "").startswith("http://127.0.0.1:")
    port = int(dev._env["IPH_APPIUM_URL"].rsplit(":", 1)[1])
    assert 4724 <= port <= 4799


def test_add_android_auto_allocates_port_when_no_url():
    pool = DevicePool()
    dev = pool.add_android("pixA", udid="S1")
    port = int(dev._env["ANH_APPIUM_URL"].rsplit(":", 1)[1])
    assert 4724 <= port <= 4799


def test_explicit_appium_url_skips_allocation():
    pool = DevicePool()
    dev = pool.add_ios("phoneB", udid="U2", appium_url="http://mac.local:4723")
    assert dev._env["IPH_APPIUM_URL"] == "http://mac.local:4723"


def test_distinct_names_get_distinct_ports():
    pool = DevicePool()
    a = pool.add_ios("alpha", udid="U1")
    b = pool.add_ios("beta", udid="U2")
    pa = int(a._env["IPH_APPIUM_URL"].rsplit(":", 1)[1])
    pb = int(b._env["IPH_APPIUM_URL"].rsplit(":", 1)[1])
    assert pa != pb


# ---- D5: parallel ensure_all_ready + broadcast + status ---------------

class _MockAdmin:
    """Stand-in for iphone_harness.admin / android_harness.admin."""
    def __init__(self):
        self.ensure_calls = []
        self.alive_for = set()

    def ensure_daemon(self, name=None, env=None):
        self.ensure_calls.append(name)
        self.alive_for.add(name)

    def daemon_alive(self, name=None):
        return name in self.alive_for


def _stub_device(name, platform, admin=None, helpers=None):
    """Build a Device whose _load() is a no-op and uses our mock admin."""
    d = Device(name, platform)
    d._admin = admin or _MockAdmin()
    d._helpers = helpers or type("H", (), {})()
    d._load = lambda: None
    return d


def test_ensure_all_ready_parallel_fires_all():
    pool = DevicePool()
    admins = {}
    for n in ("a", "b", "c"):
        dev = _stub_device(n, "ios")
        admins[n] = dev._admin
        pool._devices[n] = dev

    results = pool.ensure_all_ready(max_workers=3)
    assert results == {"a": "ready", "b": "ready", "c": "ready"}
    for n, a in admins.items():
        assert n in a.ensure_calls


def test_broadcast_collects_errors_and_results():
    pool = DevicePool()
    pool._devices["good"] = _stub_device("good", "ios")
    pool._devices["bad"] = _stub_device("bad", "android")

    def fn(d):
        if d.name == "bad":
            raise RuntimeError("simulated failure")
        return f"ok-{d.name}"

    out = pool.broadcast(fn, max_workers=2)
    assert out["good"] == {"result": "ok-good"}
    assert "error" in out["bad"]
    assert "simulated failure" in out["bad"]["error"]


def test_status_with_mock_admin_reports_alive():
    pool = DevicePool()
    admin = _MockAdmin()
    admin.alive_for.add("alive-one")
    pool._devices["alive-one"] = _stub_device("alive-one", "ios", admin=admin)
    pool._devices["dead-one"] = _stub_device("dead-one", "android", admin=admin)

    s = pool.status()
    assert s["alive-one"]["daemon"] == "alive"
    assert s["dead-one"]["daemon"] == "not running"


def test_one_slow_device_does_not_block_others():
    import time

    pool = DevicePool()
    for n in ("fast1", "slow", "fast2"):
        pool._devices[n] = _stub_device(n, "ios")

    def fn(d):
        if d.name == "slow":
            time.sleep(0.3)
        return d.name

    start = time.time()
    out = pool.broadcast(fn, max_workers=3)
    elapsed = time.time() - start
    assert set(out.keys()) == {"fast1", "slow", "fast2"}
    assert elapsed < 0.6
