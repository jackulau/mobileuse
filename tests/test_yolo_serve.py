"""D4 — YoloDetector serving wrapper: import-guarded, confidence-gated, canonical shape.

ultralytics is absent in CI, so the inert path (no weights / no dep -> []/None, never
raise) is the primary contract. The result-parsing + gate logic are exercised directly
with a stub Results object, needing no ultralytics or trained weights.
"""
import mobile_use.train_detector as td
from mobile_use.train_detector import YoloDetector


class _Boxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)
        self.cls = _Arr(cls)


class _Arr:
    def __init__(self, v):
        self._v = v

    def tolist(self):
        return self._v


class _Results:
    def __init__(self, xyxy, conf, cls, names):
        self.boxes = _Boxes(xyxy, conf, cls)
        self.names = names


def test_from_env_none_without_weights(monkeypatch):
    monkeypatch.delenv("MU_DETECTOR_WEIGHTS", raising=False)
    assert YoloDetector.from_env() is None


def test_from_env_none_when_ultralytics_absent(monkeypatch, tmp_path):
    w = tmp_path / "best.pt"
    w.write_bytes(b"fake")
    monkeypatch.setenv("MU_DETECTOR_WEIGHTS", str(w))
    monkeypatch.setattr(td, "available", lambda: False)
    assert YoloDetector.from_env() is None          # dep absent -> inert


def test_predict_inert_without_model(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "available", lambda: False)
    det = YoloDetector(str(tmp_path / "nope.pt"))
    assert det.available() is False
    assert det.predict("anything.png") == []        # no raise
    assert det.locate("anything.png") is None


def test_parse_results_shape_and_gate():
    det = YoloDetector("x.pt", min_confidence=0.5)
    res = [_Results(
        xyxy=[[10, 20, 110, 60], [0, 0, 40, 40]],
        conf=[0.9, 0.3],                            # second below the 0.5 gate -> dropped
        cls=[0, 1],
        names={0: "Search", 1: "Send"},
    )]
    parsed = det._parse_results(res)
    assert len(parsed) == 1                          # gate dropped the 0.3 box
    m = parsed[0]
    assert m["label"] == "Search" and m["method"] == "yolo"
    assert m["confidence"] == 0.9
    assert m["cx"] == 60.0 and m["cy"] == 40.0       # center of (10,20)-(110,60)
    assert m["bbox"] == [10.0, 20.0, 100.0, 40.0]    # x,y,w,h pixel space


class _DriftBoxes:
    """Simulates an ultralytics API change: the .xyxy accessor no longer exists."""


class _DriftResults:
    def __init__(self):
        self.boxes = _DriftBoxes()
        self.names = {}


def test_parse_results_warns_once_on_api_drift(capsys):
    det = YoloDetector("x.pt")
    td._parse_drift_warned = False                    # deterministic one-shot start
    out1 = det._parse_results([_DriftResults()])
    out2 = det._parse_results([_DriftResults()])
    assert out1 == [] and out2 == []                  # drift -> empty, never a crash
    err = capsys.readouterr().err
    assert err.count("YOLO result parsing failed") == 1   # surfaced exactly once


def test_min_conf_env_default(monkeypatch):
    det = YoloDetector("x.pt")
    assert det.min_confidence == td._DEFAULT_MIN_CONFIDENCE
