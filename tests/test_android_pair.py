"""`mobile-use android pair` — cable-free Android 11+ onboarding.

Device-free: _run_adb is monkeypatched throughout (never executes real adb).
"""
import sys

import pytest

from mobile_use import devices

# ---- adb_pair classification --------------------------------------------------

@pytest.mark.parametrize("text, ok", [
    ("Successfully paired to 192.168.1.42:37123 [guid=adb-xxx]", True),
    ("successfully paired to 10.0.0.7:40001", True),
    ("Failed: Unable to start pairing client.", False),
    ("failed to pair to 192.168.1.42:37123", False),
    ("Pairing code is incorrect. Please try again.", False),
])
def test_adb_pair_classifies_text(monkeypatch, text, ok):
    monkeypatch.setattr(devices, "_run_adb",
                        lambda args, timeout=20.0: (True, text))
    got, _detail = devices.adb_pair("192.168.1.42:37123", "123456")
    assert got is ok


def test_adb_pair_missing_adb(monkeypatch):
    monkeypatch.setattr(devices, "_run_adb",
                        lambda args, timeout=20.0: (False, "adb not found on PATH (install Android Platform Tools)"))
    ok, detail = devices.adb_pair("192.168.1.42:37123", "123456")
    assert ok is False
    assert "adb not found" in detail


def test_adb_pair_passes_args(monkeypatch):
    seen = {}

    def fake(args, timeout=20.0):
        seen["args"] = args
        return True, "Successfully paired"

    monkeypatch.setattr(devices, "_run_adb", fake)
    devices.adb_pair("192.168.1.42:37123", "123456")
    assert seen["args"] == ["pair", "192.168.1.42:37123", "123456"]


# ---- android_pair_main flows ----------------------------------------------------

def test_pair_main_success_prints_next_command(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_run_adb",
                        lambda args, timeout=20.0: (True, "Successfully paired to 192.168.1.42:37123"))
    rc = devices.android_pair_main(["192.168.1.42:37123", "123456"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mobile-use android wifi 192.168.1.42:5555 --persist" in out


def test_pair_main_failure_exits_one_with_checklist(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_run_adb",
                        lambda args, timeout=20.0: (True, "Failed: Unable to start pairing client."))
    rc = devices.android_pair_main(["192.168.1.42:37123", "123456"])
    assert rc == 1
    assert "Checklist" in capsys.readouterr().err


def test_pair_main_missing_adb_exits_one_with_hint(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_run_adb",
                        lambda args, timeout=20.0: (False, "adb not found on PATH (install Android Platform Tools)"))
    rc = devices.android_pair_main(["192.168.1.42:37123", "123456"])
    assert rc == 1
    assert "adb not found" in capsys.readouterr().out


def test_pair_main_requires_port(capsys):
    rc = devices.android_pair_main(["192.168.1.42", "123456"])
    assert rc == 2
    assert "NOT 5555" in capsys.readouterr().err


def test_pair_main_usage_errors(capsys):
    assert devices.android_pair_main(["only-one-arg"]) == 2
    assert devices.android_pair_main([]) == 2


def test_pair_main_help_exits_zero(capsys):
    rc = devices.android_pair_main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pairing code" in out.lower()
    assert "Wireless debugging" in out


def test_pair_main_help_explains_where_code_lives(capsys):
    devices.android_pair_main(["-h"])
    out = capsys.readouterr().out
    assert "Developer options" in out


# ---- cli routing ------------------------------------------------------------

def test_cli_routes_android_pair(monkeypatch):
    import mobile_use.cli as cli
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(devices, "android_pair_main", fake_main)
    monkeypatch.setattr(sys, "argv",
                        ["mobile-use", "android", "pair", "192.168.1.42:37123", "123456"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 0
    assert seen["argv"] == ["192.168.1.42:37123", "123456"]
