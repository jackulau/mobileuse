"""D17 — device-free unit tests for _send()'s retry + error classification.

_send() distinguishes three failure modes, none previously tested directly
(test_recovery only covered the retry_on_disconnect decorator, which monkeypatches
_send away):
  - transient OSError  -> drop conn, ensure_daemon(), retry; exhaust -> --doctor/--reload
  - server 'stale'/'session' error -> retry WITHOUT ensure_daemon; exhaust -> --reload
  - other server error -> raise immediately, no retry, no ensure_daemon
Both harnesses share this code path, so both are exercised.
"""
import importlib

import pytest

PAIRS = [
    ("iphone_harness.helpers", "iphone_harness.admin"),
    ("android_harness.helpers", "android_harness.admin"),
]


def _wire(monkeypatch, helpers_name, admin_name, request_impl):
    helpers = importlib.import_module(helpers_name)
    admin = importlib.import_module(admin_name)
    counts = {"ensure": 0, "request": 0}

    class _FakeSock:
        def settimeout(self, t):
            pass
    monkeypatch.setattr(helpers, "_get_conn", lambda timeout=5.0: (_FakeSock(), None))
    monkeypatch.setattr(helpers, "_drop_conn", lambda: None)
    monkeypatch.setattr(helpers.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(admin, "ensure_daemon", lambda *a, **k: counts.__setitem__("ensure", counts["ensure"] + 1))

    def _req(c, token, req):
        counts["request"] += 1
        return request_impl(counts["request"])
    monkeypatch.setattr(helpers.ipc, "request", _req)
    return helpers, counts


@pytest.mark.parametrize("helpers_name, admin_name", PAIRS)
def test_transient_oserror_recovers_with_one_ensure(helpers_name, admin_name, monkeypatch):
    def impl(n):
        if n == 1:
            raise OSError("connection reset")
        return {"result": {"ok": True}}
    helpers, counts = _wire(monkeypatch, helpers_name, admin_name, impl)
    r = helpers._send({"method": "window_size", "params": {}})
    assert r == {"result": {"ok": True}}
    assert counts["ensure"] == 1
    assert counts["request"] == 2


@pytest.mark.parametrize("helpers_name, admin_name", PAIRS)
def test_exhausted_oserror_raises_with_doctor_reload_hint(helpers_name, admin_name, monkeypatch):
    def impl(n):
        raise OSError("connection refused")
    helpers, counts = _wire(monkeypatch, helpers_name, admin_name, impl)
    with pytest.raises(RuntimeError) as ei:
        helpers._send({"method": "window_size", "params": {}})
    msg = str(ei.value)
    assert "--doctor" in msg or "--reload" in msg
    # ensure_daemon called once per retry (MAX_RETRIES).
    assert counts["ensure"] == helpers.MAX_RETRIES


@pytest.mark.parametrize("helpers_name, admin_name", PAIRS)
def test_stale_server_error_retries_without_ensure(helpers_name, admin_name, monkeypatch):
    def impl(n):
        return {"error": "session is stale"}
    helpers, counts = _wire(monkeypatch, helpers_name, admin_name, impl)
    with pytest.raises(RuntimeError) as ei:
        helpers._send({"method": "tap", "params": {}})
    assert "--reload" in str(ei.value)
    assert counts["ensure"] == 0, "stale path must NOT call ensure_daemon"
    assert counts["request"] >= 2, "stale path should retry"


@pytest.mark.parametrize("helpers_name, admin_name", PAIRS)
def test_non_stale_server_error_raises_immediately(helpers_name, admin_name, monkeypatch):
    def impl(n):
        return {"error": "element not found"}
    helpers, counts = _wire(monkeypatch, helpers_name, admin_name, impl)
    with pytest.raises(RuntimeError) as ei:
        helpers._send({"method": "tap", "params": {}})
    assert "element not found" in str(ei.value)
    assert counts["ensure"] == 0
    assert counts["request"] == 1, "non-stale error must NOT retry"
