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
    assert marks[0]["source"] == "template"     # source now reflects the grounding method
    # patch spans px x[100,130) y[60,80) => center (115,70); / scale 2.0 => logical (57.5, 35)
    assert abs(marks[0]["cx"] - 57.5) <= 2
    assert abs(marks[0]["cy"] - 35.0) <= 2


# ---- D5: trained YOLO detector wired as primary local grounding ----------------

class _StubLocator:
    """Stand-in for YoloDetector / matcher: returns preset canonical match dicts."""
    def __init__(self, matches):
        self._m = matches

    def available(self):
        return True

    def predict(self, shot):
        return list(self._m)

    def locate_all(self, shot, max_results=12):
        return list(self._m)


def _yolo_match(label="Send", cx=10.0, cy=20.0):
    return {"label": label, "confidence": 0.95, "cx": cx, "cy": cy,
            "bbox": [cx - 5, cy - 5, 10.0, 10.0], "method": "yolo"}


def test_detector_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MU_YOLO_DETECTOR", raising=False)
    loop = _loop(monkeypatch, tmp_path)
    assert loop._get_detector() is None          # gated off unless MU_YOLO_DETECTOR=1


def test_local_matches_prefers_yolo_over_template(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    loop._detector = _StubLocator([_yolo_match()])
    loop._detector_loaded = True
    # A template matcher is present too, but YOLO wins when it returns matches.
    loop._matcher = _StubLocator([{"label": "T", "confidence": 0.9, "cx": 1, "cy": 1,
                                   "bbox": [0, 0, 2, 2], "method": "template"}])
    loop._matcher_loaded = True
    ms = loop._local_matches("any.png")
    assert ms and ms[0]["method"] == "yolo" and ms[0]["label"] == "Send"


def test_local_matches_falls_back_to_template(monkeypatch, tmp_path):
    loop = _loop(monkeypatch, tmp_path)
    loop._detector = _StubLocator([])            # YOLO present but finds nothing
    loop._detector_loaded = True
    loop._matcher = _StubLocator([{"label": "T", "confidence": 0.9, "cx": 1, "cy": 1,
                                   "bbox": [0, 0, 2, 2], "method": "template"}])
    loop._matcher_loaded = True
    ms = loop._local_matches("any.png")
    assert ms and ms[0]["method"] == "template"  # degraded one rung


def test_visual_marks_tags_yolo_source(monkeypatch, tmp_path):
    from PIL import Image
    shot = tmp_path / "s.png"
    Image.new("RGB", (200, 100), (0, 0, 0)).save(shot)
    loop = _loop(monkeypatch, tmp_path)
    loop._detector = _StubLocator([_yolo_match(cx=100.0, cy=40.0)])
    loop._detector_loaded = True
    state = {"screenshot_path": str(shot), "ui_tree": [],
             "window_size": {"width": 100, "height": 50}}   # scale 200/100 = 2.0
    marks = loop._visual_marks(state)
    assert marks and marks[0]["source"] == "yolo"
    assert abs(marks[0]["cx"] - 50.0) <= 1       # 100px / scale 2.0 -> 50 logical
