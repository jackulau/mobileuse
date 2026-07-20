"""Guards for the expanded doctor on both harnesses.

Each `_check_*` must return a (bool, str) tuple. `run_doctor` must print at
least 8 numbered '[N/M]' check lines (we shipped 11 Android / 12 iOS).
"""
import io
import re
from contextlib import redirect_stdout

import pytest

CHECKS_BOTH = [
    "_check_brew_pkg",
    "_check_node",
    "_check_appium_installed",
    "_check_driver_installed",
    "_check_env_file",
    "_check_cli_on_path",
    "_check_python_pkg",
]


@pytest.mark.parametrize("name", CHECKS_BOTH)
def test_ios_check_signatures(name):
    from iphone_harness import admin
    fn = getattr(admin, name)
    # Try calling with a sensible argument (driver/cli/brew_pkg take one).
    if name in ("_check_driver_installed", "_check_brew_pkg"):
        res = fn("nonexistent-thing-xyz")
    elif name == "_check_cli_on_path":
        res = fn("nonexistent-cli-xyz")
    else:
        res = fn()
    assert isinstance(res, tuple) and len(res) == 2
    assert isinstance(res[0], bool)
    assert isinstance(res[1], str)


@pytest.mark.parametrize("name", CHECKS_BOTH)
def test_android_check_signatures(name):
    from android_harness import admin
    fn = getattr(admin, name)
    if name in ("_check_driver_installed", "_check_brew_pkg"):
        res = fn("nonexistent-thing-xyz")
    elif name == "_check_cli_on_path":
        res = fn("nonexistent-cli-xyz")
    else:
        res = fn()
    assert isinstance(res, tuple) and len(res) == 2
    assert isinstance(res[0], bool)
    assert isinstance(res[1], str)


def test_ios_check_xcode_returns_tuple():
    from iphone_harness.admin import _check_xcode
    res = _check_xcode()
    assert isinstance(res, tuple) and len(res) == 2


def _run_doctor_output(harness_module):
    buf = io.StringIO()
    with redirect_stdout(buf):
        harness_module.run_doctor()
    return buf.getvalue()


def test_ios_doctor_prints_8plus_numbered_checks():
    from iphone_harness import admin
    out = _run_doctor_output(admin)
    numbered = re.findall(r"^\[\d+/\d+\]", out, flags=re.MULTILINE)
    assert len(numbered) >= 8, f"only saw {len(numbered)} numbered lines:\n{out}"


def test_android_doctor_prints_8plus_numbered_checks():
    from android_harness import admin
    out = _run_doctor_output(admin)
    numbered = re.findall(r"^\[\d+/\d+\]", out, flags=re.MULTILINE)
    assert len(numbered) >= 8, f"only saw {len(numbered)} numbered lines:\n{out}"


def test_ios_doctor_includes_remediation_line_per_fail():
    """Every FAIL should have a Fix: line immediately after."""
    from iphone_harness import admin
    out = _run_doctor_output(admin)
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "FAIL:" in line:
            # Next line within reasonable distance should be a Fix line.
            window = "\n".join(lines[i + 1 : i + 3])
            assert "Fix:" in window, f"no Fix: after FAIL at line {i}:\n{line}\nwindow:\n{window}"


def test_android_doctor_includes_remediation_line_per_fail():
    from android_harness import admin
    out = _run_doctor_output(admin)
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "FAIL:" in line:
            window = "\n".join(lines[i + 1 : i + 3])
            assert "Fix:" in window, f"no Fix: after FAIL at line {i}:\n{line}\nwindow:\n{window}"


def test_error_messages_point_to_doctor():
    """_send wraps unreachable error with `--doctor` remediation."""
    import inspect

    import android_harness.helpers as ah
    import iphone_harness.helpers as ih
    src_i = inspect.getsource(ih._send)
    src_a = inspect.getsource(ah._send)
    assert "iphone-harness --doctor" in src_i
    assert "android-harness --doctor" in src_a
    assert "reload" in src_i
    assert "reload" in src_a


# ---- new doctor v2 checks --------------------------------------------------

def test_ios_check_wda_signing_returns_tuple():
    from iphone_harness.admin import _check_wda_signing
    res = _check_wda_signing()
    assert isinstance(res, tuple) and len(res) == 2
    assert isinstance(res[0], bool)
    assert isinstance(res[1], str)


