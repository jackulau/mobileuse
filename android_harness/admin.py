"""Daemon lifecycle, doctor, --reload for Android harness."""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mobile_use._platform import (
    LINUX_ADB_PKGS,
    LINUX_NODE_PKGS,
    install_hint,
    is_linux,
    is_macos,
    kill_pid,
    process_exists,
)

from . import _ipc as ipc

NAME = os.environ.get("ANH_NAME", "default")


def _appium_url():
    """Call-time lookup — ANH_APPIUM_URL set after import (quickstart
    --autostart, pool env overrides) must be honored by doctor probes."""
    return os.environ.get("ANH_APPIUM_URL", "http://127.0.0.1:4723")


APPIUM_URL = _appium_url()  # import-time snapshot kept for back-compat repr/logs


def is_remote_daemon(name=None):
    """True when ANH_CONNECT points at a daemon we don't manage locally.
    Same client-only mode as iphone_harness — caller is responsible for the
    remote daemon, ensure_daemon won't spawn locally."""
    spec = os.environ.get("ANH_CONNECT")
    if not spec:
        return False
    try:
        kind, *_ = ipc.parse_endpoint(spec)
    except ValueError:
        return False
    return kind == "tcp"


def _version():
    try:
        from importlib.metadata import version
        return version("mobile-use")
    except Exception:
        return None


def daemon_alive(name=None):
    return ipc.ping(name or NAME, timeout=1.0)


def _pid_alive(pid):
    """True if a process with this pid exists.

    Delegates the probe to _platform.process_exists, which is Windows-safe:
    on Windows os.kill(pid, 0) calls TerminateProcess and would KILL the pid
    being probed, so the POSIX os.kill idiom must never run there."""
    # isinstance(True, int) is True — reject bool to avoid probing pid 1/True.
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return process_exists(pid)


def cleanup_stale(name=None):
    """Remove leftover .pid + (for AF_UNIX) .sock from a dead daemon.

    TCP endpoints (ANH_BIND=tcp://...) have no socket file to clean.
    """
    name = name or NAME
    pid_path = ipc.pid_path(name)
    endpoint = ipc.bind_endpoint(name)

    if ipc.ping(name, timeout=0.3):
        return False

    cleaned = False
    try:
        recorded = int(pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, UnicodeDecodeError, PermissionError, OSError):
        recorded = None

    if recorded is not None and not _pid_alive(recorded):
        try:
            pid_path.unlink()
            cleaned = True
        except FileNotFoundError:
            pass
    elif recorded is None and pid_path.exists():
        try:
            pid_path.unlink()
            cleaned = True
        except FileNotFoundError:
            pass

    # TCP endpoint (e.g. tcp://127.0.0.1:8763) has no file — skip socket cleanup.
    if endpoint[0] == "unix":
        from pathlib import Path as _P
        sock_path = _P(endpoint[1])
        if sock_path.exists():
            try:
                sock_path.unlink()
                cleaned = True
            except FileNotFoundError:
                pass

    return cleaned


# Freshness window for the deep (device round-trip) ensure probe. A verified
# ensure within the TTL is trusted, mirroring the daemon's own PROBE_INTERVAL
# pattern — without this, EVERY ensure_daemon call cost an active_app RPC even
# when the daemon was verified moments ago.
_ENSURE_TTL_DEFAULT = 10.0
_ensure_ok_at = {}        # name -> time.monotonic() of last VERIFIED deep probe


def _ensure_ttl():
    try:
        return float(os.environ.get("ANH_ENSURE_TTL", str(_ENSURE_TTL_DEFAULT)))
    except (TypeError, ValueError):
        return _ENSURE_TTL_DEFAULT


def ensure_cache_bust(name=None):
    """Forget the last verified ensure (one name, or all) so the next
    ensure_daemon runs the full deep probe. Called on stale-session signals."""
    if name is None:
        _ensure_ok_at.clear()
    else:
        _ensure_ok_at.pop(name, None)


def _maybe_reconnect_wifi_device(env=None):
    """One adb-connect attempt when ANH_UDID is an ip:port serial that's not
    currently in `adb devices` — sessions self-heal after a host reboot or adb
    server restart without the user re-running `android wifi`. Never raises,
    never loops; the persistent variant is `mobile-use wifi reconnect`."""
    serial = (env or os.environ).get("ANH_UDID", "")
    try:
        from mobile_use.netcheck import looks_like_wifi_serial
        if not looks_like_wifi_serial(serial):
            return
        from mobile_use.devices import _run_adb, adb_connect
        ok, out = _run_adb(["devices"])
        if ok and serial in out:
            return
        host, _, port_s = serial.rpartition(":")
        adb_connect(host, int(port_s) if port_s.isdigit() else 5555)
    except Exception:
        pass


