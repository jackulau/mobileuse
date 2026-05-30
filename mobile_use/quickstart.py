"""`mobile-use quickstart` — full first-run smoke.

Two phases. Each phase short-circuits the whole thing on failure with the
single most-actionable next step.

  1. Doctor: imports the platform's run_doctor() and runs it. Non-zero exit
     means a dependency is missing. We don't try to "fix" anything from this
     command — that's `mobile-use bootstrap`'s job. We just point at the
     next-best command.

  2. Smoke: ensures the daemon is up; calls `active_app()` and `screenshot()`.
     If either fails, prints the underlying error and points at `--doctor`.

Auto-detects platform if one device is paired; otherwise requires
--ios / --android.
"""
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from mobile_use._platform import is_linux, is_macos

APPIUM_URL = os.environ.get("IPH_APPIUM_URL") or os.environ.get("ANH_APPIUM_URL") or "http://127.0.0.1:4723"


def appium_reachable(url=None, timeout=1.5):
    """True if the Appium server responds on /status. False on any error."""
    url = (url or APPIUM_URL).rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, ConnectionError, OSError):
        return False
    except Exception:
        return False


def run_appium_phase(*, autostart=False):
    """Preflight: check Appium server is up at 4723. Returns (ok, msg).

    If `autostart=True` and Appium isn't reachable but the CLI is installed,
    spawn `appium --base-path /` detached and re-check. Otherwise print the
    exact command the user needs to run.
    """
    if appium_reachable():
        return True, f"Appium server reachable at {APPIUM_URL}"

    appium_cli = shutil.which("appium")
    if appium_cli is None:
        return False, (
            "Appium server not reachable AND `appium` CLI not installed.\n"
            "   Fix: run `mobile-use bootstrap` to install Appium + drivers."
        )

    if not autostart:
        return False, (
            f"Appium server not running on {APPIUM_URL}.\n"
            "   Fix: open a separate terminal and run:\n"
            "     appium --base-path /\n"
            "   Or re-run quickstart with --autostart-appium to spawn it for you."
        )

    # Try to start it. Detach so we don't block on its lifetime.
    log_path = os.path.join("/tmp", "mobile-use-appium.log")
    try:
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [appium_cli, "--base-path", "/"],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception as e:
        return False, f"failed to start Appium: {e}"

    # Wait up to ~6s for it to come up.
    import time
    for _ in range(12):
        time.sleep(0.5)
        if appium_reachable():
            return True, f"Appium server started (log: {log_path})"
    return False, (
        f"Spawned Appium but it didn't become reachable in 6s.\n"
        f"   Check {log_path} for errors."
    )


def _detect_platform():
    # Mirror mobile_use.cli._detect_platform without re-importing the world.
    ios = bool(os.environ.get("IPH_UDID"))
    android = bool(os.environ.get("ANH_UDID"))
    if not ios and shutil.which("idevice_id"):
        try:
            ios = bool(subprocess.check_output(["idevice_id", "-l"], timeout=1.5,
                                               stderr=subprocess.DEVNULL).decode().strip())
        except Exception:
            pass
    # A booted iOS Simulator counts as a connected iOS target (XCUITest drives it).
    if not ios and shutil.which("xcrun"):
        try:
            out = subprocess.check_output(
                ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                timeout=3.0, stderr=subprocess.DEVNULL).decode()
            ios = '"state" : "Booted"' in out or '"state": "Booted"' in out
        except Exception:
            pass
    if not android and shutil.which("adb"):
        try:
            out = subprocess.check_output(["adb", "devices"], timeout=1.5,
                                           stderr=subprocess.DEVNULL).decode().strip()
            android = any("\tdevice" in l for l in out.splitlines()[1:])
        except Exception:
            pass
    if ios and not android:
        return "ios"
    if android and not ios:
        return "android"
    return None


