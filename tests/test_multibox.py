"""Unit tests for mobile_use.multibox — pool management without devices."""
from unittest.mock import patch

import pytest

from mobile_use.multibox import Device, DevicePool


def test_device_repr():
    d = Device("test", "ios")
    assert "test" in repr(d)
    assert "ios" in repr(d)


def test_unsupported_verb_names_platform():
    # A platform-only verb accessed on the wrong platform must name the platform (not a
    # bare "no helper"), so DevicePool.broadcast's per-device error is actionable.
    d = Device("px", "android")
    with pytest.raises(AttributeError, match="not supported on android"):
        d.swipe_back            # iOS-only verb; attribute access alone triggers the proxy
    d2 = Device("ip", "ios")
    with pytest.raises(AttributeError, match="not supported on ios"):
        d2.key_event            # Android-only verb on iOS


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


# ---- shared server + per-device driver ports (collision-free default) -------

def test_add_ios_defaults_to_shared_server_with_driver_ports():
    import json as _json

    from mobile_use.multibox import _IOS_MJPEG_RANGE, _IOS_WDA_LOCAL_RANGE
    pool = DevicePool()
    dev = pool.add_ios("phoneA", udid="U1")
    # Shared server: no per-device Appium URL override — the daemon inherits
    # IPH_APPIUM_URL env or the 4723 default.
    assert "IPH_APPIUM_URL" not in dev._env
    caps = _json.loads(dev._env["IPH_CAPS"])
    assert _IOS_WDA_LOCAL_RANGE[0] <= caps["appium:wdaLocalPort"] <= _IOS_WDA_LOCAL_RANGE[1]
    assert _IOS_MJPEG_RANGE[0] <= caps["appium:mjpegServerPort"] <= _IOS_MJPEG_RANGE[1]


def test_add_android_defaults_to_shared_server_with_driver_ports():
    import json as _json

    from mobile_use.multibox import _ANDROID_MJPEG_RANGE, _ANDROID_SYSTEM_RANGE
    pool = DevicePool()
    dev = pool.add_android("pixA", udid="S1")
    assert "ANH_APPIUM_URL" not in dev._env
    caps = _json.loads(dev._env["ANH_CAPS"])
    assert _ANDROID_SYSTEM_RANGE[0] <= caps["appium:systemPort"] <= _ANDROID_SYSTEM_RANGE[1]
    assert _ANDROID_MJPEG_RANGE[0] <= caps["appium:mjpegServerPort"] <= _ANDROID_MJPEG_RANGE[1]


def test_explicit_appium_url_is_dedicated_server_opt_in():
    pool = DevicePool()
    dev = pool.add_ios("phoneB", udid="U2", appium_url="http://mac.local:4723")
    assert dev._env["IPH_APPIUM_URL"] == "http://mac.local:4723"


def test_distinct_names_get_distinct_driver_ports():
    import json as _json
    pool = DevicePool()
    a = pool.add_ios("alpha", udid="U1")
    b = pool.add_ios("beta", udid="U2")
    ca, cb = _json.loads(a._env["IPH_CAPS"]), _json.loads(b._env["IPH_CAPS"])
    assert ca["appium:wdaLocalPort"] != cb["appium:wdaLocalPort"]
    assert ca["appium:mjpegServerPort"] != cb["appium:mjpegServerPort"]


def test_user_supplied_caps_always_win(monkeypatch):
    import json as _json
    monkeypatch.setenv("ANH_CAPS", _json.dumps({"appium:systemPort": 8255,
                                                "appium:disableWindowAnimation": True}))
    pool = DevicePool()
    dev = pool.add_android("pixUser", udid="S9")
    caps = _json.loads(dev._env["ANH_CAPS"])
    assert caps["appium:systemPort"] == 8255          # user value kept
    assert caps["appium:disableWindowAnimation"] is True
    assert "appium:mjpegServerPort" in caps           # auto port still added


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


# ---- wireless iOS multi-device (wda_url) + from_remembered --------------------

def test_add_ios_wda_url_rides_per_device_env():
    pool = DevicePool()
    d1 = pool.add_ios("ip1", udid="UDID-1", wda_url="http://192.168.1.50:8100")
    d2 = pool.add_ios("ip2", udid="UDID-2", wda_url="http://192.168.1.51:8100")
    assert d1._env["IPH_WDA_URL"] == "http://192.168.1.50:8100"
    assert d2._env["IPH_WDA_URL"] == "http://192.168.1.51:8100"
    # No cross-talk: each device's spawn env carries its own URL.
    e1, e2 = d1._build_env(), d2._build_env()
    assert e1["IPH_WDA_URL"] != e2["IPH_WDA_URL"]
    assert e1["IPH_NAME"] == "ip1" and e2["IPH_NAME"] == "ip2"


def test_add_ios_wda_url_none_keeps_current_behavior():
    pool = DevicePool()
    d = pool.add_ios("ip1", udid="UDID-1")
    assert "IPH_WDA_URL" not in d._env


def test_from_remembered_builds_pool_from_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    from mobile_use.wifi_store import remember_device
    remember_device("android", serial="192.168.1.42:5555")
    remember_device("android", serial="192.168.1.43:5557")
    remember_device("ios", udid="00008140-AAA", wda_url="http://192.168.1.50:8100")

    pool = DevicePool.from_remembered()
    assert len(pool) == 3
    assert len(pool.android_devices) == 2
    assert len(pool.ios_devices) == 1
    ios_dev = pool.ios_devices[0]
    assert ios_dev._env["IPH_WDA_URL"] == "http://192.168.1.50:8100"
    assert ios_dev._env["IPH_UDID"] == "00008140-AAA"
    serials = {d._env["ANH_UDID"] for d in pool.android_devices}
    assert serials == {"192.168.1.42:5555", "192.168.1.43:5557"}


def test_from_remembered_empty_store_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    with pytest.raises(RuntimeError, match="--persist"):
        DevicePool.from_remembered()


def test_from_remembered_platform_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    from mobile_use.wifi_store import remember_device
    remember_device("android", serial="192.168.1.42:5555")
    remember_device("ios", wda_url="http://192.168.1.50:8100")
    pool = DevicePool.from_remembered("android")
    assert len(pool) == 1
    assert pool.android_devices


def test_from_remembered_propagates_ios_kwargs(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    from mobile_use.wifi_store import remember_device
    remember_device("ios", udid="00008140-AAA", wda_url="http://x:8100")
    pool = DevicePool.from_remembered(xcode_org_id="TEAM123", wda_bundle_id="com.x.wda")
    d = pool.ios_devices[0]
    assert d._env["IPH_XCODE_ORG_ID"] == "TEAM123"
    assert d._env["IPH_WDA_BUNDLE_ID"] == "com.x.wda"


def test_from_remembered_names_sanitized_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    from mobile_use.wifi_store import remember_device
    # Same host on two ports -> sanitized names collide at the host level only
    # if ports are stripped; assert both exist and are valid daemon names.
    remember_device("android", serial="192.168.1.42:5555")
    remember_device("ios", wda_url="http://192.168.1.42:5555")  # same string, other platform
    pool = DevicePool.from_remembered()
    import re as _re
    names = [d.name for d in pool.devices]
    assert len(names) == len(set(names)) == 2
    assert all(_re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n) for n in names)
