"""Unit tests for mobile_use.quickstart (mobile-use quickstart)."""
import pytest


def test_imports():
    from mobile_use.quickstart import main, run_doctor_phase, run_smoke_phase  # noqa
    assert callable(main)
    assert callable(run_doctor_phase)
    assert callable(run_smoke_phase)


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

    called = {"doctor": 0, "smoke": 0}
    monkeypatch.setattr(quickstart, "run_doctor_phase",
                        lambda p: (called.__setitem__("doctor", called["doctor"] + 1) or True, ""))
    monkeypatch.setattr(quickstart, "run_smoke_phase",
                        lambda p: (called.__setitem__("smoke", called["smoke"] + 1) or True, ""))

    rc = quickstart.main(["--skip-doctor"])
    assert rc == 0
    assert called["doctor"] == 0
    assert called["smoke"] == 1
