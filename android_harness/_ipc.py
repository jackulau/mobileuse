"""Daemon IPC plumbing. AF_UNIX socket on POSIX.

Same protocol as iphone_harness/_ipc.py — AF_UNIX JSON-line RPC, one daemon
per ANH_NAME. Separate socket namespace so iOS and Android daemons can coexist.
"""
import asyncio
import json
import os
import re
import socket
from pathlib import Path

ANH_TMP_DIR = os.environ.get("ANH_TMP_DIR")
ANH_RUNTIME_DIR = os.environ.get("ANH_RUNTIME_DIR") or ANH_TMP_DIR
_TMP = Path(ANH_TMP_DIR or "/tmp")
_RUNTIME = Path(ANH_RUNTIME_DIR or "/tmp")
_TMP.mkdir(parents=True, exist_ok=True)
_RUNTIME.mkdir(parents=True, exist_ok=True)
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _check(name):
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid ANH_NAME {name!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return name


def _runtime_stem(name):
    _check(name)
    return "anh" if ANH_RUNTIME_DIR else f"anh-{name}"


def _tmp_stem(name):
    _check(name)
    return "anh" if ANH_TMP_DIR else f"anh-{name}"


def log_path(name):    return _TMP / f"{_tmp_stem(name)}.log"
def pid_path(name):    return _RUNTIME / f"{_runtime_stem(name)}.pid"
def _sock_path(name):  return _RUNTIME / f"{_runtime_stem(name)}.sock"


def sock_addr(name):
    return str(_sock_path(name))


def spawn_kwargs():
    return {"start_new_session": True}


def connect(name, timeout=1.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(_sock_path(name)))
    return s, None


_MAX_MSG = 64 * 1024 * 1024  # 64 MB cap

def request(c, token, req):
    """Caps incoming data at _MAX_MSG to prevent unbounded memory growth."""
    if token:
        req = {**req, "token": token}
    c.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = c.recv(1 << 16)
        if not chunk:
            break
        data += chunk
        if len(data) > _MAX_MSG:
            raise RuntimeError(
                f"IPC response exceeded {_MAX_MSG // (1024*1024)}MB cap — daemon malfunction?"
            )
    return json.loads(data or b"{}")


def ping(name, timeout=1.0):
    try:
        c, token = connect(name, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return False
    try:
        resp = request(c, token, {"meta": "ping"})
        return isinstance(resp, dict) and resp.get("pong") is True
    except (OSError, ValueError, AttributeError):
        return False
    finally:
        try: c.close()
        except OSError: pass


def identify(name, timeout=1.0):
    try:
        c, token = connect(name, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return None
    try:
        resp = request(c, token, {"meta": "ping"})
        if not isinstance(resp, dict) or resp.get("pong") is not True:
            return None
        pid = resp.get("pid")
        return pid if type(pid) is int and 0 < pid < (1 << 31) else None
    except (OSError, ValueError, AttributeError):
        return None
    finally:
        try: c.close()
        except OSError: pass


async def serve(name, handler):
    path = str(_sock_path(name))
    if os.path.exists(path):
        os.unlink(path)
    old_umask = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(handler, path=path)
    finally:
        os.umask(old_umask)
    async with server:
        await asyncio.Event().wait()


def expected_token():
    return None


def cleanup_endpoint(name):
    p = _sock_path(name)
    try: p.unlink()
    except FileNotFoundError: pass
