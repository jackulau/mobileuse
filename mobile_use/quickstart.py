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
                   f"`mobile-use bootstrap` to install missing deps, then re-run.")


def run_smoke_phase(platform):
    """Returns (ok: bool, msg: str). Hits the device via the daemon."""
    print(f"\n[smoke] {platform}: ensure daemon + call active_app() + screenshot()")
    if platform == "ios":
        from iphone_harness.admin import ensure_daemon
        from iphone_harness import helpers as h
    else:
        from android_harness.admin import ensure_daemon
        from android_harness import helpers as h
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
    args = p.parse_args(argv)

    platform = platform or args.platform or _detect_platform()
    if platform is None:
        print("Cannot detect platform. Either:\n"
              "  - Connect one device (iPhone or Android), or\n"
              "  - Pass --ios or --android\n"
              "Then re-run `mobile-use quickstart`.", file=sys.stderr)
        return 2

    print(f"mobile-use quickstart  ({platform})")
    print("=" * 60)

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
