"""A4 — cable-free unplug-survival doctor wiring.

Device-free: assert mDNS WDA URLs parse/probe through netcheck, and the
tunnel-readiness advisory behaves (never a hard FAIL).
"""
import iphone_harness.admin as admin
import mobile_use.devices as devices
import mobile_use.netcheck as netcheck


def test_mdns_wda_url_parses():
    # The confirmed-working cable-free path: mDNS hostname, not a raw IP.
    host, port = netcheck.parse_host_port("http://iPhone.local:8100", default_port=8100)
    # urllib lowercases the netloc host — harmless, mDNS/DNS are case-insensitive.
    assert host.lower() == "iphone.local"
    assert port == 8100


def test_wda_url_reachable_uses_netcheck(monkeypatch):
    monkeypatch.setattr(netcheck, "target_reachable",
                        lambda url, default_port=None, timeout=2.0: (True, f"probed {url}"))
    ok, detail = admin._check_wda_url_reachable("http://iPhone.local:8100")
    assert ok is True
    assert "iPhone.local" in detail


def test_tunnel_advisory_skipped_when_not_installed(monkeypatch):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: False)
    ok, detail = admin._check_cablefree_tunnel()
    assert ok is True  # advisory — never a hard FAIL
    assert "skipped" in detail


def test_tunnel_advisory_up(monkeypatch):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status", lambda *a, **k: (True, "ok", {}))
    ok, detail = admin._check_cablefree_tunnel()
    assert ok is True
    assert "up" in detail.lower()


def test_tunnel_advisory_down_warns_but_not_fail(monkeypatch):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status", lambda *a, **k: (False, "closed", None))
    ok, detail = admin._check_cablefree_tunnel()
    assert ok is True  # still not a hard FAIL
    assert "WARN" in detail
    assert "ios tunnel" in detail
