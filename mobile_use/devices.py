"""Multi-device discovery + CLI helpers.

Auto-detect connected iOS + Android devices without forcing the user to look
up UDIDs by hand. Powers `mobile-use devices list/status/reload` and
`DevicePool.from_connected()`.

Discovery is best-effort:
- iOS: `idevice_id -l` (libimobiledevice). Empty on Windows / when not
  installed — return [] with no error. Names come from `idevicename`.
- Android: `adb devices -l`. Empty when adb missing.

Status enumerates running named daemons by scanning the IPC socket dir
(`/tmp/iph-*.sock`, `/tmp/anh-*.sock`). Includes the default (unnamed) socket
when present.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mobile_use._platform import default_runtime_base

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _which(cmd):
    return shutil.which(cmd)


def _ios_udids():
    if _which("idevice_id") is None:
        return []
    try:
        out = subprocess.check_output(
            ["idevice_id", "-l"], timeout=3.0, stderr=subprocess.DEVNULL
        ).decode().strip()
        return [u for u in out.splitlines() if u]
    except Exception:
        return []


def _ios_sims():
    """Booted iOS Simulators as [(udid, name)] via `xcrun simctl list ... -j`.

    Physical-device discovery (idevice_id) never returns Simulators, so an Apple
    Silicon dev with no spare iPhone couldn't get a UDID auto-filled or smoke-test
    against the standard CI/dev target. XCUITest drives a sim by its UDID exactly
    like a real device. Returns [] off macOS / without Xcode / on any parse error.
    """
    if _which("xcrun") is None:
        return []
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            timeout=5.0, stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return []
    try:
        import json
        data = json.loads(out)
    except (ValueError, TypeError):
        return []
    sims = []
    for runtime_devices in (data.get("devices") or {}).values():
        for dev in runtime_devices:
            if dev.get("state") == "Booted" and dev.get("udid"):
                sims.append((dev["udid"], dev.get("name") or "iOS Simulator"))
    return sims


def _ios_name(udid):
    if _which("idevicename") is None:
        return None
    try:
        out = subprocess.check_output(
            ["idevicename", "-u", udid], timeout=3.0, stderr=subprocess.DEVNULL
        ).decode().strip()
        return out or None
    except Exception:
        return None


def _adb_devices_long():
    """Parse `adb devices -l` → list of (serial, model_or_None).

    Lines look like:
      SERIAL\tdevice usb:... product:foo model:Pixel_7 device:panther transport_id:1
    """
    if _which("adb") is None:
        return []
    try:
        out = subprocess.check_output(
            ["adb", "devices", "-l"], timeout=3.0, stderr=subprocess.DEVNULL
        ).decode()
    except Exception:
        return []
    result = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "device" not in line.split():
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device":
                continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        model = None
        for tok in parts[2:]:
            if tok.startswith("model:"):
                model = tok.split(":", 1)[1].replace("_", " ")
                break
        result.append((serial, model))
    return result


# ---- adb-over-Wi-Fi --------------------------------------------------------

def _run_adb(args, timeout=10.0):
    """Run ``adb <args>`` -> ``(ok, output)``.

    ok is False when adb is missing or exits non-zero. Centralized so the Wi-Fi
    helpers share one implementation tests can monkeypatch in a single place.
    """
    adb = _which("adb")
    if adb is None:
        return False, "adb not found on PATH (install Android Platform Tools)"
    try:
        out = subprocess.check_output(
            [adb, *args], timeout=timeout, stderr=subprocess.STDOUT
        ).decode(errors="replace").strip()
        return True, out
    except subprocess.CalledProcessError as e:
        msg = (e.output or b"").decode(errors="replace").strip()
        return False, msg or f"adb {' '.join(args)} failed (rc={e.returncode})"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def adb_enable_tcpip(port=5555, usb_serial=None, timeout=10.0):
    """``adb [-s serial] tcpip <port>`` — restart adbd on the device in TCP mode.

    Requires the device on USB at call time (this is the one step Wi-Fi can't
    bootstrap itself). Returns ``(ok, detail)``.
    """
    pre = ["-s", usb_serial] if usb_serial else []
    return _run_adb(pre + ["tcpip", str(port)], timeout=timeout)


def adb_connect(ip, port=5555, timeout=10.0):
    """``adb connect <ip>:<port>``. Returns ``(ok, detail)``.

    adb exits 0 even when it prints "failed to connect", so success is decided
    from the message text, not the exit code.
    """
    ran, out = _run_adb(["connect", f"{ip}:{port}"], timeout=timeout)
    if not ran:
        return False, out
    low = out.lower()
    ok = "connected to" in low and not any(w in low for w in ("failed", "cannot", "unable"))
    return ok, out


def adb_disconnect(ip, port=5555, timeout=10.0):
    """``adb disconnect <ip>:<port>``. Returns ``(ok, detail)``."""
    return _run_adb(["disconnect", f"{ip}:{port}"], timeout=timeout)


ANDROID_WIFI_HELP = """\
mobile-use android wifi <ip[:port]> — drive an Android device over Wi-Fi (adb-over-TCP).

