"""B1 — latency instrumentation in the perceive->decide->act loop.

Reuses the device-free fake-helpers install from test_agent_run.py so run()
executes end-to-end with no device, and asserts the timing surface that the
before/after benchmark (B6) and the cache (B3) build on.
"""
import sys
import types

from tests.test_agent_run import _loop


def test_run_reports_timings_and_llm_calls(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)

    steps = {"n": 0}

    def llm(prompt):
        steps["n"] += 1
        if steps["n"] == 1:
            return '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}'
        return '{"done": true, "reason": "finished"}'

    result = loop.run("do a thing", llm, max_steps=5)

    assert result["status"] == "done"
    t = result["timings"]
    # every phase + counters present
    for key in ("perceive_ms", "decide_ms", "act_ms", "llm_calls", "steps",
                "total_ms", "avg_perceive_ms", "avg_decide_ms", "avg_act_ms"):
        assert key in t, f"missing timing key {key}"
    assert t["llm_calls"] == 2          # one action + one done
    assert t["steps"] >= 1
    assert t["total_ms"] >= 0.0
    # per-step timing recorded for the action step
    acted = [h for h in result["history"] if "timing" in h]
    assert acted and "decide_ms" in acted[0]["timing"]


def test_slow_llm_dominates_decide_phase(monkeypatch, tmp_path):
    """A deliberately slow llm should show up in decide_ms, not perceive/act —
    proving the instrumentation attributes latency to the right phase."""
    import time as _time
    loop = _loop(monkeypatch, tmp_path)

    def slow_llm(prompt):
        _time.sleep(0.02)
        return '{"done": true, "reason": "done"}'

    result = loop.run("x", slow_llm, max_steps=2)
    t = result["timings"]
    assert t["decide_ms"] >= 18.0          # ~20ms sleep landed in decide
    assert t["decide_ms"] > t["perceive_ms"]
