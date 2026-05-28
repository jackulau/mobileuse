"""Tests for per-device Appium port allocation (D3)."""
import socket
from unittest.mock import patch

import pytest

from mobile_use.multibox import (
    _APPIUM_PORT_RANGE,
    _allocate_appium_port,
    _port_is_free,
)


def test_default_range_skips_4723():
    low, high = _APPIUM_PORT_RANGE
    assert low == 4724
    assert high == 4799


def test_allocate_in_range():
    p = _allocate_appium_port("test-device-1")
    assert 4724 <= p <= 4799


def test_allocate_is_deterministic_per_name():
    p1 = _allocate_appium_port("same-name")
    p2 = _allocate_appium_port("same-name")
    assert p1 == p2


def test_allocate_distinct_names_distinct_ports():
    p1 = _allocate_appium_port("device-A")
    p2 = _allocate_appium_port("device-B")
    assert p1 != p2


def test_allocate_falls_back_when_port_bound():
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        chosen_first = _allocate_appium_port("collision-name")
        held.bind(("127.0.0.1", chosen_first))
        held.listen(1)
        chosen_second = _allocate_appium_port("collision-name")
        assert chosen_second != chosen_first
        assert 4724 <= chosen_second <= 4799
    finally:
        held.close()


def test_allocate_raises_when_range_saturated(monkeypatch):
    monkeypatch.setattr("mobile_use.multibox._port_is_free", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="no free port"):
        _allocate_appium_port("anything")


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