USAGE:
  mobile-use android wifi <ip>              tcpip 5555 (over USB) then connect <ip>:5555
  mobile-use android wifi <ip:port>         use a non-default port
  mobile-use android wifi <ip> --usb SERIAL pick which USB device to switch (if several)
  mobile-use android wifi <ip> --disconnect drop the adb-over-Wi-Fi connection

On success it prints the ip:port serial to set as ANH_UDID. The device must be
USB-connected once so `adb tcpip` can switch it; after that it's wireless.
"""


def android_wifi_main(argv):
    """`mobile-use android wifi <ip[:port]> [--disconnect] [--usb SERIAL] [--port N]`."""
    if not argv or argv[0] in {"-h", "--help"}:
        print(ANDROID_WIFI_HELP)
        return 0 if argv else 2

    target = None
    disconnect = False
    usb = None
    port = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--disconnect":
            disconnect = True
        elif a == "--usb" and i + 1 < len(argv):
            usb = argv[i + 1]; i += 1
        elif a == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                print(f"invalid --port {argv[i + 1]!r}", file=sys.stderr)
                return 2
            i += 1
        elif not a.startswith("-") and target is None:
            target = a
        i += 1

    if not target:
        print("usage: mobile-use android wifi <ip[:port]> [--disconnect] [--usb SERIAL] [--port N]",
              file=sys.stderr)
        return 2

    from mobile_use.netcheck import parse_host_port
    try:
        ip, p = parse_host_port(target, default_port=(port or 5555))
    except ValueError as e:
        print(f"bad target {target!r}: {e}", file=sys.stderr)
        return 2

    if disconnect:
        ok, detail = adb_disconnect(ip, p)
        print(detail)
        return 0 if ok else 1

    # 1) Switch the USB-connected device into TCP mode. Best-effort: it may
    #    already be in tcpip mode from a prior run, so a failure here is not fatal
    #    — we still try to connect.
    _ran, detail = adb_enable_tcpip(port=p, usb_serial=usb)
    print(f"adb tcpip {p}: {detail}")
    # 2) Connect over the network.
    ok, detail = adb_connect(ip, p)
    print(f"adb connect {ip}:{p}: {detail}")
    if not ok:
        print("\nCould not connect. Checklist:\n"
              "  - device + this host on the SAME Wi-Fi network\n"
              "  - device USB-connected once so `adb tcpip` could switch it (re-run on USB)\n"
              "  - the adb TCP port isn't firewalled")
        return 1
    print(f"\nConnected over Wi-Fi. Set this serial:\n  ANH_UDID={ip}:{p}\n"
          "  (add it to .env, or `mobile-use init` after the device is reachable)")
    return 0


# ---- iOS Wi-Fi (cable-free WebDriverAgent over mDNS) ----------------------

WDA_DEFAULT_PORT = 8100


def _sanitize_bonjour(name):
    """Approximate Apple's Bonjour hostname for a device name.

    iOS advertises ``<DeviceName>.local`` on the LAN, but Bonjour munges the
    name: spaces/underscores become hyphens and other punctuation is dropped
    (``Jack's iPhone`` -> ``Jacks-iPhone``). Returns the munged label, or None.
    """
    if not name:
        return None
    s = name.strip().replace(" ", "-").replace("_", "-")
    s = re.sub(r"[^A-Za-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or None


def _ios_mdns_candidates(udid=None):
    """Candidate ``<name>.local`` hostnames for the iPhone, best-guess first.

    Derives names from ``idevicename`` (single udid) or ``discover_connected``
    (all physical iPhones). Both the Bonjour-munged label and the raw
    space-to-hyphen form are tried, since the exact munging is heuristic.
    """
    names = []
    if udid:
        n = _ios_name(udid)
        if n:
            names.append(n)
    else:
        for d in discover_connected():
            if d.get("platform") == "ios" and not d.get("simulator"):
                n = _ios_name(d["udid"]) or d.get("name")
                if n:
                    names.append(n)
    cands, seen = [], set()
    for n in names:
        for h in (_sanitize_bonjour(n), n.strip().replace(" ", "-")):
            if h and h not in seen:
                seen.add(h)
                cands.append(f"{h}.local")
    return cands


def ios_wifi_target(udid=None, host=None, port=WDA_DEFAULT_PORT, probe=True, timeout=2.0):
    """Resolve the wireless WebDriverAgent URL for the iPhone.

    Prefers the mDNS hostname ``<DeviceName>.local:<port>`` — Bonjour resolves it
    on the LAN, and it has been observed to work where a raw Wi-Fi IP did not
    (the IP is often on a different subnet / not routed, while mDNS is). Falls
    back to an explicit ``host`` (e.g. a known Wi-Fi IP) when given.

    With ``probe=True`` each candidate is TCP-probed on ``port`` and the first
    reachable one wins; otherwise the top candidate is returned unprobed.

    Returns a dict ``{url, host, port, source, reachable, candidates}`` (source
    in {"mdns","explicit"}), or None when there are no candidates at all.
    """
    from mobile_use.netcheck import target_reachable

    candidates = [(h, "mdns") for h in _ios_mdns_candidates(udid)]
    if host:
        candidates.append((host, "explicit"))  # explicit IP is the fallback
    if not candidates:
        return None

    tried = []
    for h, source in candidates:
        entry = {"url": f"http://{h}:{port}", "host": h, "port": port, "source": source}
        if not probe:
            entry["reachable"] = None
            entry["candidates"] = [entry]
            return entry
        ok, _detail = target_reachable(entry["url"], default_port=port, timeout=timeout)
        entry["reachable"] = ok
        tried.append(entry)
        if ok:
            entry["candidates"] = tried
            return entry

    best = dict(tried[0])  # nothing reachable — return the best candidate, flagged
    best["candidates"] = tried
    return best


def _env_path():
    """The .env file to persist into — repo root, else agent-workspace, else default."""
    from mobile_use.setup_env import DEFAULT_ENV_PATH, ALT_ENV_PATH
    if DEFAULT_ENV_PATH.exists():
        return DEFAULT_ENV_PATH
    if ALT_ENV_PATH.exists():
        return ALT_ENV_PATH
    return DEFAULT_ENV_PATH


def _upsert_env_var(path, key, value):
    """Set/replace ``key=value`` in a .env, preserving every other line.

    Line-based (not render_env) so it never drops hand-edited or unknown keys.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    out, replaced = [], False
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{key}={value}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


def _ios17_tunnel_hint():
    """One-line reminder that iOS 17+ also needs the RemoteXPC tunnel up."""
    return ("iOS 17+ also needs the RemoteXPC tunnel running over Wi-Fi — "
            "check it with `mobile-use ios tunnel` (one `sudo` step).")


IOS_WIFI_HELP = """\
mobile-use ios wifi [host] — drive an iPhone over Wi-Fi (cable-free WebDriverAgent).

USAGE:
  mobile-use ios wifi                 auto-discover the iPhone's mDNS name (<name>.local:8100)
  mobile-use ios wifi 10.0.0.7        use an explicit Wi-Fi IP / host as fallback
  mobile-use ios wifi --port 8100     WDA port (default 8100)
  mobile-use ios wifi --udid UDID     pick which paired iPhone to name-resolve
  mobile-use ios wifi --no-probe      skip the TCP reachability probe
  mobile-use ios wifi --persist       write IPH_WDA_URL=... into .env
  mobile-use ios wifi --check         exit 0 only if the resolved WDA is reachable

Prefers the mDNS hostname (<DeviceName>.local) — it resolves on the LAN even when
the raw Wi-Fi IP is on an unrouted subnet. On iOS 17+ the RemoteXPC tunnel must
also be up; see `mobile-use ios tunnel`.
"""


def ios_wifi_main(argv):
    """`mobile-use ios wifi [host] [--port N] [--udid U] [--no-probe] [--persist] [--check]`."""
    if argv and argv[0] in {"-h", "--help"}:
        print(IOS_WIFI_HELP)
        return 0

    host = udid = port = None
    probe = True
    persist = check = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                print(f"invalid --port {argv[i + 1]!r}", file=sys.stderr)
                return 2
            i += 1
        elif a == "--udid" and i + 1 < len(argv):
            udid = argv[i + 1]; i += 1
        elif a == "--no-probe":
            probe = False
        elif a == "--persist":
            persist = True
        elif a == "--check":
            check = True
        elif not a.startswith("-") and host is None:
            host = a
        i += 1

    udid = udid or os.environ.get("IPH_UDID") or None
    res = ios_wifi_target(udid=udid, host=host, port=(port or WDA_DEFAULT_PORT), probe=probe)
    if res is None:
        print("No iPhone found to name-resolve, and no explicit host given.\n"
              "  - Connect the iPhone over USB once (so it can be named), or\n"
              "  - pass the Wi-Fi IP:  mobile-use ios wifi <ip>", file=sys.stderr)
        return 1

    url, reach, src = res["url"], res["reachable"], res["source"]
    if probe:
        print(f"WebDriverAgent ({src}): {url} — {'reachable' if reach else 'NOT reachable'}")
    else:
        print(f"WebDriverAgent ({src}): {url} — (not probed)")

    if persist:
        path = _upsert_env_var(_env_path(), "IPH_WDA_URL", url)
        print(f"persisted IPH_WDA_URL to {path}")

    print(f"\nSet this to drive over Wi-Fi:\n  IPH_WDA_URL={url}")
    print(f"\n{_ios17_tunnel_hint()}")

    if check:
        return 0 if reach else 1
    return 0 if (reach or not probe) else 1


# ---- iOS RemoteXPC tunnel (pymobiledevice3 tunneld) -----------------------

TUNNELD_HOST = "127.0.0.1"
TUNNELD_PORT = 49151  # pymobiledevice3 tunneld REST API default (TUNNELD_DEFAULT_ADDRESS)


def _pymobiledevice3_available():
    """True if the pymobiledevice3 package is importable."""
    try:
        import importlib.util
        return importlib.util.find_spec("pymobiledevice3") is not None
    except Exception:
        return False


def _tunneld_start_cmd():
    """The exact one-liner to start the RemoteXPC tunnel daemon (needs sudo).

    pymobiledevice3 ships as a module; only some installs put a console script on
    PATH. Emit whichever form will actually run on this machine.
    """
    if _which("pymobiledevice3"):
        return "sudo pymobiledevice3 remote tunneld"
    return "sudo python3 -m pymobiledevice3 remote tunneld"


def tunneld_status(host=TUNNELD_HOST, port=TUNNELD_PORT, timeout=1.5):
    """Probe the pymobiledevice3 tunneld REST API. Returns (up, detail, tunnels).

    ``tunnels`` is the parsed JSON tunnel map when readable, else None. Read-only
    — a plain TCP connect + a best-effort GET; never disturbs a running connector.
    """
    from mobile_use.netcheck import tcp_reachable
    ok, detail = tcp_reachable(host, port, timeout=timeout)
    if not ok:
        return False, detail, None
    tunnels = None
    try:
        import json as _json
        import urllib.request
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout) as r:
            tunnels = _json.loads(r.read().decode())
    except Exception:
        pass  # API up but list unreadable (version skew) — still "up".
    return True, f"tunneld reachable at {host}:{port}", tunnels


