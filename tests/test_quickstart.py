"""Unit tests for mobile_use.quickstart (mobile-use quickstart)."""
import pytest


def test_imports():
    from mobile_use.quickstart import main, run_doctor_phase, run_smoke_phase, run_appium_phase, appium_reachable  # noqa
    assert callable(main)
    assert callable(run_doctor_phase)
    assert callable(run_smoke_phase)
    assert callable(run_appium_phase)
    assert callable(appium_reachable)


def test_main_help_smoke(capsys):
    from mobile_use.quickstart import main
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Doctor + smoke" in out


def test_no_platform_no_devices_returns_2(monkeypatch, capsys):
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "_detect_platform", lambda: None)
    rc = quickstart.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Cannot detect platform" in err


def test_doctor_phase_short_circuits_on_fail(monkeypatch, capsys):
    """When doctor returns non-zero, main returns 1 and does not run smoke."""
    from mobile_use import quickstart

    monkeypatch.setattr(quickstart, "_detect_platform", lambda: "ios")
    monkeypatch.setattr(quickstart, "run_appium_phase", lambda **kw: (True, "stub"))
    # Bypass D5 Linux+ios short-circuit (test runs cross-platform in CI).
    monkeypatch.setenv("IPH_APPIUM_URL", "http://my-mac.local:4723")

    called = {"smoke": 0}
    def fake_smoke(p):
        called["smoke"] += 1
        return True, ""
    monkeypatch.setattr(quickstart, "run_smoke_phase", fake_smoke)

    def fake_doctor(p):
        return False, "doctor failed"
    monkeypatch.setattr(quickstart, "run_doctor_phase", fake_doctor)

    rc = quickstart.main([])
    assert rc == 1
    assert called["smoke"] == 0, "smoke phase must not run when doctor fails"


def test_skip_doctor_jumps_to_smoke(monkeypatch):
    from mobile_use import quickstart

    monkeypatch.setattr(quickstart, "_detect_platform", lambda: "android")
    monkeypatch.setattr(quickstart, "run_appium_phase", lambda **kw: (True, "stub"))

    called = {"doctor": 0, "smoke": 0}
    monkeypatch.setattr(quickstart, "run_doctor_phase",
                        lambda p: (called.__setitem__("doctor", called["doctor"] + 1) or True, ""))
    monkeypatch.setattr(quickstart, "run_smoke_phase",
                        lambda p: (called.__setitem__("smoke", called["smoke"] + 1) or True, ""))

    rc = quickstart.main(["--skip-doctor"])
    assert rc == 0
    assert called["doctor"] == 0
    assert called["smoke"] == 1


def test_appium_phase_aborts_when_unreachable(monkeypatch):
    """If Appium server is down and not autostart, abort before doctor."""
    from mobile_use import quickstart

    monkeypatch.setattr(quickstart, "_detect_platform", lambda: "ios")
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(quickstart.shutil, "which", lambda c: "/usr/local/bin/appium")
    # Bypass the Linux+ios remote-Mac short-circuit added in D5 — this test
    # is exercising the Appium preflight branch, not the platform gate.
    monkeypatch.setenv("IPH_APPIUM_URL", "http://my-mac.local:4723")

    called = {"doctor": 0}
    monkeypatch.setattr(quickstart, "run_doctor_phase",
                        lambda p: (called.__setitem__("doctor", called["doctor"] + 1) or True, ""))

    rc = quickstart.main([])
    assert rc == 1
    assert called["doctor"] == 0, "doctor must not run when appium preflight fails"


def test_appium_phase_message_contains_remediation(monkeypatch, capsys):
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(quickstart.shutil, "which", lambda c: "/usr/local/bin/appium")

    ok, msg = quickstart.run_appium_phase(autostart=False)
    assert ok is False
    assert "appium --base-path /" in msg


def test_appium_phase_when_cli_missing(monkeypatch):
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(quickstart.shutil, "which", lambda c: None)

    ok, msg = quickstart.run_appium_phase(autostart=False)
    assert ok is False
    assert "mobile-use bootstrap" in msg


def test_appium_phase_passes_when_reachable(monkeypatch):
    from mobile_use import quickstart
    monkeypatch.setattr(quickstart, "appium_reachable", lambda *a, **kw: True)

    ok, msg = quickstart.run_appium_phase()
    assert ok is True
    assert "reachable" in msg


def test_appium_reachable_returns_false_on_url_error(monkeypatch):
    from mobile_use import quickstart
    import urllib.error
    def boom(url, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(quickstart.urllib.request, "urlopen", boom)
    assert quickstart.appium_reachable() is False
