"""TCP reachability probe for wireless device endpoints.

Used by doctor as a preflight: when a Wi-Fi target is configured — an iOS
``appium:webDriverAgentUrl`` on the iPhone's Wi-Fi IP:8100, or an Android
``adb`` ``ip:5555`` serial — confirm the host:port actually accepts a TCP
connection before Appium fails the session create with a slow, opaque timeout.

Pure stdlib sockets: no device, no Appium, no third-party deps.
"""
import socket
from urllib.parse import urlparse


def parse_host_port(target, default_port=None):
    """Parse a target string into ``(host, port)``.

    Accepts the three shapes the harness actually configures::

        "http://192.168.1.5:8100/wd/hub" -> ("192.168.1.5", 8100)
        "192.168.1.5:5555"               -> ("192.168.1.5", 5555)
        "192.168.1.5"  (+ default_port)  -> ("192.168.1.5", default_port)

    Raises ValueError when there is no host, or no port and no ``default_port``.
    """
    if not target:
        raise ValueError("empty target")
    s = str(target).strip()
    host = None
    port = None
    if "://" in s:
        p = urlparse(s)
        host = p.hostname
        port = p.port  # None when the URL has no explicit port
    elif ":" in s:
        # bare "host:port" — rpartition so we keep the last colon as the split.
        host, _, ps = s.rpartition(":")
        try:
            port = int(ps)
        except ValueError as e:
            raise ValueError(f"invalid port in {target!r}") from e
    else:
        host = s
    if not host:
        raise ValueError(f"no host in {target!r}")
    if port is None:
        port = default_port
    if port is None:
        raise ValueError(f"no port in {target!r} and no default_port given")
    return host, int(port)


def tcp_reachable(host, port, timeout=2.0):
    """Return ``(ok, detail)``. ok is True iff a TCP connect to host:port succeeds."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, f"{host}:{port} reachable"
    except (OSError, ValueError, OverflowError) as e:
        return False, f"{host}:{port} not reachable ({e.__class__.__name__}: {e})"


def target_reachable(target, default_port=None, timeout=2.0):
    """``parse_host_port`` then ``tcp_reachable``, in one call. Returns ``(ok, detail)``.

    A parse failure returns ``(False, detail)`` rather than raising, so doctor
    can call this on user-supplied config without a try/except at the call site.
    """
    try:
        host, port = parse_host_port(target, default_port=default_port)
    except ValueError as e:
        return False, f"invalid target {target!r}: {e}"
    return tcp_reachable(host, port, timeout=timeout)


def looks_like_wifi_serial(serial):
    """True if an Android serial looks like a Wi-Fi endpoint (``host:port``).

    USB serials are opaque ids (e.g. "39121FDJG0012E"); a TCP/Wi-Fi serial is
    ``<ip>:<port>`` (e.g. "192.168.1.5:5555"). Used by doctor to decide whether
    to run a reachability preflight against ANH_UDID.
    """
    if not serial or "://" in str(serial):
        return False
    s = str(serial)
    host, sep, port = s.rpartition(":")
    return bool(sep and host and port.isdigit())