IOS_TUNNEL_HELP = """\
mobile-use ios tunnel [--check] — RemoteXPC tunnel status for cable-free iOS 17+.

On iOS 17+ Apple's RemoteXPC replaced lockdownd, so WebDriverAgent is only
reachable through a tunnel. For cable-free (Wi-Fi) control the tunnel daemon must
be running so the connection survives unplugging USB:

  sudo pymobiledevice3 remote tunneld      # the one privileged step (keep running)

This command probes the tunneld REST API (127.0.0.1:49151) and reports whether
the tunnel is up. It NEVER runs sudo for you — it prints the exact command.

  --check    exit 0 if the tunnel is up, 1 otherwise (for scripts / doctor)
"""


def ios_tunnel_main(argv):
    """`mobile-use ios tunnel [--check]` — RemoteXPC tunnel health + start command."""
    if argv and argv[0] in {"-h", "--help"}:
        print(IOS_TUNNEL_HELP)
        return 0

    if not _pymobiledevice3_available():
        print("pymobiledevice3 is not installed (needed for the iOS 17+ RemoteXPC tunnel).\n"
              "  install:  python3 -m pip install pymobiledevice3", file=sys.stderr)
        return 1

    up, detail, tunnels = tunneld_status()
    if up:
        n = len(tunnels) if isinstance(tunnels, dict) else None
        suffix = f" ({n} device tunnel{'' if n == 1 else 's'})" if n is not None else ""
        print(f"RemoteXPC tunnel: UP — {detail}{suffix}")
        return 0

    print(f"RemoteXPC tunnel: DOWN — {detail}")
    print("\nStart it (keep running in its own terminal — needs sudo once):\n"
          f"  {_tunneld_start_cmd()}")
    print("\nThen re-check:  mobile-use ios tunnel --check")
    return 1


