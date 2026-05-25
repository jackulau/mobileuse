"""Daemon IPC plumbing — AF_UNIX (default) or TCP.

Same protocol as iphone_harness/_ipc.py — JSON-line RPC, one daemon per
ANH_NAME. Separate socket namespace so iOS and Android daemons can coexist.

TCP mode (via ANH_BIND server-side / ANH_CONNECT client-side env vars) lets
a viewer process or remote operator drive Android over the network. See
iphone_harness/_ipc.py for the endpoint URI grammar.
"""
import asyncio
import json
import os
import re
import socket
import sys
from pathlib import Path

ANH_TMP_DIR = os.environ.get("ANH_TMP_DIR")
ANH_RUNTIME_DIR = os.environ.get("ANH_RUNTIME_DIR") or ANH_TMP_DIR
_TMP = Path(ANH_TMP_DIR or "/tmp")
_RUNTIME = Path(ANH_RUNTIME_DIR or "/tmp")
_TMP.mkdir(parents=True, exist_ok=True)
_RUNTIME.mkdir(parents=True, exist_ok=True)
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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


def parse_endpoint(spec):
    """Parse 'unix:/path' or 'tcp://host:port' → ('unix', path) | ('tcp', host, port)."""
    if not isinstance(spec, str) or not spec:
        raise ValueError("empty endpoint spec")
    if spec.startswith("unix:"):
        path = spec[len("unix:"):]
        if path.startswith("//"):
            path = path[2:]
        if not path:
            raise ValueError(f"unix endpoint missing path: {spec!r}")
        return ("unix", path)
    if spec.startswith("tcp://"):
        rest = spec[len("tcp://"):]
        if ":" not in rest:
            raise ValueError(f"tcp endpoint missing port: {spec!r}")
        host, _, port_s = rest.rpartition(":")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        try:
            port = int(port_s)
        except ValueError:
            raise ValueError(f"tcp endpoint port not integer: {spec!r}")
        if not (0 < port < 65536):
            raise ValueError(f"tcp endpoint port out of range: {port}")
        if not host:
            raise ValueError(f"tcp endpoint missing host: {spec!r}")
        return ("tcp", host, port)
    raise ValueError(f"endpoint must start with 'unix:' or 'tcp://': {spec!r}")


def bind_endpoint(name):
    """Server-side endpoint. ANH_BIND overrides; default = unix path under runtime dir."""
    spec = os.environ.get("ANH_BIND")
    if spec:
        return parse_endpoint(spec)
    return ("unix", str(_sock_path(name)))


def connect_endpoint(name):
    """Client-side endpoint. ANH_CONNECT overrides; default = unix path under runtime dir."""
    spec = os.environ.get("ANH_CONNECT")
    if spec:
        return parse_endpoint(spec)
    return ("unix", str(_sock_path(name)))


def sock_addr(name):
    """Human-readable endpoint string for logs/error messages. Honors ANH_BIND."""
    ep = bind_endpoint(name)
    if ep[0] == "unix":
        return ep[1]
    return f"tcp://{ep[1]}:{ep[2]}"


def spawn_kwargs():
    return {"start_new_session": True}


def connect(name, timeout=1.0):
    """Blocking client. Returns (sock, token); token is always None.

    Endpoint comes from ANH_CONNECT (or default AF_UNIX path).
    """
    ep = connect_endpoint(name)
    if ep[0] == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(ep[1])
        return s, None
    _, host, port = ep
    s = socket.create_connection((host, port), timeout=timeout)
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
    """Run the server until cancelled. Endpoint comes from ANH_BIND (or default AF_UNIX)."""
    ep = bind_endpoint(name)
    if ep[0] == "unix":
        path = ep[1]
        if os.path.exists(path):
            os.unlink(path)
        old_umask = os.umask(0o077)
        try:
            server = await asyncio.start_unix_server(handler, path=path)
        finally:
            os.umask(old_umask)
    else:
        _, host, port = ep
        if host not in _LOOPBACK:
            print(
                f"android-harness: WARNING — TCP daemon binding to {host}:{port} "
                f"(non-loopback). RPC is unauthenticated; use an SSH tunnel "
                f"(ssh -L {port}:127.0.0.1:{port} <host>) or restrict at firewall.",
                file=sys.stderr,
            )
        server = await asyncio.start_server(handler, host=host, port=port)
    async with server:
        await asyncio.Event().wait()


def expected_token():
    return None


def cleanup_endpoint(name):
    """Remove the unix socket file. No-op for TCP."""
    ep = bind_endpoint(name)
    if ep[0] != "unix":
        return
    try:
        Path(ep[1]).unlink()
    except FileNotFoundError:
        pass
