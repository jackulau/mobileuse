"""Unit tests for mobile_use.multibox — pool management without devices."""
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
