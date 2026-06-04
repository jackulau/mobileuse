"""B3 — perception/action cache: skip the LLM on repeated identical screens.

Two layers: the PerceptionCache unit itself, and its wiring into AgentLoop.run()
(asserted via the llm_calls counter from B1 — a cache hit must not call the LLM).
"""
import sys
import types

import pytest

from mobile_use.perception_cache import PerceptionCache, screen_signature
from tests.test_agent_run import _loop

# ---- screen_signature -----------------------------------------------------

def test_signature_stable_and_position_quantized():
    a = [{"label": "Go", "type": "Button", "cx": 100, "cy": 50}]
    b = [{"label": "Go", "type": "Button", "cx": 103, "cy": 52}]   # <8px jitter
    c = [{"label": "Stop", "type": "Button", "cx": 100, "cy": 50}]
    assert screen_signature(a) == screen_signature(b)   # jitter ignored
    assert screen_signature(a) != screen_signature(c)   # label change matters


def test_signature_order_independent():
    a = [{"label": "A", "cx": 1, "cy": 1}, {"label": "B", "cx": 2, "cy": 2}]
    assert screen_signature(a) == screen_signature(list(reversed(a)))


def test_signature_app_aware():
    marks = [{"label": "Open", "cx": 10, "cy": 10}]
    assert (screen_signature(marks, {"bundleId": "com.a"})
            != screen_signature(marks, {"bundleId": "com.b"}))


# ---- PerceptionCache ------------------------------------------------------

def test_cache_hit_and_miss():
    c = PerceptionCache(ttl=100)
    assert c.get("task", 0, "sig") is None          # miss
    c.put("task", 0, "sig", {"fn": "tap"})
    assert c.get("task", 0, "sig") == {"fn": "tap"}  # hit
    assert c.stats["hits"] == 1 and c.stats["misses"] == 1


def test_cache_ttl_expiry():
    c = PerceptionCache(ttl=10)
    c.put("t", 0, "s", {"fn": "tap"}, now=1000.0)
    assert c.get("t", 0, "s", now=1005.0) == {"fn": "tap"}  # within ttl
    assert c.get("t", 0, "s", now=1020.0) is None           # expired


def test_cache_disabled_never_hits():
    c = PerceptionCache(enabled=False)
    c.put("t", 0, "s", {"fn": "tap"})
    assert c.get("t", 0, "s") is None


def test_none_signature_never_caches():
    c = PerceptionCache()
    c.put("t", 0, None, {"fn": "tap"})
    assert c.get("t", 0, None) is None


# ---- wired into run() -----------------------------------------------------

def test_run_skips_llm_on_repeated_screen(monkeypatch, tmp_path):
    """A constant screen must produce fewer LLM calls than steps (cache replays)."""
    loop = _loop(monkeypatch, tmp_path)
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        return '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}'

    result = loop.run("repeat task", llm, max_steps=6)
    t = result["timings"]
    assert t["steps"] == 6
    assert t["cache_hits"] >= 1                 # the cache fired
    assert t["llm_calls"] == calls["n"]         # counter matches real calls
    assert t["llm_calls"] < t["steps"]          # fewer LLM calls than steps => faster
    assert result["cache"]["hits"] >= 1


def test_cache_off_calls_llm_every_step(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "0")
    loop = _loop(monkeypatch, tmp_path)
    calls = {"n": 0}

    def llm(prompt):
        calls["n"] += 1
        return '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}'

    result = loop.run("t", llm, max_steps=4)
    assert result["timings"]["llm_calls"] == 4   # no caching => one call per step
    assert result["timings"]["cache_hits"] == 0
