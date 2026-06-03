"""D4 — TCP reachability probe (mobile_use/netcheck.py).

Device-free: parsing is pure; reachability is exercised against a real local
listening socket (reachable) and a just-closed port (refused).
"""
import socket

import pytest

from mobile_use import netcheck as N


@pytest.mark.parametrize("target, default, expected", [
    ("http://192.168.1.5:8100/wd/hub", None, ("192.168.1.5", 8100)),
    ("https://host:8100", None, ("host", 8100)),
    ("192.168.1.5:5555", None, ("192.168.1.5", 5555)),
    ("192.168.1.5", 5555, ("192.168.1.5", 5555)),
    ("my-iphone.local:8100", None, ("my-iphone.local", 8100)),
])
def test_parse_host_port_ok(target, default, expected):
    assert N.parse_host_port(target, default_port=default) == expected


@pytest.mark.parametrize("target, default", [
    ("", None),
    (None, None),
    ("192.168.1.5", None),       # no port, no default
    ("192.168.1.5:notaport", None),
])
def test_parse_host_port_raises(target, default):
    with pytest.raises(ValueError):
        N.parse_host_port(target, default_port=default)


def test_tcp_reachable_true_against_local_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    try:
        ok, detail = N.tcp_reachable(host, port, timeout=1.0)
        assert ok is True
        assert "reachable" in detail
    finally:
        srv.close()


def test_tcp_reachable_false_against_closed_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    host, port = srv.getsockname()
    srv.close()  # nothing listening now
    ok, detail = N.tcp_reachable(host, port, timeout=0.5)
    assert ok is False
    assert "not reachable" in detail


def test_target_reachable_parse_failure_is_soft():
    ok, detail = N.target_reachable("", default_port=None)
    assert ok is False
    assert "invalid target" in detail


def test_target_reachable_end_to_end():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _host, port = srv.getsockname()
    try:
        ok, _ = N.target_reachable(f"http://127.0.0.1:{port}/wd/hub", timeout=1.0)
        assert ok is True
    finally:
        srv.close()


@pytest.mark.parametrize("serial, expected", [
    ("192.168.1.5:5555", True),
    ("10.0.0.7:5555", True),
    ("39121FDJG0012E", False),   # USB serial
    ("", False),
    (None, False),
    ("emulator-5554", False),    # has a dash, no numeric port after colon
])
def test_looks_like_wifi_serial(serial, expected):
    assert N.looks_like_wifi_serial(serial) is expected


# --- doctor wiring (the preflight only appears when a Wi-Fi target is set) ---

import io  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402


def _closed_local_endpoint():
    """A 127.0.0.1:port with nothing listening (fast connection-refused)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    _host, port = srv.getsockname()
    srv.close()
    return port


def _doctor_out(mod):
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            mod.run_doctor()
        except SystemExit:
            pass
    return buf.getvalue()


def test_ios_doctor_adds_reachability_check_when_wda_url_set(monkeypatch):
    from iphone_harness import admin
    port = _closed_local_endpoint()
    monkeypatch.setenv("IPH_WDA_URL", f"http://127.0.0.1:{port}")
    out = _doctor_out(admin)
    assert "WebDriverAgent reachable over Wi-Fi" in out
    assert "not reachable" in out  # closed port -> the check fails (as it should)


def test_ios_doctor_no_reachability_check_without_wda_url(monkeypatch):
    from iphone_harness import admin
    monkeypatch.delenv("IPH_WDA_URL", raising=False)
    out = _doctor_out(admin)
    assert "WebDriverAgent reachable over Wi-Fi" not in out


def test_android_doctor_reachability_only_for_wifi_serial(monkeypatch):
    from android_harness import admin
    port = _closed_local_endpoint()
    monkeypatch.setenv("ANH_UDID", f"127.0.0.1:{port}")
    out = _doctor_out(admin)
    assert "Device reachable over Wi-Fi" in out
    # A USB-style serial must NOT trigger the network preflight.
    monkeypatch.setenv("ANH_UDID", "39121FDJG0012E")
    out2 = _doctor_out(admin)
    assert "Device reachable over Wi-Fi" not in out2
