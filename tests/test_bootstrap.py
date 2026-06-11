"""Unit tests for mobile_use.bootstrap.

Mocks shutil.which + subprocess so tests run without any real install side
effects. Verifies plan() composition, OK vs MISSING accounting, and that
the dry-run path never invokes subprocess.check_call.
"""
import sys
from unittest.mock import patch

import pytest


def _stub_have(present):
    """Return a `_have` replacement that says yes for names in `present`."""
    def _have(cmd):
        return cmd in present
    return _have


def test_plan_includes_ios_and_android_by_default():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=True)
    labels = " ".join(label for label, *_ in steps)
    assert "xcuitest" in labels
    assert "uiautomator2" in labels
    assert "libimobiledevice" in labels
    # Android step label varies by host (Linux: "Android Platform Tools (adb) — Linux"
    # vs macOS: "Android Platform Tools (adb) — Homebrew on macOS"); match the stem.
    assert "Android Platform Tools" in labels or "adb" in labels.lower()


def test_plan_excludes_android_when_ios_only():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=False)
    labels = " ".join(label for label, *_ in steps)
    assert "uiautomator2" not in labels
    assert "Android Platform Tools" not in labels
    assert "adb" not in labels.lower()
    assert "xcuitest" in labels


def test_plan_excludes_ios_when_android_only():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=False, android=True)
    labels = " ".join(label for label, *_ in steps)
    assert "xcuitest" not in labels
    assert "libimobiledevice" not in labels
    assert "uiautomator2" in labels


def test_dry_run_does_not_invoke_subprocess(capsys, monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_have", _stub_have({"brew"}))
    monkeypatch.setattr(bootstrap, "_brew_has", lambda pkg: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda name: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)

    called = []
    def fake_check_call(*a, **kw):
        called.append(a)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)

    rc = bootstrap.run(dry_run=True)
    assert called == [], "dry_run must not call subprocess.check_call"
    out = capsys.readouterr().out
    assert "would run" in out


