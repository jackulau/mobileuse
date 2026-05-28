"""CLI behavior on Linux hosts: init, quickstart, doctor.

Validates that:
  - `mobile-use init --ios-only` on Linux prints clear remote-Mac guidance
  - `mobile-use quickstart --ios` on Linux without IPH_APPIUM_URL refuses
    early with the SSH tunnel + remote-daemon recipe
  - `mobile-use quickstart --android` on Linux runs (mocked) without
    touching any macOS-only path
  - `mobile-use --doctor` on Linux invokes both platform doctors without
    crashing on macOS-only checks
"""
import sys

import pytest

# ---- mobile-use init -----------------------------------------------------

def test_init_ios_only_on_linux_prints_remote_mac_guidance(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import setup_env
    # Stub device detection so init doesn't hit real adb/idevice_id
    monkeypatch.setattr(setup_env, "detect_devices",
                        lambda: {"ios": [], "android": []})
    # Stub render_env to avoid prompting
    target = tmp_path / ".env"
    setup_env.main(["--ios-only", "--yes", "--print"])
    out = capsys.readouterr().out
    assert "iOS local setup needs macOS" in out or "macOS" in out
    assert "IPH_APPIUM_URL" in out or "remote-daemon" in out
    assert "SETUP.md" in out


def test_init_android_only_on_linux_no_macos_noise(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import setup_env
    monkeypatch.setattr(setup_env, "detect_devices",
                        lambda: {"ios": [], "android": []})
    setup_env.main(["--android-only", "--yes", "--print"])
    out = capsys.readouterr().out
    # Should NOT print iOS macOS guidance when user is Android-only
    assert "iOS local setup needs macOS" not in out


def test_init_macos_no_remote_guidance(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    from mobile_use import setup_env
    monkeypatch.setattr(setup_env, "detect_devices",
                        lambda: {"ios": [], "android": []})
    setup_env.main(["--ios-only", "--yes", "--print"])
    out = capsys.readouterr().out
    # macOS host: no remote-Mac guidance (user is on a Mac)
    assert "iOS local setup needs macOS" not in out


# ---- mobile-use quickstart ----------------------------------------------

def test_quickstart_ios_on_linux_without_remote_url_aborts(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("IPH_APPIUM_URL", raising=False)
    from mobile_use import quickstart
    # Avoid Appium probe + device detection
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(quickstart, "_detect_platform", lambda: None)
    rc = quickstart.main(["--ios"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "remote macOS Appium" in out or "remote-daemon" in out
    assert "SETUP.md" in out


def test_quickstart_ios_on_linux_with_localhost_url_aborts(monkeypatch, capsys):
    """Even if IPH_APPIUM_URL is set, if it's localhost we know it's wrong."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("IPH_APPIUM_URL", "http://127.0.0.1:4723")
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(quickstart, "_detect_platform", lambda: None)
    rc = quickstart.main(["--ios"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "remote macOS Appium" in out


def test_quickstart_ios_on_linux_with_remote_url_proceeds(monkeypatch, capsys):
    """When IPH_APPIUM_URL is a real-looking remote URL, quickstart proceeds (mocked)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("IPH_APPIUM_URL", "http://my-mac.local:4723")
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(quickstart, "_detect_platform", lambda: None)
    # Stub the doctor + smoke phases so we don't hit real daemons
    monkeypatch.setattr(quickstart, "run_doctor_phase", lambda p: (True, ""))
    monkeypatch.setattr(quickstart, "run_smoke_phase", lambda p: (True, ""))

    rc = quickstart.main(["--ios"])
    out = capsys.readouterr().out
    # Did not short-circuit; reached the quickstart header
    assert "mobile-use quickstart" in out
    assert rc == 0


def test_quickstart_android_on_linux_proceeds(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(quickstart, "_detect_platform", lambda: None)
    monkeypatch.setattr(quickstart, "run_doctor_phase", lambda p: (True, ""))
    monkeypatch.setattr(quickstart, "run_smoke_phase", lambda p: (True, ""))
    rc = quickstart.main(["--android"])
    out = capsys.readouterr().out
    assert "android" in out.lower()
    assert rc == 0


# ---- mobile-use --doctor (both platforms) -------------------------------

def test_doctor_both_on_linux_no_crash(monkeypatch, capsys):
    """`mobile-use --doctor` on Linux exits cleanly even when iOS deps missing."""
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import cli
    # Stub the platform run_doctor functions so we don't hit real Appium/adb
    monkeypatch.setattr("iphone_harness.admin.run_doctor", lambda: 1)
    monkeypatch.setattr("android_harness.admin.run_doctor", lambda: 0)
    rc = cli._doctor_both()
    out = capsys.readouterr().out
    assert "iOS" in out or "iphone-harness" in out
    assert "Android" in out or "android-harness" in out
    # Both ran without raising
    assert rc in (0, 1)


def test_doctor_iphone_skips_xcode_on_linux(monkeypatch, capsys):
    """End-to-end: iphone_harness run_doctor on Linux marks Xcode + WDA OK (skipped)."""
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    # Stub all checks so doctor doesn't try to probe live system
    monkeypatch.setattr(admin, "_check_libimobiledevice", lambda: (True, "ok"))
    monkeypatch.setattr(admin, "_check_node", lambda: (True, "v"))
    monkeypatch.setattr(admin, "_check_appium_installed", lambda: (True, "v"))
    monkeypatch.setattr(admin, "_check_driver_installed", lambda *a: (True, "v"))
    monkeypatch.setattr(admin, "_check_python_pkg", lambda: (True, "ok"))
    monkeypatch.setattr(admin, "_check_cli_on_path", lambda *a: (True, "p"))
    monkeypatch.setattr(admin, "_check_env_file", lambda: (True, ".env"))
    monkeypatch.setattr(admin, "_check_appium", lambda: (True, "ok"))
    monkeypatch.setattr(admin, "_check_device", lambda: (True, "paired"))
    monkeypatch.setattr(admin, "_check_battery", lambda: (True, "80%"))
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_log_tail", lambda: "")

    rc = admin.run_doctor()
    out = capsys.readouterr().out
    # Xcode + WDA signing checks must be marked "skipped" on Linux, NOT FAIL
    xcode_lines = [l for l in out.splitlines() if "Xcode" in l]
    wda_lines = [l for l in out.splitlines() if "WebDriverAgent" in l or "WDA" in l]
    for line in xcode_lines + wda_lines:
        # Either OK or it's a heading line — never FAIL
        if "FAIL" in line:
            pytest.fail(f"Linux doctor FAILed a macOS-only check: {line}")
    assert rc == 0  # all stubs return ok