def ensure_daemon(wait=30.0, name=None, env=None):
    name = name or NAME

    if is_remote_daemon(name):
        spec = os.environ.get("ANH_CONNECT", "")
        if daemon_alive(name):
            return
        raise RuntimeError(
            f"android-harness: remote daemon unreachable at {spec}.\n"
            f"  This host is in client-only mode (ANH_CONNECT set).\n"
            f"  Checks on the remote host:\n"
            f"    - daemon running?  (ssh remote 'pgrep -fa android_harness.daemon')\n"
            f"    - bound to TCP?    (ssh remote 'lsof -iTCP -sTCP:LISTEN | grep python')\n"
            f"    - reachable port?  (telnet/netstat to confirm)\n"
        )

    if daemon_alive(name):
        # A verified deep probe within the TTL is trusted — the local liveness
        # ping above still ran (so a dead daemon always falls through to spawn),
        # only the device round-trip is skipped.
        last = _ensure_ok_at.get(name)
        ttl = _ensure_ttl()
        if last is not None and ttl > 0 and (time.monotonic() - last) < ttl:
            return
        try:
            s, token = ipc.connect(name, timeout=3.0)
            resp = ipc.request(s, token, {
                "method": "active_app",
                "params": {},
            })
            if isinstance(resp, dict) and "result" in resp:
                _ensure_ok_at[name] = time.monotonic()
                return
        except Exception:
            pass
        ensure_cache_bust(name)
        restart_daemon(name)
    else:
        ensure_cache_bust(name)

    _maybe_reconnect_wifi_device(env)
    cleanup_stale(name)

    e = {**os.environ, **({"ANH_NAME": name} if name else {}), **(env or {})}
    # ANH_DAEMON_MODULE is a test-only escape hatch; defaults to real daemon.
    module = e.get("ANH_DAEMON_MODULE", "android_harness.daemon")
    p = subprocess.Popen(
        [sys.executable, "-m", module],
        env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **ipc.spawn_kwargs(),
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        if daemon_alive(name):
            return
        if p.poll() is not None:
            break
        time.sleep(0.2)
    msg = _log_tail(name) or "(no log output)"
    raise RuntimeError(
        f"android-harness daemon didn't come up — last log lines:\n{msg}\n"
        f"Run `android-harness --doctor` to diagnose."
    )


def _log_tail(name=None, n=30):
    p = ipc.log_path(name or NAME)
    try:
        return "\n".join(p.read_text(encoding="utf-8").splitlines()[-n:])
    except FileNotFoundError:
        return ""


def restart_daemon(name=None):
    name = name or NAME
    pid_path = str(ipc.pid_path(name))
    daemon_pid = ipc.identify(name, timeout=1.0)

    if daemon_alive(name):
        try:
            s, _ = ipc.connect(name, timeout=2.0)
            try:
                ipc.request(s, None, {"meta": "shutdown"})
            finally:
                s.close()
        except Exception:
            pass

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not daemon_alive(name):
            break
        time.sleep(0.1)

    if daemon_alive(name) and daemon_pid:
        kill_pid(daemon_pid, hard=False)  # Windows-safe terminate (see _platform.kill_pid)
        deadline = time.time() + 2.0
        while time.time() < deadline and _pid_alive(daemon_pid):
            time.sleep(0.1)

    if daemon_pid and _pid_alive(daemon_pid):
        kill_pid(daemon_pid, hard=True)
        time.sleep(0.2)

    try: os.unlink(pid_path)
    except FileNotFoundError: pass
    ipc.cleanup_endpoint(name)


# ---- doctor ----------------------------------------------------------------

def _check_appium():
    try:
        with urllib.request.urlopen(f"{_appium_url()}/status", timeout=2.0) as r:
            data = r.read().decode()
            return True, data[:200]
    except urllib.error.URLError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _check_device():
    udid = os.environ.get("ANH_UDID")
    if not udid:
        return False, "ANH_UDID not set"
    try:
        out = subprocess.check_output(["adb", "devices"], timeout=5.0).decode().strip()
        lines = [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]
        if udid in lines:
            return True, f"connected ({udid})"
        return False, f"serial {udid} not in `adb devices`: {lines!r}"
    except FileNotFoundError:
        hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
        return False, f"`adb` not installed ({hint})"
    except Exception as e:
        return False, str(e)


# Parity with iphone_harness.admin — each returns (ok: bool, info: str).

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANDROID_REQUIRED_ENV = ("ANH_UDID",)


def _check_adb():
    """Return (True, path) if adb is on PATH. Distro-neutral check."""
    p = shutil.which("adb")
    if p is None:
        return False, "adb not on PATH"
    try:
        v = subprocess.check_output([p, "version"], timeout=3.0,
                                    stderr=subprocess.STDOUT).decode().strip().splitlines()[0]
        return True, v
    except Exception:
        return True, p


def _check_brew_pkg(pkg):
    """Legacy shim — kept so external callers + tests still import this."""
    if not is_macos():
        return True, "(skipped — non-macOS)"
    brew = shutil.which("brew")
    if brew is None:
        return False, "brew not installed"
    try:
        out = subprocess.check_output([brew, "list", "--versions", pkg],
                                      timeout=4.0, stderr=subprocess.DEVNULL).decode().strip()
        return (True, out) if out else (False, f"{pkg} not installed")
    except subprocess.CalledProcessError:
        return False, f"{pkg} not installed"
    except Exception as e:
        return False, str(e)


def _check_node():
    if shutil.which("node") is None:
        return False, "node not on PATH"
    if shutil.which("npm") is None:
        return False, "npm not on PATH"
    try:
        v = subprocess.check_output(["node", "--version"], timeout=3.0).decode().strip()
        return True, v
    except Exception as e:
        return False, str(e)


def _check_appium_installed():
    appium = shutil.which("appium")
    if appium is None:
        return False, "appium not on PATH"
    try:
        v = subprocess.check_output([appium, "--version"], timeout=4.0).decode().strip()
        return True, v
    except Exception as e:
        return False, str(e)


def _check_driver_installed(name):
    appium = shutil.which("appium")
    if appium is None:
        return False, "appium not on PATH"
    try:
        out = subprocess.check_output([appium, "driver", "list", "--installed"],
                                      timeout=10.0, stderr=subprocess.STDOUT).decode()
        if name in out:
            for line in out.splitlines():
                if name in line:
                    return True, line.strip()
            return True, "installed"
        return False, f"driver {name!r} not installed"
    except Exception as e:
        return False, str(e)


def _check_env_file():
    candidates = [REPO_ROOT / ".env", REPO_ROOT / "agent-workspace" / ".env"]
    found = next((p for p in candidates if p.exists()), None)
    if found is None:
        return False, "no .env at repo root or agent-workspace/"
    text = found.read_text(encoding="utf-8")
    missing = []
    for key in ANDROID_REQUIRED_ENV:
        ok = False
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(f"{key}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and not val.startswith("YOUR-"):
                    ok = True
                break
        if not ok:
            missing.append(key)
    if missing:
        return False, f"{found.name} missing/blank: {', '.join(missing)}"
    return True, str(found.relative_to(REPO_ROOT))


def _check_cli_on_path(name):
    p = shutil.which(name)
    if p is None:
        return False, f"{name} not on PATH"
    return True, p


def _cli_path_fix(cli_name, module_fallback):
    """Remediation for '<cli> not on PATH' that doesn't suggest reinstalling
    when the script is already installed: framework/user pip installs drop
    console scripts in a bin dir login shells often lack."""
    import sysconfig
    try:
        scripts = Path(sysconfig.get_path("scripts"))
        installed = (scripts / cli_name).exists() or (scripts / (cli_name + ".exe")).exists()
    except Exception:
        installed = False  # sysconfig scheme quirks must never break doctor
    if installed:
        return (f'installed at {scripts} which is not on PATH — add it: '
                f'export PATH="{scripts}:$PATH"  (or run `{module_fallback}`)')
    return f"pip install -e .  (puts CLI on PATH). Otherwise run `{module_fallback}`"


def _load_env_for_doctor():
    """Doctor sees the same .env the daemon loads (real env vars still win),
    so a filled .env can't pass the file check yet fail the device check.
    Strict sandboxes opt out with MOBILE_USE_NO_REPO_ENV=1."""
    if os.environ.get("MOBILE_USE_NO_REPO_ENV") == "1":
        return
    try:
        from .daemon import _load_env
        _load_env()
    except Exception:
        pass


def _check_python_pkg():
    try:
        subprocess.check_output([sys.executable, "-c", "import android_harness, mobile_use"],
                                timeout=5.0, stderr=subprocess.STDOUT)
        return True, "importable"
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="replace").strip().splitlines()[-1][:120]
    except Exception as e:
        return False, str(e)


def _check_battery():
    """Return (True, level%) if Android battery > 20%."""
    udid = os.environ.get("ANH_UDID")
    if shutil.which("adb") is None:
        return True, "(skipped — adb not on PATH)"
    cmd = ["adb"]
    if udid:
        cmd += ["-s", udid]
    cmd += ["shell", "dumpsys", "battery"]
    try:
        out = subprocess.check_output(cmd, timeout=5.0, stderr=subprocess.DEVNULL).decode()
        # `level: 73` line
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("level:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    level = int(raw)
                except (ValueError, TypeError):
                    return True, f"(skipped — battery level unreadable: {raw!r})"
                if level < 20:
                    return True, f"{level}% (WARN: low — plug in to avoid disconnect)"
                return True, f"{level}%"
        return True, "(skipped — level field missing)"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return True, "(skipped — battery info unavailable)"


def _check_screen_unlocked():
    """Return (True, state) if Android device screen is unlocked. (Doesn't fail if locked,
    just informs the user.)"""
    if shutil.which("adb") is None:
        return True, "(skipped — adb not on PATH)"
    udid = os.environ.get("ANH_UDID")
    cmd = ["adb"]
    if udid:
        cmd += ["-s", udid]
    cmd += ["shell", "dumpsys", "power"]
    try:
        out = subprocess.check_output(cmd, timeout=5.0, stderr=subprocess.DEVNULL).decode()
        if "mWakefulness=Awake" in out:
            return True, "screen on, awake"
        return True, "screen off (helpers will wake_device() before interacting)"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return True, "(skipped — power info unavailable)"


def _check_android_wifi_reachable(serial):
    """Preflight an adb-over-Wi-Fi serial (ip:port). (bool, detail)."""
    from mobile_use.netcheck import target_reachable
    return target_reachable(serial, default_port=5555, timeout=2.0)


def run_doctor():
    _load_env_for_doctor()
    print(f"android-harness {_version() or '(dev)'}\n")
    # Advisory version summary (support matrix + detected toolchain). Informational
    # only — out-of-range tooling warns here but never flips the doctor exit code.
    try:
        from mobile_use.versions import toolchain_summary_text
        print(toolchain_summary_text())
        print()
    except Exception:
        pass
    rc = 0

    adb_hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
    node_hint = install_hint("node", LINUX_NODE_PKGS)
    checks = [
        ("adb (Android Platform Tools)", _check_adb, (),
         adb_hint),
        ("Node.js + npm", _check_node, (),
         node_hint),
        ("Appium installed", _check_appium_installed, (),
         "npm i -g appium"),
        ("Appium uiautomator2 driver", _check_driver_installed, ("uiautomator2",),
         "appium driver install uiautomator2"),
        ("Python package installed (pip install -e .)", _check_python_pkg, (),
         "Run from repo root: pip install -e .  (or `mobile-use bootstrap`)"),
        ("`android-harness` CLI on PATH", _check_cli_on_path, ("android-harness",),
         _cli_path_fix("android-harness", "python3 -m android_harness.run")),
        (".env with ANH_UDID", _check_env_file, (),
         "Copy .env.example to .env and fill in.  Or: `mobile-use init`"),
        ("Appium server reachable", _check_appium, (),
         f"Start Appium with `appium --base-path /` (port 4723; URL: {_appium_url()})"),
        ("Android device connected + USB debugging authorized", _check_device, (),
         "Plug in Android, Settings → Developer options → USB debugging → On, tap Allow on prompt"),
        ("Device battery level (>20% recommended)", _check_battery, (),
         "Plug in the device to charge. Low battery causes USB disconnects."),
        ("Screen wakefulness", _check_screen_unlocked, (),
         "Press power button. Or use wake_device() helper before interacting."),
    ]
    # Wireless preflight: only when ANH_UDID is an adb-over-Wi-Fi serial (ip:port).
    # USB serials are opaque ids and skip this — nothing to reach over the network.
    _serial = os.environ.get("ANH_UDID")
    if _serial:
        from mobile_use.netcheck import looks_like_wifi_serial
        if looks_like_wifi_serial(_serial):
            checks.append((
                f"Device reachable over Wi-Fi ({_serial})",
                _check_android_wifi_reachable, (_serial,),
                "Run `mobile-use android wifi <ip>` over USB to (re)establish adb-over-Wi-Fi, "
                "and ensure the device + host share a network.",
            ))
    total = len(checks) + 2

    for i, (label, fn, args, fix) in enumerate(checks, start=1):
        print(f"[{i}/{total}] {label}")
        try:
            ok, info = fn(*args)
        except Exception as e:
            ok, info = False, f"check raised: {e!r}"
        print(f"   {'OK' if ok else 'FAIL'}: {info}")
        if not ok:
            print(f"   Fix: {fix}")
            rc = 1

    print(f"[{total - 1}/{total}] Daemon")
    if daemon_alive():
        pid = ipc.identify(NAME) or "?"
        print(f"   OK: alive (pid={pid}, sock={ipc.sock_addr(NAME)})")
    else:
        print("   not running (will spawn on first `android-harness -c`)")

    print(f"[{total}/{total}] Recent daemon log")
    tail = _log_tail()
    if tail:
        for line in tail.splitlines()[-10:]:
            print(f"   {line}")
    else:
        print("   (no log file yet)")

    if rc == 0:
        print("\nAll checks passed. Try: `android-harness -c 'print(active_app())'`")
    else:
        print("\nOne or more checks failed. Fix the FAIL lines above, then re-run `android-harness --doctor`.")
        print("Or run `mobile-use bootstrap` to install the missing system pieces.")
    return rc
