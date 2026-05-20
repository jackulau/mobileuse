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

    e = {**os.environ, **({"ANH_NAME": name} if name else {}), **(env or {})}
    p = subprocess.Popen(
        [sys.executable, "-m", "android_harness.daemon"],
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
        time.sleep(0.5)

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


def run_doctor():
    print(f"android-harness {_version() or '(dev)'}\n")
    rc = 0

    print("[1/4] Appium server")
    ok, info = _check_appium()
    print(f"   {'OK' if ok else 'FAIL'}: {APPIUM_URL}  -- {info}")
    if not ok:
        print("   Fix: start Appium with `appium --base-path /` (port 4723).")
        rc = 1

    print("[2/4] Device connection")
    ok, info = _check_device()
    print(f"   {'OK' if ok else 'FAIL'}: {info}")
    if not ok:
        print("   Fix: connect Android device, enable USB debugging, authorize this computer.")
        rc = 1

    print("[3/4] Daemon")
    if daemon_alive():
        pid = ipc.identify(NAME) or "?"
        print(f"   OK: alive (pid={pid}, sock={ipc.sock_addr(NAME)})")
    else:
        print(f"   not running (will spawn on first `android-harness -c`)")

    print("[4/4] Recent daemon log")
    tail = _log_tail()
    if tail:
        for line in tail.splitlines()[-10:]:
            print(f"   {line}")
    else:
        print("   (no log file yet)")

    return rc
