"""Tests for WDA signing helper (mobile_use/ios_wda.py).

No real device or Xcode required. Mocks provisioning profile reads and project
filesystem lookups.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from mobile_use import ios_wda


def test_module_has_main_entry():
    assert callable(ios_wda.main)


def test_module_has_check_function():
    assert callable(ios_wda.check_wda_signing)


def test_find_wda_project_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ios_wda, "WDA_PROJECT_CANDIDATES", (str(tmp_path / "nope"),))
    assert ios_wda.find_wda_project() is None


def test_find_wda_project_finds_existing(tmp_path, monkeypatch):
    real = tmp_path / "WebDriverAgent.xcodeproj"
    real.mkdir()
    monkeypatch.setattr(ios_wda, "WDA_PROJECT_CANDIDATES", (str(real),))
    assert ios_wda.find_wda_project() == real


def test_check_signing_returns_unknown_off_macos(monkeypatch):
    monkeypatch.setattr(ios_wda.sys, "platform", "linux")
    state, _ = ios_wda.check_wda_signing()
    assert state == "unknown"


def test_check_signing_returns_not_signed_when_no_profiles(monkeypatch):
    """No matching provisioning profile → not_signed."""
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    monkeypatch.setattr(ios_wda.shutil, "which", lambda _: "/usr/bin/security")
    monkeypatch.setattr(ios_wda, "_provisioning_profiles", lambda: iter(()))
    state, details = ios_wda.check_wda_signing(wda_bundle="com.test.wda")
    assert state == "not_signed"
    assert "com.test.wda" in details


def test_check_signing_returns_signed_when_valid_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    monkeypatch.setattr(ios_wda.shutil, "which", lambda _: "/usr/bin/security")
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    profile = {
        "Entitlements": {"application-identifier": "ABC123.com.test.wda"},
        "ExpirationDate": expiry,
        "TeamName": "TestTeam",
    }
    monkeypatch.setattr(ios_wda, "_provisioning_profiles",
                        lambda: iter([(tmp_path / "x.mobileprovision", profile)]))
    state, details = ios_wda.check_wda_signing(wda_bundle="com.test.wda")
    assert state == "signed"
    assert "TestTeam" in details


def test_check_signing_returns_expired_when_profile_past(monkeypatch, tmp_path):
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    monkeypatch.setattr(ios_wda.shutil, "which", lambda _: "/usr/bin/security")
    expiry = datetime.now(timezone.utc) - timedelta(days=3)
    profile = {
        "Entitlements": {"application-identifier": "ABC123.com.test.wda"},
        "ExpirationDate": expiry,
        "TeamName": "TestTeam",
    }
    monkeypatch.setattr(ios_wda, "_provisioning_profiles",
                        lambda: iter([(tmp_path / "x.mobileprovision", profile)]))
    state, details = ios_wda.check_wda_signing(wda_bundle="com.test.wda")
    assert state == "expired"
    assert "ago" in details


def test_check_signing_picks_latest_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    monkeypatch.setattr(ios_wda.shutil, "which", lambda _: "/usr/bin/security")
    older = datetime.now(timezone.utc) + timedelta(days=5)
    newer = datetime.now(timezone.utc) + timedelta(days=60)
    p1 = {"Entitlements": {"application-identifier": "ABC.com.test.wda"},
          "ExpirationDate": older, "TeamName": "Old"}
    p2 = {"Entitlements": {"application-identifier": "ABC.com.test.wda"},
          "ExpirationDate": newer, "TeamName": "New"}
    monkeypatch.setattr(ios_wda, "_provisioning_profiles",
                        lambda: iter([(tmp_path / "old.mp", p1),
                                      (tmp_path / "new.mp", p2)]))
    state, details = ios_wda.check_wda_signing(wda_bundle="com.test.wda")
    assert state == "signed"
    assert "New" in details


def test_main_check_only_returns_1_when_not_signed(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("not_signed", "test"))
    rc = ios_wda.main(["--check"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "signed" in out  # output still mentions "signed" (in "not_signed")


def test_main_check_only_returns_0_when_signed(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("signed", "valid"))
    rc = ios_wda.main(["--check"])
    assert rc == 0


def test_main_help_returns_0_and_prints_usage(capsys):
    rc = ios_wda.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sign-wda" in out.lower()
    assert "--check" in out


def test_main_no_check_when_signed(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("signed", "TestTeam"))
    rc = ios_wda.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "signed" in out


def test_main_when_not_signed_no_project_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("not_signed", "test"))
    monkeypatch.setattr(ios_wda, "find_wda_project", lambda: None)
    rc = ios_wda.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "appium driver install xcuitest" in out


def test_main_output_matches_doctor_pattern(monkeypatch, capsys):
    """The output must contain a recognizable state for the doctor + grep verify."""
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("expired", "30d ago"))
    ios_wda.main(["--check"])
    out = capsys.readouterr().out
    assert any(s in out for s in ("signed", "expired", "not signed"))


def test_matches_wda_app_id_suffix():
    profile = {"Entitlements": {"application-identifier": "ABC123.com.user.wda"}}
    assert ios_wda._matches_wda(profile, "com.user.wda") is True
    profile2 = {"Entitlements": {"application-identifier": "ABC123.com.other.app"}}
    assert ios_wda._matches_wda(profile2, "com.user.wda") is False


def test_matches_wda_handles_empty_entitlements():
    assert ios_wda._matches_wda({}, "com.user.wda") is False
    assert ios_wda._matches_wda({"Entitlements": None}, "com.user.wda") is False


def test_check_wda_built_returns_false_when_no_derived_data(tmp_path, monkeypatch):
    fake_home = tmp_path
    monkeypatch.setenv("HOME", str(fake_home))
    built, detail = ios_wda.check_wda_built()
    assert built is False
    assert "DerivedData" in detail or "not on macOS" in detail


@pytest.mark.skipif(sys.platform == "win32", reason="WebDriverAgent/Xcode is macOS-only; iOS-from-Windows uses a remote Mac bridge (non-goal)")
def test_check_wda_built_returns_false_when_no_app(tmp_path, monkeypatch):
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    derived = tmp_path / "Library/Developer/Xcode/DerivedData"
    derived.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    built, detail = ios_wda.check_wda_built()
    assert built is False
    assert "no WebDriverAgentRunner-Runner.app" in detail


@pytest.mark.skipif(sys.platform == "win32", reason="WebDriverAgent/Xcode is macOS-only; iOS-from-Windows uses a remote Mac bridge (non-goal)")
def test_check_wda_built_returns_true_when_app_present(tmp_path, monkeypatch):
    monkeypatch.setattr(ios_wda.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    app = tmp_path / "Library/Developer/Xcode/DerivedData/WebDriverAgent-abc/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app"
    app.mkdir(parents=True)
    built, detail = ios_wda.check_wda_built()
    assert built is True
    assert "built at" in detail


def test_build_main_help_returns_0(capsys):
    rc = ios_wda.build_main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "build-wda" in out
    assert "--check" in out


def test_build_main_check_returns_1_when_not_built(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_built", lambda: (False, "no app"))
    rc = ios_wda.build_main(["--check"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "not built" in out


def test_build_main_check_returns_0_when_built(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_built", lambda: (True, "built at WebDriverAgent-xxx"))
    rc = ios_wda.build_main(["--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WDA build: built" in out


def test_build_main_already_built_returns_0(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_built", lambda: (True, "fresh"))
    rc = ios_wda.build_main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already built" in out.lower()


def test_build_main_refuses_when_unsigned(monkeypatch, capsys):
    monkeypatch.setattr(ios_wda, "check_wda_built", lambda: (False, "missing"))
    monkeypatch.setattr(ios_wda, "check_wda_signing", lambda: ("not_signed", "no profile"))
    rc = ios_wda.build_main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "sign-wda" in out


def test_build_wda_returns_error_when_project_missing(monkeypatch):
    monkeypatch.setattr(ios_wda, "find_wda_project", lambda: None)
    rc, output = ios_wda.build_wda()
    assert rc == 1
    assert "appium driver install xcuitest" in output


def test_team_id_arg_empty_when_unset(monkeypatch):
    monkeypatch.delenv("IPH_XCODE_ORG_ID", raising=False)
    assert ios_wda._team_id_arg() == []


def test_team_id_arg_propagates(monkeypatch):
    monkeypatch.setenv("IPH_XCODE_ORG_ID", "ABCD123456")
    assert ios_wda._team_id_arg() == ["DEVELOPMENT_TEAM=ABCD123456"]


def test_bundle_id_arg_propagates(monkeypatch):
    monkeypatch.setenv("IPH_WDA_BUNDLE_ID", "com.example.wda")
    assert ios_wda._bundle_id_arg() == ["PRODUCT_BUNDLE_IDENTIFIER=com.example.wda"]
