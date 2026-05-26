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
