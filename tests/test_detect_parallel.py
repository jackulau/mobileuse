"""goal/022 D8 — _detect_platform() probes run concurrently.

The bare `mobile-use` cold start used to run idevice_id then adb devices
SERIALLY (1.5s timeout each — ~3s worst case before anything happened). The
probes now run in a ThreadPoolExecutor; decision logic and outputs unchanged.
Concurrency is asserted by observing overlap from inside the probes — never by
measuring wall-clock.
"""
import threading

import mobile_use.cli as cli


def test_probes_overlap_in_time(monkeypatch):
    """Both probes must be in flight simultaneously (true concurrency)."""
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.delenv("ANH_UDID", raising=False)

    barrier = threading.Barrier(2, timeout=5.0)
    overlapped = []

    def probe_ios():
        barrier.wait()          # blocks unless the OTHER probe also runs now
        overlapped.append("ios")
        return True

    def probe_android():
        barrier.wait()
        overlapped.append("android")
        return False

    monkeypatch.setattr(cli, "_probe_ios_connected", probe_ios)
    monkeypatch.setattr(cli, "_probe_android_connected", probe_android)
    assert cli._detect_platform() == "ios"
    assert sorted(overlapped) == ["android", "ios"]


def test_decision_logic_unchanged(monkeypatch):
    monkeypatch.delenv("IPH_UDID", raising=False)
    monkeypatch.delenv("ANH_UDID", raising=False)
    cases = [
        (True, False, "ios"),
        (False, True, "android"),
        (True, True, None),      # ambiguous
        (False, False, None),    # nothing connected
    ]
    for ios, android, expected in cases:
        monkeypatch.setattr(cli, "_probe_ios_connected", lambda v=ios: v)
        monkeypatch.setattr(cli, "_probe_android_connected", lambda v=android: v)
        assert cli._detect_platform() == expected, (ios, android)


def test_udid_env_skips_probe(monkeypatch):
    """An explicit *_UDID is authoritative — its probe must not even run."""
    probed = []
    monkeypatch.setattr(cli, "_probe_ios_connected",
                        lambda: probed.append("ios") or False)
    monkeypatch.setattr(cli, "_probe_android_connected",
                        lambda: probed.append("android") or False)
    monkeypatch.setenv("IPH_UDID", "00008110-001234567890")
    monkeypatch.delenv("ANH_UDID", raising=False)
    assert cli._detect_platform() == "ios"
    assert probed == ["android"], "iOS probe must be skipped when IPH_UDID set"

    probed.clear()
    monkeypatch.setenv("ANH_UDID", "emulator-5554")
    assert cli._detect_platform() is None   # both known -> ambiguous
    assert probed == [], "no probes at all when both UDIDs set"


def test_probe_helpers_swallow_missing_binaries(monkeypatch):
    """Probe helpers return False (never raise) when the CLI tools are absent."""
    import subprocess

    def boom(*a, **kw):
        raise FileNotFoundError("idevice_id/adb not installed")

    monkeypatch.setattr(cli.subprocess, "check_output", boom)
    assert cli._probe_ios_connected() is False
    assert cli._probe_android_connected() is False
