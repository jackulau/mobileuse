"""install_app / uninstall_app / push_file / pull_file — both platforms.

Device-free: _send is faked with an in-memory store that mirrors the mock
daemons' push/pull round-trip behavior.
"""
import base64

import pytest

import android_harness.helpers as ah
import iphone_harness.helpers as ih

PLATFORMS = [ih, ah]


def _fake_daemon_send(store):
    """A _send fake implementing the daemon's file-verb contract."""
    def send(req, **kw):
        method = req.get("method")
        p = req.get("params") or {}
        if method == "install_app":
            return {"result": {"installed": p["path"]}}
        if method == "uninstall_app":
            return {"result": {"removed": p["bundle_id"]}}
        if method == "push_file":
            store[p["remote"]] = p["data_b64"]
            return {"result": {"pushed": p["remote"]}}
        if method == "pull_file":
            return {"result": {"data_b64": store[p["remote"]]}}
        raise AssertionError(f"unexpected method {method!r}")
    return send


@pytest.mark.parametrize("mod", PLATFORMS)
def test_verbs_exist_on_both_platforms(mod):
    for name in ("install_app", "uninstall_app", "push_file", "pull_file"):
        assert hasattr(mod, name) and callable(getattr(mod, name)), \
            f"{mod.__name__} missing {name}"


@pytest.mark.parametrize("mod", PLATFORMS)
def test_install_app_sends_path(mod, monkeypatch):
    store = {}
    monkeypatch.setattr(mod, "_send", _fake_daemon_send(store))
    out = mod.install_app("/tmp/My.app")
    assert out == {"installed": "/tmp/My.app"}


@pytest.mark.parametrize("mod", PLATFORMS)
def test_uninstall_app_sends_bundle(mod, monkeypatch):
    store = {}
    monkeypatch.setattr(mod, "_send", _fake_daemon_send(store))
    out = mod.uninstall_app("com.x.app")
    assert out == {"removed": "com.x.app"}


@pytest.mark.parametrize("mod", PLATFORMS)
def test_push_pull_round_trip(mod, monkeypatch, tmp_path):
    store = {}
    monkeypatch.setattr(mod, "_send", _fake_daemon_send(store))
    local = tmp_path / "notes.txt"
    local.write_bytes(b"hello device \xf0\x9f\x93\xb1")
    mod.push_file(local, "/Documents/notes.txt")
    # Content travels base64 (remote-transport safe).
    assert base64.b64decode(store["/Documents/notes.txt"]) == b"hello device \xf0\x9f\x93\xb1"

    out = tmp_path / "back.txt"
    got = mod.pull_file("/Documents/notes.txt", out)
    assert got == str(out)
    assert out.read_bytes() == b"hello device \xf0\x9f\x93\xb1"


# ---- daemon dispatch keys present (real daemons, no execution) -------------------

@pytest.mark.parametrize("daemon_mod", ["iphone_harness.daemon", "android_harness.daemon"])
def test_daemon_dispatch_keys_present(daemon_mod):
    import importlib
    d = importlib.import_module(daemon_mod)
    for key in ("install_app", "uninstall_app", "push_file", "pull_file"):
        assert key in d._DISPATCH, f"{daemon_mod} missing dispatch for {key}"


# ---- mock daemons honor the contract (parity with real dispatch) -------------------

@pytest.mark.parametrize("mock_mod", ["tests._mock_iphone_daemon", "tests._mock_android_daemon"])
def test_mock_daemons_round_trip(mock_mod):
    import asyncio
    import importlib
    m = importlib.import_module(mock_mod)
    d = m.MockDaemon()

    async def run():
        push = await d.handle({"method": "push_file",
                               "params": {"remote": "/x.txt",
                                          "data_b64": base64.b64encode(b"abc").decode()}})
        assert push["result"]["pushed"] == "/x.txt"
        pull = await d.handle({"method": "pull_file", "params": {"remote": "/x.txt"}})
        assert base64.b64decode(pull["result"]["data_b64"]) == b"abc"
        inst = await d.handle({"method": "install_app", "params": {"path": "/a.ipa"}})
        assert inst["result"]["installed"] == "/a.ipa"
        rm = await d.handle({"method": "uninstall_app", "params": {"bundle_id": "com.y"}})
        assert rm["result"]["removed"] == "com.y"

    asyncio.run(run())


# ---- agent integration: verbs curated; uninstall destructive-gated ------------------

def test_verbs_registered_in_action_schema():
    from mobile_use.agent_loop import ACTION_VERBS, DESTRUCTIVE_VERBS
    for v in ("install_app", "uninstall_app", "push_file", "pull_file"):
        assert v in ACTION_VERBS
    assert "uninstall_app" in DESTRUCTIVE_VERBS
