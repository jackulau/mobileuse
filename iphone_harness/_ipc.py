"""Daemon IPC plumbing — AF_UNIX (default) or TCP, part of mobile-use.

Agent drives a physical iPhone from a Mac. AF_UNIX keeps the path short and
avoids TCP complexity for same-host operation.

TCP mode (IPH_BIND for server-side, IPH_CONNECT for client-side) lets a
Windows or Linux host drive a remote Mac that's running Appium+WDA, and lets
a viewer process pull MJPEG frames over the network. Endpoint URIs:
    unix:/tmp/iph-default.sock   (default; identical to no env)
    tcp://127.0.0.1:8763         (loopback — recommended; pair with `ssh -L`)
    tcp://0.0.0.0:8763           (any iface — prints a security warning;
                                  the RPC is unauthenticated)
"""
import asyncio
import json
import os
import re
import socket
import sys
from pathlib import Path

from mobile_use._platform import daemon_tcp_port, default_runtime_base, is_windows

# AF_UNIX sun_path on macOS is 104 bytes. /tmp keeps the path short; macOS's
# tempfile.gettempdir() returns /var/folders/... which is too long for AF_UNIX.
# default_runtime_base() returns '/tmp' on POSIX (preserving this) and a
# Windows-writable dir on win32 (where AF_UNIX doesn't apply — TCP loopback).
IPH_TMP_DIR = os.environ.get("IPH_TMP_DIR")
IPH_RUNTIME_DIR = os.environ.get("IPH_RUNTIME_DIR") or IPH_TMP_DIR
_TMP = Path(IPH_TMP_DIR or default_runtime_base())
_RUNTIME = Path(IPH_RUNTIME_DIR or default_runtime_base())
_TMP.mkdir(parents=True, exist_ok=True)
_RUNTIME.mkdir(parents=True, exist_ok=True)
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _check(name):
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid IPH_NAME {name!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return name


def _runtime_stem(name):
    _check(name)
    return "iph" if IPH_RUNTIME_DIR else f"iph-{name}"


def _tmp_stem(name):
    _check(name)
    return "iph" if IPH_TMP_DIR else f"iph-{name}"


def log_path(name):    return _TMP / f"{_tmp_stem(name)}.log"
def pid_path(name):    return _RUNTIME / f"{_runtime_stem(name)}.pid"
def _sock_path(name):  return _RUNTIME / f"{_runtime_stem(name)}.sock"


def parse_endpoint(spec):
    """Parse 'unix:/path' or 'tcp://host:port' → ('unix', path) | ('tcp', host, port).

    Raises ValueError on malformed input.
    """
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


def _default_endpoint(name):
    """Transport used when no IPH_BIND/IPH_CONNECT override is set.

    POSIX → AF_UNIX socket file (short path under the runtime dir). Windows →
    TCP loopback on a deterministic per-name port: AF_UNIX is unavailable on
    Windows CPython (no socket.AF_UNIX, no asyncio.start_unix_server), so the
    daemon binds TCP and the client connects TCP. Both sides compute the SAME
    port from the name (mobile_use._platform.daemon_tcp_port) so routing-by-name
    survives with zero shared state."""
    _check(name)
    if is_windows():
        return ("tcp", "127.0.0.1", daemon_tcp_port(name))
    return ("unix", str(_sock_path(name)))


def bind_endpoint(name):
    """Server-side endpoint. IPH_BIND overrides; default = unix on POSIX / tcp loopback on Windows."""
    spec = os.environ.get("IPH_BIND")
    if spec:
        return parse_endpoint(spec)
    return _default_endpoint(name)


def connect_endpoint(name):
    """Client-side endpoint. IPH_CONNECT overrides; default = unix on POSIX / tcp loopback on Windows."""
    spec = os.environ.get("IPH_CONNECT")
    if spec:
        return parse_endpoint(spec)
    return _default_endpoint(name)


def sock_addr(name):
    """Human-readable endpoint string for logs/error messages. Honors IPH_BIND."""
    ep = bind_endpoint(name)
    if ep[0] == "unix":
        return ep[1]
    return f"tcp://{ep[1]}:{ep[2]}"


def spawn_kwargs():
    """Popen kwargs that detach the daemon from the launching process.

    POSIX → start_new_session=True (setsid: new session so the daemon outlives
    the terminal that spawned it). Windows → DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
    (start_new_session is a POSIX-only setsid and does NOT detach on Windows).
    The Windows creationflags constants only exist in the subprocess module on
    Windows, so they are referenced solely inside the is_windows() branch."""
    if is_windows():
        import subprocess
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def token_path(name):
    """Per-name auto-token file (0600, runtime dir). TCP transports only."""
    return _RUNTIME / f"{_runtime_stem(name)}.token"


def _load_or_create_token(name):
    p = token_path(name)
    try:
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except (FileNotFoundError, OSError):
        pass
    import secrets
    tok = secrets.token_hex(16)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


def expected_token(name=None):
    """Token every TCP request must carry, or None for tokenless transports.

    IPH_TOKEN env wins. Otherwise: TCP transports get a per-name secret
    auto-generated + persisted 0600 in the runtime dir (the same-host client
    auto-loads it, so local Windows works out of the box while every TCP
    request still authenticates). AF_UNIX stays tokenless — socket file
    permissions remain the boundary."""
    env = os.environ.get("IPH_TOKEN")
    if env:
        return env
    name = name or os.environ.get("IPH_NAME", "default")
    if bind_endpoint(name)[0] != "tcp":
        return None
    return _load_or_create_token(name)


def client_token(name):
    """Token to attach to outgoing requests, or None.

    IPH_TOKEN env wins (remote clients set it by hand). For TCP transports the
    daemon's persisted token file is auto-loaded — same host, same user, zero
    config. AF_UNIX sends no token."""
    env = os.environ.get("IPH_TOKEN")
    if env:
        return env
    if connect_endpoint(name)[0] != "tcp":
        return None
    try:
        tok = token_path(name).read_text(encoding="utf-8").strip()
        return tok or None
    except (FileNotFoundError, OSError):
        return None


def authorize(req, name):
    """None when this request may proceed; an error dict to send back when it
    must be rejected. Tokenless transports (AF_UNIX) always authorize."""
    want = expected_token(name)
    if want is None or req.get("token") == want:
        return None
    return {
        "error": ("auth required: this daemon speaks TCP and every request "
                  "must carry its token. Set IPH_TOKEN to the daemon's token "
                  f"(same host: contents of {token_path(name)}) and retry."),
        "auth": False,
    }


def connect(name, timeout=1.0):
    """Blocking client. Returns (sock, token); token is None on AF_UNIX and
    the auto-loaded/env token on TCP (request() attaches it per call).

    Endpoint comes from IPH_CONNECT (or default AF_UNIX path).
    """
    ep = connect_endpoint(name)
    if ep[0] == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(ep[1])
        return s, None
    _, host, port = ep
    s = socket.create_connection((host, port), timeout=timeout)
    return s, client_token(name)


_MAX_MSG = 64 * 1024 * 1024  # 64 MB cap — covers any iPhone screenshot, screen video

def request(c, token, req):
    """One-shot send + recv + parse on an open socket. Caller closes the socket.

    Caps incoming data at _MAX_MSG to prevent unbounded memory growth from a
    malfunctioning or compromised daemon.
    """
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
    if not data.endswith(b"\n"):
        # Peer closed before delivering a complete newline-framed response —
        # an empty buffer or a truncated write (flaky link / daemon died mid-reply).
        # Raise a reconnectable ConnectionError (an OSError subclass) so _send drops
        # the socket and retries, instead of leaking a raw JSONDecodeError to the
        # agent or silently parsing an empty buffer as {}.
        raise ConnectionError("IPC connection closed mid-frame")
    return json.loads(data)


def ping(name, timeout=1.0):
    """True iff a live daemon answers our ping. A stale .sock or unrelated
    listener won't reply with the right shape — never trust a bare connect."""
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
    """Return the live daemon's PID, or None if unreachable."""
    try:
        c, token = connect(name, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return None
    try:
        resp = request(c, token, {"meta": "ping"})
        if not isinstance(resp, dict) or resp.get("pong") is not True:
            return None
        pid = resp.get("pid")
        # Reject bool (isinstance(True, int) is True), 0, negatives, and absurd values.
        return pid if type(pid) is int and 0 < pid < (1 << 31) else None
    except (OSError, ValueError, AttributeError):
        return None
    finally:
        try: c.close()
        except OSError: pass


async def serve(name, handler):
    """Run the server until cancelled. Endpoint comes from IPH_BIND (or default AF_UNIX)."""
    ep = bind_endpoint(name)
    if ep[0] == "unix":
        path = ep[1]
        if os.path.exists(path):
            os.unlink(path)
        # umask 0o077 makes bind() create the socket as 0600 — no TOCTOU window before chmod.
        old_umask = os.umask(0o077)
        try:
            # limit=_MAX_MSG raises the StreamReader buffer above asyncio's 64KB
            # default so a legitimate >64KB request line (e.g. set_value/paste_text
            # with a long body) is read instead of breaking the connection.
            server = await asyncio.start_unix_server(handler, path=path, limit=_MAX_MSG)
        finally:
            os.umask(old_umask)
    else:
        _, host, port = ep
        expected_token(name)  # ensure the per-name token exists before clients race it
        if host not in _LOOPBACK:
            print(
                f"iphone-harness: WARNING — TCP daemon binding to {host}:{port} "
                f"(non-loopback). Every request must carry the daemon token "
                f"(remote clients: set IPH_TOKEN to the contents of "
                f"{token_path(name)}). Still prefer an SSH tunnel "
                f"(ssh -L {port}:127.0.0.1:{port} <mac>) or restrict at firewall.",
                file=sys.stderr,
            )
        server = await asyncio.start_server(handler, host=host, port=port, limit=_MAX_MSG)
    async with server:
        await asyncio.Event().wait()


def cleanup_endpoint(name):
    """Remove the unix socket file. No-op for TCP."""
    ep = bind_endpoint(name)
    if ep[0] != "unix":
        return
    try:
        Path(ep[1]).unlink()
    except FileNotFoundError:
        pass