def test_ios_check_battery_returns_tuple():
    from iphone_harness.admin import _check_battery
    res = _check_battery()
    assert isinstance(res, tuple) and len(res) == 2


def test_ios_check_device_empty_usb_hints_auto_lock(monkeypatch):
    # No devices on USB: the message must point at the phone locking / USB-restricted
    # mode (the common "worked a second ago, now gone" flap), not a bare list dump.
    from iphone_harness import admin
    monkeypatch.setenv("IPH_UDID", "ABC123")
    monkeypatch.setattr(admin.subprocess, "check_output", lambda *a, **k: b"")
    ok, msg = admin._check_device()
    assert ok is False
    assert ("Auto-Lock" in msg) or ("USB-restricted" in msg)


def test_ios_check_device_other_device_hints_udid(monkeypatch):
    # A different device is on USB -> point at IPH_UDID / wrong phone, NOT auto-lock.
    from iphone_harness import admin
    monkeypatch.setenv("IPH_UDID", "ABC123")
    monkeypatch.setattr(admin.subprocess, "check_output", lambda *a, **k: b"OTHERUDID\n")
    ok, msg = admin._check_device()
    assert ok is False
    assert "IPH_UDID" in msg
    assert "Auto-Lock" not in msg


def test_ios_check_device_present_is_ok(monkeypatch):
    from iphone_harness import admin
    monkeypatch.setenv("IPH_UDID", "ABC123")
    monkeypatch.setattr(admin.subprocess, "check_output", lambda *a, **k: b"ABC123\n")
    ok, msg = admin._check_device()
    assert ok is True
    assert "paired" in msg


def test_android_check_battery_returns_tuple():
    from android_harness.admin import _check_battery
    res = _check_battery()
    assert isinstance(res, tuple) and len(res) == 2


def test_android_check_screen_unlocked_returns_tuple():
    from android_harness.admin import _check_screen_unlocked
    res = _check_screen_unlocked()
    assert isinstance(res, tuple) and len(res) == 2


def test_ios_doctor_includes_wda_signing_line():
    from iphone_harness import admin
    out = _run_doctor_output(admin)
    assert "WebDriverAgent" in out or "WDA" in out


def test_ios_doctor_includes_battery_line():
    from iphone_harness import admin
    out = _run_doctor_output(admin)
    assert "battery" in out.lower()


def test_android_doctor_includes_battery_line():
    from android_harness import admin
    out = _run_doctor_output(admin)
    assert "battery" in out.lower()


def test_total_doctor_checks_at_least_14():
    """Combined iOS + Android numbered checks should be ≥14 (verify D8 target)."""
    import re

    from android_harness import admin as anh_admin
    from iphone_harness import admin as ios_admin
    ios_out = _run_doctor_output(ios_admin)
    anh_out = _run_doctor_output(anh_admin)
    ios_n = len(re.findall(r"^\[\d+/\d+\]", ios_out, flags=re.MULTILINE))
    anh_n = len(re.findall(r"^\[\d+/\d+\]", anh_out, flags=re.MULTILINE))
    assert ios_n + anh_n >= 14, f"only {ios_n} iOS + {anh_n} Android checks"


# ---- doctor reads .env (the env-file check and the device check must agree) --
#
# Old behavior: `.env with IPH_UDID` passed while `iPhone paired` said
# "IPH_UDID not set" — doctor contradicted itself because env-reading checks
# never sourced the .env the daemon loads.

import os


def test_ios_doctor_env_loader_reads_env_file(monkeypatch, tmp_path):
    from iphone_harness import admin, daemon
    envf = tmp_path / ".env"
    envf.write_text("IPH_UDID=DOCTOR-ENV-TEST\n", encoding="utf-8")
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.delenv("MOBILE_USE_NO_REPO_ENV", raising=False)
    monkeypatch.setattr(daemon, "_env_candidates", lambda: (envf,))
    admin._load_env_for_doctor()
    assert os.environ.get("IPH_UDID") == "DOCTOR-ENV-TEST"


