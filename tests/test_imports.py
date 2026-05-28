"""Smoke tests — verify all modules import and CLI entry points resolve."""


def test_import_mobile_use():
    import mobile_use
    assert hasattr(mobile_use, "__version__")
    assert hasattr(mobile_use, "Device")
    assert hasattr(mobile_use, "DevicePool")


def test_import_iphone_harness():
    import iphone_harness
    from iphone_harness import _ipc, admin, daemon, helpers


def test_import_android_harness():
    import android_harness
    from android_harness import _ipc, admin, daemon, helpers


def test_import_shared_modules():
    from mobile_use import agent_loop, cli, multibox, session, skills


def test_cli_entry_points():
    from importlib.metadata import entry_points
    eps = {e.name: e for e in entry_points(group="console_scripts")
           if e.name in ("mobile-use", "iphone-harness", "android-harness")}
    assert "mobile-use" in eps
    assert "iphone-harness" in eps
    assert "android-harness" in eps
    assert eps["mobile-use"].value == "mobile_use.cli:main"
    assert eps["iphone-harness"].value == "iphone_harness.run:main"
    assert eps["android-harness"].value == "android_harness.run:main"


def test_version_string():
    from mobile_use import __version__
    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) >= 2
