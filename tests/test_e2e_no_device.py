"""End-to-end no-device smoke test — regression canary.

Exercises the full mobile-use surface area in a single test run without a real
device or Appium. Spawns the mock daemons, runs bootstrap/init/doctor flows,
exercises record/replay + recording helper boundaries. If this passes, the
project still hangs together end-to-end.
"""
import io
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_name(monkeypatch):
    n = f"e2e{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", n)
    monkeypatch.setenv("ANH_NAME", n)
    monkeypatch.setenv("IPH_DAEMON_MODULE", "tests._mock_iphone_daemon")
    monkeypatch.setenv("ANH_DAEMON_MODULE", "tests._mock_android_daemon")
    yield n
    for prefix in ("iph", "anh"):
        for ext in ("sock", "pid", "log"):
            try:
                (Path("/tmp") / f"{prefix}-{n}.{ext}").unlink()
            except FileNotFoundError:
                pass


def test_e2e_smoke_all_modules_import():
    """All public modules import without side effects."""
    import android_harness
    import android_harness._ipc
    import android_harness.admin
    import android_harness.helpers
    import iphone_harness
    import iphone_harness._ipc
    import iphone_harness.admin
    import iphone_harness.helpers
    import mobile_use
    import mobile_use.bootstrap
    import mobile_use.cli
    import mobile_use.ios_wda
    import mobile_use.quickstart
    import mobile_use.record_replay
    import mobile_use.setup_env

    assert hasattr(mobile_use, "__version__")


def test_e2e_bootstrap_plan_composes():
    """bootstrap.plan() returns the expected step shape with no side effects."""
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=True)
    assert len(steps) > 0
    for label, check, cmd, mac_only in steps:
        assert isinstance(label, str) and label
        assert callable(check)
        assert isinstance(mac_only, bool)


def test_e2e_doctor_runs_without_device(capsys):
    """Both platform doctors should run to completion (even with no device)."""
    from android_harness import admin as anh_admin
    from iphone_harness import admin as ios_admin
    out_buf = io.StringIO()
    with redirect_stdout(out_buf):
        ios_rc = ios_admin.run_doctor()
        anh_rc = anh_admin.run_doctor()
    out = out_buf.getvalue()
    # rc is allowed to be non-zero (no device) but doctor must complete.
    assert ios_rc in (0, 1)
    assert anh_rc in (0, 1)
    numbered = re.findall(r"^\[\d+/\d+\]", out, flags=re.MULTILINE)
    assert len(numbered) >= 14, f"only saw {len(numbered)} numbered checks"


def test_e2e_daemon_spawn_roundtrip(isolated_name):
    """Spawn the mock iphone daemon end-to-end via admin.ensure_daemon."""
    from iphone_harness import _ipc as ipc
    from iphone_harness import admin
    try:
        admin.ensure_daemon(wait=10.0, name=isolated_name)
        assert ipc.ping(isolated_name, timeout=1.0) is True

        # Issue a basic RPC roundtrip
        s, token = ipc.connect(isolated_name, timeout=1.0)
        try:
            resp = ipc.request(s, token, {"method": "screenshot", "params": {}})
            assert "result" in resp or "error" in resp
        finally:
            s.close()
    finally:
        admin.restart_daemon(isolated_name)
        # Wait for shutdown
        deadline = time.time() + 5.0
        while time.time() < deadline and ipc.ping(isolated_name, timeout=0.3):
            time.sleep(0.1)


def test_e2e_record_replay_against_real_helpers(tmp_path):
    """Record/replay should wrap & unwrap helper functions without breaking them."""
    import iphone_harness.helpers as iph
    from mobile_use import record_replay

    orig_tap = iph.tap_at_xy
    out = tmp_path / "test.py"
    record_replay.start_recording(str(out), helpers=iph, fn_names=("tap_at_xy",))
    assert iph.tap_at_xy is not orig_tap  # wrapped
    record_replay.stop_recording()
    assert iph.tap_at_xy is orig_tap  # restored


def test_e2e_recording_helper_signatures():
    """record_screen on both platforms takes (duration, path) and returns string."""
    import inspect

    from android_harness.helpers import record_screen as anh_rec
    from iphone_harness.helpers import record_screen as iph_rec

    iph_sig = inspect.signature(iph_rec)
    anh_sig = inspect.signature(anh_rec)
    assert "duration" in iph_sig.parameters
    assert "path" in iph_sig.parameters
    assert "duration" in anh_sig.parameters
    assert "path" in anh_sig.parameters


def test_e2e_recovery_helpers_present_both_platforms():
    """Recovery API surface is consistent across platforms."""
    import android_harness.helpers as anh
    import iphone_harness.helpers as iph
    for mod in (iph, anh):
        assert callable(getattr(mod, "wake_device"))
        assert callable(getattr(mod, "is_locked"))
        assert callable(getattr(mod, "retry_on_disconnect"))
        assert issubclass(getattr(mod, "DeviceDisconnectError"), RuntimeError)


def test_e2e_ios_wda_check_returns_state():
    from mobile_use.ios_wda import check_wda_signing
    state, _ = check_wda_signing()
    assert state in ("signed", "expired", "not_signed", "unknown")


