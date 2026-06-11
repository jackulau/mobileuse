"""Bootstrap plan + run behavior on Windows hosts (static win32 verification).

Mirrors test_bootstrap_linux.py: sys.platform monkeypatched to 'win32',
subprocess.check_call ALWAYS stubbed (nothing ever executes), shutil.which
faked, and both MOBILE_USE_FAKE_* seams pinned in determinism tests.
"""
import sys

import pytest


def _setup_windows(monkeypatch, *, have=()):
    """Pretend we're on win32 with only `have` binaries on PATH. Stubs
    check_call so no test can ever execute a real installer."""
    monkeypatch.setattr(sys, "platform", "win32")
    from mobile_use import bootstrap
    calls = []
    monkeypatch.setattr(bootstrap.subprocess, "check_call",
                        lambda cmd, **k: calls.append(cmd) or 0)
    monkeypatch.setattr(bootstrap.shutil, "which",
                        lambda c: f"C:\\fake\\{c}.cmd" if c in have else None)
    # Pin both probe seams: plan must be deterministic, never live-probed.
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_DRIVERS", "")
    monkeypatch.setenv("MOBILE_USE_FAKE_BREW_PKGS", "")
    return bootstrap, calls


# ---- plan() ------------------------------------------------------------------

def test_plan_windows_adb_step_uses_winget(monkeypatch):
    bootstrap, _ = _setup_windows(monkeypatch)
    steps = bootstrap.plan(ios=False, android=True)
    adb = next(s for s in steps if "adb" in s[0].lower())
    label, _check, cmd, mac_only = adb
    assert mac_only is False
    assert cmd[:2] == ["winget", "install"]
    assert "Google.PlatformTools" in cmd
    assert "winget" in label


def test_plan_windows_node_step_uses_winget(monkeypatch):
    bootstrap, _ = _setup_windows(monkeypatch)
    steps = bootstrap.plan(ios=False, android=True)
    node = next(s for s in steps if "Node" in s[0])
    _label, _check, cmd, mac_only = node
    assert mac_only is False
    assert cmd[:2] == ["winget", "install"]
    assert "OpenJS.NodeJS.LTS" in cmd


def test_plan_windows_has_zero_brew_commands(monkeypatch):
    bootstrap, _ = _setup_windows(monkeypatch)
    steps = bootstrap.plan(ios=False, android=True)
    for _label, _check, cmd, _mac_only in steps:
        if cmd:
            assert "brew" not in " ".join(str(c) for c in cmd)


def test_plan_windows_ios_steps_remain_mac_only(monkeypatch):
    bootstrap, _ = _setup_windows(monkeypatch)
    steps = bootstrap.plan(ios=True, android=False)
    xcode = next(s for s in steps if "Xcode" in s[0])
    assert xcode[3] is True  # mac_only -> run() SKIPs on win32


def test_plan_windows_deterministic(monkeypatch):
    bootstrap, _ = _setup_windows(monkeypatch, have=("adb", "node", "npm"))
    a = [(s[0], s[2]) for s in bootstrap.plan(ios=True, android=True)]
    b = [(s[0], s[2]) for s in bootstrap.plan(ios=True, android=True)]
    assert a == b


# ---- run() -------------------------------------------------------------------

def test_run_windows_dry_run_never_executes(monkeypatch, capsys):
    bootstrap, calls = _setup_windows(monkeypatch)
    rc = bootstrap.run(ios=False, android=True, dry_run=True)
    out = capsys.readouterr().out
    assert calls == [], "dry-run must execute nothing"
    assert "would run" in out
    assert "winget install" in out
    assert rc == 0 or rc == 1  # rc reflects missing tools, not execution


def test_run_windows_resolves_cmd_shims(monkeypatch):
    """npm/appium/winget argv[0] must be shutil.which-resolved at execution
    so CreateProcess gets the .cmd path."""
    bootstrap, calls = _setup_windows(
        monkeypatch, have=("winget", "node", "npm", "appium"))
    rc = bootstrap.run(ios=False, android=True, dry_run=False)
    assert calls, "expected at least one install attempt"
    for cmd in calls:
        assert cmd[0].startswith("C:\\fake\\") or cmd[0] == sys.executable, \
            f"argv[0] not resolved: {cmd[0]!r}"


def test_run_windows_skips_ios_with_remote_hint(monkeypatch, capsys):
    bootstrap, _ = _setup_windows(monkeypatch)
    bootstrap.run(ios=True, android=False, dry_run=True)
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "macOS" in out  # remote-mac guidance, not silence


def test_resolve_argv0_passthrough_when_not_found(monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap.shutil, "which", lambda c: None)
    assert bootstrap._resolve_argv0(["ghost", "-v"]) == ["ghost", "-v"]
    assert bootstrap._resolve_argv0([]) == []


# ---- install_hint on win32 -----------------------------------------------------

def test_install_hint_windows_known_packages(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from mobile_use._platform import (
        LINUX_ADB_PKGS,
        LINUX_NODE_PKGS,
        install_hint,
    )
    assert install_hint("android-platform-tools", LINUX_ADB_PKGS) == \
        "winget install --id Google.PlatformTools"
    assert install_hint("node", LINUX_NODE_PKGS) == \
        "winget install --id OpenJS.NodeJS.LTS"


def test_install_hint_windows_unknown_package_generic(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from mobile_use._platform import LINUX_LIBIMOBILEDEVICE_PKGS, install_hint
    hint = install_hint("libimobiledevice ideviceinstaller", LINUX_LIBIMOBILEDEVICE_PKGS)
    assert "winget" in hint
    assert "brew" not in hint


def test_appium_driver_probe_uses_resolved_binary(monkeypatch):
    """The live probe must spawn the which-resolved path (appium.cmd), not the
    bare name — and missing appium returns False instead of raising."""
    monkeypatch.setattr(sys, "platform", "win32")
    from mobile_use import bootstrap
    monkeypatch.delenv("MOBILE_USE_FAKE_APPIUM_DRIVERS", raising=False)
    seen = {}

    def fake_check_output(cmd, **k):
        seen["argv0"] = cmd[0]
        return b"xcuitest\nuiautomator2\n"

    monkeypatch.setattr(bootstrap.shutil, "which",
                        lambda c: "C:\\fake\\appium.cmd" if c == "appium" else None)
    monkeypatch.setattr(bootstrap.subprocess, "check_output", fake_check_output)
    assert bootstrap._appium_driver_installed("xcuitest") is True
    assert seen["argv0"] == "C:\\fake\\appium.cmd"

    monkeypatch.setattr(bootstrap.shutil, "which", lambda c: None)
    assert bootstrap._appium_driver_installed("xcuitest") is False