def discover_connected():
    """Return a list of discovered devices.

    Each entry: {"platform": "ios"|"android", "udid": str, "name": str}

    Names are derived from device metadata when possible, otherwise a
    deterministic platform-indexed default (e.g. "ios-1", "android-1").
    Collisions are disambiguated by appending an index (`Pixel 7-2`).
    """
    out = []
    seen_names = {}

    ios_udids = _ios_udids()
    for i, udid in enumerate(ios_udids, start=1):
        name = _ios_name(udid) or f"ios-{i}"
        out.append({"platform": "ios", "udid": udid, "name": name})

    # Booted iOS Simulators (skip any already reported as physical, just in case).
    physical = set(ios_udids)
    for udid, sim_name in _ios_sims():
        if udid in physical:
            continue
        out.append({"platform": "ios", "udid": udid, "name": sim_name, "simulator": True})

    from mobile_use.netcheck import looks_like_wifi_serial
    for i, (serial, model) in enumerate(_adb_devices_long(), start=1):
        name = model or f"android-{i}"
        transport = "wifi" if looks_like_wifi_serial(serial) else "usb"
        out.append({"platform": "android", "udid": serial, "name": name, "transport": transport})

    for entry in out:
        base = entry["name"]
        count = seen_names.get(base, 0)
        seen_names[base] = count + 1
        if count > 0:
            entry["name"] = f"{base}-{count + 1}"

    for entry in out:
        sanitized = re.sub(r"[^A-Za-z0-9_-]", "-", entry["name"])[:64]
        if not _NAME_RE.match(sanitized):
            sanitized = f"{entry['platform']}-{abs(hash(entry['udid'])) % 10000}"
        entry["name"] = sanitized

    return out


