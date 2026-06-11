"""wifi reconnect — re-establish remembered wireless devices + ensure hooks.

Device-free: adb, reachability probes, and mDNS resolution are monkeypatched.
"""
import json
import sys

import pytest

from mobile_use import devices


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    import mobile_use.wifi_store as w
    return w


# ---- wifi_reconnect: android ---------------------------------------------------

def test_reconnect_issues_adb_connect_per_android_entry(store, monkeypatch):
    store.remember_device("android", serial="192.168.1.42:5555")
    store.remember_device("android", serial="192.168.1.43:5557")
    calls = []
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (True, "open"))
    monkeypatch.setattr(devices, "adb_connect",
                        lambda ip, p, **k: (calls.append((ip, p)) or
                                            (True, f"connected to {ip}:{p}")))
    results = devices.wifi_reconnect()
    assert sorted(calls) == [("192.168.1.42", 5555), ("192.168.1.43", 5557)]
    assert all(r["ok"] for r in results)


def test_reconnect_unreachable_android_fails_fast_without_adb(store, monkeypatch):
    store.remember_device("android", serial="192.168.1.42:5555")
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (False, "closed"))
    called = []
    monkeypatch.setattr(devices, "adb_connect",
                        lambda *a, **k: called.append(a) or (True, "x"))
    results = devices.wifi_reconnect()
    assert called == []
    assert results[0]["ok"] is False
    assert "not reachable" in results[0]["detail"]


def test_reconnect_refreshes_last_seen_on_success(store, monkeypatch):
    store.remember_device("android", serial="192.168.1.42:5555")
    old_seen = store.remembered_devices()[0]["last_seen"]
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (True, "open"))
    monkeypatch.setattr(devices, "adb_connect",
                        lambda ip, p, **k: (True, f"connected to {ip}:{p}"))
    devices.wifi_reconnect()
    assert store.remembered_devices()[0]["last_seen"] >= old_seen


# ---- wifi_reconnect: ios --------------------------------------------------------

def test_reconnect_ios_refreshes_url_in_store(store, monkeypatch, tmp_path):
    store.remember_device("ios", udid="00008140-AAA",
                          wda_url="http://192.168.1.50:8100")
    monkeypatch.setattr(
        devices, "ios_wifi_target",
        lambda udid=None, host=None, port=8100, probe=True, timeout=2.0: {
            "url": "http://iPhone.local:8100", "host": "iPhone.local",
            "port": port, "source": "mdns", "reachable": True, "candidates": []})
    monkeypatch.setattr(devices, "_env_path", lambda: tmp_path / "absent.env")
    results = devices.wifi_reconnect()
    assert results[0]["ok"] is True
    assert "re-resolved" in results[0]["detail"]
    entry = store.remembered_devices("ios")[0]
    assert entry["wda_url"] == "http://iPhone.local:8100"


def test_reconnect_ios_updates_env_only_when_persisted(store, monkeypatch, tmp_path):
    store.remember_device("ios", udid="00008140-AAA",
                          wda_url="http://192.168.1.50:8100")
    envf = tmp_path / ".env"
    envf.write_text("IPH_WDA_URL=http://192.168.1.50:8100\nKEEP=1\n",
                    encoding="utf-8")
    monkeypatch.setattr(devices, "_env_path", lambda: envf)
    monkeypatch.setattr(
        devices, "ios_wifi_target",
        lambda udid=None, host=None, port=8100, probe=True, timeout=2.0: {
            "url": "http://iPhone.local:8100", "host": "iPhone.local",
            "port": port, "source": "mdns", "reachable": True, "candidates": []})
    devices.wifi_reconnect()
    body = envf.read_text(encoding="utf-8")
    assert "IPH_WDA_URL=http://iPhone.local:8100" in body
    assert "KEEP=1" in body


def test_reconnect_ios_unreachable_reports_fail(store, monkeypatch):
    store.remember_device("ios", wda_url="http://192.168.1.50:8100")
    monkeypatch.setattr(
        devices, "ios_wifi_target",
        lambda udid=None, host=None, port=8100, probe=True, timeout=2.0: None)
    results = devices.wifi_reconnect()
    assert results[0]["ok"] is False
    assert "WDA not reachable" in results[0]["detail"]


def test_reconnect_platform_filter(store, monkeypatch):
    store.remember_device("android", serial="192.168.1.42:5555")
    store.remember_device("ios", wda_url="http://x:8100")
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (True, "open"))
    monkeypatch.setattr(devices, "adb_connect",
                        lambda ip, p, **k: (True, "connected to x"))
    results = devices.wifi_reconnect("android")
    assert len(results) == 1
    assert results[0]["platform"] == "android"


# ---- wifi_reconnect_main ---------------------------------------------------------

def test_reconnect_main_exit_codes(store, monkeypatch, capsys):
    assert devices.wifi_reconnect_main([]) == 0  # empty store = success
    assert "--persist" in capsys.readouterr().out

    store.remember_device("android", serial="192.168.1.42:5555")
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (False, "closed"))
    assert devices.wifi_reconnect_main([]) == 1


