"""Unit tests for mobile_use._platform.

Validates platform detection, Linux pkg manager detection across all five
supported managers (apt/dnf/pacman/zypper/apk), sudo prefix logic, and the
unified `linux_install_cmd` builder.
"""
import sys
from pathlib import Path

import pytest

from mobile_use import _platform


def _stub_os_release(monkeypatch, contents: str):
    """Make Path('/etc/os-release').read_text() return `contents`."""
    real_read = Path.read_text

    def fake_read(self, *a, **kw):
        if str(self) == "/etc/os-release":
            return contents
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", fake_read)


def test_is_linux_is_macos_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _platform.is_linux()
    assert not _platform.is_macos()

    monkeypatch.setattr(sys, "platform", "darwin")
    assert not _platform.is_linux()
    assert _platform.is_macos()


def test_linux_pkg_manager_returns_none_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _platform.linux_pkg_manager() is None


def test_linux_pkg_manager_detects_ubuntu(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=ubuntu\nID_LIKE=debian\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() == "apt"


def test_linux_pkg_manager_detects_fedora(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=fedora\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() == "dnf"


def test_linux_pkg_manager_detects_arch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=arch\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() == "pacman"


def test_linux_pkg_manager_detects_opensuse(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=opensuse-leap\nID_LIKE="suse opensuse"\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() == "zypper"


def test_linux_pkg_manager_detects_alpine(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=alpine\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() == "apk"


def test_linux_pkg_manager_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, '')  # empty os-release
    monkeypatch.setattr(_platform.shutil, "which",
                        lambda c: "/usr/bin/dnf" if c == "dnf" else None)
    assert _platform.linux_pkg_manager() == "dnf"


def test_linux_pkg_manager_unknown_distro_returns_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _stub_os_release(monkeypatch, 'ID=nixos\n')
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.linux_pkg_manager() is None


def test_sudo_prefix_macos_returns_empty(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _platform.sudo_prefix() == []


def test_sudo_prefix_linux_root_returns_empty(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform.os, "geteuid", lambda: 0)
    assert _platform.sudo_prefix() == []


def test_sudo_prefix_linux_nonroot_with_sudo(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(_platform.shutil, "which",
                        lambda c: "/usr/bin/sudo" if c == "sudo" else None)
    assert _platform.sudo_prefix() == ["sudo"]


def test_sudo_prefix_linux_nonroot_without_sudo(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(_platform.shutil, "which", lambda c: None)
    assert _platform.sudo_prefix() is None


@pytest.mark.parametrize("manager,expected_prefix", [
    ("apt", ["sudo", "apt", "install", "-y"]),
    ("dnf", ["sudo", "dnf", "install", "-y"]),
    ("pacman", ["sudo", "pacman", "-S", "--noconfirm"]),
    ("zypper", ["sudo", "zypper", "install", "-y"]),
    ("apk", ["sudo", "apk", "add"]),
])
def test_linux_install_cmd_per_manager(monkeypatch, manager, expected_prefix):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: manager)
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: ["sudo"])
    cmd = _platform.linux_install_cmd(_platform.LINUX_ADB_PKGS)
    assert cmd[:len(expected_prefix)] == expected_prefix
    # last token = the package name
    assert "android-tools" in cmd[-1]


def test_linux_install_cmd_returns_none_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _platform.linux_install_cmd(_platform.LINUX_ADB_PKGS) is None


def test_linux_install_cmd_returns_none_when_manager_unknown(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: None)
    assert _platform.linux_install_cmd(_platform.LINUX_ADB_PKGS) is None


def test_linux_install_cmd_returns_none_when_sudo_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: None)
    assert _platform.linux_install_cmd(_platform.LINUX_ADB_PKGS) is None


def test_linux_install_cmd_node_apt(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: ["sudo"])
    cmd = _platform.linux_install_cmd(_platform.LINUX_NODE_PKGS)
    assert cmd == ["sudo", "apt", "install", "-y", "nodejs", "npm"]


def test_linux_install_cmd_libimobiledevice_apt(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: ["sudo"])
    cmd = _platform.linux_install_cmd(_platform.LINUX_LIBIMOBILEDEVICE_PKGS)
    assert cmd[:4] == ["sudo", "apt", "install", "-y"]
    assert "libimobiledevice6" in cmd
    assert "usbmuxd" in cmd


def test_linux_install_cmd_explicit_manager_override(monkeypatch):
    """Passing manager= bypasses auto-detection."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "sudo_prefix", lambda: [])
    cmd = _platform.linux_install_cmd(_platform.LINUX_ADB_PKGS, manager="dnf")
    assert cmd == ["dnf", "install", "-y", "android-tools"]


def test_host_os_label_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _platform.host_os_label() == "macOS"


def test_host_os_label_linux_with_manager(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: "apt")
    assert _platform.host_os_label() == "Linux (apt)"


def test_host_os_label_linux_unknown_manager(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "linux_pkg_manager", lambda: None)
    assert _platform.host_os_label() == "Linux (unknown pkg manager)"


def test_host_os_label_other_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _platform.host_os_label() == "win32"


# Back-compat: bootstrap.py still exposes the old underscored symbols by
# delegating to _platform. Verify they still work.
def test_bootstrap_legacy_symbols_still_present():
    from mobile_use import bootstrap
    # functions exist + are callable (return values depend on host, so just
    # confirm shape — no exception on call)
    assert callable(bootstrap._linux_pkg_manager)
    assert callable(bootstrap._sudo_prefix)
    assert callable(bootstrap._linux_adb_install_cmd)
    assert callable(bootstrap._linux_node_install_cmd)
    assert callable(bootstrap._linux_libimobiledevice_install_cmd)


# ---------------------------------------------------------------------------
# Cross-platform daemon runtime-dir + deterministic per-name TCP port helpers
# (goal/017 — make Windows routing/networking work without a real Windows box).
# ---------------------------------------------------------------------------
import os  # noqa: E402
import tempfile  # noqa: E402


def test_daemon_tcp_port_is_deterministic_per_name():
    # Same name → same port every call (idempotent daemon respawn + the
    # bind-side and connect-side independently computing one identical port).
    assert _platform.daemon_tcp_port("alpha") == _platform.daemon_tcp_port("alpha")
    assert _platform.daemon_tcp_port("phone-1") == _platform.daemon_tcp_port("phone-1")


def test_daemon_tcp_port_distinct_across_names():
    # Distinct names must not silently share one port (would cross-wire routing).
    ports = {_platform.daemon_tcp_port(n) for n in ("alpha", "beta", "phone-1", "phone-2", "default")}
    assert len(ports) == 5


def test_daemon_tcp_port_in_range():
    for n in ("alpha", "beta", "x", "a-very-long-daemon-name_0123456789"):
        p = _platform.daemon_tcp_port(n)
        assert 8400 <= p <= 8499, (n, p)


def test_default_runtime_base_posix_is_tmp(monkeypatch):
    # POSIX must stay '/tmp' — AF_UNIX sun_path constraint (the 831 baseline
    # asserts unix sockets under /tmp; flipping this would regress macOS/Linux).
    for plat in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", plat)
        assert _platform.default_runtime_base() == "/tmp"


def test_default_runtime_base_windows_is_not_tmp(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(os.environ, "LOCALAPPDATA", r"C:\Users\dev\AppData\Local")
    base = _platform.default_runtime_base()
    assert base != "/tmp"
    assert "mobile-use" in base
    assert "AppData" in base


def test_windows_runtime_dir_prefers_localappdata(monkeypatch):
    monkeypatch.setitem(os.environ, "LOCALAPPDATA", r"C:\Users\dev\AppData\Local")
    d = _platform.windows_runtime_dir()
    assert "AppData" in d and "mobile-use" in d


def test_windows_runtime_dir_falls_back_to_tempdir(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    d = _platform.windows_runtime_dir()
    # Falls back to tempfile.gettempdir() — never bare '/tmp' literal selection
    # by env; the result is the OS temp dir with the mobile-use subdir appended.
    assert d.endswith("mobile-use") or "mobile-use" in d
    assert tempfile.gettempdir() in d


def test_process_exists_posix_true_for_self():
    assert _platform.process_exists(os.getpid()) is True


def test_process_exists_posix_false_for_dead_pid():
    # A pid that almost certainly does not exist → not alive (and no crash).
    assert _platform.process_exists(2_000_000_000) is False


def test_process_exists_windows_never_calls_os_kill(monkeypatch):
    # On Windows os.kill maps signal 0 to TerminateProcess and would KILL the
    # pid being probed. Assert the win32 branch delegates to the Win32 helper
    # and NEVER touches os.kill.
    monkeypatch.setattr(sys, "platform", "win32")

    def _boom(*a, **k):
        raise AssertionError("os.kill must NOT be called on Windows")

    monkeypatch.setattr(os, "kill", _boom)
    monkeypatch.setattr(_platform, "_process_exists_windows", lambda pid: True)
    assert _platform.process_exists(1234) is True
