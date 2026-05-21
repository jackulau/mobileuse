"""Daemon lifecycle, doctor, --reload — part of mobile-use.

The agent-facing functions are:
  - ensure_daemon()      idempotent — spawns the daemon if not running
  - restart_daemon()     stops the running daemon (next call respawns)
  - run_doctor()         diagnostic: Appium up, device paired, WDA trusted, daemon healthy
"""
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import _ipc as ipc

NAME = os.environ.get("IPH_NAME", "default")
APPIUM_URL = os.environ.get("IPH_APPIUM_URL", "http://127.0.0.1:4723")


def _version():
    try:
        from importlib.metadata import version
        return version("mobile-use")
    except Exception:
        return None


def daemon_alive(name=None):
    return ipc.ping(name or NAME, timeout=1.0)


def ensure_daemon(wait=30.0, name=None, env=None):
    """Spawn the daemon if no live one is reachable. Idempotent."""
    name = name or NAME
    if daemon_alive(name):
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
                return
        except Exception:
            pass
        restart_daemon(name)

    e = {**os.environ, **({"IPH_NAME": name} if name else {}), **(env or {})}
    p = subprocess.Popen(
        [sys.executable, "-m", "iphone_harness.daemon"],
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
        return "\n".join(p.read_text().splitlines()[-n:])
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
    if daemon_alive(name) and daemon_pid:
        try:
            os.kill(daemon_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        time.sleep(0.5)

    # Step 4: cleanup pid + sock files.
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
        return False, "`idevice_id` not installed (brew install libimobiledevice)"
    except Exception as e:
        return False, str(e)


def _check_brew_pkg(pkg):
    """Return (True, version) if Homebrew has `pkg` installed, else False."""
    if sys.platform != "darwin":
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
    text = found.read_text()
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
    """Return (True, version) if Xcode is selected (`xcodebuild -version`)."""
    if sys.platform != "darwin":
        return True, "(skipped — non-macOS)"
    if shutil.which("xcodebuild") is None:
        return False, "xcodebuild not on PATH"
    try:
        v = subprocess.check_output(["xcodebuild", "-version"], timeout=4.0,
                                    stderr=subprocess.STDOUT).decode().strip().splitlines()[0]
        return True, v
    except Exception as e:
        return False, str(e)


def run_doctor():
    """Diagnostic. Prints status of each external dependency. Returns 0 on all-green."""
    print(f"iphone-harness {_version() or '(dev)'}\n")
    rc = 0

    checks = [
        ("Homebrew package: libimobiledevice", _check_brew_pkg, ("libimobiledevice",),
         "brew install libimobiledevice ideviceinstaller"),
        ("Node.js + npm", _check_node, (),
         "brew install node"),
        ("Appium installed", _check_appium_installed, (),
         "npm i -g appium"),
        ("Appium xcuitest driver", _check_driver_installed, ("xcuitest",),
         "appium driver install xcuitest  (or: appium-xcuitest-driver@10.43.1)"),
        ("Xcode (selected)", _check_xcode, (),
         "Install Xcode from App Store, then `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`"),
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
    ]
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
        print(f"   not running (will spawn on first `iphone-harness -c`)")

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
