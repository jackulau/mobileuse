"""D15 — throttle the per-request liveness probe + reactive session recovery.

_ensure_session() ran on every request and, when a driver already existed, fired
a full extra round-trip (mobile:activeAppInfo on iOS / current_activity on
Android) BEFORE the real command — so every step paid a constant extra
round-trip. Now the deep probe is throttled (skipped within PROBE_INTERVAL of a
successful command) and the dispatch path reconnects reactively on a session
error, preserving the auto-recovery guarantee.
"""
import asyncio
import importlib
import time

import pytest


class _FakeDriver:
    def __init__(self):
        self.session_id = "fake"
        self.probe_calls = 0
        self.window_calls = 0

    @property
    def current_activity(self):          # Android deep probe
        self.probe_calls += 1
        return ".Activity"

    def execute_script(self, script, args=None):  # iOS deep probe (activeAppInfo)
        if script == "mobile: activeAppInfo":
            self.probe_calls += 1
        return {"bundleId": "x"}

    def get_window_size(self):
        self.window_calls += 1
        return {"width": 390, "height": 844}

    def quit(self):
        pass


def _make_daemon(mod_name):
    daemon = importlib.import_module(mod_name)
    d = daemon.Daemon()
    d._loop = asyncio.get_event_loop()
    return daemon, d


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_probe_throttled_when_recent(mod_name):
    async def run():
        daemon, d = _make_daemon(mod_name)
        d.driver = _FakeDriver()
        d._last_ok = time.monotonic()  # a command just succeeded → skip deep probe
        await d.handle({"method": "window_size", "params": {}})
        await d.handle({"method": "window_size", "params": {}})
        return d
    d = asyncio.run(run())
    assert d.driver.window_calls == 2
    assert d.driver.probe_calls == 0, "deep probe must be throttled for back-to-back commands"


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_probe_runs_once_when_stale(mod_name):
    async def run():
        daemon, d = _make_daemon(mod_name)
        d.driver = _FakeDriver()
        d._last_ok = 0.0  # stale → first call runs the deep probe, then throttles
        await d.handle({"method": "window_size", "params": {}})
        await d.handle({"method": "window_size", "params": {}})
        return d
    d = asyncio.run(run())
    assert d.driver.probe_calls == 1, "deep probe should run exactly once after staleness, then throttle"


@pytest.mark.parametrize("mod_name", ["iphone_harness.daemon", "android_harness.daemon"])
def test_reactive_reconnect_on_session_error(mod_name):
    class _FlakyDriver(_FakeDriver):
        def __init__(self):
            super().__init__()
            self._fail_next = True

        def get_window_size(self):
            self.window_calls += 1
            if self._fail_next:
                self._fail_next = False
                raise RuntimeError("invalid session id: session does not exist")
            return {"width": 1, "height": 2}

    async def run():
        daemon, d = _make_daemon(mod_name)
        d.driver = _FlakyDriver()
        d._last_ok = time.monotonic()
        healthy = _FakeDriver()

        async def fake_connect():
            d.driver = healthy
        d._connect = fake_connect

        resp = await d.handle({"method": "window_size", "params": {}})
        return resp, d, healthy
    resp, d, healthy = asyncio.run(run())
    assert "result" in resp, f"reactive reconnect should recover, got {resp}"
    # After the session error the daemon reconnected and retried on the healthy
    # driver (width 390), instead of surfacing the error.
    assert resp["result"]["width"] == 390
    assert d.driver is healthy
    assert healthy.window_calls == 1, "retry must run on the reconnected driver"
