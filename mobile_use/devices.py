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
    print(f"unknown subcommand {sub!r}\n\n{HELP}", file=sys.stderr)
    return 2
