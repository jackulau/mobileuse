"""Unit tests for mobile_use.setup_env (mobile-use init)."""
from unittest.mock import patch

import pytest


def test_parse_env_missing_file(tmp_path):
    from mobile_use.setup_env import parse_env
    assert parse_env(tmp_path / "nope.env") == {}


def test_parse_env_strips_quotes_and_comments(tmp_path):
    from mobile_use.setup_env import parse_env
    p = tmp_path / "e"
    p.write_text("""
# comment
IPH_UDID="abc"
ANH_UDID=def
EMPTY=
    """)
    out = parse_env(p)
    assert out["IPH_UDID"] == "abc"
    assert out["ANH_UDID"] == "def"
    assert out["EMPTY"] == ""


def test_has_real_value_rejects_placeholders():
    from mobile_use.setup_env import has_real_value
    assert has_real_value({"K": "real"}, "K")
    assert not has_real_value({"K": ""}, "K")
    assert not has_real_value({"K": "YOUR-IPHONE-UDID-HERE"}, "K")
    assert not has_real_value({"K": "YOURTEAMID"}, "K")
    assert not has_real_value({}, "K")


def test_build_env_yes_mode_preserves_existing(monkeypatch):
    from mobile_use.setup_env import build_env
    devices = {"ios": ["UDID-A"], "android": []}
    existing = {"IPH_XCODE_ORG_ID": "ABCDE12345"}
    out = build_env(existing=existing, devices=devices, ios=True, android=False, yes=True)
    assert out["IPH_UDID"] == "UDID-A"
    assert out["IPH_XCODE_ORG_ID"] == "ABCDE12345"  # preserved
    assert "IPH_WDA_BUNDLE_ID" in out  # defaulted


def test_build_env_does_not_overwrite_existing_real_values(monkeypatch):
    from mobile_use.setup_env import build_env
    existing = {"IPH_UDID": "EXISTING", "IPH_XCODE_ORG_ID": "TEAM",
                "IPH_WDA_BUNDLE_ID": "com.existing.wda"}
    devices = {"ios": ["NEW-UDID"], "android": []}
    out = build_env(existing=existing, devices=devices, ios=True, android=False, yes=True)
    assert out["IPH_UDID"] == "EXISTING"  # device detection did not overwrite
    assert out["IPH_XCODE_ORG_ID"] == "TEAM"
    assert out["IPH_WDA_BUNDLE_ID"] == "com.existing.wda"


def test_build_env_auto_fills_single_android():
    from mobile_use.setup_env import build_env
    devices = {"ios": [], "android": ["SERIAL-42"]}
    out = build_env(existing={}, devices=devices, ios=False, android=True, yes=True)
    assert out["ANH_UDID"] == "SERIAL-42"


def test_render_env_includes_required_keys():
    from mobile_use.setup_env import render_env
    text = render_env({"IPH_UDID": "U", "IPH_XCODE_ORG_ID": "T",
                       "IPH_WDA_BUNDLE_ID": "com.x.wda", "ANH_UDID": "S"})
    assert "IPH_UDID=U" in text
    assert "ANH_UDID=S" in text


def test_write_env_round_trips(tmp_path):
    from mobile_use.setup_env import parse_env, write_env
    target = tmp_path / "env"
    write_env(target, {"IPH_UDID": "X", "IPH_XCODE_ORG_ID": "T",
                       "IPH_WDA_BUNDLE_ID": "com.y", "ANH_UDID": "S"})
    back = parse_env(target)
    assert back["IPH_UDID"] == "X"
    assert back["ANH_UDID"] == "S"


def test_main_yes_print_smoke(capsys):
    from mobile_use.setup_env import main
    rc = main(["--yes", "--print"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "IPH_UDID=" in out


def test_main_rejects_both_platform_flags(capsys):
    from mobile_use.setup_env import main
    rc = main(["--ios-only", "--android-only"])
    assert rc == 2