def discovery_hints():
    """Return a list of human-readable hints when discovery returns empty.

    Tells the user which tool is missing per platform.
    """
    hints = []
    if _which("idevice_id") is None:
        if sys.platform == "darwin":
            hints.append("iOS: install libimobiledevice — `brew install libimobiledevice`")
        elif sys.platform == "linux":
            hints.append("iOS: install libimobiledevice — `apt install libimobiledevice-utils` or distro equivalent")
        else:
            hints.append("iOS on Windows: not directly supported — use --remote-daemon to a Mac (see SETUP.md)")
    if _which("adb") is None:
        if sys.platform == "darwin":
            hints.append("Android: install adb — `brew install --cask android-platform-tools`")
        elif sys.platform == "linux":
            hints.append("Android: install adb — `apt install android-tools-adb` or distro equivalent")
        else:
            hints.append("Android: install Android Platform Tools and add `adb` to PATH")
    return hints


# ---- running-daemon enumeration -------------------------------------------

def _socket_dirs():
    """Directories the iOS and Android daemons actually place sockets in.

    Mirrors each harness's own resolution (IPH/ANH_RUNTIME_DIR -> *_TMP_DIR ->
    /tmp). The daemons never consult TMPDIR, so neither do we: on macOS TMPDIR
    defaults to /var/folders/... while daemons write to /tmp, and a custom
    RUNTIME_DIR would otherwise be missed entirely — both made `devices
    status/reload/view` report "No named daemons running" against live daemons.
    Resolved dynamically (per call) so it tracks the current environment.
    The fallback is _platform.default_runtime_base() — '/tmp' on POSIX (matching
    the daemons) and a Windows-writable dir on win32 (where the daemons also
    resolve their pid/runtime files), so discovery never scans the wrong drive.
    """
    base = default_runtime_base()
    ios = os.environ.get("IPH_RUNTIME_DIR") or os.environ.get("IPH_TMP_DIR") or base
    anh = os.environ.get("ANH_RUNTIME_DIR") or os.environ.get("ANH_TMP_DIR") or base
    seen, dirs = set(), []
    for d in (ios, anh):
        if d not in seen:
            seen.add(d)
            dirs.append(Path(d))
    return dirs


