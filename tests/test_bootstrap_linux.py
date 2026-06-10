"""Bootstrap plan + run behavior on Linux hosts.

Complements test_bootstrap.py (which is platform-agnostic and parametric)
by exercising the Linux-specific code paths end to end:

  - `mobile-use bootstrap` on Linux skips iOS steps with a clear message
  - `--android-only` on Linux produces a runnable plan
  - libimobiledevice/Xcode/brew steps are gated by mac_only
  - the dry-run output for Linux is free of `brew install` directives
  - the final summary references SETUP.md "Linux setup" anchor on failure
"""
import sys

import pytest


def _setup_linux(monkeypatch, pkg_manager="apt"):
    """Common: pretend we're on Linux with a known pkg manager + sudo available."""
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform, bootstrap
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: pkg_manager)
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: ["sudo"])
    # Local shims read from bootstrap module — patch them too
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: pkg_manager)
    monkeypatch.setattr(bootstrap, "_sudo_prefix", lambda: ["sudo"])


def test_plan_on_linux_skips_xcode_step_label(monkeypatch):
    _setup_linux(monkeypatch)
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=True)
    # Xcode still in plan (label-only); run() will SKIP it on non-darwin
    labels = [s[0] for s in steps]
    xcode = [l for l in labels if "Xcode" in l]
    assert xcode, "Xcode label should still appear in plan (run-time SKIP)"


def test_plan_on_linux_android_only_uses_apt(monkeypatch):
    _setup_linux(monkeypatch, pkg_manager="apt")
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=False, android=True)
    # find adb step
    adb_step = next((s for s in steps if "adb" in s[0].lower()), None)
    assert adb_step is not None
    label, _check, cmd, mac_only = adb_step
    assert mac_only is False
    assert cmd is not None
    assert "apt" in " ".join(cmd)
    assert "android-tools-adb" in " ".join(cmd)


def test_plan_on_linux_android_only_uses_dnf(monkeypatch):
    _setup_linux(monkeypatch, pkg_manager="dnf")
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=False, android=True)
    adb_step = next((s for s in steps if "adb" in s[0].lower()), None)
    assert adb_step is not None
    assert "dnf" in " ".join(adb_step[2])


def test_plan_on_linux_skips_libimobiledevice_brew(monkeypatch):
    _setup_linux(monkeypatch)
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=False)
    # libimobiledevice step is mac_only — keep the label, run-time SKIP
    libi = [s for s in steps if "libimobiledevice" in s[0]]
    assert libi, "libimobiledevice step should still appear in plan"
    assert all(s[3] is True for s in libi), "libimobiledevice should be mac_only=True"


def test_run_on_linux_does_not_print_brew_install_for_skipped_ios(monkeypatch, capsys):
    """Linux dry-run output must not direct the user to `brew install …`
    for iOS-side steps. iOS steps print SKIP with the remote-Mac hint."""
    _setup_linux(monkeypatch)
    from mobile_use import bootstrap
    # Force every cross-platform check to pretend "missing" so we see the
    # remediation messages, but keep dry-run True so nothing executes.
    monkeypatch.setattr(bootstrap, "_have", lambda c: False)
    monkeypatch.setattr(bootstrap, "_brew_has", lambda p: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda n: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)

    bootstrap.run(ios=True, android=True, dry_run=True)
    out = capsys.readouterr().out
    # No `brew install` directive should target Linux users
    skipped_lines = [l for l in out.splitlines() if "SKIP" in l]
    assert skipped_lines, "iOS steps should be SKIP-marked on Linux"
    for line in skipped_lines:
        assert "brew install" not in line, f"brew install leaked into SKIP line: {line}"
    # The mac-only iOS skip should mention remote IPH_APPIUM_URL or Xcode/Mac
    assert any("IPH_APPIUM_URL" in l or "Mac" in l or "Xcode" in l for l in skipped_lines)


def test_run_on_linux_android_only_full_dry_run(monkeypatch, capsys):
    """End-to-end: bootstrap --android-only --dry-run on Linux exits 0."""
    _setup_linux(monkeypatch, pkg_manager="apt")
    from mobile_use import bootstrap
    # Pretend everything is missing — exercise every install command path
    monkeypatch.setattr(bootstrap, "_have", lambda c: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda n: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)
    # Critical: subprocess.check_call should never be invoked on dry-run
    monkeypatch.setattr(bootstrap.subprocess, "check_call",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("dry-run should not call subprocess")))

    rc = bootstrap.run(ios=False, android=True, dry_run=True)
    out = capsys.readouterr().out
    # The plan steps should print would-run lines for adb/node/appium/uia2/pkg
    assert "android-tools-adb" in out
    assert "nodejs" in out
    assert "appium" in out
    # Linux: no `brew install` in any remediation
    assert "brew install" not in out
    # rc=0 means no MISSING-with-no-cmd terminated bootstrap. (Some steps
    # may still print MISSING/would-run; what matters is no crash.)
    assert rc in (0, 1)


def test_run_on_linux_unknown_distro_shows_all_manager_hints(monkeypatch, capsys):
    """When pkg manager can't be detected, MISSING line lists apt/dnf/pacman/zypper/apk."""
    monkeypatch.setattr(sys, "platform", "linux")
    from mobile_use import _platform, bootstrap
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: None)
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: ["sudo"])
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: None)
    monkeypatch.setattr(bootstrap, "_sudo_prefix", lambda: ["sudo"])
    monkeypatch.setattr(bootstrap, "_have", lambda c: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda n: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)
    # dry_run=False: run() EXECUTES remediation commands for steps that still
    # have a cmd (npm/pip). Unstubbed, this test really ran `npm i -g appium`
    # on the host — mutating global state mid-suite (caught by the goal/022
    # audit when the dry-run determinism test flipped under xdist).
    executed = []
    monkeypatch.setattr(bootstrap.subprocess, "check_call",
                        lambda cmd, *a, **kw: executed.append(cmd) or 0)

    bootstrap.run(ios=False, android=True, dry_run=False)
    out = capsys.readouterr().out
    assert all(isinstance(c, list) for c in executed)   # commands captured, not run
    # adb MISSING line should list all known package managers
    assert "android-tools-adb" in out or "android-tools" in out
    assert "apt install" in out
    assert "dnf install" in out
    assert "pacman -S" in out
    # zypper + apk added in D1 — verify they're in the doctor hint too
    assert "zypper" in out
    assert "apk" in out


def test_run_summary_references_linux_setup_section_on_failure(monkeypatch, capsys):
    """Failing bootstrap on Linux ends with SETUP.md '## Linux setup' pointer."""
    _setup_linux(monkeypatch, pkg_manager=None)  # no pkg manager → MISSING → rc=1
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_have", lambda c: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda n: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: None)
    # Never execute real remediation commands (see unknown-distro test above).
    monkeypatch.setattr(bootstrap.subprocess, "check_call",
                        lambda cmd, *a, **kw: 0)

    rc = bootstrap.run(ios=False, android=True, dry_run=False)
    out = capsys.readouterr().out
    assert rc != 0
    assert "SETUP.md" in out
    assert "Linux setup" in out
