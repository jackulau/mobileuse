"""iOS-on-Linux via remote macOS Appium server.

Pattern: a Linux (or Windows) host drives iOS by talking to a remote macOS
host where Appium + WebDriverAgent + iphone-harness daemon run. The local
host never needs Xcode, codesigning, or libimobiledevice; it only needs
network reachability to the remote macOS daemon.

Two flavors are supported by the harness:

  1. IPH_CONNECT=tcp://<mac>:8763
       The iphone-harness `iphone-harness -c` and `mobile-use --ios -c`
       commands DON'T spawn a daemon locally — they connect over TCP to
       the remote daemon. (Implemented in iphone_harness.admin.is_remote_daemon
       and ensure_daemon's client-only branch.)

  2. IPH_APPIUM_URL=http://<mac>:4723
       The local daemon talks to the remote Appium server. The remote Mac
       handles WDA on its own. The local host runs python iphone_harness
       processes, but no Xcode/security/idevice_id calls happen.

Both are tested below.
"""
import os
import sys

import pytest


# ---- IPH_CONNECT TCP daemon (client-only mode) --------------------------

def test_is_remote_daemon_off_by_default(monkeypatch):
    monkeypatch.delenv("IPH_CONNECT", raising=False)
    from iphone_harness import admin
    assert admin.is_remote_daemon() is False


def test_is_remote_daemon_tcp_set_true(monkeypatch):
    monkeypatch.setenv("IPH_CONNECT", "tcp://192.168.1.10:8763")
    from iphone_harness import admin
    assert admin.is_remote_daemon() is True


def test_is_remote_daemon_unix_set_false(monkeypatch):
    """Local unix socket path is NOT remote — only TCP triggers client-mode."""
    monkeypatch.setenv("IPH_CONNECT", "unix:/tmp/something.sock")
    from iphone_harness import admin
    assert admin.is_remote_daemon() is False


def test_remote_daemon_skips_local_spawn_when_alive(monkeypatch):
    """When daemon is alive over TCP, ensure_daemon returns without spawning."""
    monkeypatch.setenv("IPH_CONNECT", "tcp://192.168.1.10:8763")
    from iphone_harness import admin

    monkeypatch.setattr(admin, "daemon_alive", lambda *a, **kw: True)
    # subprocess.Popen MUST NOT be called — assert via spy
    popen_called = []
    monkeypatch.setattr(admin.subprocess, "Popen",
                        lambda *a, **kw: popen_called.append(a) or pytest.fail("local spawn happened in remote mode"))
    admin.ensure_daemon(wait=0.1)
    assert popen_called == []


def test_remote_daemon_raises_with_remediation_when_dead(monkeypatch):
    """When remote daemon unreachable, raise RuntimeError with operator checklist."""
    monkeypatch.setenv("IPH_CONNECT", "tcp://192.168.1.10:8763")
    from iphone_harness import admin
    monkeypatch.setattr(admin, "daemon_alive", lambda *a, **kw: False)
    with pytest.raises(RuntimeError) as exc_info:
        admin.ensure_daemon(wait=0.1)
    msg = str(exc_info.value)
    assert "tcp://192.168.1.10:8763" in msg or "remote daemon" in msg.lower()
    # Should suggest concrete next steps
    assert "ssh" in msg.lower() or "pgrep" in msg.lower() or "Mac" in msg


# ---- IPH_APPIUM_URL remote (local daemon → remote Appium) ---------------

def test_no_xcrun_security_xcodebuild_calls_on_linux_remote_path(monkeypatch):
    """On Linux with IPH_APPIUM_URL pointed at a remote Mac, the harness must
    NEVER invoke local Xcode-specific tools — they don't exist on Linux."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("IPH_APPIUM_URL", "http://mac.local:4723")
    monkeypatch.setenv("IPH_UDID", "abc123")

    # Spy on subprocess.check_output to catch any Xcode tool invocation
    from iphone_harness import admin
    real = admin.subprocess.check_output
    forbidden = ("xcrun", "xcodebuild", "security", "codesign")
    called_forbidden = []

    def spy_check_output(cmd, *a, **kw):
        if cmd and any(forbidden_tool in str(cmd[0]) for forbidden_tool in forbidden):
            called_forbidden.append(cmd[0])
        # Pretend tools are missing — but we don't actually want to call them.
        raise FileNotFoundError(cmd[0] if cmd else "?")

    monkeypatch.setattr(admin.subprocess, "check_output", spy_check_output)

    # Run all checks that might historically call Xcode tools
    admin._check_xcode()
    admin._check_wda_signing()
    admin._check_libimobiledevice()

    assert called_forbidden == [], (
        f"Linux+remote path triggered macOS-only tool calls: {called_forbidden}"
    )


def test_check_xcode_returns_ok_on_linux_remote(monkeypatch):
    """run_doctor on Linux must mark Xcode check as OK (skipped — not FAIL)."""
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    ok, info = admin._check_xcode()
    assert ok is True
    assert "skipped" in info.lower() or "macOS" in info


def test_check_wda_signing_returns_ok_on_linux_remote(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import admin
    ok, info = admin._check_wda_signing()
    assert ok is True
    assert "skipped" in info.lower() or "macOS" in info


# ---- CLI flag: --remote-daemon ------------------------------------------

def test_cli_remote_daemon_flag_sets_iph_connect(monkeypatch, tmp_path):
    """`mobile-use --ios --remote-daemon tcp://...` sets IPH_CONNECT for downstream."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("IPH_UDID", "abc123")
    # Don't actually run; just verify env wiring via cli's argv parser.
    # The cli passes through to iphone-harness, which we'll spy on by intercepting
    # the platform module entry.
    from mobile_use import cli

    # We need a minimal env-setting smoke test. The flag handler should set
    # os.environ["IPH_CONNECT"] before invoking the platform _run_ function.
    captured_env = {}

    def fake_run_ios(args):
        captured_env["IPH_CONNECT"] = os.environ.get("IPH_CONNECT")
        return 0

    monkeypatch.setattr(cli, "_run_ios", fake_run_ios)
    # Stub the doctor too in case the flag also wires that
    monkeypatch.setattr("iphone_harness.admin.run_doctor", lambda: 0)

    sys.argv = ["mobile-use", "--ios", "--remote-daemon", "tcp://127.0.0.1:8763", "-c", "pass"]
    try:
        cli.main()
    except SystemExit:
        pass

    assert captured_env.get("IPH_CONNECT") == "tcp://127.0.0.1:8763"
