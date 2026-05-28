"""`mobile-use bootstrap` — one-command installer.

Detects what's missing and installs it. Idempotent: re-running is safe and
quick. Output is a numbered plan followed by per-step result. Honors
`--dry-run`, `--ios-only`, `--android-only`.

Does NOT install Xcode (gates on it with clear instructions). Does NOT
install Python — assumes the user invoked this with python3.

Linux: Android path works (adb is cross-platform). iOS requires macOS+Xcode,
so iOS steps are skipped on Linux with a clear message. Detects apt/dnf/pacman
via /etc/os-release ID/ID_LIKE and emits the right install command.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# `os` is re-exported via `bootstrap.os` so existing tests that monkeypatch
# `bootstrap.os.geteuid` keep working after the _platform refactor.
assert os.path is not None  # noqa: S101  — silences pyright unused-import without removing the symbol

from . import _platform
from ._platform import (
    LINUX_ADB_PKGS,
    LINUX_LIBIMOBILEDEVICE_PKGS,
    LINUX_NODE_PKGS,
    is_linux,
    is_macos,
    linux_install_cmd,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# Step shape: (label, check_fn, run_cmd_list, mac_only, ios_step, android_step)
#   check_fn() -> True if already installed (we skip the run_cmd then)
#   run_cmd_list: argv list, runs via subprocess
#   mac_only: True -> skip on non-darwin with a warning


def _have(cmd):
    return shutil.which(cmd) is not None


# Back-compat shims — tests + external callers still reference these names.
def _linux_pkg_manager():
    return _platform.linux_pkg_manager()


def _sudo_prefix():
    return _platform.sudo_prefix()


def _linux_adb_install_cmd():
    return linux_install_cmd(LINUX_ADB_PKGS,
                             manager=_linux_pkg_manager(),
                             prefix=_sudo_prefix())


def _linux_node_install_cmd():
    return linux_install_cmd(LINUX_NODE_PKGS,
                             manager=_linux_pkg_manager(),
                             prefix=_sudo_prefix())


def _linux_libimobiledevice_install_cmd():
    return linux_install_cmd(LINUX_LIBIMOBILEDEVICE_PKGS,
                             manager=_linux_pkg_manager(),
                             prefix=_sudo_prefix())


def _have_xcode():
    """True if a usable Xcode is installed (not just Command Line Tools).

    `xcodebuild -version` only succeeds when full Xcode is selected. CLT-only
    setups return an error like 'xcode-select: error: tool xcodebuild requires Xcode'.
    """
    if not is_macos():
        return True  # iOS gating handles this; xcode irrelevant elsewhere
    if not _have("xcodebuild"):
        return False
    try:
        out = subprocess.check_output(
            ["xcodebuild", "-version"], timeout=5.0, stderr=subprocess.STDOUT
        ).decode()
        return "Xcode" in out
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _brew_has(pkg):
    if not is_macos() or not _have("brew"):
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
    check so the caller can show 'OK' for them.

    On Linux: iOS-side steps still emit (gated by mac_only → SKIP at run time).
    Android-side steps emit a Linux-native install command via the host's
    apt/dnf/pacman.
    """
    steps = []
    on_linux = is_linux()

    if ios:
        steps.append(("Xcode (full app, not just Command Line Tools)",
                      _have_xcode,
                      None,  # cannot auto-install Xcode; App Store only
                      True))
        steps.append(("Homebrew (macOS package manager)",
                      lambda: _have("brew") or not is_macos(),
                      None,  # cannot auto-install brew; print message
                      True))
        steps.append(("libimobiledevice (idevice_id, ideviceinstaller) — Homebrew on macOS",
                      lambda: _brew_has("libimobiledevice"),
                      ["brew", "install", "libimobiledevice", "ideviceinstaller"],
                      True))
    if android:
        if on_linux:
            steps.append(("Android Platform Tools (adb) — Linux",
                          lambda: _have("adb"),
                          _linux_adb_install_cmd(),
                          False))
        else:
            steps.append(("Android Platform Tools (adb) — Homebrew on macOS",
                          lambda: _brew_has("android-platform-tools") or _have("adb"),
                          ["brew", "install", "android-platform-tools"],
                          True))
    # Node + npm — macOS via brew; Linux via apt/dnf/pacman.
    if on_linux:
        steps.append(("Node.js + npm — Linux",
                      lambda: _have("node") and _have("npm"),
                      _linux_node_install_cmd(),
                      False))
    else:
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

        if mac_only and not is_macos():
            if "iOS" in label or "Xcode" in label or "libimobiledevice" in label or "xcuitest" in label:
                hint = "iOS requires macOS + Xcode (drive remote iOS via IPH_APPIUM_URL on a Mac)"
            else:
                hint = "install via your OS pkg manager"
            print(f"{prefix} {label}: SKIP ({hint})")
            continue

        if check():
            print(f"{prefix} {label}: OK")
            continue

        if cmd is None:
            # Either brew itself (macOS), or an unknown Linux distro with no
            # known package manager.
            print(f"{prefix} {label}: MISSING")
            if is_linux():
                print("   No supported package manager detected. Install manually:")
                if "adb" in label.lower() or "platform tools" in label.lower():
                    print("     - Debian/Ubuntu: sudo apt install android-tools-adb")
                    print("     - Fedora/RHEL:   sudo dnf install android-tools")
                    print("     - Arch/Manjaro:  sudo pacman -S android-tools")
                    print("     - openSUSE:      sudo zypper install android-tools")
                    print("     - Alpine:        sudo apk add android-tools")
                elif "node" in label.lower():
                    print("     - Debian/Ubuntu: sudo apt install nodejs npm")
                    print("     - Fedora/RHEL:   sudo dnf install nodejs npm")
                    print("     - Arch/Manjaro:  sudo pacman -S nodejs npm")
                    print("     - openSUSE:      sudo zypper install nodejs npm")
                    print("     - Alpine:        sudo apk add nodejs npm")
            elif "Xcode" in label:
                print("   Install Xcode from the App Store (~10 GB, free):")
                print("     1. Open the App Store, search for 'Xcode', click Get.")
                print("     2. Launch Xcode once and accept the license:")
                print("        sudo xcodebuild -license accept")
                print("     3. Point command-line tools at Xcode:")
                print("        sudo xcode-select -s /Applications/Xcode.app/Contents/Developer")
                print("   Then re-run: mobile-use bootstrap")
            else:
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
                print("   OK")
            else:
                print("   POST-INSTALL CHECK STILL FAILS — investigate manually")
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
        if is_macos():
            print("Manual reference:  SETUP.md")
        elif is_linux():
            print("Manual reference:  SETUP.md (see '## Linux setup')")
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
