"""A2 — `mobile-use ios wifi` first-class command.

Device-free: drive ios_wifi_main directly with a stubbed ios_wifi_target and a
tmp .env, asserting output, exit codes, and persistence.
"""
import mobile_use.devices as devices


def _stub_target(monkeypatch, *, reachable=True, source="mdns",
                 url="http://iPhone.local:8100", result="set"):
    def fake(udid=None, host=None, port=8100, probe=True, timeout=2.0):
        if result is None:
            return None
        return {"url": url, "host": "iPhone.local", "port": port,
                "source": source, "reachable": reachable, "candidates": []}
    monkeypatch.setattr(devices, "ios_wifi_target", fake)


def test_help_exits_zero_and_mentions_env(capsys):
    rc = devices.ios_wifi_main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "IPH_WDA_URL" in out
    assert "mDNS" in out


def test_reachable_prints_url_and_exits_zero(monkeypatch, capsys):
    _stub_target(monkeypatch, reachable=True)
    rc = devices.ios_wifi_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "IPH_WDA_URL=http://iPhone.local:8100" in out
    assert "reachable" in out
    assert "tunnel" in out  # iOS17+ hint present


def test_check_exits_one_when_unreachable(monkeypatch, capsys):
    _stub_target(monkeypatch, reachable=False)
    rc = devices.ios_wifi_main(["--check"])
    assert rc == 1
    assert "NOT reachable" in capsys.readouterr().out


def test_check_exits_zero_when_reachable(monkeypatch):
    _stub_target(monkeypatch, reachable=True)
    assert devices.ios_wifi_main(["--check"]) == 0


def test_no_device_no_host_exits_one(monkeypatch, capsys):
    _stub_target(monkeypatch, result=None)
    rc = devices.ios_wifi_main([])
    assert rc == 1
    assert "No iPhone found" in capsys.readouterr().err


def test_persist_writes_env(monkeypatch, tmp_path, capsys):
    _stub_target(monkeypatch, reachable=True)
    env = tmp_path / ".env"
    env.write_text("IPH_UDID=abc\nFOO=bar\n", encoding="utf-8")
    monkeypatch.setattr(devices, "_env_path", lambda: env)
    rc = devices.ios_wifi_main(["--persist"])
    assert rc == 0
    body = env.read_text()
    assert "IPH_WDA_URL=http://iPhone.local:8100" in body
    assert "IPH_UDID=abc" in body  # preserved
    assert "FOO=bar" in body       # preserved


def test_persist_replaces_existing_key(monkeypatch, tmp_path):
    _stub_target(monkeypatch, reachable=True, url="http://iPhone.local:8100")
    env = tmp_path / ".env"
    env.write_text("IPH_WDA_URL=http://old:8100\nKEEP=1\n", encoding="utf-8")
    monkeypatch.setattr(devices, "_env_path", lambda: env)
    devices.ios_wifi_main(["--persist"])
    body = env.read_text()
    assert "http://old:8100" not in body
    assert body.count("IPH_WDA_URL=") == 1
    assert "KEEP=1" in body


def test_invalid_port_exits_two(capsys):
    rc = devices.ios_wifi_main(["--port", "notanint"])
    assert rc == 2


def test_no_probe_exits_zero_even_if_unprobed(monkeypatch):
    def fake(udid=None, host=None, port=8100, probe=True, timeout=2.0):
        return {"url": "http://iPhone.local:8100", "host": "iPhone.local",
                "port": port, "source": "mdns", "reachable": None, "candidates": []}
    monkeypatch.setattr(devices, "ios_wifi_target", fake)
    assert devices.ios_wifi_main(["--no-probe"]) == 0
