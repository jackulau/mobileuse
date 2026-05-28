"""Doctor remediation messages must be platform-appropriate.

On macOS: `brew install …` (existing behavior — preserved).
On Linux: `sudo apt install …` / `sudo dnf install …` / etc. — never `brew`.

Tests monkeypatch `sys.platform="linux"` and the Linux pkg manager, then
exercise `_check_*` + `run_doctor`. Assertions:
  - No `brew install` substring leaks into Linux output
  - Distro-correct command appears (apt for Ubuntu mock, dnf for Fedora mock)
  - macOS-only checks (Xcode, WDA signing) auto-skip on Linux (OK, not FAIL)
"""
import sys

import pytest

# ---- mobile_use._platform.install_hint -----------------------------------

def test_install_hint_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from mobile_use._platform import LINUX_LIBIMOBILEDEVICE_PKGS, install_hint
    hint = install_hint("libimobiledevice", LINUX_LIBIMOBILEDEVICE_PKGS)
    assert hint == "brew install libimobiledevice"


def test_install_hint_linux_apt(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    from mobile_use._platform import LINUX_ADB_PKGS, install_hint
    hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
    assert hint.startswith("sudo apt install ")
    assert "android-tools-adb" in hint
    assert "brew" not in hint


def test_install_hint_linux_dnf(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "dnf")
    from mobile_use._platform import LINUX_ADB_PKGS, install_hint
    hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
    assert hint.startswith("sudo dnf install ")
    assert "android-tools" in hint
    assert "brew" not in hint


def test_install_hint_linux_pacman(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "pacman")
    from mobile_use._platform import LINUX_NODE_PKGS, install_hint
    hint = install_hint("node", LINUX_NODE_PKGS)
    assert hint.startswith("sudo pacman -S ")
    assert "nodejs" in hint
    assert "brew" not in hint


def test_install_hint_linux_zypper(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "zypper")
    from mobile_use._platform import LINUX_NODE_PKGS, install_hint
    hint = install_hint("node", LINUX_NODE_PKGS)
    assert "zypper" in hint
    assert "brew" not in hint


def test_install_hint_linux_apk(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apk")
    from mobile_use._platform import LINUX_ADB_PKGS, install_hint
    hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
    assert "apk add" in hint
    assert "brew" not in hint


def test_install_hint_linux_unknown_distro_lists_all(monkeypatch):
    """When no pkg manager detected, hint lists all five options."""
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: None)
    from mobile_use._platform import LINUX_ADB_PKGS, install_hint
    hint = install_hint("android-platform-tools", LINUX_ADB_PKGS)
    assert "apt install" in hint
    assert "dnf install" in hint
    assert "pacman -S" in hint
    assert "zypper install" in hint
    assert "apk add" in hint
    assert "brew" not in hint


# ---- iphone_harness.admin: Linux behavior --------------------------------

def test_iphone_check_xcode_skipped_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    ok, info = admin._check_xcode()
    assert ok is True
    assert "skipped" in info.lower()
    assert "macOS" in info or "Xcode" in info


def test_iphone_check_wda_signing_skipped_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    ok, info = admin._check_wda_signing()
    assert ok is True
    assert "skipped" in info.lower()


def test_iphone_check_device_no_brew_on_linux(monkeypatch):
    """When idevice_id is missing on Linux, the error message must not say 'brew install'."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("IPH_UDID", "abc123")

    from iphone_harness import admin
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")

    # Force `idevice_id` to be "not found"
    original_check_output = admin.subprocess.check_output
    def fake_check_output(cmd, *a, **kw):
        if cmd and cmd[0] == "idevice_id":
            raise FileNotFoundError(cmd[0])
        return original_check_output(cmd, *a, **kw)
    monkeypatch.setattr(admin.subprocess, "check_output", fake_check_output)

    ok, info = admin._check_device()
    assert ok is False
    assert "brew" not in info
    assert "apt install" in info
    assert "libimobiledevice" in info


def test_iphone_check_libimobiledevice_uses_path_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    # Pretend idevice_id is on PATH
    monkeypatch.setattr(admin.shutil, "which", lambda c: "/usr/bin/idevice_id" if c == "idevice_id" else None)
    ok, info = admin._check_libimobiledevice()
    assert ok is True
    assert "idevice_id" in info


def test_iphone_check_libimobiledevice_missing_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    monkeypatch.setattr(admin.shutil, "which", lambda c: None)
    ok, info = admin._check_libimobiledevice()
    assert ok is False
    assert "PATH" in info


def test_iphone_run_doctor_linux_no_brew_in_output(monkeypatch, capsys):
    """End-to-end: run_doctor on Linux must not print 'brew install' anywhere."""
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    # Make every check return failure so all `fix:` lines print.
    monkeypatch.setattr(admin, "_check_libimobiledevice", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_node", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_appium_installed", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_driver_installed", lambda *a: (False, "missing"))
    monkeypatch.setattr(admin, "_check_python_pkg", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_cli_on_path", lambda *a: (False, "missing"))
    monkeypatch.setattr(admin, "_check_env_file", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_appium", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_device", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_battery", lambda: (True, "skipped"))
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_log_tail", lambda: "")

    admin.run_doctor()
    out = capsys.readouterr().out
    assert "brew install" not in out, f"brew leaked into Linux doctor output:\n{out}"


# ---- android_harness.admin: Linux behavior -------------------------------

def test_android_check_adb_uses_path_check(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from android_harness import admin
    monkeypatch.setattr(admin.shutil, "which", lambda c: "/usr/bin/adb" if c == "adb" else None)

    # Stub version probe so test doesn't need a real adb
    monkeypatch.setattr(admin.subprocess, "check_output",
                        lambda *a, **kw: b"Android Debug Bridge version 1.0.41\n")

    ok, info = admin._check_adb()
    assert ok is True
    assert "1.0.41" in info or "/usr/bin/adb" in info


def test_android_check_adb_missing(monkeypatch):
    from android_harness import admin
    monkeypatch.setattr(admin.shutil, "which", lambda c: None)
    ok, info = admin._check_adb()
    assert ok is False
    assert "PATH" in info


def test_android_check_device_no_brew_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("ANH_UDID", "abc123")
    from android_harness import admin
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    monkeypatch.setattr(admin.subprocess, "check_output",
                        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("adb")))
    ok, info = admin._check_device()
    assert ok is False
    assert "brew" not in info
    assert "apt install" in info


def test_android_run_doctor_linux_no_brew_in_output(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    from android_harness import admin
    from mobile_use import _platform
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    monkeypatch.setattr(admin, "_check_adb", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_node", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_appium_installed", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_driver_installed", lambda *a: (False, "missing"))
    monkeypatch.setattr(admin, "_check_python_pkg", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_cli_on_path", lambda *a: (False, "missing"))
    monkeypatch.setattr(admin, "_check_env_file", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_appium", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_device", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_battery", lambda: (True, "skipped"))
    monkeypatch.setattr(admin, "_check_screen_unlocked", lambda: (True, "skipped"))
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_log_tail", lambda: "")

    admin.run_doctor()
    out = capsys.readouterr().out
    assert "brew install" not in out, f"brew leaked into Linux doctor output:\n{out}"


def test_iphone_run_doctor_macos_still_shows_brew(monkeypatch, capsys):
    """On macOS the existing brew remediation must still appear (no regression)."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from iphone_harness import admin
    # Make checks fail so fix: lines print
    monkeypatch.setattr(admin, "_check_libimobiledevice", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_node", lambda: (False, "missing"))
    monkeypatch.setattr(admin, "_check_appium_installed", lambda: (True, "v"))
    monkeypatch.setattr(admin, "_check_driver_installed", lambda *a: (True, "v"))
    monkeypatch.setattr(admin, "_check_xcode", lambda: (True, "Xcode 15"))
    monkeypatch.setattr(admin, "_check_python_pkg", lambda: (True, "ok"))
    monkeypatch.setattr(admin, "_check_cli_on_path", lambda *a: (True, "p"))
    monkeypatch.setattr(admin, "_check_env_file", lambda: (True, ".env"))
    monkeypatch.setattr(admin, "_check_appium", lambda: (True, "ok"))
    monkeypatch.setattr(admin, "_check_device", lambda: (True, "paired"))
    monkeypatch.setattr(admin, "_check_wda_signing", lambda: (True, "signed"))
    monkeypatch.setattr(admin, "_check_battery", lambda: (True, "80%"))
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_log_tail", lambda: "")

    admin.run_doctor()
    out = capsys.readouterr().out
    # macOS doctor should mention brew somewhere — at minimum in the libimobiledevice fix
    assert "brew install" in out, f"brew vanished from macOS doctor output:\n{out}"
