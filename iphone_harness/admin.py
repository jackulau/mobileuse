"""Daemon lifecycle, doctor, --reload — part of mobile-use.

The agent-facing functions are:
  - ensure_daemon()      idempotent — spawns the daemon if not running
  - restart_daemon()     stops the running daemon (next call respawns)
  - run_doctor()         diagnostic: Appium up, device paired, WDA trusted, daemon healthy
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mobile_use._platform import (
    LINUX_LIBIMOBILEDEVICE_PKGS,
    LINUX_NODE_PKGS,
    install_hint,
    is_linux,
    is_macos,
    kill_pid,
    process_exists,
)

from . import _ipc as ipc

NAME = os.environ.get("IPH_NAME", "default")
APPIUM_URL = os.environ.get("IPH_APPIUM_URL", "http://127.0.0.1:4723")


def is_remote_daemon(name=None):
    """True when IPH_CONNECT points at a daemon we don't manage locally
    (Windows/Linux client-only mode driving a remote Mac).

    Heuristic: IPH_CONNECT is set AND parses as a TCP endpoint. We trust the
    operator's intent — if they set IPH_CONNECT=tcp://127.0.0.1:8763 with a
    daemon running on the same host, that's still considered "remote" from
    admin's perspective (ensure_daemon won't try to spawn locally; user is
    responsible for the daemon). This keeps the rule simple: TCP = manual.
    """
    spec = os.environ.get("IPH_CONNECT")
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
    # isinstance(True, int) is True in Python — reject bool to avoid probing pid 1/True.
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return process_exists(pid)


def cleanup_stale(name=None):
    """Remove leftover .pid + (for AF_UNIX) .sock from a dead daemon.

    Called before spawn to keep stale state from a kill -9 / OOM / crash from
    confusing the next ensure_daemon. Safe to call when no files exist or when
    a live daemon owns them. TCP endpoints (IPH_BIND=tcp://...) have no socket
    file to clean — only the pidfile.
    """
    name = name or NAME
    pid_path = ipc.pid_path(name)
    endpoint = ipc.bind_endpoint(name)

    # If a live daemon is responding, leave everything alone.
    if ipc.ping(name, timeout=0.3):
        return False

    cleaned = False
    # Read PID file if present; only unlink if the recorded process is gone.
    # Tolerate binary garbage (UnicodeDecodeError) + empty string (ValueError) +
    # permission-denied (PermissionError) — all mean "treat as stale, remove".
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
# pattern — without this, EVERY ensure_daemon call cost an activeAppInfo RPC
# even when the daemon was verified moments ago.
_ENSURE_TTL_DEFAULT = 10.0
_ensure_ok_at = {}        # name -> time.monotonic() of last VERIFIED deep probe


def _ensure_ttl():
    try:
        return float(os.environ.get("IPH_ENSURE_TTL", str(_ENSURE_TTL_DEFAULT)))
    except (TypeError, ValueError):
        return _ENSURE_TTL_DEFAULT


def ensure_cache_bust(name=None):
    """Forget the last verified ensure (one name, or all) so the next
    ensure_daemon runs the full deep probe. Called on stale-session signals."""
    if name is None:
        _ensure_ok_at.clear()
    else:
        _ensure_ok_at.pop(name, None)


def ensure_daemon(wait=30.0, name=None, env=None):
    """Spawn the daemon if no live one is reachable. Idempotent.

    In client-only mode (IPH_CONNECT=tcp://<remote>:port — e.g. from a Windows
    or Linux host driving a remote Mac), this function NEVER spawns a local
    daemon. It only pings; on failure it raises a remote-side checklist.
    """
    name = name or NAME

    if is_remote_daemon(name):
        spec = os.environ.get("IPH_CONNECT", "")
        if daemon_alive(name):
            return
        raise RuntimeError(
            f"iphone-harness: remote daemon unreachable at {spec}.\n"
            f"  This host is in client-only mode (IPH_CONNECT set).\n"
            f"  Checks on the remote Mac:\n"
            f"    - daemon running?  (ssh mac 'pgrep -fa iphone_harness.daemon')\n"
            f"    - bound to TCP?    (ssh mac 'lsof -iTCP -sTCP:LISTEN | grep python')\n"
            f"    - reachable port?  (Windows: `Test-NetConnection -ComputerName <mac> -Port <port>`)\n"
            f"  Tip: prefer `ssh -L 8763:127.0.0.1:8763 <mac>` over exposing the\n"
            f"  daemon on 0.0.0.0 — the RPC is unauthenticated."
        )

    if daemon_alive(name):
        # A verified deep probe within the TTL is trusted — the local liveness
        # ping above still ran (so a dead daemon always falls through to spawn),
        # only the device round-trip is skipped.
        last = _ensure_ok_at.get(name)
        ttl = _ensure_ttl()
        if last is not None and ttl > 0 and (time.monotonic() - last) < ttl:
            return
        # Live ping is enough — but verify the Appium-side handshake too. A
        # daemon whose webdriver session died still answers meta:* but errors
        # on real method calls. Probe with `mobile: activeAppInfo` (cheap, real call).
        try:
            s, token = ipc.connect(name, timeout=3.0)
            resp = ipc.request(s, token, {
                "method": "appium",
                "params": {"script": "mobile: activeAppInfo", "args": {}},
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

    # Stale .sock or .pid from a hard-killed previous daemon would confuse spawn.
    cleanup_stale(name)

    e = {**os.environ, **({"IPH_NAME": name} if name else {}), **(env or {})}
    # IPH_DAEMON_MODULE is a test-only escape hatch; defaults to real daemon.
    module = e.get("IPH_DAEMON_MODULE", "iphone_harness.daemon")
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
        f"iphone-harness daemon didn't come up — last log lines:\n{msg}\n"
        f"Run `iphone-harness --doctor` to diagnose."
    )


def _log_tail(name=None, n=30):
    p = ipc.log_path(name or NAME)
    try:
        return "\n".join(p.read_text(encoding="utf-8").splitlines()[-n:])
    except FileNotFoundError:
        return ""


def restart_daemon(name=None):
    """Best-effort daemon shutdown + cleanup. Verifies identity before signaling."""
    name = name or NAME
    pid_path = str(ipc.pid_path(name))

    daemon_pid = ipc.identify(name, timeout=1.0)

    # Step 1: ask the daemon to shut down via IPC if it's reachable.
    if daemon_alive(name):
        try:
            s, _ = ipc.connect(name, timeout=2.0)
            try:
                ipc.request(s, None, {"meta": "shutdown"})
            finally:
                s.close()
        except Exception:
            pass

    # Step 2: wait briefly for it to exit gracefully.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not daemon_alive(name):
            break
        time.sleep(0.1)

    # Step 3: if still alive AND we verified identity, escalate to SIGTERM.
    # (Windows-safe hard kill via _platform.kill_pid.)
    if daemon_alive(name) and daemon_pid:
        kill_pid(daemon_pid, hard=False)
        # Wait up to 2s for SIGTERM to settle.
        deadline = time.time() + 2.0
        while time.time() < deadline and _pid_alive(daemon_pid):
            time.sleep(0.1)

    # Step 4: hard-kill the daemon if it's still alive after SIGTERM.
    if daemon_pid and _pid_alive(daemon_pid):
        kill_pid(daemon_pid, hard=True)
        # Reap zombie state on local process.
        time.sleep(0.2)

    # Step 5: cleanup pid + sock files.
    try: os.unlink(pid_path)
    except FileNotFoundError: pass
    ipc.cleanup_endpoint(name)


# ---- doctor ----------------------------------------------------------------
#
# Each `_check_*` returns (ok: bool, info: str). `info` is a one-line state
# string ("paired (UUID)", "missing", "version 2.18.0"). `run_doctor` prints
# numbered lines and a remediation per FAIL.

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

IOS_REQUIRED_ENV = ("IPH_UDID", "IPH_XCODE_ORG_ID", "IPH_WDA_BUNDLE_ID")


def _check_appium():
    try:
        with urllib.request.urlopen(f"{APPIUM_URL}/status", timeout=2.0) as r:
            data = r.read().decode()
            return True, data[:200]
    except urllib.error.URLError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _check_device():
    udid = os.environ.get("IPH_UDID")
    if not udid:
        return False, "IPH_UDID not set"
    try:
        out = subprocess.check_output(["idevice_id", "-l"], timeout=5.0).decode().strip().splitlines()
        if udid in out:
            return True, f"paired ({udid})"
        return False, f"udid {udid} not in `idevice_id -l`: {out!r}"
    except FileNotFoundError:
        hint = install_hint("libimobiledevice", LINUX_LIBIMOBILEDEVICE_PKGS)
        return False, f"`idevice_id` not installed ({hint})"
    except Exception as e:
        return False, str(e)


def _check_libimobiledevice():
    """Return (True, info) if libimobiledevice tools are available.

    On macOS: asks Homebrew. On Linux: checks `idevice_id` on PATH (typical
    apt/dnf/pacman libimobiledevice package installs this binary). Linux
    users don't have brew — checking the binary directly is the honest
    test for "are the tools usable".
    """
    if is_macos():
        brew = shutil.which("brew")
        if brew is None:
            return False, "brew not installed"
        try:
            out = subprocess.check_output([brew, "list", "--versions", "libimobiledevice"],
                                          timeout=4.0, stderr=subprocess.DEVNULL).decode().strip()
            return (True, out) if out else (False, "libimobiledevice not installed")
        except subprocess.CalledProcessError:
            return False, "libimobiledevice not installed"
        except Exception as e:
            return False, str(e)
    if is_linux():
        if shutil.which("idevice_id") is None:
            return False, "libimobiledevice (`idevice_id`) not on PATH"
        return True, shutil.which("idevice_id")
    return True, "(skipped — non-macOS / non-Linux host)"


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
    """Return (True, version) if node + npm are on PATH."""
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
    """Return (True, version) if `appium --version` works."""
    appium = shutil.which("appium")
    if appium is None:
        return False, "appium not on PATH"
    try:
        v = subprocess.check_output([appium, "--version"], timeout=4.0).decode().strip()
        return True, v
    except Exception as e:
        return False, str(e)


def _check_driver_installed(name):
    """Return (True, info) if Appium has the named driver installed."""
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
    """Return (True, path) if a .env exists with all IOS_REQUIRED_ENV filled."""
    candidates = [REPO_ROOT / ".env", REPO_ROOT / "agent-workspace" / ".env"]
    found = next((p for p in candidates if p.exists()), None)
    if found is None:
        return False, "no .env at repo root or agent-workspace/"
    text = found.read_text(encoding="utf-8")
    missing = []
    for key in IOS_REQUIRED_ENV:
        # Line that begins with `<KEY>=` and isn't immediately followed by a
        # placeholder marker.
        ok = False
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(f"{key}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and not val.startswith("YOUR-") and val != "YOURTEAMID":
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


def _check_python_pkg():
    try:
        subprocess.check_output([sys.executable, "-c", "import iphone_harness, mobile_use"],
                                timeout=5.0, stderr=subprocess.STDOUT)
        return True, "importable"
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="replace").strip().splitlines()[-1][:120]
    except Exception as e:
        return False, str(e)


def _check_xcode():
    """Return (True, version) if Xcode is selected. Auto-skipped off macOS."""
    if not is_macos():
        return True, "(skipped — Xcode is macOS-only; drive iOS from Linux via remote IPH_APPIUM_URL)"
    if shutil.which("xcodebuild") is None:
        return False, "xcodebuild not on PATH"
    try:
        v = subprocess.check_output(["xcodebuild", "-version"], timeout=4.0,
                                    stderr=subprocess.STDOUT).decode().strip().splitlines()[0]
        return True, v
    except Exception as e:
        return False, str(e)


def _check_wda_signing():
    """Return (True, state) if WDA is signed. Auto-skipped off macOS — signing is Xcode-only."""
    if not is_macos():
        return True, "(skipped — WDA signing requires macOS + Xcode)"
    try:
        from mobile_use.ios_wda import check_wda_signing
        state, details = check_wda_signing()
        if state == "signed":
            return True, details
        return False, f"{state}: {details}"
    except Exception as e:
        return False, f"WDA check failed: {e}"


def _check_battery():
    """Return (True, level%) if device battery > 20%. Falls back to skipped if no tool."""
    udid = os.environ.get("IPH_UDID")
    if not udid:
        return True, "(skipped — IPH_UDID not set)"
    if shutil.which("ideviceinfo") is None:
        return True, "(skipped — ideviceinfo not installed)"
    try:
        # `ideviceinfo -k BatteryCurrentCapacity` returns 0-100
        out = subprocess.check_output(
            ["ideviceinfo", "-u", udid, "-q", "com.apple.mobile.battery", "-k", "BatteryCurrentCapacity"],
            timeout=5.0, stderr=subprocess.DEVNULL,
        ).decode().strip()
        try:
            level = int(out)
        except (ValueError, TypeError):
            return True, f"(skipped — battery level unreadable: {out!r})"
        if level < 20:
            return True, f"{level}% (WARN: low — plug in to avoid disconnect)"
        return True, f"{level}%"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return True, "(skipped — battery info unavailable)"


def _check_wda_url_reachable(url):
    """Preflight a wireless WebDriverAgent endpoint (IPH_WDA_URL). (bool, detail).

    Handles mDNS hostnames (``http://iPhone.local:8100``) transparently — the OS
    Bonjour resolver turns ``.local`` into an address inside ``socket`` — which is
    the path confirmed to work when a raw Wi-Fi IP was on an unrouted subnet.
    """
    from mobile_use.netcheck import target_reachable
    return target_reachable(url, default_port=8100, timeout=2.0)


def _check_cablefree_tunnel():
    """Advisory: is the RemoteXPC tunnel up so cable-free survives unplug? (bool, detail).

    Never a hard FAIL — Appium's bundled appium-ios-remotexpc can also provide the
    tunnel, so a down pymobiledevice3 tunneld is only a WARN (mirrors the battery
    check's WARN style). The point is to catch "WDA reachable now over USB, but the
    moment you unplug it dies" before the user hits it mid-session.
    """
    try:
        from mobile_use.devices import _pymobiledevice3_available, tunneld_status
    except Exception:
        return True, "(skipped — devices module unavailable)"
    if not _pymobiledevice3_available():
        return True, "(skipped — pymobiledevice3 not installed; Appium may tunnel instead)"
    up, detail, _tunnels = tunneld_status()
    if up:
        return True, f"RemoteXPC tunnel up ({detail})"
    return True, ("tunnel DOWN (WARN: on iOS 17+ cable-free drops when USB is unplugged — "
                  "start it: `mobile-use ios tunnel`)")


def run_doctor():
    """Diagnostic. Prints status of each external dependency. Returns 0 on all-green."""
    print(f"iphone-harness {_version() or '(dev)'}\n")
    # Advisory version summary (support matrix + detected toolchain). Informational
    # only — out-of-range tooling warns here but never flips the doctor exit code.
    try:
        from mobile_use.versions import toolchain_summary_text
        print(toolchain_summary_text())
        print()
    except Exception:
        pass
    rc = 0

    libimobiledevice_hint = install_hint("libimobiledevice ideviceinstaller", LINUX_LIBIMOBILEDEVICE_PKGS)
    node_hint = install_hint("node", LINUX_NODE_PKGS)
    xcode_hint = ("(skipped — Linux host. To drive iOS from Linux, set "
                  "IPH_APPIUM_URL=http://<your-mac>:4723 and run Appium on the Mac.)"
                  if is_linux() else
                  "Install Xcode from App Store, then `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`")
    checks = [
        ("libimobiledevice + ideviceinstaller", _check_libimobiledevice, (),
         libimobiledevice_hint),
        ("Node.js + npm", _check_node, (),
         node_hint),
        ("Appium installed", _check_appium_installed, (),
         "npm i -g appium"),
        ("Appium xcuitest driver", _check_driver_installed, ("xcuitest",),
         "appium driver install xcuitest  (or: appium-xcuitest-driver@10.43.1)"),
        ("Xcode (selected)", _check_xcode, (),
         xcode_hint),
        ("Python package installed (pip install -e .)", _check_python_pkg, (),
         "Run from repo root: pip install -e .  (or `mobile-use bootstrap`)"),
        ("`iphone-harness` CLI on PATH", _check_cli_on_path, ("iphone-harness",),
         "pip install -e .  (puts CLI on PATH). Otherwise run `python3 -m iphone_harness.run`"),
        (".env with IPH_UDID / IPH_XCODE_ORG_ID / IPH_WDA_BUNDLE_ID", _check_env_file, (),
         "Copy .env.example to .env and fill in.  Or: `mobile-use init`"),
        ("Appium server reachable", _check_appium, (),
         f"Start Appium with `appium --base-path /` (port 4723; URL: {APPIUM_URL})"),
        ("iPhone paired + Developer Mode on", _check_device, (),
         "Plug in iPhone, `Trust This Computer` if prompted, Settings → Privacy & Security → Developer Mode → On"),
        ("WebDriverAgent signed (provisioning profile present + not expired)", _check_wda_signing, (),
         "Run `mobile-use ios sign-wda` — opens Xcode for the 6-step signing dance."),
        ("Device battery level (>20% recommended)", _check_battery, (),
         "Plug in the iPhone to charge. Low battery causes USB disconnects during long sessions."),
    ]
    # Wireless preflight: only when IPH_WDA_URL is configured. A configured-but-
    # unreachable WDA is a real FAIL (it would hang the session create otherwise).
    _wda_url = os.environ.get("IPH_WDA_URL")
    if _wda_url:
        checks.append((
            f"WebDriverAgent reachable over Wi-Fi ({_wda_url})",
            _check_wda_url_reachable, (_wda_url,),
            "Ensure WDA is running on the iPhone and the Mac shares its network. "
            "On iOS 17+, start the RemoteXPC tunnel first (see `--doctor` version block).",
        ))
        # Cable-free needs the tunnel up to survive unplug (iOS 17+). Advisory.
        checks.append((
            "Cable-free survives unplug (RemoteXPC tunnel)",
            _check_cablefree_tunnel, (),
            "Start the tunnel and keep it running: `mobile-use ios tunnel` "
            "(prints the one `sudo pymobiledevice3 remote tunneld` step).",
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
        print("   not running (will spawn on first `iphone-harness -c`)")

    print(f"[{total}/{total}] Recent daemon log")
    tail = _log_tail()
    if tail:
        for line in tail.splitlines()[-10:]:
            print(f"   {line}")
    else:
        print("   (no log file yet)")

    if rc == 0:
        print("\nAll checks passed. Try: `iphone-harness -c 'print(active_app())'`")
    else:
        print("\nOne or more checks failed. Fix the FAIL lines above, then re-run `iphone-harness --doctor`.")
        print("Or run `mobile-use bootstrap` to install the missing system pieces.")
    return rc
