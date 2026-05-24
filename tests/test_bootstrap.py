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
    assert "android-platform-tools" in labels


def test_plan_excludes_android_when_ios_only():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=False)
    labels = " ".join(label for label, *_ in steps)
    assert "uiautomator2" not in labels
    assert "android-platform-tools" not in labels
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
    monkeypatch.setattr(bootstrap, "_have", _stub_have({"brew", "node", "npm", "appium"}))
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
        if str(self) == "/etc/os-release":
            return 'ID=ubuntu\nID_LIKE=debian\n'
        return real_read(self, *a, **kw)
    monkeypatch.setattr(bootstrap.Path, "read_text", fake_read)
    assert bootstrap._linux_pkg_manager() == "apt"


def test_linux_pkg_manager_detects_fedora(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    real_read = bootstrap.Path.read_text
    def fake_read(self, *a, **kw):
        if str(self) == "/etc/os-release":
            return 'ID=fedora\n'
        return real_read(self, *a, **kw)
    monkeypatch.setattr(bootstrap.Path, "read_text", fake_read)
    assert bootstrap._linux_pkg_manager() == "dnf"


def test_linux_pkg_manager_detects_arch(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    real_read = bootstrap.Path.read_text
    def fake_read(self, *a, **kw):
        if str(self) == "/etc/os-release":
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
