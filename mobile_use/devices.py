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

    for i, (serial, model) in enumerate(_adb_devices_long(), start=1):
        name = model or f"android-{i}"
        out.append({"platform": "android", "udid": serial, "name": name})

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

def _socket_dir():
    return Path(os.environ.get("TMPDIR", "/tmp"))


def list_running_daemons():
    """Scan the socket dir for active named daemons.

    Returns list of {"platform": "ios"|"android", "name": str|None, "alive": bool,
    "socket": str}. `name=None` is the default (unnamed) daemon.
    """
    out = []
    sd = _socket_dir()
    try:
        entries = list(sd.iterdir())
    except OSError:
        return out

    for p in entries:
        m = re.match(r"^(iph|anh)(?:-([A-Za-z0-9_-]{1,64}))?\.sock$", p.name)
        if not m:
            continue
        prefix, name = m.group(1), m.group(2)
        platform = "ios" if prefix == "iph" else "android"
        alive = _probe_daemon(platform, name)
        out.append({"platform": platform, "name": name, "alive": alive, "socket": str(p)})
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
    rows = [(d["platform"], d["name"], d["udid"]) for d in devices]
    print(_format_table(rows, ["PLATFORM", "NAME", "UDID"]))
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