def test_android_doctor_env_loader_reads_env_file(monkeypatch, tmp_path):
    from android_harness import admin, daemon
    envf = tmp_path / ".env"
    envf.write_text("ANH_UDID=DOCTOR-ENV-TEST\n", encoding="utf-8")
    monkeypatch.delenv("ANH_UDID", raising=False)
    monkeypatch.delenv("MOBILE_USE_NO_REPO_ENV", raising=False)
    monkeypatch.setattr(daemon, "_env_candidates", lambda: (envf,))
    admin._load_env_for_doctor()
    assert os.environ.get("ANH_UDID") == "DOCTOR-ENV-TEST"


def test_doctor_env_loader_never_overrides_real_env(monkeypatch, tmp_path):
    from iphone_harness import admin, daemon
    envf = tmp_path / ".env"
    envf.write_text("IPH_UDID=FROM-FILE\n", encoding="utf-8")
    monkeypatch.setenv("IPH_UDID", "FROM-ENV")
    monkeypatch.delenv("MOBILE_USE_NO_REPO_ENV", raising=False)
    monkeypatch.setattr(daemon, "_env_candidates", lambda: (envf,))
    admin._load_env_for_doctor()
    assert os.environ.get("IPH_UDID") == "FROM-ENV"


def test_doctor_env_loader_respects_no_repo_env(monkeypatch, tmp_path):
    from iphone_harness import admin, daemon
    envf = tmp_path / ".env"
    envf.write_text("IPH_UDID=SHOULD-NOT-LOAD\n", encoding="utf-8")
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.setenv("MOBILE_USE_NO_REPO_ENV", "1")
    monkeypatch.setattr(daemon, "_env_candidates", lambda: (envf,))
    admin._load_env_for_doctor()
    assert os.environ.get("IPH_UDID") is None


# ---- CLI-on-PATH remediation tells the truth ---------------------------------
#
# Old behavior: suggested `pip install -e .` even when the script was already
# installed and the actual problem was the scripts dir missing from PATH
# (framework Python's bin dir, e.g. /Library/Frameworks/.../3.X/bin).


def test_ios_cli_path_fix_detects_path_problem(monkeypatch, tmp_path):
    from iphone_harness import admin
    (tmp_path / "iphone-harness").touch()
    monkeypatch.setattr("sysconfig.get_path", lambda key: str(tmp_path))
    fix = admin._cli_path_fix("iphone-harness", "python3 -m iphone_harness.run")
    assert "not on PATH" in fix
    assert str(tmp_path) in fix
    assert "pip install" not in fix


def test_ios_cli_path_fix_not_installed_suggests_pip(monkeypatch, tmp_path):
    from iphone_harness import admin
    monkeypatch.setattr("sysconfig.get_path", lambda key: str(tmp_path))
    fix = admin._cli_path_fix("iphone-harness", "python3 -m iphone_harness.run")
    assert "pip install -e ." in fix
    assert "python3 -m iphone_harness.run" in fix


def test_android_cli_path_fix_detects_path_problem(monkeypatch, tmp_path):
    from android_harness import admin
    (tmp_path / "android-harness").touch()
    monkeypatch.setattr("sysconfig.get_path", lambda key: str(tmp_path))
    fix = admin._cli_path_fix("android-harness", "python3 -m android_harness.run")
    assert "not on PATH" in fix
    assert str(tmp_path) in fix
    assert "pip install" not in fix


def test_android_cli_path_fix_windows_exe_detected(monkeypatch, tmp_path):
    from android_harness import admin
    (tmp_path / "android-harness.exe").touch()
    monkeypatch.setattr("sysconfig.get_path", lambda key: str(tmp_path))
    fix = admin._cli_path_fix("android-harness", "python3 -m android_harness.run")
    assert "not on PATH" in fix
    assert "pip install" not in fix


def test_admin_appium_url_is_call_time(monkeypatch):
    """IPH/ANH_APPIUM_URL set AFTER import must be honored by doctor probes."""
    from android_harness import admin as anh_admin
    from iphone_harness import admin as ios_admin
    monkeypatch.setenv("IPH_APPIUM_URL", "http://late-mac:4723")
    monkeypatch.setenv("ANH_APPIUM_URL", "http://late-box:4723")
    assert ios_admin._appium_url() == "http://late-mac:4723"
    assert anh_admin._appium_url() == "http://late-box:4723"
