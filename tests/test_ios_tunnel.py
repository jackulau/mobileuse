"""A3 — `mobile-use ios tunnel`: RemoteXPC tunnel status + start command.

Device-free: stub pymobiledevice3 availability + the tunneld probe.
"""
import mobile_use.devices as devices
import mobile_use.netcheck as netcheck


def test_help_exits_zero_and_mentions_tunneld_and_sudo(capsys):
    rc = devices.ios_tunnel_main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tunneld" in out
    assert "sudo" in out


def test_not_installed_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: False)
    rc = devices.ios_tunnel_main([])
    assert rc == 1
    assert "not installed" in capsys.readouterr().err


def test_tunnel_up_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status",
                        lambda *a, **k: (True, "tunneld reachable at 127.0.0.1:49151", {"UDID": []}))
    rc = devices.ios_tunnel_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "UP" in out
    assert "1 device tunnel" in out  # singular, 1 udid


def test_tunnel_down_prints_start_command_and_exits_one(monkeypatch, capsys):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status",
                        lambda *a, **k: (False, "127.0.0.1:49151 not reachable", None))
    rc = devices.ios_tunnel_main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "DOWN" in out
    assert "remote tunneld" in out
    assert "sudo" in out


def test_check_up_exits_zero(monkeypatch):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status", lambda *a, **k: (True, "ok", None))
    assert devices.ios_tunnel_main(["--check"]) == 0


def test_check_down_exits_one(monkeypatch):
    monkeypatch.setattr(devices, "_pymobiledevice3_available", lambda: True)
    monkeypatch.setattr(devices, "tunneld_status", lambda *a, **k: (False, "down", None))
    assert devices.ios_tunnel_main(["--check"]) == 1


def test_tunneld_status_down_when_port_closed(monkeypatch):
    monkeypatch.setattr(netcheck, "tcp_reachable", lambda *a, **k: (False, "closed"))
    up, detail, tunnels = devices.tunneld_status()
    assert up is False
    assert tunnels is None


def test_start_cmd_uses_module_form_when_no_console_script(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda cmd: None)
    assert devices._tunneld_start_cmd() == "sudo python3 -m pymobiledevice3 remote tunneld"


def test_start_cmd_uses_console_script_when_on_path(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda cmd: "/usr/local/bin/pymobiledevice3")
    assert devices._tunneld_start_cmd() == "sudo pymobiledevice3 remote tunneld"
