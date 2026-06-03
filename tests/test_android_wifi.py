"""D5 — Android adb-over-Wi-Fi: subcommand + helpers + TCP-serial discovery.

Device-free: adb is mocked (no real device, no adb binary required).
"""
import subprocess
import sys

import pytest

from mobile_use import devices


# ---- _run_adb --------------------------------------------------------------

def test_run_adb_missing_adb(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: None)
    ok, out = devices._run_adb(["devices"])
    assert ok is False
    assert "adb not found" in out


def test_run_adb_success(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: "/usr/bin/adb")
    monkeypatch.setattr(devices.subprocess, "check_output",
                        lambda *a, **k: b"connected to 1.2.3.4:5555")
    ok, out = devices._run_adb(["connect", "1.2.3.4:5555"])
    assert ok is True
    assert "connected to" in out


def test_run_adb_nonzero_exit(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: "/usr/bin/adb")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, a[0], output=b"error: device offline")

    monkeypatch.setattr(devices.subprocess, "check_output", boom)
    ok, out = devices._run_adb(["connect", "x"])
    assert ok is False
    assert "device offline" in out


# ---- adb_connect text classification ---------------------------------------

@pytest.mark.parametrize("text, ok", [
    ("connected to 1.2.3.4:5555", True),
    ("already connected to 1.2.3.4:5555", True),
    ("failed to connect to 1.2.3.4:5555", False),
    ("cannot connect to 1.2.3.4:5555: Connection refused", False),
    ("unable to connect", False),
])
def test_adb_connect_classifies_text(monkeypatch, text, ok):
    monkeypatch.setattr(devices, "_run_adb", lambda args, timeout=10.0: (True, text))
    got, _detail = devices.adb_connect("1.2.3.4", 5555)
    assert got is ok


def test_adb_connect_adb_missing(monkeypatch):
    monkeypatch.setattr(devices, "_run_adb", lambda args, timeout=10.0: (False, "adb not found on PATH"))
    ok, detail = devices.adb_connect("1.2.3.4", 5555)
    assert ok is False
    assert "adb not found" in detail


def test_adb_enable_tcpip_passes_usb_serial(monkeypatch):
    seen = {}

    def fake(args, timeout=10.0):
        seen["args"] = args
        return True, "restarting in TCP mode port: 5555"

    monkeypatch.setattr(devices, "_run_adb", fake)
    ok, _ = devices.adb_enable_tcpip(port=5555, usb_serial="USB123")
    assert ok is True
    assert seen["args"] == ["-s", "USB123", "tcpip", "5555"]


# ---- android_wifi_main flows ------------------------------------------------

def test_wifi_main_success(monkeypatch, capsys):
    monkeypatch.setattr(devices, "adb_enable_tcpip", lambda **k: (True, "restarting in TCP mode port: 5555"))
    monkeypatch.setattr(devices, "adb_connect", lambda ip, p, **k: (True, f"connected to {ip}:{p}"))
    rc = devices.android_wifi_main(["192.168.1.5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ANH_UDID=192.168.1.5:5555" in out


def test_wifi_main_custom_port_from_target(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(devices, "adb_enable_tcpip", lambda **k: (True, "ok"))

    def fake_connect(ip, p, **k):
        seen["ipp"] = (ip, p)
        return True, f"connected to {ip}:{p}"

    monkeypatch.setattr(devices, "adb_connect", fake_connect)
    rc = devices.android_wifi_main(["10.0.0.7:5557"])
    assert rc == 0
    assert seen["ipp"] == ("10.0.0.7", 5557)


def test_wifi_main_connect_failure_returns_1(monkeypatch):
    monkeypatch.setattr(devices, "adb_enable_tcpip", lambda **k: (True, "ok"))
    monkeypatch.setattr(devices, "adb_connect", lambda ip, p, **k: (False, "failed to connect"))
    assert devices.android_wifi_main(["192.168.1.5"]) == 1


def test_wifi_main_disconnect(monkeypatch):
    seen = {}

    def fake_disc(ip, p, **k):
        seen["ipp"] = (ip, p)
        return True, "disconnected"

    monkeypatch.setattr(devices, "adb_disconnect", fake_disc)
    rc = devices.android_wifi_main(["192.168.1.5:5555", "--disconnect"])
    assert rc == 0
    assert seen["ipp"] == ("192.168.1.5", 5555)


def test_wifi_main_no_target_is_usage_error():
    assert devices.android_wifi_main(["--disconnect"]) == 2


def test_wifi_main_bad_port_is_usage_error():
    assert devices.android_wifi_main(["1.2.3.4", "--port", "notaport"]) == 2


def test_wifi_main_help():
    assert devices.android_wifi_main(["-h"]) == 0
    assert devices.android_wifi_main([]) == 2


# ---- discovery surfaces TCP serials w/ transport tag ------------------------

def test_discover_tags_transport(monkeypatch):
    monkeypatch.setattr(devices, "_ios_udids", lambda: [])
    monkeypatch.setattr(devices, "_ios_sims", lambda: [])
    monkeypatch.setattr(devices, "_adb_devices_long",
                        lambda: [("192.168.1.5:5555", "Pixel 7"), ("39121FDJ", "Pixel 6")])
    out = devices.discover_connected()
    by_udid = {d["udid"]: d.get("transport") for d in out}
    assert by_udid["192.168.1.5:5555"] == "wifi"
    assert by_udid["39121FDJ"] == "usb"


def test_list_output_has_transport_column(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_ios_udids", lambda: [])
    monkeypatch.setattr(devices, "_ios_sims", lambda: [])
    monkeypatch.setattr(devices, "_adb_devices_long", lambda: [("192.168.1.5:5555", "Pixel 7")])
    devices._cmd_list([])
    out = capsys.readouterr().out
    assert "TRANSPORT" in out
    assert "wifi" in out


# ---- cli routing ------------------------------------------------------------

def test_cli_routes_android_wifi(monkeypatch):
    import mobile_use.cli as cli
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(devices, "android_wifi_main", fake_main)
    monkeypatch.setattr(sys, "argv", ["mobile-use", "android", "wifi", "10.0.0.5", "--port", "5557"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 0
    assert seen["argv"] == ["10.0.0.5", "--port", "5557"]
