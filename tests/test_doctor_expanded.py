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
