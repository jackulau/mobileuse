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


# ---- init must not destroy config it doesn't manage --------------------------
#
# Regression: render_env emitted only its fixed allowlist, so an init re-run
# silently dropped IPH_WDA_URL (wireless persistence), IPH_CAPS/ANH_CAPS,
# MU_* knobs, and hand-added keys.


def test_init_preserves_wifi_persist(tmp_path, monkeypatch):
    from mobile_use.setup_env import main, parse_env
    envf = tmp_path / ".env"
    envf.write_text(
        "# my hand-tuned config\n"
        "IPH_UDID=OLD-UDID\n"
        "IPH_WDA_URL=http://192.168.1.50:8100\n"
        "MY_CUSTOM_KEY=keepme\n"
        "MU_PREACT_DISMISS=0\n",
        encoding="utf-8",
    )
    # No devices detected; --yes keeps existing values.
    monkeypatch.setattr("mobile_use.setup_env.detect_devices",
                        lambda: {"ios": [], "android": []})
    rc = main(["--yes", "--path", str(envf)])
    assert rc == 0
    back = parse_env(envf)
    assert back["IPH_WDA_URL"] == "http://192.168.1.50:8100"
    assert back["MY_CUSTOM_KEY"] == "keepme"
    assert back["MU_PREACT_DISMISS"] == "0"
    assert back["IPH_UDID"] == "OLD-UDID"
    # Managed keys still scaffolded for the user to fill.
    assert "ANH_UDID" in back or "ANH_UDID=" in envf.read_text(encoding="utf-8")
    # Comments survive verbatim.
    assert "# my hand-tuned config" in envf.read_text(encoding="utf-8")


def test_init_refreshes_managed_key_in_place(tmp_path, monkeypatch):
    from mobile_use.setup_env import main, parse_env
    envf = tmp_path / ".env"
    envf.write_text(
        "IPH_WDA_URL=http://192.168.1.50:8100\n"
        "IPH_UDID=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("mobile_use.setup_env.detect_devices",
                        lambda: {"ios": ["NEW-UDID-123"], "android": []})
    rc = main(["--yes", "--path", str(envf)])
    assert rc == 0
    back = parse_env(envf)
    assert back["IPH_UDID"] == "NEW-UDID-123"
    assert back["IPH_WDA_URL"] == "http://192.168.1.50:8100"
    text = envf.read_text(encoding="utf-8")
    assert text.count("IPH_UDID=") == 1


def test_merge_env_text_updates_in_place_preserves_order():
    from mobile_use.setup_env import merge_env_text
    text = (
        "# header comment\n"
        "CUSTOM_FIRST=1\n"
        "IPH_UDID=old\n"
        "CUSTOM_LAST=2\n"
    )
    merged = merge_env_text(text, {"IPH_UDID": "new"})
    lines = [ln for ln in merged.splitlines() if ln]
    assert lines.index("CUSTOM_FIRST=1") < lines.index("IPH_UDID=new") < lines.index("CUSTOM_LAST=2")
    assert "# header comment" in merged


def test_write_env_fresh_file_still_renders_template(tmp_path):
    from mobile_use.setup_env import write_env
    target = tmp_path / "fresh.env"
    write_env(target, {"IPH_UDID": "X", "IPH_XCODE_ORG_ID": "T",
                       "IPH_WDA_BUNDLE_ID": "com.y", "ANH_UDID": "S"})
    text = target.read_text(encoding="utf-8")
    assert "# iOS (iphone-harness)" in text
    assert "# Android (android-harness)" in text


# ---- init and wireless --persist write the SAME file --------------------------


def test_env_target_path_agrees_with_devices_env_path(tmp_path, monkeypatch):
    import mobile_use.setup_env as se
    from mobile_use.devices import _env_path
    repo_env = tmp_path / "repo.env"
    ws_env = tmp_path / "ws.env"
    monkeypatch.setattr(se, "DEFAULT_ENV_PATH", repo_env)
    monkeypatch.setattr(se, "ALT_ENV_PATH", ws_env)

    # Neither exists -> both pick the repo default (where it will be created).
    assert se.env_target_path() == repo_env
    assert _env_path() == repo_env

    # Only workspace exists -> both pick workspace.
    ws_env.write_text("X=1\n", encoding="utf-8")
    assert se.env_target_path() == ws_env
    assert _env_path() == ws_env

    # Both exist -> repo wins (matches daemon load precedence).
    repo_env.write_text("Y=2\n", encoding="utf-8")
    assert se.env_target_path() == repo_env
    assert _env_path() == repo_env