def test_run_returns_0_when_everything_installed(monkeypatch, capsys):
    from mobile_use import bootstrap
    # Stub every "present?" check to True regardless of host: the test is
    # the macOS+Linux happy path where every dep is already installed.
    # `adb` is included so the Linux-branch plan also sees it as OK.
    monkeypatch.setattr(bootstrap, "_have",
                        _stub_have({"brew", "node", "npm", "appium", "xcodebuild", "adb"}))
    monkeypatch.setattr(bootstrap, "_have_xcode", lambda: True)
    monkeypatch.setattr(bootstrap, "_brew_has", lambda pkg: True)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda name: True)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: True)

    rc = bootstrap.run(dry_run=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "bootstrap complete" in out


def test_main_rejects_both_platform_flags(capsys):
    from mobile_use import bootstrap
    rc = bootstrap.main(["--ios-only", "--android-only"])
    assert rc == 2


def test_main_dry_run_smoke():
    from mobile_use import bootstrap
    # Should never raise even on a partially-set-up dev box.
    rc = bootstrap.main(["--dry-run"])
    assert rc in (0, 1)  # 0 if everything installed; 1 if brew missing


# ---- linux package manager detection -------------------------------------

def test_linux_pkg_manager_returns_none_on_macos(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    assert bootstrap._linux_pkg_manager() is None


def test_linux_pkg_manager_detects_ubuntu(monkeypatch, tmp_path):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    fake = tmp_path / "os-release"
    fake.write_text('ID=ubuntu\nID_LIKE=debian\n')
    # bootstrap reads /etc/os-release directly, so we need to patch Path or read.
    # Simpler: stub _linux_pkg_manager helpers via monkeypatch of the function inputs.
    # Use unittest.mock on Path.read_text via a wrapper.
    real_read = bootstrap.Path.read_text
    def fake_read(self, *a, **kw):
        if self.as_posix() == "/etc/os-release":
            return 'ID=ubuntu\nID_LIKE=debian\n'
        return real_read(self, *a, **kw)
    monkeypatch.setattr(bootstrap.Path, "read_text", fake_read)
    assert bootstrap._linux_pkg_manager() == "apt"


def test_linux_pkg_manager_detects_fedora(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    real_read = bootstrap.Path.read_text
    def fake_read(self, *a, **kw):
        if self.as_posix() == "/etc/os-release":
            return 'ID=fedora\n'
        return real_read(self, *a, **kw)
    monkeypatch.setattr(bootstrap.Path, "read_text", fake_read)
    assert bootstrap._linux_pkg_manager() == "dnf"


def test_linux_pkg_manager_detects_arch(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    real_read = bootstrap.Path.read_text
    def fake_read(self, *a, **kw):
        if self.as_posix() == "/etc/os-release":
            return 'ID=arch\n'
        return real_read(self, *a, **kw)
    monkeypatch.setattr(bootstrap.Path, "read_text", fake_read)
    assert bootstrap._linux_pkg_manager() == "pacman"


def test_linux_adb_install_cmd_apt(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    cmd = bootstrap._linux_adb_install_cmd()
    assert "apt" in " ".join(cmd)
    assert "android-tools-adb" in " ".join(cmd)


def test_linux_adb_install_cmd_dnf(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "dnf")
    cmd = bootstrap._linux_adb_install_cmd()
    assert "dnf" in " ".join(cmd)


def test_linux_adb_install_cmd_pacman(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "pacman")
    cmd = bootstrap._linux_adb_install_cmd()
    assert "pacman" in " ".join(cmd)


def test_linux_adb_install_returns_none_when_unknown(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: None)
    assert bootstrap._linux_adb_install_cmd() is None


def test_linux_node_install_apt(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    cmd = bootstrap._linux_node_install_cmd()
    assert "nodejs" in " ".join(cmd)
    assert "npm" in " ".join(cmd)


def test_linux_plan_uses_apt_for_adb(monkeypatch):
    """On Linux+apt, plan should include an apt install command for adb."""
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    steps = bootstrap.plan(ios=False, android=True)
    # Find the adb step
    for label, check, cmd, mac_only in steps:
        if "adb" in label.lower():
            assert cmd is not None
            assert "apt" in " ".join(cmd)
            assert mac_only is False
            break
    else:
        pytest.fail("no adb step found in Linux plan")


def test_linux_plan_skips_xcuitest_in_android_only(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap, "_linux_pkg_manager", lambda: "apt")
    steps = bootstrap.plan(ios=False, android=True)
    labels = " ".join(label for label, *_ in steps)
    assert "xcuitest" not in labels
    assert "libimobiledevice" not in labels


# ---- xcode preflight -----------------------------------------------------

def test_have_xcode_returns_true_off_macos(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    assert bootstrap._have_xcode() is True


def test_have_xcode_returns_false_when_xcodebuild_missing(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap, "_have", lambda c: False)
    assert bootstrap._have_xcode() is False


def test_have_xcode_returns_true_when_xcodebuild_works(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap, "_have", lambda c: True)
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *a, **kw: b"Xcode 16.0\n")
    assert bootstrap._have_xcode() is True


def test_have_xcode_returns_false_when_xcodebuild_errors(monkeypatch):
    """xcode-select error on CLT-only setup must return False, not raise."""
    import subprocess as sp

    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap, "_have", lambda c: True)
    def boom(*a, **kw):
        raise sp.CalledProcessError(1, ["xcodebuild"], output=b"xcode-select: error: tool xcodebuild requires Xcode")
    monkeypatch.setattr(bootstrap.subprocess, "check_output", boom)
    assert bootstrap._have_xcode() is False


def test_plan_includes_xcode_step_for_ios(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    steps = bootstrap.plan(ios=True, android=False)
    labels = [s[0] for s in steps]
    assert any("Xcode" in lbl for lbl in labels), f"Xcode step missing from iOS plan: {labels}"


def test_plan_omits_xcode_when_android_only(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    steps = bootstrap.plan(ios=False, android=True)
    labels = [s[0] for s in steps]
    assert not any("Xcode" in lbl for lbl in labels), f"Xcode in android-only plan: {labels}"


def test_run_halts_with_xcode_remediation_when_missing(monkeypatch, capsys):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap, "_have_xcode", lambda: False)
    monkeypatch.setattr(bootstrap, "_have", lambda c: True)
    monkeypatch.setattr(bootstrap, "_brew_has", lambda pkg: True)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda name: True)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: True)

    rc = bootstrap.run(ios=True, android=False, dry_run=True)
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "App Store" in out
    assert "xcode-select" in out
    assert rc == 1



# ---- probes can't lie: fake seams print a loud warning -------------------------

def test_run_warns_when_fake_seams_active(monkeypatch, capsys):
    from mobile_use import bootstrap
    monkeypatch.setenv("MOBILE_USE_FAKE_BREW_PKGS", "")
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_DRIVERS", "")
    monkeypatch.setattr(bootstrap.subprocess, "check_call", lambda *a, **k: 0)
    bootstrap.run(ios=False, android=True, dry_run=True)
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "fabricated" in out


def test_run_no_warning_without_fake_seams(monkeypatch, capsys):
    from mobile_use import bootstrap
    monkeypatch.delenv("MOBILE_USE_FAKE_BREW_PKGS", raising=False)
    monkeypatch.delenv("MOBILE_USE_FAKE_APPIUM_DRIVERS", raising=False)
    monkeypatch.setattr(bootstrap.subprocess, "check_call", lambda *a, **k: 0)
    bootstrap.run(ios=False, android=True, dry_run=True)
    out = capsys.readouterr().out
    assert "fabricated" not in out
