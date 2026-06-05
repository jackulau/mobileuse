"""D6 — confidence-gated local-detector short-circuit of the VLM (tree PRESENT).

Objective (a): when a confident local detection maps to the task target, tap it
directly and skip the LLM for that step — even though the accessibility tree exists.
OFF by default; all guards (flag, confidence gate, task-named label, uniqueness) must
hold. These tests pin both the win (fewer llm_calls) and the safety (never fires when
a guard fails). Cache is disabled throughout so the measured reduction is purely the
short-circuit, not the action cache.
"""
from tests.test_agent_run import _loop


class _StubDetector:
    def __init__(self, matches):
        self._m = matches

    def available(self):
        return True

    def predict(self, shot):
        return list(self._m)


def _match(label="Search", conf=0.95, cx=100.0, cy=50.0):
    return {"label": label, "confidence": conf, "cx": cx, "cy": cy,
            "bbox": [cx - 10, cy - 10, 20.0, 20.0], "method": "yolo"}


def _count_llm(monkeypatch, tmp_path, *, shortcircuit, detector_matches, task):
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "0")        # isolate the short-circuit
    monkeypatch.setenv("MU_DETECTOR_MIN_CONF", "0.78")
    if shortcircuit:
        monkeypatch.setenv("MU_LOCAL_SHORTCIRCUIT", "1")
    else:
        monkeypatch.delenv("MU_LOCAL_SHORTCIRCUIT", raising=False)
    loop = _loop(monkeypatch, tmp_path)
    if detector_matches is not None:
        loop._detector = _StubDetector(detector_matches)
        loop._detector_loaded = True
    calls = {"n": 0}

    def llm(_prompt):
        calls["n"] += 1
        return '{"fn": "tap_at_xy", "kwargs": {"x": 1, "y": 1}}'

    res = loop.run(task, llm, max_steps=6)
    return res, calls["n"]


def test_shortcircuit_reduces_llm_calls_with_tree_present(monkeypatch, tmp_path):
    task = "tap the Search button to find coffee"
    base_res, base_calls = _count_llm(
        monkeypatch, tmp_path, shortcircuit=False, detector_matches=None, task=task)
    sc_res, sc_calls = _count_llm(
        monkeypatch, tmp_path, shortcircuit=True,
        detector_matches=[_match()], task=task)

    assert base_calls == 6                                # no detector -> LLM every step
    assert base_res["timings"]["llm_calls"] == 6
    assert sc_calls < base_calls                          # short-circuit skipped LLM calls
    assert sc_res["timings"]["shortcircuits"] >= 1


def test_no_shortcircuit_when_flag_off(monkeypatch, tmp_path):
    res, calls = _count_llm(monkeypatch, tmp_path, shortcircuit=False,
                            detector_matches=[_match()],
                            task="tap the Search button")
    assert calls == 6 and res["timings"]["shortcircuits"] == 0


def test_no_shortcircuit_below_confidence_gate(monkeypatch, tmp_path):
    res, calls = _count_llm(monkeypatch, tmp_path, shortcircuit=True,
                            detector_matches=[_match(conf=0.5)],   # below 0.78 gate
                            task="tap the Search button")
    assert calls == 6 and res["timings"]["shortcircuits"] == 0


def test_no_shortcircuit_when_label_not_in_task(monkeypatch, tmp_path):
    res, calls = _count_llm(monkeypatch, tmp_path, shortcircuit=True,
                            detector_matches=[_match(label="Settings")],
                            task="tap the Search button")        # "settings" not named
    assert calls == 6 and res["timings"]["shortcircuits"] == 0


def test_no_shortcircuit_when_target_ambiguous(monkeypatch, tmp_path):
    # Two task-named confident matches -> ambiguous -> must NOT fire (defer to VLM).
    res, calls = _count_llm(
        monkeypatch, tmp_path, shortcircuit=True,
        detector_matches=[_match(cx=100), _match(cx=200)],
        task="tap the Search button")
    assert calls == 6 and res["timings"]["shortcircuits"] == 0
