"""D2 — installed Appium/driver version detection + doctor wiring.

Device-free + Appium-free: every probe is pinned via the FAKE seams
(MOBILE_USE_FAKE_APPIUM_VERSION / MOBILE_USE_FAKE_DRIVER_VERSIONS), so these
tests never depend on a live `appium` install.
"""
import io
from contextlib import redirect_stdout

from mobile_use import versions as V


def test_appium_version_fake_seam(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "3.0.1")
    assert V.appium_version() == "3.0.1"


def test_appium_version_fake_empty_means_absent(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "")
    assert V.appium_version() is None


def test_installed_driver_version_fake_seam(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=7.0.1,uiautomator2=3.1.0")
    assert V.installed_driver_version("xcuitest") == "7.0.1"
    assert V.installed_driver_version("uiautomator2") == "3.1.0"
    assert V.installed_driver_version("espresso") is None  # not listed


def test_check_toolchain_appium_absent_returns_info_only(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "")
    results = V.check_toolchain_versions()
    assert len(results) == 1
    level, msg = results[0]
    assert level == "info"
    assert "Appium not detected" in msg


def test_check_toolchain_all_current_is_ok(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "3.0.1")
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=9.0.0,uiautomator2=4.0.0")
    results = V.check_toolchain_versions()
    levels = [lvl for lvl, _ in results]
    assert levels == ["ok", "ok", "ok"]


def test_check_toolchain_old_driver_warns(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "3.0.1")
    # xcuitest below XCUITEST_MIN -> warn; uiautomator2 healthy -> ok.
    old_xc = f"{V.XCUITEST_MIN[0] - 1}.0.0"
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", f"xcuitest={old_xc},uiautomator2=4.0.0")
    levels = [lvl for lvl, _ in V.check_toolchain_versions()]
    assert "warn" in levels  # the old xcuitest driver
    # and the missing-driver case warns too
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "uiautomator2=4.0.0")
    miss = V.check_toolchain_versions()
    assert any(lvl == "info" and "xcuitest driver not detected" in msg for lvl, msg in miss)


def test_check_toolchain_appium2_ok_but_recommends_3(monkeypatch):
    # Appium 2 genuinely works (level ok), but the message recommends 3.x.
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "2.11.5")
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=9.0.0,uiautomator2=4.0.0")
    level, msg = V.check_toolchain_versions()[0]
    assert level == "ok"
    assert "recommended" in msg.lower()


def test_check_toolchain_appium1_warns(monkeypatch):
    # Appium 1 is below the minimum -> warn.
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "1.22.0")
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=9.0.0,uiautomator2=4.0.0")
    level, _msg = V.check_toolchain_versions()[0]
    assert level == "warn"


def test_toolchain_summary_text_structure(monkeypatch):
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "3.0.1")
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=9.0.0,uiautomator2=4.0.0")
    text = V.toolchain_summary_text()
    assert "Supported versions" in text
    assert "Detected toolchain" in text
    assert "3.0.1" in text


def _run_doctor_output(harness_module, monkeypatch):
    # Pin the toolchain so the version block is deterministic regardless of host.
    monkeypatch.setenv("MOBILE_USE_FAKE_APPIUM_VERSION", "3.0.1")
    monkeypatch.setenv("MOBILE_USE_FAKE_DRIVER_VERSIONS", "xcuitest=9.0.0,uiautomator2=4.0.0")
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            harness_module.run_doctor()
        except SystemExit:
            pass
    return buf.getvalue()


def test_ios_doctor_includes_version_block(monkeypatch):
    from iphone_harness import admin
    out = _run_doctor_output(admin, monkeypatch)
    assert "Supported versions" in out
    assert "Detected toolchain" in out


def test_android_doctor_includes_version_block(monkeypatch):
    from android_harness import admin
    out = _run_doctor_output(admin, monkeypatch)
    assert "Supported versions" in out
    assert "Detected toolchain" in out
