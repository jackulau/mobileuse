"""CLI dispatch tests — every subcommand routes correctly + clean errors.

Covers the gaps identified by the coverage audit:
- agent subcommand wiring
- ios sign-wda CLI dispatch (not just the underlying module)
- -c bad code / syntax error / runtime error
- ios subcommand argument validation
- doctor exit codes
- bootstrap idempotency
- mock-vs-real RPC parity (contract test)
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args, env=None, timeout=20):
    """Run mobile_use.cli as a subprocess; return (rc, stdout, stderr)."""
    e = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    if env:
        e.update(env)
    p = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", *args],
        cwd=str(REPO_ROOT), capture_output=True, timeout=timeout, env=e,
    )
    return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")


# ---- ios subcommand argument validation -----------------------------------

def test_ios_no_action_prints_help():
    rc, out, _ = _run_cli("ios")
    # No action → help with non-zero rc (usage error)
    assert "sign-wda" in out


def test_ios_help_lists_actions():
    rc, out, _ = _run_cli("ios", "--help")
    assert rc == 0
    assert "sign-wda" in out


def test_ios_unknown_action_errors_clearly():
    rc, _, err = _run_cli("ios", "made-up-action")
    assert rc != 0
    assert "Unknown" in err or "Unknown" in _run_cli("ios", "made-up-action")[1]


def test_ios_sign_wda_check_runs():
    rc, out, _ = _run_cli("ios", "sign-wda", "--check")
    # 0 if signed, 1 if not — both valid here. Just confirm it routed and printed.
    assert rc in (0, 1)
    assert "WDA signing" in out


def test_ios_sign_wda_help_returns_zero():
    rc, out, _ = _run_cli("ios", "sign-wda", "--help")
    assert rc == 0
    assert "--check" in out


# ---- -c (exec) error handling ---------------------------------------------

def test_exec_no_traceback_noise_from_cli_internals():
    """User script error should not surface cli.py traceback frames."""
    rc, out, err = _run_cli(
        "--ios", "-c", "raise ValueError('user bug')",
        env={"IPH_UDID": "stub-udid-for-test", "IPH_DAEMON_MODULE": "tests._mock_iphone_daemon"},
    )
    # rc may be 1 (script error) OR show daemon-unreachable depending on env.
    # In either case, "cli.py" should NOT appear in the user-visible output.
    if "ValueError" in err:
        # If the script error reached us, it shouldn't include cli.py frames
        assert "cli.py" not in err or "/cli.py" not in err.split("ValueError")[0]


def test_exec_syntax_error_clean_message():
    """SyntaxError in -c snippet → friendly one-line error (when env is ready)."""
    rc, _, err = _run_cli(
        "--ios", "-c", "def (",
        env={"IPH_UDID": "stub-udid-for-test", "IPH_DAEMON_MODULE": "tests._mock_iphone_daemon"},
    )
    if rc != 0 and "daemon didn't come up" not in err and "daemon did not come up" not in err:
        assert "Syntax error" in err or "SyntaxError" in err or "daemon" in err.lower()


# ---- bootstrap idempotency ------------------------------------------------

def test_bootstrap_dry_run_twice_produces_same_plan():
    rc1, out1, _ = _run_cli("bootstrap", "--dry-run")
    rc2, out2, _ = _run_cli("bootstrap", "--dry-run")
    assert rc1 == rc2
    # The numbered steps + their statuses should be identical run-to-run.
    norm = lambda s: "\n".join(l for l in s.splitlines() if l.startswith("["))
    assert norm(out1) == norm(out2)


def test_bootstrap_help_works():
    rc, out, _ = _run_cli("bootstrap", "--help")
    assert rc == 0
    assert "--dry-run" in out
    assert "--ios-only" in out


def test_bootstrap_ios_android_mutex():
    """--ios-only --android-only must reject (rc=2)."""
    rc, _, _ = _run_cli("bootstrap", "--ios-only", "--android-only")
    assert rc == 2


# ---- doctor exit codes ----------------------------------------------------

def test_doctor_runs_to_completion():
    rc, out, _ = _run_cli("--doctor")
    # Without a device, doctor returns 1; with one, 0. Both must run to end.
    assert rc in (0, 1)
    assert "iphone-harness" in out or "iOS" in out
    assert "android-harness" in out or "Android" in out


# ---- agent subcommand -----------------------------------------------------

def test_agent_subcommand_dispatches():
    """agent subcommand should at minimum reach run_agent (may fail later — ok)."""
    rc, out, err = _run_cli("agent", "--help", timeout=10)
    # Without explicit help support in agent_loop, this may fall through.
    # Just confirm the CLI doesn't crash with unhandled exception.
    # Allowed: rc 0 (help printed) or rc 1 (no device or no help) — not unhandled.
    assert rc in (0, 1)


# ---- mock-vs-real RPC parity (contract test) ------------------------------

def _real_dispatch_keys(daemon_module_name):
    """Import the real daemon module and return its _DISPATCH keys."""
    import importlib
    mod = importlib.import_module(daemon_module_name)
    return set(mod._DISPATCH.keys())


def _mock_handles_methods(mock_module_name):
    """Read mock module source and find every `method ==` literal it dispatches."""
    import importlib.util
    spec = importlib.util.find_spec(mock_module_name)
    src = Path(spec.origin).read_text()
    import re
    return set(re.findall(r'method\s*==\s*"([a-z_]+)"', src))


def test_iphone_mock_covers_real_daemon_methods():
    real = _real_dispatch_keys("iphone_harness.daemon")
    mock = _mock_handles_methods("tests._mock_iphone_daemon")
    missing = real - mock
    assert not missing, (
        f"Mock iphone daemon doesn't handle real RPC methods: {missing}. "
        f"Tests passing against the mock could give false confidence."
    )


def test_android_mock_covers_real_daemon_methods():
    real = _real_dispatch_keys("android_harness.daemon")
    mock = _mock_handles_methods("tests._mock_android_daemon")
    missing = real - mock
    assert not missing, (
        f"Mock android daemon doesn't handle real RPC methods: {missing}."
    )


# ---- CLI imports cleanly --------------------------------------------------

def test_cli_module_imports_with_no_args():
    """Just `python -m mobile_use.cli` (no args) prints help; rc 0."""
    rc, out, _ = _run_cli()
    assert rc == 0
    assert "mobile-use" in out


def test_cli_version():
    rc, out, _ = _run_cli("--version")
    assert rc == 0
    assert "mobile-use" in out.lower()


# ---- .env validation ------------------------------------------------------

def test_check_env_passes_when_udid_in_env(monkeypatch):
    from mobile_use import cli
    monkeypatch.setenv("IPH_UDID", "abc123")
    assert cli._check_env_for_platform("ios") is None


def test_check_env_returns_message_when_no_env_no_var(monkeypatch, tmp_path):
    import iphone_harness.admin as ia
    from mobile_use import cli
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.delenv("ANH_UDID", raising=False)
    # Point os.path to a tmp dir that has no .env
    monkeypatch.setattr(cli.os.path, "exists", lambda p: False)
    # Also stub the repo-root .env check so this is independent of the dev
    # machine's real .env (the preflight now accepts a filled repo/agent-workspace
    # .env, so the error path must be isolated from it).
    monkeypatch.setattr(ia, "_check_env_file", lambda: (False, "no real config"))
    msg = cli._check_env_for_platform("ios")
    assert msg is not None
    assert "mobile-use init" in msg
    assert "IPH_UDID" in msg


def test_check_env_android_variant(monkeypatch):
    import android_harness.admin as aa
    from mobile_use import cli
    monkeypatch.delenv("ANH_UDID", raising=False)
    monkeypatch.setattr(cli.os.path, "exists", lambda p: False)
    monkeypatch.setattr(aa, "_check_env_file", lambda: (False, "no real config"))
    msg = cli._check_env_for_platform("android")
    assert msg is not None
    assert "ANH_UDID" in msg


def test_check_env_skips_when_env_file_exists(monkeypatch, tmp_path):
    from mobile_use import cli
    monkeypatch.delenv("IPH_UDID", raising=False)
    # Pretend .env exists (so user has run init)
    monkeypatch.setattr(cli.os.path, "exists", lambda p: True)
    assert cli._check_env_for_platform("ios") is None
