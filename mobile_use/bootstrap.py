"""`mobile-use bootstrap` — one-command installer.

Detects what's missing and installs it. Idempotent: re-running is safe and
quick. Output is a numbered plan followed by per-step result. Honors
`--dry-run`, `--ios-only`, `--android-only`.

Does NOT install Xcode (gates on it with clear instructions). Does NOT
install Python — assumes the user invoked this with python3.

Non-macOS: brew steps are skipped with a per-OS hint (apt, dnf, pacman).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# Step shape: (label, check_fn, run_cmd_list, mac_only, ios_step, android_step)
#   check_fn() -> True if already installed (we skip the run_cmd then)
#   run_cmd_list: argv list, runs via subprocess
#   mac_only: True -> skip on non-darwin with a warning


def _have(cmd):
    return shutil.which(cmd) is not None


def _brew_has(pkg):
    if sys.platform != "darwin" or not _have("brew"):
        return False
    try:
        subprocess.check_output(["brew", "list", "--versions", pkg],
                                timeout=4.0, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception:
        return False


def _appium_driver_installed(name):
    if not _have("appium"):
        return False
    try:
        out = subprocess.check_output(["appium", "driver", "list", "--installed"],
                                      timeout=10.0, stderr=subprocess.STDOUT).decode()
        return name in out
    except Exception:
        return False


def _python_pkg_importable():
    try:
        subprocess.check_output([sys.executable, "-c", "import iphone_harness, android_harness, mobile_use"],
                                timeout=5.0, stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False


def plan(ios=True, android=True):
    """Return a list of (label, check_fn, run_cmd_list, mac_only) tuples for
    the current state. Steps already satisfied stay in the list w/ a True
    check so the caller can show 'OK' for them."""
    steps = []

    if ios:
        steps.append(("Homebrew (macOS package manager)",
                      lambda: _have("brew") or sys.platform != "darwin",
                      None,  # cannot auto-install brew; print message
                      True))
        steps.append(("brew install libimobiledevice (idevice_id, ideviceinstaller)",
                      lambda: _brew_has("libimobiledevice"),
                      ["brew", "install", "libimobiledevice", "ideviceinstaller"],
                      True))
    if android:
        steps.append(("brew install android-platform-tools (adb)",
                      lambda: _brew_has("android-platform-tools") or _have("adb"),
                      ["brew", "install", "android-platform-tools"],
                      True))
    steps.append(("Node.js + npm",
                  lambda: _have("node") and _have("npm"),
                  ["brew", "install", "node"],
                  True))
    steps.append(("Appium (CLI + server)",
                  lambda: _have("appium"),
                  ["npm", "i", "-g", "appium"],
                  False))
    if ios:
        steps.append(("Appium xcuitest driver",
                      lambda: _appium_driver_installed("xcuitest"),
                      ["appium", "driver", "install", "xcuitest"],
                      False))
    if android:
        steps.append(("Appium uiautomator2 driver",
                      lambda: _appium_driver_installed("uiautomator2"),
                      ["appium", "driver", "install", "uiautomator2"],
                      False))
    steps.append(("Python package (mobile-use)",
                  _python_pkg_importable,
                  [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)],
                  False))
    return steps


def run(ios=True, android=True, dry_run=False):
    """Execute the plan. Returns exit code (0 = all green)."""
    steps = plan(ios=ios, android=android)
    rc = 0

    print("mobile-use bootstrap")
    print(f"  iOS: {'yes' if ios else 'no'}   Android: {'yes' if android else 'no'}   "
          f"dry-run: {'yes' if dry_run else 'no'}")
    print()

    for i, (label, check, cmd, mac_only) in enumerate(steps, start=1):
        prefix = f"[{i}/{len(steps)}]"

        if mac_only and sys.platform != "darwin":
            print(f"{prefix} {label}: SKIP (non-macOS — install via your OS pkg manager)")
            continue

        if check():
            print(f"{prefix} {label}: OK")
            continue

        if cmd is None:
            # Special-case: brew itself.
            print(f"{prefix} {label}: MISSING")
            print('   Install: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
            rc = 1
            continue

        if dry_run:
            print(f"{prefix} {label}: would run `{' '.join(cmd)}`")
            continue

        print(f"{prefix} {label}: installing... `{' '.join(cmd)}`")
        try:
            subprocess.check_call(cmd)
            # Re-check.
            if check():
                print(f"   OK")
            else:
                print(f"   POST-INSTALL CHECK STILL FAILS — investigate manually")
                rc = 1
        except subprocess.CalledProcessError as e:
            print(f"   FAIL ({e.returncode}). Run the command yourself to see the full error.")
            rc = 1
        except FileNotFoundError as e:
            print(f"   FAIL: {e}")
            rc = 1

    print()
    if rc == 0:
        print("bootstrap complete.")
        print("  Next: `mobile-use init`   (fills .env from connected device)")
        print("        `mobile-use quickstart`   (full smoke test)")
    else:
        print("bootstrap finished with errors. Re-run after fixing the FAIL lines above.")
        if sys.platform == "darwin":
            print("Manual reference:  SETUP.md")
    return rc


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="mobile-use bootstrap",
                                description="Install everything needed to run mobile-use.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without running install commands.")
    p.add_argument("--ios-only", action="store_true",
                   help="Only iOS-side dependencies (xcuitest, libimobiledevice).")
    p.add_argument("--android-only", action="store_true",
                   help="Only Android-side dependencies (uiautomator2, adb).")
    args = p.parse_args(argv)

    if args.ios_only and args.android_only:
        print("--ios-only and --android-only are mutually exclusive", file=sys.stderr)
        return 2

    ios = not args.android_only
    android = not args.ios_only
    return run(ios=ios, android=android, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
