"""Wireless remember-store: round-trip, multi-device, corruption, forget."""
import json

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_WIFI_STORE", str(tmp_path / "wifi.json"))
    import mobile_use.wifi_store as w
    return w


def test_round_trip_via_env_override(store, tmp_path):
    store.remember_device("android", serial="192.168.1.42:5555")
    assert (tmp_path / "wifi.json").exists()
    devs = store.remembered_devices()
    assert len(devs) == 1
    assert devs[0]["serial"] == "192.168.1.42:5555"
    assert devs[0]["last_seen"]


def test_three_simultaneous_devices_survive(store):
    store.remember_device("android", serial="192.168.1.42:5555", name="pixel")
    store.remember_device("android", host="192.168.1.43", port=5555)
    store.remember_device("ios", udid="00008140-AAA",
                          wda_url="http://192.168.1.50:8100")
    assert len(store.remembered_devices()) == 3
    assert len(store.remembered_devices("android")) == 2
    assert len(store.remembered_devices("ios")) == 1
    # host+port derived an android serial
    serials = {e["serial"] for e in store.remembered_devices("android")}
    assert "192.168.1.43:5555" in serials


def test_corrupt_file_falls_back_fresh(store, tmp_path):
    (tmp_path / "wifi.json").write_text("{not json!!", encoding="utf-8")
    assert store.remembered_devices() == []
    # And writes recover from the corruption.
    store.remember_device("ios", wda_url="http://x:8100")
    assert len(store.remembered_devices()) == 1


def test_wrong_shape_falls_back_fresh(store, tmp_path):
    (tmp_path / "wifi.json").write_text(json.dumps({"devices": "nope"}),
                                        encoding="utf-8")
    assert store.remembered_devices() == []


def test_upsert_replaces_not_duplicates(store):
    store.remember_device("android", serial="192.168.1.42:5555", name="old")
    store.remember_device("android", serial="192.168.1.42:5555", name="new")
    devs = store.remembered_devices("android")
    assert len(devs) == 1
    assert devs[0]["name"] == "new"


def test_forget_removes(store):
    store.remember_device("android", serial="192.168.1.42:5555")
    store.remember_device("ios", udid="00008140-AAA")
    assert store.forget_device("android", serial="192.168.1.42:5555") == 1
    assert store.remembered_devices("android") == []
    assert len(store.remembered_devices("ios")) == 1


def test_no_cross_platform_identity_collision(store):
    # Same identity string on both platforms must coexist independently.
    store.remember_device("android", serial="SHARED-ID")
    store.remember_device("ios", udid="SHARED-ID")
    assert len(store.remembered_devices()) == 2
    store.forget_device("android", serial="SHARED-ID")
    assert len(store.remembered_devices("ios")) == 1


def test_identity_required(store):
    with pytest.raises(ValueError):
        store.remember_device("android", name="no-identity")


def test_atomic_write_leaves_no_tmp(store, tmp_path):
    store.remember_device("ios", wda_url="http://x:8100")
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_missing_file_loads_empty(store):
    assert store.load_store()["devices"] == []
