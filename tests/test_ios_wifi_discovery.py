"""A1 — mDNS-preferred Wi-Fi WebDriverAgent target discovery.

Device-free: monkeypatch the device-name source and the TCP reachability probe
so nothing touches a real iPhone. Mirrors the device-free style of
tests/test_ios_wifi.py (no Appium, no sockets to real hosts).
"""
import mobile_use.devices as devices
import mobile_use.netcheck as netcheck


def test_sanitize_bonjour_munges_like_apple():
    assert devices._sanitize_bonjour("Jack's iPhone") == "Jacks-iPhone"
    assert devices._sanitize_bonjour("iPhone") == "iPhone"
    assert devices._sanitize_bonjour("my_phone 12 Pro") == "my-phone-12-Pro"
    assert devices._sanitize_bonjour("   ") is None
    assert devices._sanitize_bonjour(None) is None


def test_mdns_candidates_from_device_name(monkeypatch):
    monkeypatch.setattr(devices, "_ios_name", lambda udid: "Jack's iPhone")
    cands = devices._ios_mdns_candidates(udid="UDID123")
    # Bonjour-munged form is first; both forms are .local hostnames.
    assert cands[0] == "Jacks-iPhone.local"
    assert all(c.endswith(".local") for c in cands)


def test_mdns_candidates_empty_when_no_devices(monkeypatch):
    monkeypatch.setattr(devices, "discover_connected", lambda: [])
    assert devices._ios_mdns_candidates() == []


def test_prefers_mdns_when_reachable(monkeypatch):
    monkeypatch.setattr(devices, "_ios_mdns_candidates", lambda udid=None: ["iPhone.local"])
    monkeypatch.setattr(netcheck, "target_reachable", lambda *a, **k: (True, "ok"))
    res = devices.ios_wifi_target(udid="X", host="10.0.0.5")
    assert res["source"] == "mdns"
    assert res["url"] == "http://iPhone.local:8100"
    assert res["reachable"] is True


def test_falls_back_to_explicit_ip_when_mdns_unreachable(monkeypatch):
    monkeypatch.setattr(devices, "_ios_mdns_candidates", lambda udid=None: ["iPhone.local"])

    def fake_reach(url, default_port=None, timeout=2.0):
        return ("10.0.0.5" in url), "probe"

    monkeypatch.setattr(netcheck, "target_reachable", fake_reach)
    res = devices.ios_wifi_target(host="10.0.0.5")
    assert res["source"] == "explicit"
    assert res["host"] == "10.0.0.5"
    assert res["reachable"] is True


def test_returns_best_candidate_when_nothing_reachable(monkeypatch):
    monkeypatch.setattr(devices, "_ios_mdns_candidates", lambda udid=None: ["iPhone.local"])
    monkeypatch.setattr(netcheck, "target_reachable", lambda *a, **k: (False, "nope"))
    res = devices.ios_wifi_target()
    assert res is not None
    assert res["reachable"] is False
    assert res["url"] == "http://iPhone.local:8100"


def test_none_when_no_candidates(monkeypatch):
    monkeypatch.setattr(devices, "_ios_mdns_candidates", lambda udid=None: [])
    assert devices.ios_wifi_target() is None


def test_probe_false_returns_top_unprobed(monkeypatch):
    monkeypatch.setattr(devices, "_ios_mdns_candidates", lambda udid=None: ["iPhone.local"])
    res = devices.ios_wifi_target(probe=False)
    assert res["reachable"] is None
    assert res["source"] == "mdns"
