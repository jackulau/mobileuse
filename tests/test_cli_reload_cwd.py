"""D12 — `mobile-use --reload` (no device) and `-c` from a foreign cwd.

Two documented recovery flows used to break:
(1) Bare `mobile-use --reload` with no device hit the auto-detect gate and died
    with "Cannot auto-detect platform" — the exact stale-state case it exists for.
(2) `-c` from any non-repo directory falsely aborted with an env error even after
    a successful `init`, because the preflight only checked cwd/.env while the
    daemon loads .env from the repo root / agent-workspace.
"""
import sys

import mobile_use.cli as cli


def test_bare_reload_nukes_both_without_device(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_reload_both", lambda: calls.append("reload"))
    monkeypatch.setattr(cli, "_detect_platform", lambda: None)
    monkeypatch.setattr(sys, "argv", ["mobile-use", "--reload"])
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.delenv("ANH_UDID", raising=False)
    # Must return cleanly (reload both), not SystemExit on auto-detect.
    cli.main()
    assert calls == ["reload"]


def test_reload_both_calls_restart_on_each_platform(monkeypatch):
    import android_harness.admin as aa
    import iphone_harness.admin as ia
    hit = []
    monkeypatch.setattr(ia, "restart_daemon", lambda *a, **k: hit.append("ios"))
    monkeypatch.setattr(aa, "restart_daemon", lambda *a, **k: hit.append("android"))
    cli._reload_both()
    assert hit == ["ios", "android"]


def test_check_env_accepts_filled_repo_env_from_foreign_cwd(monkeypatch, tmp_path):
    import iphone_harness.admin as ia
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.setattr(ia, "_check_env_file", lambda: (True, ".env"))
    assert cli._check_env_for_platform("ios") is None


def test_check_env_still_errors_when_repo_env_is_placeholder(monkeypatch, tmp_path):
    import iphone_harness.admin as ia
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.setattr(ia, "_check_env_file", lambda: (False, "IPH_UDID missing/blank"))
    err = cli._check_env_for_platform("ios")
    assert err is not None and "init" in err


def test_check_env_android_path(monkeypatch, tmp_path):
    import android_harness.admin as aa
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANH_UDID", raising=False)
    monkeypatch.setattr(aa, "_check_env_file", lambda: (True, ".env"))
    assert cli._check_env_for_platform("android") is None
