"""goal/022 D9 — ensure_daemon freshness window (_ENSURE_TTL).

When the daemon is alive, ensure_daemon used to open a fresh socket and run a
real device round-trip (activeAppInfo / active_app) on EVERY call. A verified
deep probe is now trusted for IPH_ENSURE_TTL / ANH_ENSURE_TTL seconds (default
10): within the window only the cheap local liveness ping runs. A dead daemon,
a failed probe, or a stale-session signal busts the memo, so error paths stay
exactly as reactive as before.
"""
import pytest

import android_harness.admin as aa
import iphone_harness.admin as ia


@pytest.fixture(autouse=True)
def _clean_memos():
    ia.ensure_cache_bust()
    aa.ensure_cache_bust()
    yield
    ia.ensure_cache_bust()
    aa.ensure_cache_bust()


def _wire(monkeypatch, admin, alive=True, probe_ok=True):
    """Stub the IPC layer; returns dicts counting probe + restart calls."""
    counts = {"probe": 0, "restart": 0, "spawn": 0}
    monkeypatch.setattr(admin, "is_remote_daemon", lambda name: False)
    monkeypatch.setattr(admin, "daemon_alive", lambda name: alive)

    class _Sock:
        def close(self):
            pass

    def fake_connect(name, timeout=None):
        return _Sock(), "tok"

    def fake_request(sock, token, req):
        counts["probe"] += 1
        if probe_ok:
            return {"result": {"bundleId": "com.example"}}
        return {"error": "no session"}

    monkeypatch.setattr(admin.ipc, "connect", fake_connect)
    monkeypatch.setattr(admin.ipc, "request", fake_request)
    monkeypatch.setattr(admin, "restart_daemon",
                        lambda name=None: counts.__setitem__("restart", counts["restart"] + 1))
    # The spawn tail must never run in these tests — alive+probe_ok returns early.
    monkeypatch.setattr(admin, "cleanup_stale", lambda name=None: None)
    return counts


@pytest.mark.parametrize("admin", [ia, aa])
def test_within_ttl_skips_probe(monkeypatch, admin):
    counts = _wire(monkeypatch, admin, alive=True, probe_ok=True)
    admin.ensure_daemon(name="fresh-test")
    assert counts["probe"] == 1
    admin.ensure_daemon(name="fresh-test")
    admin.ensure_daemon(name="fresh-test")
    assert counts["probe"] == 1, "verified probe within TTL must be trusted"
    assert counts["restart"] == 0


@pytest.mark.parametrize("admin,env", [(ia, "IPH_ENSURE_TTL"),
                                       (aa, "ANH_ENSURE_TTL")])
def test_ttl_zero_disables_memo(monkeypatch, admin, env):
    monkeypatch.setenv(env, "0")
    counts = _wire(monkeypatch, admin, alive=True, probe_ok=True)
    admin.ensure_daemon(name="ttl0-test")
    admin.ensure_daemon(name="ttl0-test")
    assert counts["probe"] == 2, "TTL 0 must restore probe-every-call"


@pytest.mark.parametrize("admin", [ia, aa])
def test_expired_ttl_reprobes(monkeypatch, admin):
    counts = _wire(monkeypatch, admin, alive=True, probe_ok=True)
    admin.ensure_daemon(name="exp-test")
    # Age the memo entry past the TTL without sleeping.
    admin._ensure_ok_at["exp-test"] -= admin._ensure_ttl() + 1
    admin.ensure_daemon(name="exp-test")
    assert counts["probe"] == 2


@pytest.mark.parametrize("admin", [ia, aa])
def test_bust_forces_full_probe(monkeypatch, admin):
    counts = _wire(monkeypatch, admin, alive=True, probe_ok=True)
    admin.ensure_daemon(name="bust-test")
    admin.ensure_cache_bust("bust-test")
    admin.ensure_daemon(name="bust-test")
    assert counts["probe"] == 2, "bust must invalidate the freshness memo"


@pytest.mark.parametrize("admin", [ia, aa])
def test_memo_is_per_name(monkeypatch, admin):
    counts = _wire(monkeypatch, admin, alive=True, probe_ok=True)
    admin.ensure_daemon(name="dev-a")
    admin.ensure_daemon(name="dev-b")
    assert counts["probe"] == 2, "each daemon name has its own freshness window"