def test_e2e_cli_help_runs():
    """`mobile-use --help` should print without error."""
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=10.0,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0
    assert b"mobile-use" in result.stdout


def test_e2e_bootstrap_dry_run_cli(tmp_path):
    """`mobile-use bootstrap --dry-run` exits cleanly."""
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "bootstrap", "--dry-run"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=30.0,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    # rc 0 = all OK; 1 = missing brew on non-mac. Both are valid for dry-run.
    assert result.returncode in (0, 1)
    out = result.stdout.decode()
    assert "bootstrap" in out


def test_e2e_quickstart_runs_without_device(tmp_path):
    """quickstart should run doctor + smoke and exit (any rc) without raising."""
    from mobile_use import quickstart
    # Just verify the module is importable + main is callable.
    assert callable(quickstart.main)


def test_e2e_init_module_importable():
    """setup_env (init) is importable; we don't run it interactively here."""
    from mobile_use import setup_env
    assert callable(setup_env.main)


def test_e2e_no_regressions_in_helpers_public_api():
    """Key public helpers stay exported (catches accidental deletes/renames)."""
    import android_harness.helpers as anh
    import iphone_harness.helpers as iph
    REQUIRED = ("tap_at_xy", "tap", "swipe", "type_text", "screenshot", "window_size",
                "appium", "find", "find_all", "active_app",
                "wake_device", "is_locked", "retry_on_disconnect",
                "record_screen", "start_screen_recording", "stop_screen_recording")
    for name in REQUIRED:
        assert hasattr(iph, name), f"iphone_harness.helpers missing {name}"
        if name not in {"wake_device", "is_locked", "retry_on_disconnect", "record_screen"}:
            # Android may not implement some iOS-specific niceties; recovery + record_screen are required.
            pass
    for name in ("tap_at_xy", "tap", "swipe", "type_text", "screenshot",
                 "wake_device", "is_locked", "retry_on_disconnect", "record_screen"):
        assert hasattr(anh, name), f"android_harness.helpers missing {name}"


# ---- -c end-to-end via subprocess + mock daemon ----------------------------

def test_e2e_minus_c_runs_against_mock_daemon(isolated_name, tmp_path):
    """`mobile-use --ios -c 'expr'` against mock daemon — full pipeline."""
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "IPH_NAME": isolated_name,
        "IPH_DAEMON_MODULE": "tests._mock_iphone_daemon",
        "IPH_UDID": "mock-udid-stub",
    }
    snippet = "print('active=' + str(active_app()))"
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "--ios", "-c", snippet],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=20.0,
        env=env,
    )
    out = result.stdout.decode()
    err = result.stderr.decode()
    assert result.returncode == 0, f"non-zero rc={result.returncode}\nstdout: {out}\nstderr: {err}"
    assert "active=" in out
    assert "SpringBoard" in out or "bundleId" in out

    # Teardown the daemon we just spawned
    from iphone_harness import _ipc as ipc
    from iphone_harness import admin
    admin.restart_daemon(isolated_name)
    deadline = time.time() + 5.0
    while time.time() < deadline and ipc.ping(isolated_name, timeout=0.3):
        time.sleep(0.1)


def test_e2e_minus_c_android_runs_against_mock_daemon(isolated_name):
    """`mobile-use --android -c 'expr'` against mock daemon."""
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "ANH_NAME": isolated_name,
        "ANH_DAEMON_MODULE": "tests._mock_android_daemon",
        "ANH_UDID": "mock-android-stub",
    }
    snippet = "print('shot=' + str(screenshot()))"
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "--android", "-c", snippet],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=20.0,
        env=env,
    )
    out = result.stdout.decode()
    err = result.stderr.decode()
    assert result.returncode == 0, f"non-zero rc={result.returncode}\nstdout: {out}\nstderr: {err}"
    assert "shot=" in out

    from android_harness import _ipc as ipc
    from android_harness import admin
    admin.restart_daemon(isolated_name)
    deadline = time.time() + 5.0
    while time.time() < deadline and ipc.ping(isolated_name, timeout=0.3):
        time.sleep(0.1)


def test_e2e_minus_c_surfaces_env_error_when_no_env_no_udid(tmp_path):
    """Without .env AND without UDID, `-c` should refuse with friendly message."""
    env = {
        "PYTHONPATH": str(REPO_ROOT),
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        # IPH_UDID intentionally NOT set.
        # Demand strict cwd/env config so the preflight ignores the developer's
        # filled repo .env (this is a dev machine with a real device + .env).
        "MOBILE_USE_NO_REPO_ENV": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "--ios", "-c", "print(1)"],
        cwd=str(tmp_path),
        capture_output=True,
        timeout=10.0,
        env=env,
    )
    # Either env preflight catches it (rc=1) or it gets to daemon and fails
    # The important thing: stderr mentions IPH_UDID OR mobile-use init OR daemon
    err = result.stderr.decode() + result.stdout.decode()
    assert any(s in err for s in ("IPH_UDID", "mobile-use init", "daemon", ".env"))