def _daemon_endpoint_str(platform, name):
    """Human-readable endpoint for a daemon known only by its pid file.

    On Windows the default transport is TCP loopback, so there is no .sock path
    to display — derive the bind endpoint (tcp://127.0.0.1:<port>) from the
    harness's own resolution instead. Best-effort; returns "" on any error."""
    try:
        if platform == "ios":
            from iphone_harness import _ipc as ipc
        else:
            from android_harness import _ipc as ipc
        return ipc.sock_addr(name if name is not None else "default")
    except Exception:
        return ""


def list_running_daemons():
    """Enumerate active named daemons across the daemons' runtime dirs.

    Scans for BOTH the unix socket files (POSIX default transport) AND the pid
    files. The pid file is always written and is the ONLY on-disk marker on
    Windows, where the default transport is TCP loopback and no .sock exists —
    a .sock-only scan reported "No named daemons running" against live Windows
    daemons. Deduped by (platform, name) so a POSIX daemon with both a .sock and
    a .pid isn't listed twice.

    Returns list of {"platform": "ios"|"android", "name": str|None, "alive": bool,
    "socket": str}. `name=None` is the default (unnamed) daemon. The "socket"
    field is the .sock path when present, else the resolved endpoint (a tcp://
    uri on Windows).
    """
    pat = re.compile(r"^(iph|anh)(?:-([A-Za-z0-9_-]{1,64}))?\.(sock|pid)$")
    found = {}  # (platform, name) -> socket/endpoint string (.sock path wins)
    for sd in _socket_dirs():
        try:
            entries = list(sd.iterdir())
        except OSError:
            continue
        for p in entries:
            m = pat.match(p.name)
            if not m:
                continue
            prefix, name, ext = m.group(1), m.group(2), m.group(3)
            platform = "ios" if prefix == "iph" else "android"
            key = (platform, name)
            if ext == "sock":
                found[key] = str(p)  # prefer the real socket path for display
            elif key not in found:
                found[key] = _daemon_endpoint_str(platform, name)
    out = []
    for (platform, name), endpoint in found.items():
        out.append({
            "platform": platform, "name": name,
            "alive": _probe_daemon(platform, name), "socket": endpoint,
        })
    return out


def _probe_daemon(platform, name):
    try:
        if platform == "ios":
            from iphone_harness import admin as iph_admin
            return bool(iph_admin.daemon_alive(name))
        from android_harness import admin as anh_admin
        return bool(anh_admin.daemon_alive(name))
    except Exception:
        return False


# ---- CLI -------------------------------------------------------------------

HELP = """\
mobile-use devices — auto-discover and manage connected devices.

USAGE:
  mobile-use devices list           Auto-detect connected iOS + Android devices.
  mobile-use devices list --json    Same, JSON output (machine-readable).
  mobile-use devices status         Show running named daemons.
  mobile-use devices status --json  Same, JSON output.
  mobile-use devices reload <name>  cleanup_stale + restart_daemon for one name.
  mobile-use devices reload --all   Reload every running named daemon.
  mobile-use devices view           Live MJPEG grid of every connected device.

DISCOVERY:
  iOS uses `idevice_id -l` (libimobiledevice).
  Android uses `adb devices -l`.
  Empty list (no error) when the tool isn't installed.

TIPS:
  - Pair with the Python API: `DevicePool.from_connected()` builds a pool
    from the same discovery output.
  - On Windows, iOS discovery returns empty — use a remote Mac daemon via
    `--remote-daemon` (see SETUP.md).
"""


