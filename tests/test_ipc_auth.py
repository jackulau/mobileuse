"""IPC token auth — TCP requests must authenticate; AF_UNIX stays tokenless.

Unit layer: expected_token / client_token / authorize on both twins.
Integration layer: a real mock daemon over TCP — auto-token round-trips for
the same-host client, while a hand-rolled tokenless request is rejected.
"""
import contextlib
import json
import os
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from android_harness import _ipc as anh_ipc
from iphone_harness import _ipc as iph_ipc

REPO_ROOT = Path(__file__).resolve().parents[1]
TWINS = [(iph_ipc, "IPH"), (anh_ipc, "ANH")]


def _name():
    return f"auth{uuid.uuid4().hex[:10]}"


def _clean(prefix, name):
    for ext in ("sock", "pid", "log", "token"):
        try:
            (Path("/tmp") / f"{prefix.lower()}-{name}.{ext}").unlink()
        except FileNotFoundError:
            pass


# ---- unit: expected_token / client_token / authorize ---------------------------

@pytest.mark.parametrize("ipc,prefix", TWINS)
def test_unix_transport_stays_tokenless(ipc, prefix, monkeypatch):
    monkeypatch.delenv(f"{prefix}_BIND", raising=False)
    monkeypatch.delenv(f"{prefix}_CONNECT", raising=False)
    monkeypatch.delenv(f"{prefix}_TOKEN", raising=False)
    name = _name()
    assert ipc.expected_token(name) is None
    assert ipc.client_token(name) is None
    assert ipc.authorize({"meta": "ping"}, name) is None


@pytest.mark.parametrize("ipc,prefix", TWINS)
def test_tcp_tokenless_request_rejected_tokened_accepted(ipc, prefix, monkeypatch):
    name = _name()
    monkeypatch.setenv(f"{prefix}_BIND", "tcp://127.0.0.1:18999")
    monkeypatch.delenv(f"{prefix}_TOKEN", raising=False)
    try:
        deny = ipc.authorize({"meta": "ping"}, name)
        assert deny is not None
        assert deny.get("auth") is False
        assert "auth required" in deny["error"]
        tok = ipc.expected_token(name)
        assert tok
        assert ipc.authorize({"meta": "ping", "token": tok}, name) is None
        assert ipc.authorize({"meta": "ping", "token": "wrong"}, name) is not None
    finally:
        _clean(prefix, name)


@pytest.mark.parametrize("ipc,prefix", TWINS)
def test_env_token_overrides_file_token(ipc, prefix, monkeypatch):
    name = _name()
    monkeypatch.setenv(f"{prefix}_BIND", "tcp://127.0.0.1:18999")
    monkeypatch.setenv(f"{prefix}_TOKEN", "sekret-from-env")
    try:
        assert ipc.expected_token(name) == "sekret-from-env"
        assert ipc.client_token(name) == "sekret-from-env"
        assert ipc.authorize({"token": "sekret-from-env"}, name) is None
        assert ipc.authorize({"token": "other"}, name) is not None
        # env token short-circuits — no file is created
        assert not ipc.token_path(name).exists()
    finally:
        _clean(prefix, name)


@pytest.mark.parametrize("ipc,prefix", TWINS)
def test_auto_token_round_trips_and_is_0600(ipc, prefix, monkeypatch):
    name = _name()
    monkeypatch.setenv(f"{prefix}_BIND", "tcp://127.0.0.1:18999")
    monkeypatch.setenv(f"{prefix}_CONNECT", "tcp://127.0.0.1:18999")
    monkeypatch.delenv(f"{prefix}_TOKEN", raising=False)
    try:
        tok = ipc.expected_token(name)          # daemon side: creates the file
        assert ipc.client_token(name) == tok    # client side: auto-loads it
        mode = stat.S_IMODE(ipc.token_path(name).stat().st_mode)
        assert mode == 0o600, f"token file mode {oct(mode)}"
        # Stable across calls (no regeneration churn).
        assert ipc.expected_token(name) == tok
    finally:
        _clean(prefix, name)


@pytest.mark.parametrize("ipc,prefix", TWINS)
def test_unix_client_sends_no_token(ipc, prefix, monkeypatch):
    monkeypatch.delenv(f"{prefix}_CONNECT", raising=False)
    monkeypatch.delenv(f"{prefix}_TOKEN", raising=False)
    assert ipc.client_token(_name()) is None


# ---- integration: mock daemon over TCP ------------------------------------------

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@contextlib.contextmanager
def _mock_tcp_daemon(port, name):
    env = {**os.environ, "IPH_NAME": name, "IPH_BIND": f"tcp://127.0.0.1:{port}"}
    env.pop("IPH_TOKEN", None)
    p = subprocess.Popen(
        [sys.executable, "-m", "tests._mock_iphone_daemon"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(REPO_ROOT), start_new_session=True,
    )
    saved = {k: os.environ.get(k) for k in ("IPH_CONNECT", "IPH_TOKEN")}
    os.environ["IPH_CONNECT"] = f"tcp://127.0.0.1:{port}"
    os.environ.pop("IPH_TOKEN", None)
    try:
        yield p
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            p.terminate()
            p.wait(timeout=3.0)
        except Exception:
            p.kill()
        _clean("IPH", name)


def _wait_alive(name, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if iph_ipc.ping(name, timeout=0.3):
            return True
        time.sleep(0.05)
    return False


def test_tcp_mock_daemon_auto_token_ping_and_tokenless_reject():
    port = _free_port()
    name = _name()
    with _mock_tcp_daemon(port, name):
        # Auto-token path: ipc.connect attaches the file token — ping succeeds.
        assert _wait_alive(name), "mock TCP daemon never answered an authed ping"

        # Hand-rolled tokenless request on a raw socket must be rejected.
        raw = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        try:
            raw.sendall((json.dumps({"meta": "ping"}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = raw.recv(65536)
                if not chunk:
                    break
                data += chunk
            resp = json.loads(data)
        finally:
            raw.close()
        assert resp.get("pong") is not True
        assert resp.get("auth") is False
        assert "auth required" in resp.get("error", "")

        # Wrong token also rejected.
        raw = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        try:
            raw.sendall((json.dumps({"meta": "ping", "token": "nope"}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = raw.recv(65536)
                if not chunk:
                    break
                data += chunk
            resp = json.loads(data)
        finally:
            raw.close()
        assert resp.get("auth") is False
