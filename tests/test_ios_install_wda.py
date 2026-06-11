"""`mobile-use ios install-wda` — pre-signed WDA install via pymobiledevice3.

Device-free: subprocess fully stubbed, pymobiledevice3 presence faked.
"""
import subprocess
import sys

import pytest

from mobile_use import ios_wda

# ---- _pymobiledevice3_cmd ------------------------------------------------------

def test_pmd3_prefers_console_script(monkeypatch):
    monkeypatch.setattr(ios_wda.shutil, "which",
                        lambda c: "/usr/local/bin/pymobiledevice3" if c == "pymobiledevice3" else None)
    assert ios_wda._pymobiledevice3_cmd() == ["/usr/local/bin/pymobiledevice3"]


def test_pmd3_module_fallback(monkeypatch):
    monkeypatch.setattr(ios_wda.shutil, "which", lambda c: None)
    import importlib.util
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: object() if name == "pymobiledevice3" else real_find(name))
    assert ios_wda._pymobiledevice3_cmd() == [sys.executable, "-m", "pymobiledevice3"]


def test_pmd3_absent_returns_none(monkeypatch):
    monkeypatch.setattr(ios_wda.shutil, "which", lambda c: None)
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert ios_wda._pymobiledevice3_cmd() is None


# ---- install_wda_main ------------------------------------------------------------

def _fake_ipa(tmp_path):
    ipa = tmp_path / "WebDriverAgent.ipa"
    ipa.write_bytes(b"PK\x03\x04fakezip")
    return ipa


def test_install_success_prints_next_steps(monkeypatch, tmp_path, capsys):
    ipa = _fake_ipa(tmp_path)
    seen = {}
    monkeypatch.setattr(ios_wda, "_pymobiledevice3_cmd", lambda: ["/fake/pmd3"])

    def fake_check_output(cmd, **k):
        seen["cmd"] = cmd
        return b"InstallationPackage: 100%\n"

    monkeypatch.setattr(ios_wda.subprocess, "check_output", fake_check_output)
    rc = ios_wda.install_wda_main([str(ipa)])
    assert rc == 0
    assert seen["cmd"][:3] == ["/fake/pmd3", "apps", "install"]
    out = capsys.readouterr().out
    assert "mobile-use ios wifi" in out
    assert "--persist" in out
    assert "tunnel" in out


def test_install_passes_udid(monkeypatch, tmp_path):
    ipa = _fake_ipa(tmp_path)
    seen = {}
    monkeypatch.setattr(ios_wda, "_pymobiledevice3_cmd", lambda: ["/fake/pmd3"])
    monkeypatch.setattr(ios_wda.subprocess, "check_output",
                        lambda cmd, **k: seen.update(cmd=cmd) or b"ok")
    rc = ios_wda.install_wda_main([str(ipa), "--udid", "00008140-AAA"])
    assert rc == 0
    assert "--udid" in seen["cmd"]
    assert "00008140-AAA" in seen["cmd"]


def test_install_missing_tool_actionable_hint(monkeypatch, tmp_path, capsys):
    ipa = _fake_ipa(tmp_path)
    monkeypatch.setattr(ios_wda, "_pymobiledevice3_cmd", lambda: None)
    rc = ios_wda.install_wda_main([str(ipa)])
    assert rc == 1
    assert "pip install pymobiledevice3" in capsys.readouterr().err


def test_install_failure_prints_checklist(monkeypatch, tmp_path, capsys):
    ipa = _fake_ipa(tmp_path)
    monkeypatch.setattr(ios_wda, "_pymobiledevice3_cmd", lambda: ["/fake/pmd3"])

    def boom(cmd, **k):
        raise subprocess.CalledProcessError(1, cmd, output=b"ApplicationVerificationFailed")

    monkeypatch.setattr(ios_wda.subprocess, "check_output", boom)
    rc = ios_wda.install_wda_main([str(ipa)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Checklist" in err
    assert "signed for THIS device" in err


def test_install_missing_ipa_is_usage_error(tmp_path, capsys):
    rc = ios_wda.install_wda_main([str(tmp_path / "ghost.ipa")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_install_no_args_usage(capsys):
    assert ios_wda.install_wda_main([]) == 2
    assert ios_wda.install_wda_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "pre-signed" in out.lower() or "PRE-SIGNED" in out


# ---- cli routing ------------------------------------------------------------------

def test_cli_routes_ios_install_wda(monkeypatch):
    import mobile_use.cli as cli
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(ios_wda, "install_wda_main", fake_main)
    monkeypatch.setattr(sys, "argv",
                        ["mobile-use", "ios", "install-wda", "/x/wda.ipa", "--udid", "U1"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 0
    assert seen["argv"] == ["/x/wda.ipa", "--udid", "U1"]


# ---- cross-platform messaging --------------------------------------------------------

def test_windows_hint_mentions_install_wda(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from mobile_use._platform import windows_ios_setup_hint
    hint = windows_ios_setup_hint()
    assert "install-wda" in hint
    assert "ONCE" in hint