VIEW_HELP = """\
mobile-use devices view — live multi-device MJPEG viewer.

USAGE:
  mobile-use devices view                       Open every connected device in a browser grid.
  mobile-use devices view --no-browser          Print URL but don't auto-open.
  mobile-use devices view --port 8765           Use a specific port (default: auto).
  mobile-use devices view --fps 4               Per-device frame rate (default 4 for multi).
  mobile-use devices view --devices A,B         Cherry-pick a subset by NAME (from `devices list`).
  mobile-use devices view --mock                Stub backend for CI / smoke tests.

The viewer hosts one MJPEG stream per device under /stream/<name> and a
combined grid index at /. Loopback-only (no auth). Read-only mirror — use
the agent loop / `-c` for input.
"""


def _format_table(rows, headers):
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = lambda parts: "  ".join(str(p).ljust(w) for p, w in zip(parts, widths))
    out = [line(headers), line(["-" * w for w in widths])]
    for row in rows:
        out.append(line(row))
    return "\n".join(out)


def _cmd_list(args):
    as_json = "--json" in args
    devices = discover_connected()
    if as_json:
        print(json.dumps(devices, indent=2))
        return 0
    if not devices:
        print("No devices connected.")
        for h in discovery_hints():
            print(f"  hint: {h}")
        return 0
    rows = [(d["platform"], d["name"], d.get("transport", "usb"), d["udid"]) for d in devices]
    print(_format_table(rows, ["PLATFORM", "NAME", "TRANSPORT", "UDID"]))
    return 0


def _cmd_status(args):
    as_json = "--json" in args
    daemons = list_running_daemons()
    if as_json:
        print(json.dumps(daemons, indent=2))
        return 0
    if not daemons:
        print("No named daemons running.")
        return 0
    rows = [
        (d["platform"], d["name"] or "(default)", "alive" if d["alive"] else "stale", d["socket"])
        for d in daemons
    ]
    print(_format_table(rows, ["PLATFORM", "NAME", "STATE", "SOCKET"]))
    return 0


def _cmd_reload(args):
    if not args:
        print("usage: mobile-use devices reload <name> | --all", file=sys.stderr)
        return 2
    targets = []
    if args[0] == "--all":
        for d in list_running_daemons():
            targets.append((d["platform"], d["name"]))
    else:
        name = args[0]
        if not _NAME_RE.match(name):
            print(f"invalid name {name!r}: must match [A-Za-z0-9_-]{{1,64}}", file=sys.stderr)
            return 2
        found = False
        for d in list_running_daemons():
            if d["name"] == name:
                targets.append((d["platform"], name))
                found = True
        if not found:
            print(f"no daemon named {name!r} — check `mobile-use devices status`", file=sys.stderr)
            return 1

    if not targets:
        print("Nothing to reload.")
        return 0

    rc = 0
    for platform, name in targets:
        label = f"{platform}/{name or '(default)'}"
        try:
            if platform == "ios":
                from iphone_harness import admin as adm
            else:
                from android_harness import admin as adm
            adm.cleanup_stale(name)
            adm.restart_daemon(name)
            print(f"reloaded {label}")
        except Exception as e:
            print(f"failed {label}: {e}", file=sys.stderr)
            rc = 1
    return rc