def run_doctor_phase(platform):
    """Returns (ok: bool, msg: str)."""
    if platform == "ios":
        from iphone_harness.admin import run_doctor
    else:
        from android_harness.admin import run_doctor
    rc = run_doctor()
    if rc == 0:
        return True, ""
    return False, ("doctor reported failures (see above). Run "
                   "`mobile-use bootstrap` to install missing deps, then re-run.")


def run_smoke_phase(platform):
    """Returns (ok: bool, msg: str). Hits the device via the daemon."""
    print(f"\n[smoke] {platform}: ensure daemon + call active_app() + screenshot()")
    if platform == "ios":
        from iphone_harness import helpers as h
        from iphone_harness.admin import ensure_daemon
    else:
        from android_harness import helpers as h
        from android_harness.admin import ensure_daemon
    try:
        ensure_daemon()
    except Exception as e:
        return False, (f"daemon did not come up: {e}.\n"
                       f"   Fix: run `{platform[0]}phone-harness --doctor` "
                       "and address the first FAIL.")

    try:
        app = h.active_app()
    except Exception as e:
        return False, (f"active_app() failed: {e}.\n"
                       "   Most often: device sleeping (unlock it) or "
                       "Appium session died (--reload).")

    try:
        path = h.screenshot()
    except Exception as e:
        return False, f"screenshot() failed: {e}."

    print(f"  active_app = {app}")
    print(f"  screenshot = {path}")
    return True, ""


def main(argv=None, *, platform=None):
    import argparse
    p = argparse.ArgumentParser(prog="mobile-use quickstart",
                                description="Doctor + smoke. Proves the whole chain works.")
    p.add_argument("--ios", action="store_const", const="ios", dest="platform")
    p.add_argument("--android", action="store_const", const="android", dest="platform")
    p.add_argument("--skip-doctor", action="store_true",
                   help="Jump straight to the smoke test.")
    p.add_argument("--autostart-appium", action="store_true",
                   help="If Appium server is down, spawn it in the background.")
    args = p.parse_args(argv)

    platform = platform or args.platform or _detect_platform()
    if platform is None:
        print("Cannot detect platform. Either:\n"
              "  - Connect one device (iPhone or Android), or\n"
              "  - Pass --ios or --android\n"
              "Then re-run `mobile-use quickstart`.", file=sys.stderr)
        return 2

    # Linux-on-iOS: clearly explain the remote-Mac requirement before doctor
    # noise. Without a remote Appium URL, the local checks can't possibly pass.
    if platform == "ios" and is_linux():
        iph_url = os.environ.get("IPH_APPIUM_URL", "")
        looks_local = (not iph_url) or any(loc in iph_url for loc in
                                            ("127.0.0.1", "localhost", "::1"))
        if looks_local:
            print("mobile-use quickstart --ios on Linux requires a remote macOS Appium server.")
            print()
            print("  Quick setup:")
            print("    1. On a Mac with Xcode + WDA signed:")
            print("         IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'")
            print("    2. From this Linux host (SSH tunnel):")
            print("         ssh -L 8763:127.0.0.1:8763 <mac-host>")
            print("    3. Re-run with remote daemon URL:")
            print("         mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c '...'")
            print()
            print("  See SETUP.md → 'iOS from Windows / Linux'.")
            return 2

    print(f"mobile-use quickstart  ({platform})")
    print("=" * 60)

    print(f"[preflight] Appium server on {APPIUM_URL}")
    ok, msg = run_appium_phase(autostart=args.autostart_appium)
    print(f"  {msg}")
    if not ok:
        print(f"\n[abort] {msg}")
        return 1

    if not args.skip_doctor:
        ok, msg = run_doctor_phase(platform)
        if not ok:
            print(f"\n[abort] {msg}")
            return 1

    ok, msg = run_smoke_phase(platform)
    if not ok:
        print(f"\n[abort] {msg}")
        return 1

    print("\nQuickstart passed. mobile-use is ready.")
    print("Try a cleanup demo:")
    if platform == "ios":
        print("  DRY_RUN=1 python3 docs/demos/clean-and-organize-ios.py")
    else:
        print("  DRY_RUN=1 python3 docs/demos/clean-and-organize-android.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
