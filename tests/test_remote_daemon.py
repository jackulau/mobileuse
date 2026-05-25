"""Client-only / remote-daemon mode tests.

Covers the path a Windows or Linux host takes when driving iOS via a remote
Mac (or Android via remote anywhere): IPH_CONNECT / ANH_CONNECT pointed at
tcp://<host>:port causes admin.ensure_daemon to skip local-spawn entirely
and raise a remediation if the remote daemon is unreachable.

Cross-references with test_ipc_tcp.py (transport-level) and test_cli_dispatch.py
(CLI flag wiring). This file is exclusively the client-mode policy layer.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iphone_harness import admin as iph_admin
from android_harness import admin as anh_admin


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---- is_remote_daemon() policy -------------------------------------------

def test_is_remote_daemon_unset_is_false(monkeypatch):
    monkeypatch.delenv("IPH_CONNECT", raising=False)
    monkeypatch.delenv("ANH_CONNECT", raising=False)
    assert iph_admin.is_remote_daemon() is False
    assert anh_admin.is_remote_daemon() is False


def test_is_remote_daemon_tcp_is_true(monkeypatch):
    monkeypatch.setenv("IPH_CONNECT", "tcp://192.168.1.10:8763")
    monkeypatch.setenv("ANH_CONNECT", "tcp://192.168.1.10:8764")
    assert iph_admin.is_remote_daemon() is True
    assert anh_admin.is_remote_daemon() is True


def test_is_remote_daemon_loopback_tcp_still_true(monkeypatch):
    """tcp://127.0.0.1 = SSH-tunnel pattern — also client-only mode."""
    monkeypatch.setenv("IPH_CONNECT", "tcp://127.0.0.1:8763")
    assert iph_admin.is_remote_daemon() is True


def test_is_remote_daemon_unix_is_false(monkeypatch):
    monkeypatch.setenv("IPH_CONNECT", "unix:/tmp/iph-default.sock")
    assert iph_admin.is_remote_daemon() is False


def test_is_remote_daemon_malformed_uri_is_false(monkeypatch):
    """Malformed IPH_CONNECT shouldn't accidentally trip client-only mode."""
    monkeypatch.setenv("IPH_CONNECT", "not a uri")
    assert iph_admin.is_remote_daemon() is False


# ---- ensure_daemon in client_mode never spawns ---------------------------

def test_client_mode_ensure_daemon_raises_remediation_iphone(monkeypatch):
    """When IPH_CONNECT points at an unreachable TCP daemon, ensure_daemon
    must NOT try to spawn locally — it raises a remote-side checklist."""
    monkeypatch.setenv("IPH_CONNECT", "tcp://127.0.0.1:1")  # port 1 = guaranteed unreachable
    monkeypatch.delenv("IPH_BIND", raising=False)
    with pytest.raises(RuntimeError) as exc:
        iph_admin.ensure_daemon(wait=1.0)
    msg = str(exc.value)
    assert "remote daemon unreachable" in msg
    assert "client-only mode" in msg
    assert "ssh -L" in msg or "ssh mac" in msg


def test_client_mode_ensure_daemon_raises_remediation_android(monkeypatch):
    monkeypatch.setenv("ANH_CONNECT", "tcp://127.0.0.1:1")
    monkeypatch.delenv("ANH_BIND", raising=False)
    with pytest.raises(RuntimeError) as exc:
        anh_admin.ensure_daemon(wait=1.0)
    msg = str(exc.value)
    assert "remote daemon unreachable" in msg
    assert "client-only mode" in msg


def test_client_mode_ensure_daemon_does_not_spawn_subprocess(monkeypatch):
    """Belt-and-suspenders: ensure no `Popen` is called in client-only mode."""
    monkeypatch.setenv("IPH_CONNECT", "tcp://127.0.0.1:1")
    monkeypatch.delenv("IPH_BIND", raising=False)
    called = {"popen": 0}
    real_popen = subprocess.Popen

    def _spy(*a, **kw):
        called["popen"] += 1
        return real_popen(*a, **kw)

    monkeypatch.setattr("iphone_harness.admin.subprocess.Popen", _spy)
    with pytest.raises(RuntimeError):
        iph_admin.ensure_daemon(wait=1.0)
    assert called["popen"] == 0


# ---- CLI --remote-daemon flag wiring -------------------------------------

def _run_cli(args, env_extra=None):
    """Run `mobile-use <args>` as a subprocess; return (rc, stdout, stderr)."""
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", *args],
        env=env, capture_output=True, text=True, timeout=15.0,
        cwd=str(REPO_ROOT),
    )
    return p.returncode, p.stdout, p.stderr


def test_cli_help_advertises_remote_daemon():
    """`mobile-use --help` must show the --remote-daemon flag (verify hook)."""
    rc, out, _ = _run_cli(["--help"])
    assert rc == 0
    assert "--remote-daemon" in out


def test_cli_help_advertises_headed_flag():
    rc, out, _ = _run_cli(["--help"])
    assert rc == 0
    assert "--headed" in out
    assert "--headless" in out


def test_cli_remote_daemon_invalid_uri_exits():
    """Bad URI should fail fast with a clear message (not 'remote daemon unreachable')."""
    rc, out, err = _run_cli(["--ios", "--remote-daemon", "not_a_uri", "-c", "pass"])
    assert rc != 0
    combined = out + err
    assert "Invalid --remote-daemon URI" in combined or "Invalid" in combined


# ---- _platform helpers ---------------------------------------------------

def test_platform_is_windows_only_on_win32(monkeypatch):
    """is_windows() reflects sys.platform; not host-detected via shell."""
    from mobile_use import _platform
    # Use the actual sys.platform — the function is a thin wrapper.
    import sys as _sys
    assert _platform.is_windows() is (_sys.platform == "win32")


def test_platform_needs_remote_mac_for_ios_on_non_darwin():
    """On macOS, needs_remote_mac_for_ios() is False; elsewhere True."""
    from mobile_use import _platform
    import sys as _sys
    expected = _sys.platform != "darwin"
    assert _platform.needs_remote_mac_for_ios() is expected


def test_platform_windows_ios_setup_hint_mentions_remote_daemon():
    from mobile_use import _platform
    hint = _platform.windows_ios_setup_hint()
    assert "--remote-daemon" in hint
    assert "tcp://" in hint
    assert "Mac" in hint
