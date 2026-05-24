"""Daemon lifecycle, doctor, --reload for Android harness."""
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import _ipc as ipc

NAME = os.environ.get("ANH_NAME", "default")
APPIUM_URL = os.environ.get("ANH_APPIUM_URL", "http://127.0.0.1:4723")


def _version():
    try:
        from importlib.metadata import version
        return version("mobile-use")
    except Exception:
        return None


def daemon_alive(name=None):
    return ipc.ping(name or NAME, timeout=1.0)


def _pid_alive(pid):
    """True if a process with this pid exists."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def cleanup_stale(name=None):
    """Remove leftover .pid + .sock from a dead daemon (no process behind them)."""
    name = name or NAME
    pid_path = ipc.pid_path(name)
    sock_path_str = ipc.sock_addr(name)
    from pathlib import Path as _P
    sock_path = _P(sock_path_str)

    if ipc.ping(name, timeout=0.3):
        return False

    cleaned = False
    try:
        recorded = int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError):
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

    if sock_path.exists():
        try:
            sock_path.unlink()
            cleaned = True
        except FileNotFoundError:
            pass

    return cleaned


def ensure_daemon(wait=30.0, name=None, env=None):
    name = name or NAME
    if daemon_alive(name):
        try:
            s, token = ipc.connect(name, timeout=3.0)
            resp = ipc.request(s, token, {
                "method": "active_app",
                "params": {},
            })
            if isinstance(resp, dict) and "result" in resp:
                return
        except Exception:
            pass
        restart_daemon(name)

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
        return "\n".join(p.read_text().splitlines()[-n:])
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
        try:
            os.kill(daemon_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline and _pid_alive(daemon_pid):
            time.sleep(0.1)

    if daemon_pid and _pid_alive(daemon_pid):
        try:
            os.kill(daemon_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        time.sleep(0.2)

    try: os.unlink(pid_path)
    except FileNotFoundError: pass
    ipc.cleanup_endpoint(name)


# ---- doctor ----------------------------------------------------------------

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
        return False, "`adb` not installed (brew install android-platform-tools)"
    except Exception as e:
        return False, str(e)


# Parity with iphone_harness.admin — each returns (ok: bool, info: str).

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANDROID_REQUIRED_ENV = ("ANH_UDID",)


def _check_brew_pkg(pkg):
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
    text = found.read_text()
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
                level = int(line.split(":", 1)[1].strip())
                if level < 20:
                    return False, f"{level}% (low — plug in to avoid disconnect)"
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


def run_doctor():
    print(f"android-harness {_version() or '(dev)'}\n")
    rc = 0

    checks = [
        ("Homebrew package: android-platform-tools (adb)", _check_brew_pkg, ("android-platform-tools",),
         "brew install android-platform-tools"),
        ("Node.js + npm", _check_node, (),
         "brew install node"),
        ("Appium installed", _check_appium_installed, (),
         "npm i -g appium"),
        ("Appium uiautomator2 driver", _check_driver_installed, ("uiautomator2",),
         "appium driver install uiautomator2"),
        ("Python package installed (pip install -e .)", _check_python_pkg, (),
         "Run from repo root: pip install -e .  (or `mobile-use bootstrap`)"),
        ("`android-harness` CLI on PATH", _check_cli_on_path, ("android-harness",),
         "pip install -e .  (puts CLI on PATH). Otherwise run `python3 -m android_harness.run`"),
        (".env with ANH_UDID", _check_env_file, (),
         "Copy .env.example to .env and fill in.  Or: `mobile-use init`"),
        ("Appium server reachable", _check_appium, (),
         f"Start Appium with `appium --base-path /` (port 4723; URL: {APPIUM_URL})"),
        ("Android device connected + USB debugging authorized", _check_device, (),
         "Plug in Android, Settings → Developer options → USB debugging → On, tap Allow on prompt"),
        ("Device battery level (>20% recommended)", _check_battery, (),
         "Plug in the device to charge. Low battery causes USB disconnects."),
        ("Screen wakefulness", _check_screen_unlocked, (),
         "Press power button. Or use wake_device() helper before interacting."),
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
        print(f"   not running (will spawn on first `android-harness -c`)")

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
