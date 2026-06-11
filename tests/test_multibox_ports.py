"""Per-device port allocation: deterministic, range-correct, race-free."""
import hashlib
import socket

import pytest

from mobile_use.multibox import (
    _ANDROID_MJPEG_RANGE,
    _ANDROID_SYSTEM_RANGE,
    _APPIUM_PORT_RANGE,
    _IOS_MJPEG_RANGE,
    _IOS_WDA_LOCAL_RANGE,
    _allocate_appium_port,
    _allocate_port,
    _port_is_free,
)


def test_default_appium_range_skips_4723():
    low, high = _APPIUM_PORT_RANGE
    assert low == 4724
    assert high == 4799


def test_driver_port_ranges_do_not_overlap():
    ranges = [_APPIUM_PORT_RANGE, _IOS_WDA_LOCAL_RANGE, _ANDROID_SYSTEM_RANGE,
              _IOS_MJPEG_RANGE, _ANDROID_MJPEG_RANGE]
    seen = set()
    for low, high in ranges:
        span = set(range(low, high + 1))
        assert not (span & seen), f"range {low}-{high} overlaps another"
        seen |= span
    # And every reserved port/range stays untouchable: 4723 shared Appium,
    # 8100 device-side WDA default, 9100/7810 single-device mjpeg defaults,
    # 8400-8499 daemon TCP RPC range.
    for reserved in (4723, 8100, 9100, 7810, *range(8400, 8500)):
        assert reserved not in seen


def test_allocate_in_range():
    p = _allocate_port("test-device-1", _ANDROID_SYSTEM_RANGE)
    assert _ANDROID_SYSTEM_RANGE[0] <= p <= _ANDROID_SYSTEM_RANGE[1]


def test_allocate_is_idempotent_per_name():
    p1 = _allocate_port("same-name-x", _ANDROID_SYSTEM_RANGE)
    p2 = _allocate_port("same-name-x", _ANDROID_SYSTEM_RANGE)
    assert p1 == p2


def test_allocate_deterministic_start_from_name_hash(monkeypatch):
    """First allocation for a fresh name lands on the sha256-derived slot when
    free — this is what keeps ports stable across separate runs."""
    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    monkeypatch.setattr(mb, "_port_is_free", lambda *a, **k: True)
    name = "determinism-check"
    low, high = _IOS_WDA_LOCAL_RANGE
    span = high - low + 1
    expected = low + (int(hashlib.sha256(name.encode()).hexdigest(), 16) % span)
    assert _allocate_port(name, _IOS_WDA_LOCAL_RANGE) == expected


def test_allocate_distinct_names_distinct_ports():
    p1 = _allocate_port("device-A", _IOS_MJPEG_RANGE)
    p2 = _allocate_port("device-B", _IOS_MJPEG_RANGE)
    assert p1 != p2


def test_allocate_probes_past_bound_port(monkeypatch):
    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        name = "collision-name-z"
        low, high = _ANDROID_MJPEG_RANGE
        span = high - low + 1
        hashed = low + (int(hashlib.sha256(name.encode()).hexdigest(), 16) % span)
        held.bind(("127.0.0.1", hashed))
        held.listen(1)
        chosen = _allocate_port(name, _ANDROID_MJPEG_RANGE)
        assert chosen != hashed
        assert low <= chosen <= high
    finally:
        held.close()


def test_allocate_raises_when_range_saturated(monkeypatch):
    import mobile_use.multibox as mb
    monkeypatch.setattr(mb, "_claimed_ports", set())
    monkeypatch.setattr(mb, "_assigned", {})
    monkeypatch.setattr(mb, "_port_is_free", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="no free port"):
        _allocate_port("anything-sat", _IOS_WDA_LOCAL_RANGE)


def test_appium_port_wrapper_still_works():
    p = _allocate_appium_port("legacy-dedicated")
    assert 4724 <= p <= 4799


def test_port_is_free_basic():
    assert _port_is_free(0) is True


def test_port_is_free_false_when_bound():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert _port_is_free(port) is False
    finally:
        s.close()
