"""D18 — surface booted iOS Simulators in device discovery.

idevice_id only lists physical iPhones, so a Mac with no spare device (the
standard CI/dev case) couldn't auto-fill a UDID or smoke-test against a
Simulator — even though XCUITest drives a sim by UDID like a real device. Now
`xcrun simctl list devices booted -j` is merged into discovery / init.
"""
import json

import mobile_use.devices as devices
import mobile_use.setup_env as setup_env

_SIMCTL_JSON = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-5": [
            {"udid": "SIM-UDID-1", "name": "iPhone 15 Pro", "state": "Booted"},
            {"udid": "SIM-UDID-OFF", "name": "iPhone SE", "state": "Shutdown"},
        ],
    }
})


def _fake_check_output(json_out):
    def _co(cmd, timeout=None, stderr=None):
        if cmd[:2] == ["xcrun", "simctl"]:
            return json_out.encode()
        raise FileNotFoundError(cmd[0])
    return _co


def test_devices_module_probes_simctl():
    assert "simctl" in open(devices.__file__).read()


def test_ios_sims_parses_booted_only(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: "/usr/bin/xcrun" if c == "xcrun" else None)
    monkeypatch.setattr(devices.subprocess, "check_output", _fake_check_output(_SIMCTL_JSON))
    sims = devices._ios_sims()
    assert sims == [("SIM-UDID-1", "iPhone 15 Pro")], sims  # Shutdown sim excluded


def test_discover_includes_booted_sim(monkeypatch):
    monkeypatch.setattr(devices, "_ios_udids", lambda: [])
    monkeypatch.setattr(devices, "_adb_devices_long", lambda: [])
    monkeypatch.setattr(devices, "_ios_sims", lambda: [("SIM-UDID-1", "iPhone 15 Pro")])
    found = devices.discover_connected()
    assert len(found) == 1
    assert found[0]["udid"] == "SIM-UDID-1"
    assert found[0]["platform"] == "ios"
    assert found[0].get("simulator") is True


def test_setup_env_detect_includes_sims(monkeypatch):
    monkeypatch.setattr(setup_env, "_idevice_id", lambda: [])
    monkeypatch.setattr(setup_env, "_adb_devices", lambda: [])
    monkeypatch.setattr(setup_env, "_ios_sim_udids", lambda: ["SIM-UDID-1"])
    out = setup_env.detect_devices()
    assert out["ios"] == ["SIM-UDID-1"]


def test_ios_sims_empty_without_xcrun(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: None)
    assert devices._ios_sims() == []


def test_ios_sims_handles_bad_json(monkeypatch):
    monkeypatch.setattr(devices, "_which", lambda c: "/usr/bin/xcrun")
    monkeypatch.setattr(devices.subprocess, "check_output", _fake_check_output("not json"))
    assert devices._ios_sims() == []
