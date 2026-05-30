"""Regression: the daemon must quit its WebDriver session on shutdown and route
SIGTERM through the graceful stop path, so `--reload` / container-stop don't
orphan the XCUITest/UIAutomator2 session on the device until newCommandTimeout.
"""
import asyncio
import importlib
import inspect
import uuid

import pytest


class _FakeDriver:
    def __init__(self):
        self.quit_called = False
        self.session_id = "fake-session"

    def quit(self):
        self.quit_called = True


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_shutdown_quits_driver(mod_name, monkeypatch):
    daemon = importlib.import_module(mod_name)
    name = f"sd{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(daemon, "NAME", name)
    fake = _FakeDriver()

    async def run():
        d = daemon.Daemon()
        d.stop = asyncio.Event()
        d._loop = asyncio.get_running_loop()
        d.driver = fake
        d.stop.set()  # request immediate graceful shutdown
        await daemon.serve(d)
        return d

    d = asyncio.run(run())
    assert fake.quit_called is True, "serve() shutdown must quit the WebDriver session"
    assert d.driver is None
    # endpoint cleaned up
    from importlib import import_module
    ipc = import_module(mod_name.split(".")[0] + "._ipc")
    import os
    assert not os.path.exists(ipc.sock_addr(name))


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_start_registers_sigterm_handler(mod_name):
    daemon = importlib.import_module(mod_name)
    src = inspect.getsource(daemon.Daemon.start)
    assert "add_signal_handler" in src and "SIGTERM" in src, (
        "start() must route SIGTERM through the graceful stop path"
    )