def test_reconnect_main_json(store, monkeypatch, capsys):
    store.remember_device("android", serial="192.168.1.42:5555")
    monkeypatch.setattr("mobile_use.netcheck.tcp_reachable",
                        lambda h, p, timeout=2.0: (True, "open"))
    monkeypatch.setattr(devices, "adb_connect",
                        lambda ip, p, **k: (True, "connected to x"))
    rc = devices.wifi_reconnect_main(["--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["ok"] is True


def test_reconnect_main_help(capsys):
    assert devices.wifi_reconnect_main(["--help"]) == 0
    assert "remember" in capsys.readouterr().out.lower()


def test_cli_routes_wifi_reconnect(monkeypatch):
    import mobile_use.cli as cli
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(devices, "wifi_reconnect_main", fake_main)
    monkeypatch.setattr(sys, "argv", ["mobile-use", "wifi", "reconnect", "--json"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 0
    assert seen["argv"] == ["--json"]


# ---- android ensure hook ----------------------------------------------------------

def _android_admin():
    from android_harness import admin
    return admin


def test_android_hook_fires_once_for_wifi_serial(monkeypatch):
    admin = _android_admin()
    monkeypatch.setenv("ANH_UDID", "192.168.1.42:5555")
    connects = []
    monkeypatch.setattr("mobile_use.devices._run_adb",
                        lambda args, timeout=10.0: (True, "List of devices attached\n"))
    monkeypatch.setattr("mobile_use.devices.adb_connect",
                        lambda ip, p, **k: connects.append((ip, p)) or (True, "ok"))
    admin._maybe_reconnect_wifi_device()
    assert connects == [("192.168.1.42", 5555)]


def test_android_hook_never_fires_for_usb_serial(monkeypatch):
    admin = _android_admin()
    monkeypatch.setenv("ANH_UDID", "39121FDJ400ESK")
    connects = []
    monkeypatch.setattr("mobile_use.devices.adb_connect",
                        lambda ip, p, **k: connects.append((ip, p)) or (True, "ok"))
    admin._maybe_reconnect_wifi_device()
    assert connects == []


def test_android_hook_skips_when_already_connected(monkeypatch):
    admin = _android_admin()
    monkeypatch.setenv("ANH_UDID", "192.168.1.42:5555")
    connects = []
    monkeypatch.setattr(
        "mobile_use.devices._run_adb",
        lambda args, timeout=10.0: (True, "List of devices attached\n192.168.1.42:5555\tdevice\n"))
    monkeypatch.setattr("mobile_use.devices.adb_connect",
                        lambda ip, p, **k: connects.append((ip, p)) or (True, "ok"))
    admin._maybe_reconnect_wifi_device()
    assert connects == []


def test_android_hook_never_raises(monkeypatch):
    admin = _android_admin()
    monkeypatch.setenv("ANH_UDID", "192.168.1.42:5555")

    def boom(*a, **k):
        raise RuntimeError("adb exploded")

    monkeypatch.setattr("mobile_use.devices._run_adb", boom)
    admin._maybe_reconnect_wifi_device()  # must not raise


# ---- ios ensure hook ---------------------------------------------------------------

def _ios_admin():
    from iphone_harness import admin
    return admin


def test_ios_hook_noop_when_reachable(monkeypatch):
    admin = _ios_admin()
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    monkeypatch.setattr("mobile_use.netcheck.target_reachable",
                        lambda url, default_port=None, timeout=2.0: (True, "open"))
    resolved = []
    monkeypatch.setattr("mobile_use.devices.ios_wifi_target",
                        lambda **k: resolved.append(k) or None)
    admin._maybe_refresh_wifi_wda()
    assert resolved == []
    import os
    assert os.environ["IPH_WDA_URL"] == "http://192.168.1.50:8100"


def test_ios_hook_refreshes_env_when_unreachable(monkeypatch):
    admin = _ios_admin()
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")
    monkeypatch.setattr("mobile_use.netcheck.target_reachable",
                        lambda url, default_port=None, timeout=2.0: (False, "closed"))
    monkeypatch.setattr(
        "mobile_use.devices.ios_wifi_target",
        lambda udid=None, host=None, port=8100, probe=True: {
            "url": "http://iPhone.local:8100", "host": "iPhone.local",
            "port": port, "source": "mdns", "reachable": True, "candidates": []})
    spawn_env = {"IPH_WDA_URL": "http://192.168.1.50:8100"}
    admin._maybe_refresh_wifi_wda(spawn_env)
    import os
    assert os.environ["IPH_WDA_URL"] == "http://iPhone.local:8100"
    assert spawn_env["IPH_WDA_URL"] == "http://iPhone.local:8100"


def test_ios_hook_noop_without_url(monkeypatch):
    admin = _ios_admin()
    monkeypatch.delenv("IPH_WDA_URL", raising=False)
    probed = []
    monkeypatch.setattr("mobile_use.netcheck.target_reachable",
                        lambda *a, **k: probed.append(a) or (True, "x"))
    admin._maybe_refresh_wifi_wda()
    assert probed == []


def test_ios_hook_never_raises(monkeypatch):
    admin = _ios_admin()
    monkeypatch.setenv("IPH_WDA_URL", "http://192.168.1.50:8100")

    def boom(*a, **k):
        raise RuntimeError("network exploded")

    monkeypatch.setattr("mobile_use.netcheck.target_reachable", boom)
    admin._maybe_refresh_wifi_wda()  # must not raise
