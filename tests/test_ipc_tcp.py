"""TCP transport tests for iphone_harness + android_harness IPC.

Same mock daemons as test_ipc.py — the transport switch is env-driven
(IPH_BIND/IPH_CONNECT for iOS, ANH_BIND/ANH_CONNECT for Android), so the
daemon code path is identical between unix and tcp. These tests ensure
the swap is transparent at the protocol layer.
"""
import contextlib
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from android_harness import _ipc as anh_ipc
from iphone_harness import _ipc as iph_ipc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    """Bind a system-assigned port, close, return it. Race-y but fine for tests."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _wait_alive(ipc_mod, name, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ipc_mod.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def _spawn_mock_tcp(platform, name, port):
    """Spawn a mock daemon listening on tcp://127.0.0.1:<port>."""
    if platform == "iphone":
        module = "tests._mock_iphone_daemon"
        env = {**os.environ, "IPH_NAME": name, "IPH_BIND": f"tcp://127.0.0.1:{port}"}
    else:
        module = "tests._mock_android_daemon"
        env = {**os.environ, "ANH_NAME": name, "ANH_BIND": f"tcp://127.0.0.1:{port}"}
    return subprocess.Popen(
        [sys.executable, "-m", module],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )


@contextlib.contextmanager
def _tcp_env(prefix, port):
    """Scope IPH_CONNECT/ANH_CONNECT to this block."""
    saved = {k: os.environ.get(k) for k in (f"{prefix}_CONNECT", f"{prefix}_BIND")}
    os.environ[f"{prefix}_CONNECT"] = f"tcp://127.0.0.1:{port}"
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cleanup_files(platform, name):
    prefix = "iph" if platform == "iphone" else "anh"
    for ext in ("sock", "pid", "log"):
        try:
            (Path("/tmp") / f"{prefix}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


# ---- parse_endpoint -------------------------------------------------------

@pytest.mark.parametrize("mod", [iph_ipc, anh_ipc])
def test_transport_parse_unix(mod):
    assert mod.parse_endpoint("unix:/tmp/foo.sock") == ("unix", "/tmp/foo.sock")
    assert mod.parse_endpoint("unix:///tmp/foo.sock") == ("unix", "/tmp/foo.sock")


@pytest.mark.parametrize("mod", [iph_ipc, anh_ipc])
def test_transport_parse_tcp(mod):
    assert mod.parse_endpoint("tcp://127.0.0.1:7300") == ("tcp", "127.0.0.1", 7300)
    assert mod.parse_endpoint("tcp://localhost:8000") == ("tcp", "localhost", 8000)
    assert mod.parse_endpoint("tcp://[::1]:9000") == ("tcp", "::1", 9000)


@pytest.mark.parametrize("mod", [iph_ipc, anh_ipc])
def test_transport_parse_rejects_malformed(mod):
    for bad in ("", "http://x", "tcp://noport", "tcp://h:abc", "tcp://h:0",
                "tcp://h:99999", "unix:"):
        with pytest.raises(ValueError):
            mod.parse_endpoint(bad)


# ---- sock_addr URI form ---------------------------------------------------

def test_transport_sock_addr_tcp_returns_uri_iphone(monkeypatch):
    monkeypatch.setenv("IPH_BIND", "tcp://127.0.0.1:9876")
    assert iph_ipc.sock_addr("test") == "tcp://127.0.0.1:9876"


def test_transport_sock_addr_tcp_returns_uri_android(monkeypatch):
    monkeypatch.setenv("ANH_BIND", "tcp://127.0.0.1:9877")
    assert anh_ipc.sock_addr("test") == "tcp://127.0.0.1:9877"


def test_transport_sock_addr_unix_unchanged_default(monkeypatch):
    """No IPH_BIND set → unix .sock on POSIX, tcp:// loopback uri on Windows."""
    monkeypatch.delenv("IPH_BIND", raising=False)
    addr = iph_ipc.sock_addr("default")
    if sys.platform == "win32":
        # Windows has no AF_UNIX — the default endpoint is tcp loopback.
        assert addr.startswith("tcp://127.0.0.1:")
    else:
        assert addr.endswith(".sock")
        assert "tcp://" not in addr


# ---- cleanup_endpoint dispatch -------------------------------------------

def test_transport_cleanup_endpoint_noop_for_tcp(monkeypatch, tmp_path):
    """In TCP mode the unix file path doesn't exist; cleanup must not raise."""
    monkeypatch.setenv("IPH_BIND", "tcp://127.0.0.1:7301")
    iph_ipc.cleanup_endpoint("noexist")
    iph_ipc.cleanup_endpoint("noexist")


# ---- bind_endpoint / connect_endpoint resolution -------------------------

def test_transport_default_is_unix_iphone(monkeypatch):
    for k in ("IPH_BIND", "IPH_CONNECT"):
        monkeypatch.delenv(k, raising=False)
    bep = iph_ipc.bind_endpoint("default")
    cep = iph_ipc.connect_endpoint("default")
    if sys.platform == "win32":
        # No AF_UNIX on Windows → deterministic tcp loopback default; bind and
        # connect must resolve the IDENTICAL port (no name-routing cross-wire).
        assert bep[0] == "tcp" and cep[0] == "tcp"
        assert bep == cep
    else:
        assert bep[0] == "unix" and cep[0] == "unix"
        assert bep[1].endswith(".sock")


# ---- Windows default transport (monkeypatched — no real Windows box needed) -
# anh_ipc/iph_ipc + mobile_use._platform are imported at module top under the
# real (darwin/linux) platform, so flipping sys.platform in the body only
# changes the call-time is_windows() check — no fresh import under fake win32
# (which would hit Windows-only _winapi/_overlapped on a POSIX host).

@pytest.mark.parametrize("mod,prefix", [(iph_ipc, "IPH"), (anh_ipc, "ANH")])
def test_windows_default_transport_is_tcp_loopback(mod, prefix, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    for suf in ("_BIND", "_CONNECT"):
        monkeypatch.delenv(prefix + suf, raising=False)
    bep = mod.bind_endpoint("phone-1")
    cep = mod.connect_endpoint("phone-1")
    assert bep[0] == "tcp" and bep[1] == "127.0.0.1"
    # bind and connect MUST resolve the identical port from the name, else two
    # named daemons silently share a connection and corrupt JSON-line framing.
    assert bep == cep
    assert 8400 <= bep[2] <= 8499


@pytest.mark.parametrize("mod,prefix", [(iph_ipc, "IPH"), (anh_ipc, "ANH")])
def test_windows_default_port_per_name_and_idempotent(mod, prefix, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    for suf in ("_BIND", "_CONNECT"):
        monkeypatch.delenv(prefix + suf, raising=False)
    # idempotent: same name → same port across calls (daemon respawn safe)
    assert mod.bind_endpoint("alpha") == mod.bind_endpoint("alpha")
    # distinct names → distinct ports (name-based routing does not cross-wire)
    assert mod.bind_endpoint("alpha")[2] != mod.bind_endpoint("beta")[2]


# ---- spawn_kwargs process detachment -------------------------------------

@pytest.mark.parametrize("mod", [iph_ipc, anh_ipc])
def test_spawn_kwargs_posix_uses_start_new_session(mod, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert mod.spawn_kwargs() == {"start_new_session": True}


@pytest.mark.parametrize("mod", [iph_ipc, anh_ipc])
def test_spawn_kwargs_windows_uses_detached_creationflags(mod, monkeypatch):
    # DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP only exist in subprocess on
    # Windows; inject them so the win32 branch is exercisable on a POSIX host.
    # (Values match the real Win32 constants, so this also holds on Windows.)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    kw = mod.spawn_kwargs()
    assert "start_new_session" not in kw, "start_new_session is a POSIX no-op on Windows"
    assert kw.get("creationflags") == (0x8 | 0x200)


def test_transport_env_override_to_tcp(monkeypatch):
    monkeypatch.setenv("IPH_BIND", "tcp://127.0.0.1:9999")
    monkeypatch.setenv("IPH_CONNECT", "tcp://127.0.0.1:9999")
    assert iph_ipc.bind_endpoint("any") == ("tcp", "127.0.0.1", 9999)
    assert iph_ipc.connect_endpoint("any") == ("tcp", "127.0.0.1", 9999)


# ---- live TCP daemon — iphone --------------------------------------------

def test_ipc_tcp_iphone_roundtrip():
    """Mock daemon binds tcp, client connects tcp, ping + method dispatch."""
    name = f"tst{uuid.uuid4().hex[:10]}"
    port = _free_port()
    p = _spawn_mock_tcp("iphone", name, port)
    try:
        with _tcp_env("IPH", port):
            os.environ["IPH_NAME"] = name
            try:
                assert _wait_alive(iph_ipc, name, timeout=10.0), "daemon never bound TCP"
                assert iph_ipc.ping(name, timeout=1.0) is True
                pid = iph_ipc.identify(name, timeout=1.0)
                assert pid == p.pid
                s, _ = iph_ipc.connect(name, timeout=1.0)
                try:
                    resp = iph_ipc.request(s, None, {"method": "window_size", "params": {}})
                    assert resp["result"]["width"] == 390
                finally:
                    s.close()
            finally:
                os.environ.pop("IPH_NAME", None)
    finally:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2.0)
        _cleanup_files("iphone", name)


def test_ipc_tcp_iphone_no_unix_file_created():
    """TCP-mode daemon must NOT create /tmp/iph-<name>.sock on disk."""
    name = f"tst{uuid.uuid4().hex[:10]}"
    port = _free_port()
    sock_file = Path("/tmp") / f"iph-{name}.sock"
    try: sock_file.unlink()
    except FileNotFoundError: pass

    p = _spawn_mock_tcp("iphone", name, port)
    try:
        with _tcp_env("IPH", port):
            os.environ["IPH_NAME"] = name
            try:
                assert _wait_alive(iph_ipc, name, timeout=10.0)
                assert not sock_file.exists(), f"TCP daemon unexpectedly created {sock_file}"
            finally:
                os.environ.pop("IPH_NAME", None)
    finally:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2.0)
        _cleanup_files("iphone", name)


def test_ipc_tcp_iphone_shutdown_via_ipc():
    name = f"tst{uuid.uuid4().hex[:10]}"
    port = _free_port()
    p = _spawn_mock_tcp("iphone", name, port)
    try:
        with _tcp_env("IPH", port):
            os.environ["IPH_NAME"] = name
            try:
                assert _wait_alive(iph_ipc, name, timeout=10.0)
                s, _ = iph_ipc.connect(name, timeout=1.0)
                resp = iph_ipc.request(s, None, {"meta": "shutdown"})
                s.close()
                assert resp == {"ok": True}
                try:
                    p.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    pytest.fail("TCP daemon didn't exit after shutdown")
                assert iph_ipc.ping(name, timeout=0.5) is False
            finally:
                os.environ.pop("IPH_NAME", None)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2.0)
        _cleanup_files("iphone", name)


# ---- live TCP daemon — android -------------------------------------------

def test_ipc_tcp_android_roundtrip():
    name = f"tst{uuid.uuid4().hex[:10]}"
    port = _free_port()
    p = _spawn_mock_tcp("android", name, port)
    try:
        with _tcp_env("ANH", port):
            os.environ["ANH_NAME"] = name
            try:
                assert _wait_alive(anh_ipc, name, timeout=10.0)
                s, _ = anh_ipc.connect(name, timeout=1.0)
                try:
                    resp = anh_ipc.request(s, None, {"method": "window_size", "params": {}})
                    assert resp["result"]["width"] == 1080
                finally:
                    s.close()
            finally:
                os.environ.pop("ANH_NAME", None)
    finally:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2.0)
        _cleanup_files("android", name)


# ---- server bind warning when non-loopback -------------------------------

def test_transport_serve_warns_on_non_loopback(monkeypatch, capfd):
    """Binding to 0.0.0.0 (non-loopback) must emit a security warning."""
    import asyncio

    port = _free_port()
    monkeypatch.setenv("IPH_BIND", f"tcp://0.0.0.0:{port}")

    async def _spin():
        async def _handler(reader, writer):
            writer.close()
        task = asyncio.create_task(iph_ipc.serve("warntest", _handler))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    try:
        asyncio.run(_spin())
    except Exception:
        pass

    out, err = capfd.readouterr()
    combined = out + err
    assert "non-loopback" in combined or "WARNING" in combined.upper()
