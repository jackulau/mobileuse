"""CLI --headed / --headless flag wiring tests.

Verifies the CLI:
  - documents --headed and --headless in --help
  - sets MOBILE_USE_HEADED=1 when --headed is passed
  - sets MOBILE_USE_HEADED=0 when --headless is passed (explicit opt-out)
  - leaves MOBILE_USE_HEADED unset when neither is passed (default = off)
  - actually spawns a ViewerServer when --headed is passed (covered via a
    monkeypatch + in-process call to the platform _run_* function)

The actual ViewerServer + MJPEG plumbing is exercised by test_viewer_mjpeg.py;
this file only checks the CLI hook fires.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args, env_extra=None, timeout=10.0):
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", *args],
        env=env, capture_output=True, text=True, timeout=timeout,
        cwd=str(REPO_ROOT),
    )
    return p.returncode, p.stdout, p.stderr


# ---- HELP docs -----------------------------------------------------------

def test_cli_headed_flag_advertised_in_help():
    rc, out, _ = _run_cli(["--help"])
    assert rc == 0
    assert "--headed" in out
    assert "--headless" in out
    # Implementation detail user shouldn't have to know — but the help should
    # mention the viewer so users understand what --headed does.
    assert "viewer" in out.lower() or "mirror" in out.lower()


# ---- env-var wiring (introspect via a script that prints the env var) ---

def _print_headed_env_script():
    """Tiny script: print the value of MOBILE_USE_HEADED."""
    return "import os; print('HEADED=' + repr(os.environ.get('MOBILE_USE_HEADED')))"


def test_cli_headed_flag_sets_env(monkeypatch):
    """--headed should set MOBILE_USE_HEADED=1 before _run_ios/_run_android runs.
    Verified by calling main() in-process and observing os.environ."""
    monkeypatch.delenv("MOBILE_USE_HEADED", raising=False)
    # Stub out _run_ios + _run_android so we don't actually invoke ensure_daemon.
    from mobile_use import cli
    seen = {}
    monkeypatch.setattr(cli, "_run_ios", lambda args: seen.setdefault("ios", os.environ.get("MOBILE_USE_HEADED")))
    monkeypatch.setattr(cli, "_run_android", lambda args: None)
    monkeypatch.setattr(cli, "_detect_platform", lambda: "ios")
    monkeypatch.setattr(sys, "argv", ["mobile-use", "--ios", "--headed", "-c", "pass"])
    cli.main()
    assert seen.get("ios") == "1"


def test_cli_headless_flag_sets_env_to_zero(monkeypatch):
    monkeypatch.delenv("MOBILE_USE_HEADED", raising=False)
    from mobile_use import cli
    seen = {}
    monkeypatch.setattr(cli, "_run_ios", lambda args: seen.setdefault("ios", os.environ.get("MOBILE_USE_HEADED")))
    monkeypatch.setattr(cli, "_run_android", lambda args: None)
    monkeypatch.setattr(cli, "_detect_platform", lambda: "ios")
    monkeypatch.setattr(sys, "argv", ["mobile-use", "--ios", "--headless", "-c", "pass"])
    cli.main()
    assert seen.get("ios") == "0"


def test_cli_default_no_headed_env(monkeypatch):
    """No --headed / --headless: env var NOT set by the CLI (preserves caller's value)."""
    monkeypatch.delenv("MOBILE_USE_HEADED", raising=False)
    from mobile_use import cli
    seen = {}
    monkeypatch.setattr(cli, "_run_ios", lambda args: seen.setdefault("ios", os.environ.get("MOBILE_USE_HEADED")))
    monkeypatch.setattr(cli, "_run_android", lambda args: None)
    monkeypatch.setattr(cli, "_detect_platform", lambda: "ios")
    monkeypatch.setattr(sys, "argv", ["mobile-use", "--ios", "-c", "pass"])
    cli.main()
    assert seen.get("ios") is None  # untouched


# ---- _maybe_start_viewer hook -------------------------------------------

def test_maybe_start_viewer_off_when_env_unset(monkeypatch):
    monkeypatch.delenv("MOBILE_USE_HEADED", raising=False)
    from mobile_use.cli import _maybe_start_viewer
    assert _maybe_start_viewer("ios") is None


def test_maybe_start_viewer_logs_and_returns_none_on_failure(monkeypatch, capsys):
    """If ViewerServer can't construct (no daemon, etc), the hook returns None
    + logs a soft warning rather than crashing the user's -c script."""
    monkeypatch.setenv("MOBILE_USE_HEADED", "1")
    # Force ViewerServer constructor to blow up.
    import mobile_use.viewer.server as vs
    real = vs.ViewerServer

    class _Boom(real):
        def __init__(self, *a, **kw):
            raise RuntimeError("viewer init blew up")

    monkeypatch.setattr(vs, "ViewerServer", _Boom)
    from mobile_use.cli import _maybe_start_viewer
    result = _maybe_start_viewer("ios")
    assert result is None
    err = capsys.readouterr().err
    assert "viewer failed to start" in err


def test_maybe_start_viewer_real(monkeypatch):
    """Smoke test: with a live mock daemon, _maybe_start_viewer spawns a real
    ViewerServer + opens a URL. We assert the URL is reachable."""
    import subprocess as _sp
    import sys as _sys
    import time as _time
    import urllib.request as _ur
    import uuid as _uuid

    import iphone_harness.helpers as ih
    from iphone_harness import _ipc as ipc

    name = f"tst{_uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("IPH_NAME", name)
    monkeypatch.setattr(ih, "NAME", name)
    monkeypatch.setenv("MOBILE_USE_HEADED", "1")
    # Disable webbrowser.open so test doesn't try to actually open a tab.
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **kw: True)

    p = _sp.Popen(
        [_sys.executable, "-m", "tests._mock_iphone_daemon"],
        env={**os.environ, "IPH_NAME": name},
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        cwd=str(REPO_ROOT), start_new_session=True,
    )
    try:
        # Wait for daemon up.
        deadline = _time.time() + 5.0
        while _time.time() < deadline:
            if ipc.ping(name, timeout=0.3):
                break
            _time.sleep(0.05)
        else:
            pytest.fail("mock daemon never came up")

        from mobile_use.cli import _maybe_start_viewer
        v = _maybe_start_viewer("ios")
        assert v is not None
        assert v.url.startswith("http://127.0.0.1:")
        try:
            with _ur.urlopen(v.url, timeout=2.0) as r:
                assert r.status == 200
        finally:
            v.stop()
    finally:
        try:
            s, _ = ipc.connect(name, timeout=1.0)
            ipc.request(s, None, {"meta": "shutdown"})
            s.close()
        except Exception:
            pass
        try:
            p.wait(timeout=3.0)
        except _sp.TimeoutExpired:
            p.kill(); p.wait(timeout=2.0)
        for ext in ("sock", "pid", "log"):
            try:
                (Path("/tmp") / f"iph-{name}.{ext}").unlink()
            except FileNotFoundError:
                pass
