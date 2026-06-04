"""B6 — end-to-end wire-up + before/after benchmark.

Proves the cache makes repeated-screen runs natively faster (fewer LLM calls,
less wall time), and that the local matcher recovers marks on a tree-less screen.
"""
import numpy as np
import pytest

from mobile_use.perception_cache import synthetic_benchmark
from tests.test_agent_run import _loop


def test_synthetic_benchmark_cache_is_faster():
    r = synthetic_benchmark(llm_latency_ms=4.0, steps=12, repeats_same_screen=True)
    assert r["llm_calls_cached"] < r["llm_calls_baseline"]   # cache skipped LLM calls
    assert r["cached_ms"] < r["baseline_ms"]                  # => faster wall time
    assert r["speedup"] > 1.0


def test_benchmark_no_speedup_when_every_screen_unique():
    # Distinct screens every step -> cache can't help (honest: no false speedup).
    r = synthetic_benchmark(llm_latency_ms=2.0, steps=8, repeats_same_screen=False)
    assert r["llm_calls_cached"] == r["llm_calls_baseline"] == 8


def test_end_to_end_cached_run_faster_than_uncached(monkeypatch, tmp_path):
    """Same repeated-screen task: cache ON makes fewer LLM calls than cache OFF."""
    def slow_llm(prompt):
        import time
        time.sleep(0.003)
        return '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}'

    # cache OFF
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "0")
    off = _loop(monkeypatch, tmp_path).run("t", slow_llm, max_steps=6)

    # cache ON
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "1")
    on = _loop(monkeypatch, tmp_path).run("t", slow_llm, max_steps=6)

    assert off["timings"]["llm_calls"] == 6
    assert on["timings"]["llm_calls"] < 6
    assert on["timings"]["cache_hits"] >= 1


def test_local_matcher_recovers_marks_on_treeless_screen(monkeypatch, tmp_path):
    """Empty UI tree + a loaded matcher => perceive() recovers visual marks."""
    cv2 = pytest.importorskip("cv2")
    from PIL import Image

    from mobile_use.local_detector import LocalElementMatcher

    # A 200x100 screenshot with a distinctive patch at pixel (100,60).
    scene = np.zeros((100, 200), dtype=np.uint8)
    patch = np.zeros((20, 30), dtype=np.uint8)
    patch[:, :15] = 255
    scene[60:80, 100:130] = patch
    shot = tmp_path / "screen.png"
    Image.fromarray(scene).save(shot)

    monkeypatch.setenv("HOME", str(tmp_path))
    loop = _loop(monkeypatch, tmp_path)

    # Inject a matcher that knows the patch; force-load it.
    matcher = LocalElementMatcher(min_confidence=0.7)
    matcher.add_template("play", patch)
    loop._matcher = matcher
    loop._matcher_loaded = True

    state = {
        "screenshot_path": str(shot),
        "ui_tree": [],                       # tree-less screen
        "window_size": {"width": 100, "height": 50},  # scale = 200/100 = 2.0
    }
    marks = loop._visual_marks(state)
    assert marks, "expected visual marks recovered by the matcher"
    assert marks[0]["label"] == "play"
    assert marks[0]["source"] == "local_detector"
    # patch spans px x[100,130) y[60,80) => center (115,70); / scale 2.0 => logical (57.5, 35)
    assert abs(marks[0]["cx"] - 57.5) <= 2
    assert abs(marks[0]["cy"] - 35.0) <= 2
