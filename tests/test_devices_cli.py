"""Tests for `mobile-use devices` CLI + mobile_use/devices.py discovery."""
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from mobile_use import devices


# ---- discover_connected --------------------------------------------------

def test_discover_empty_when_no_tools(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda cmd: None)
    assert devices.discover_connected() == []


def test_discover_ios_only(monkeypatch):
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd in ("idevice_id", "idevicename") else None

    def fake_check_output(cmd, **_):
        if cmd[0] == "idevice_id":
            return b"AAAA-1\nBBBB-2\n"
        if cmd[0] == "idevicename":
            udid = cmd[-1]
            return (f"iPhone {udid[-1]}\n").encode()
        return b""

    monkeypatch.setattr(devices, "_which", fake_which)
    monkeypatch.setattr(devices.subprocess, "check_output", fake_check_output)

    out = devices.discover_connected()
    assert len(out) == 2
    assert {d["platform"] for d in out} == {"ios"}
    assert {d["udid"] for d in out} == {"AAAA-1", "BBBB-2"}
    assert all(d["name"] for d in out)


def test_discover_android_only(monkeypatch):
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd == "adb" else None

    def fake_check_output(cmd, **_):
        return (
            b"List of devices attached\n"
            b"SERIAL1\tdevice product:foo model:Pixel_7 device:panther transport_id:1\n"
            b"SERIAL2\tdevice product:bar model:Galaxy_S23 device:dm1q transport_id:2\n"
        )

    monkeypatch.setattr(devices, "_which", fake_which)
    monkeypatch.setattr(devices.subprocess, "check_output", fake_check_output)

    out = devices.discover_connected()
    assert len(out) == 2
    assert {d["platform"] for d in out} == {"android"}
    names = {d["name"] for d in out}
    assert "Pixel-7" in names
    assert "Galaxy-S23" in names


def test_discover_mixed_with_collision(monkeypatch):
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd in ("idevice_id", "idevicename", "adb") else None

    def fake_check_output(cmd, **_):
        if cmd[0] == "idevice_id":
            return b"A\nB\n"
        if cmd[0] == "idevicename":
            return b"iPhone 13\n"
        if cmd[0] == "adb":
            return (
                b"List of devices attached\n"
                b"S1\tdevice model:Pixel_7\n"
            )
        return b""

    monkeypatch.setattr(devices, "_which", fake_which)
    monkeypatch.setattr(devices.subprocess, "check_output", fake_check_output)

    out = devices.discover_connected()
    assert len(out) == 3
    ios_names = sorted(d["name"] for d in out if d["platform"] == "ios")
    assert ios_names == ["iPhone-13", "iPhone-13-2"]


def test_discover_ignores_unauthorized_android(monkeypatch):
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd == "adb" else None

    def fake_check_output(cmd, **_):
        return (
            b"List of devices attached\n"
            b"GOOD1\tdevice model:Pixel_7\n"
            b"BAD1\tunauthorized\n"
            b"BAD2\toffline\n"
        )

    monkeypatch.setattr(devices, "_which", fake_which)
    monkeypatch.setattr(devices.subprocess, "check_output", fake_check_output)

    out = devices.discover_connected()
    assert len(out) == 1
    assert out[0]["udid"] == "GOOD1"


def test_discovery_hints_when_tools_missing(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda cmd: None)
    hints = devices.discovery_hints()
    assert any("idevice" in h.lower() or "ios" in h.lower() for h in hints)
    assert any("adb" in h.lower() or "android" in h.lower() for h in hints)


# ---- list_running_daemons ------------------------------------------------

def test_list_running_daemons_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert devices.list_running_daemons() == []


def test_list_running_daemons_parses_sockets(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    (tmp_path / "iph-iphone1.sock").touch()
    (tmp_path / "anh-pixel.sock").touch()
    (tmp_path / "iph.sock").touch()
    (tmp_path / "unrelated.sock").touch()

    monkeypatch.setattr(devices, "_probe_daemon", lambda *a: False)
    out = devices.list_running_daemons()
    names = sorted((d["platform"], d["name"] or "") for d in out)
    assert names == [("android", "pixel"), ("ios", ""), ("ios", "iphone1")]


# ---- CLI ------------------------------------------------------------------

def _run_cli(*args):
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "devices", *args],
        capture_output=True, text=True, timeout=15,
    )
    return result


def test_cli_devices_help_lists_subcommands():
    r = _run_cli("--help")
    assert r.returncode == 0
    assert "list" in r.stdout.lower()
    assert "status" in r.stdout.lower()
    assert "reload" in r.stdout.lower()


def test_cli_devices_list_json_parseable():
    r = _run_cli("list", "--json")
    assert r.returncode == 0
    parsed = json.loads(r.stdout)
    assert isinstance(parsed, list)


def test_cli_devices_status_json_parseable():
    r = _run_cli("status", "--json")
    assert r.returncode == 0
    parsed = json.loads(r.stdout)
    assert isinstance(parsed, list)


def test_cli_devices_reload_requires_target():
    r = _run_cli("reload")
    assert r.returncode == 2
    assert "all" in r.stderr.lower() or "name" in r.stderr.lower()


def test_cli_devices_reload_unknown_name():
    r = _run_cli("reload", "nonexistent-name-xyz")
    assert r.returncode == 1
    assert "no daemon" in r.stderr.lower()


def test_cli_devices_reload_rejects_bad_name():
    r = _run_cli("reload", "bad name!")
    assert r.returncode == 2
    assert "invalid" in r.stderr.lower()


def test_cli_devices_unknown_subcommand():
    r = _run_cli("nope")
    assert r.returncode == 2


def test_cli_top_level_help_mentions_devices():
    r = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "devices" in r.stdout.lower()