def _parse_view_args(args):
    """Tiny argparser for `devices view` — avoids dragging argparse into the
    other subcommands (which use position-only conventions)."""
    parsed = {
        "no_browser": False, "port": None, "fps": 4,
        "devices": None, "mock": False, "help": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a in {"-h", "--help"}:
            parsed["help"] = True
        elif a == "--no-browser":
            parsed["no_browser"] = True
        elif a == "--mock":
            parsed["mock"] = True
        elif a == "--port" and i + 1 < len(args):
            try:
                parsed["port"] = int(args[i + 1])
            except ValueError:
                raise ValueError(f"--port expects an integer, got {args[i+1]!r}")
            i += 1
        elif a == "--fps" and i + 1 < len(args):
            try:
                parsed["fps"] = int(args[i + 1])
            except ValueError:
                raise ValueError(f"--fps expects an integer, got {args[i+1]!r}")
            i += 1
        elif a == "--devices" and i + 1 < len(args):
            parsed["devices"] = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 1
        else:
            raise ValueError(f"unknown flag {a!r}")
        i += 1
    return parsed


def _make_mock_view_pairs():
    return [("ios", "iphone-mock-A"), ("ios", "iphone-mock-B"), ("android", "pixel-mock-1")]


def _mock_client_factory():
    """Build a no-network NamedStreamClient-shaped stub for --mock view."""
    import base64 as _b64
    tiny_jpeg_b64 = (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
        "AQEBAQEBAQEBAQEBAQEBAQEBAQEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAcI/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/a"
        "AAgBAQABPxA//9k="
    )

    class _MockClient:
        def __init__(self, platform, name, fps, quality, max_dim):
            self.platform, self.name, self.fps = platform, name, fps
            self._n = 0

        def start(self):
            return {"running": True}

        def frame(self):
            self._n += 1
            return {"ready": True, "frame_no": self._n,
                    "jpeg_b64": tiny_jpeg_b64, "fps": float(self.fps)}

        def frame_jpeg(self):
            return _b64.b64decode(tiny_jpeg_b64)

        def stop(self):
            return {"running": False}

    return lambda p, n, f, q, m: _MockClient(p, n, f, q, m)


def _cmd_view(args):
    try:
        opts = _parse_view_args(args)
    except ValueError as e:
        print(f"{e}\n\n{VIEW_HELP}", file=sys.stderr)
        return 2

    if opts["help"]:
        print(VIEW_HELP)
        return 0

    from .viewer.multi_server import MultiViewerServer

    if opts["mock"]:
        pairs = _make_mock_view_pairs()
        factory = _mock_client_factory()
    else:
        connected = discover_connected()
        if not connected:
            print("No devices connected.", file=sys.stderr)
            for h in discovery_hints():
                print(f"  hint: {h}", file=sys.stderr)
            return 1
        if opts["devices"]:
            allowed = set(opts["devices"])
            connected = [d for d in connected if d["name"] in allowed]
            missing = allowed - {d["name"] for d in connected}
            if missing:
                print(f"unknown device(s): {sorted(missing)}", file=sys.stderr)
                print("  see `mobile-use devices list`", file=sys.stderr)
                return 1
        pairs = [(d["platform"], d["name"]) for d in connected]
        factory = None

        for platform, name in pairs:
            try:
                if platform == "ios":
                    from iphone_harness import admin as adm
                else:
                    from android_harness import admin as adm
                adm.ensure_daemon(name=name)
            except Exception as e:
                print(f"warning: daemon for {platform}/{name} not reachable: {e}",
                      file=sys.stderr)

    try:
        viewer = MultiViewerServer(
            pairs, port=opts["port"], fps=opts["fps"],
            client_factory=factory,
        )
    except ValueError as e:
        print(f"viewer init failed: {e}", file=sys.stderr)
        return 1

    viewer.start()
    print(f"multi-device viewer — {viewer.url}")
    print(f"  streaming {len(pairs)} device(s): "
          f"{', '.join(f'{p}/{n}' for p, n in pairs)}")
    print("  Ctrl+C to stop.")

    if not opts["no_browser"]:
        try:
            import webbrowser
            webbrowser.open(viewer.url)
        except Exception:
            pass

    try:
        import time as _time
        while True:
            _time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        viewer.stop()
    return 0


def main(args):
    if not args or args[0] in {"-h", "--help"}:
        print(HELP)
        return 0
    sub, sub_args = args[0], args[1:]
    if sub == "list":
        return _cmd_list(sub_args)
    if sub == "status":
        return _cmd_status(sub_args)
    if sub == "reload":
        return _cmd_reload(sub_args)
    if sub == "view":
        return _cmd_view(sub_args)
    print(f"unknown subcommand {sub!r}\n\n{HELP}", file=sys.stderr)
    return 2
