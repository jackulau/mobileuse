"""Unit tests for mobile_use.bootstrap.

Mocks shutil.which + subprocess so tests run without any real install side
effects. Verifies plan() composition, OK vs MISSING accounting, and that
the dry-run path never invokes subprocess.check_call.
"""
import sys
from unittest.mock import patch

import pytest


def _stub_have(present):
    """Return a `_have` replacement that says yes for names in `present`."""
    def _have(cmd):
        return cmd in present
    return _have


def test_plan_includes_ios_and_android_by_default():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=True)
    labels = " ".join(label for label, *_ in steps)
    assert "xcuitest" in labels
    assert "uiautomator2" in labels
    assert "libimobiledevice" in labels
    assert "android-platform-tools" in labels


def test_plan_excludes_android_when_ios_only():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=True, android=False)
    labels = " ".join(label for label, *_ in steps)
    assert "uiautomator2" not in labels
    assert "android-platform-tools" not in labels
    assert "xcuitest" in labels


def test_plan_excludes_ios_when_android_only():
    from mobile_use import bootstrap
    steps = bootstrap.plan(ios=False, android=True)
    labels = " ".join(label for label, *_ in steps)
    assert "xcuitest" not in labels
    assert "libimobiledevice" not in labels
    assert "uiautomator2" in labels


def test_dry_run_does_not_invoke_subprocess(capsys, monkeypatch):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_have", _stub_have({"brew"}))
    monkeypatch.setattr(bootstrap, "_brew_has", lambda pkg: False)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda name: False)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: False)

    called = []
    def fake_check_call(*a, **kw):
        called.append(a)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)

    rc = bootstrap.run(dry_run=True)
    assert called == [], "dry_run must not call subprocess.check_call"
    out = capsys.readouterr().out
    assert "would run" in out


def test_run_returns_0_when_everything_installed(monkeypatch, capsys):
    from mobile_use import bootstrap
    monkeypatch.setattr(bootstrap, "_have", _stub_have({"brew", "node", "npm", "appium"}))
    monkeypatch.setattr(bootstrap, "_brew_has", lambda pkg: True)
    monkeypatch.setattr(bootstrap, "_appium_driver_installed", lambda name: True)
    monkeypatch.setattr(bootstrap, "_python_pkg_importable", lambda: True)

    rc = bootstrap.run(dry_run=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "bootstrap complete" in out


def test_main_rejects_both_platform_flags(capsys):
    from mobile_use import bootstrap
    rc = bootstrap.main(["--ios-only", "--android-only"])
    assert rc == 2


def test_main_dry_run_smoke():
    from mobile_use import bootstrap
    # Should never raise even on a partially-set-up dev box.
    rc = bootstrap.main(["--dry-run"])
    assert rc in (0, 1)  # 0 if everything installed; 1 if brew missing
