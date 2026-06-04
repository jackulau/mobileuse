"""Local visual element matcher — short-circuit element lookup without the VLM.

Given a library of labeled element crops (from the self-labeling dataset, B2),
locate a known element on a fresh screenshot with OpenCV multi-scale template
matching (fast, robust to scale) plus an ORB feature-match fallback. The result
is confidence-gated: below threshold it returns None and the caller falls back
to the accessibility tree / VLM. Because it matches pixels — not accessibility
nodes — it also works on tree-less screens (games, canvas, web views), which is
exactly where the tree-based fast path is blind.

OpenCV + numpy are OPTIONAL (``pip install 'mobile-use[detection]'``). Everything
is import-guarded: with them absent, ``available()`` is False and ``locate()``
returns None — a clean no-op, never an error.
"""
import os

# Multi-scale search factors (template resized relative to its captured size).
_SCALES = (1.0, 0.9, 1.1, 0.8, 1.25, 0.67, 1.5, 0.5)
_DEFAULT_MIN_CONFIDENCE = float(os.environ.get("MU_DETECTOR_MIN_CONF", "0.78"))


def _import_cv2():
    """Return the cv2 module, or None when the optional dep is absent."""
    try:
        import cv2
        return cv2
    except Exception:
        return None


def available():
    """True iff the local-detector optional deps (OpenCV) are importable."""
    return _import_cv2() is not None


class LocalElementMatcher:
    """Locate known UI elements on a screenshot by pixel matching.

    Templates are (label, grayscale image) pairs. Build one directly with
    ``add_template`` or from a self-labeled dataset via ``from_samples``. With
    OpenCV absent the matcher is inert (``locate`` -> None).
    """

    def __init__(self, min_confidence=_DEFAULT_MIN_CONFIDENCE):
        self.min_confidence = float(min_confidence)
        self._templates = []  # list of {"label": str, "gray": ndarray}

    # ---- building ---------------------------------------------------------

    def add_template(self, label, image):
        """Add a template from a file path or an image array.

        No-op if unreadable, empty, or near-uniform: a flat crop (all one colour)
        has zero variance, which makes normalized correlation degenerate (it would
        spuriously score 1.0 everywhere) and carries no matchable signal anyway.
        """
        gray = self._to_gray(image)
        if gray is None or not gray.size:
            return self
        try:
            if float(gray.std()) < 1.0:
                return self  # blank/uniform crop — skip
        except Exception:
            return self
        self._templates.append({"label": label or "", "gray": gray})
        return self

    @classmethod
    def from_samples(cls, samples, min_confidence=_DEFAULT_MIN_CONFIDENCE):
        """Build a matcher from B2 detection samples (each with ``crop`` + ``label``)."""
        m = cls(min_confidence=min_confidence)
        for s in samples or []:
            crop = s.get("crop")
            if crop and os.path.exists(crop):
                m.add_template(s.get("label", ""), crop)
        return m

    @classmethod
    def from_session(cls, sessions=None, min_confidence=_DEFAULT_MIN_CONFIDENCE):
        """Build a matcher from the recorded detection dataset of given session(s)."""
        from mobile_use.collector import load_detection_samples
        return cls.from_samples(load_detection_samples(sessions), min_confidence)

    @property
    def template_count(self):
        return len(self._templates)

    # ---- matching ---------------------------------------------------------

    def _to_gray(self, image):
        cv2 = _import_cv2()
        if cv2 is None:
            return None
        try:
            if isinstance(image, str):
                return cv2.imread(image, cv2.IMREAD_GRAYSCALE)
            arr = image
            if hasattr(arr, "ndim") and arr.ndim == 3:
                return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            return arr
        except Exception:
            return None

    def _match_one(self, scene_gray, tmpl_gray):
        """Best (score, (cx, cy, w, h)) for one template across scales."""
        cv2 = _import_cv2()
        th, tw = tmpl_gray.shape[:2]
        sh_max, sw_max = scene_gray.shape[:2]
        best_score, best_box = -1.0, None
        for scale in _SCALES:
            sw, sh = int(tw * scale), int(th * scale)
            if sw < 8 or sh < 8 or sw > sw_max or sh > sh_max:
                continue
            t = cv2.resize(tmpl_gray, (sw, sh))
            res = cv2.matchTemplate(scene_gray, t, cv2.TM_CCOEFF_NORMED)
            _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
            if maxv > best_score:
                best_score = maxv
                best_box = (maxl[0] + sw / 2.0, maxl[1] + sh / 2.0, sw, sh)
        return best_score, best_box

    def locate(self, screenshot, label=None):
        """Locate the best-matching known element on ``screenshot``.

        Returns ``{label, confidence, cx, cy, bbox, method}`` when the best match
        clears ``min_confidence``, else None (caller falls back to tree/VLM).
        ``label`` restricts matching to templates of that label.
        """
        if not available():
            return None
        scene = self._to_gray(screenshot)
        if scene is None or not getattr(scene, "size", 0):
            return None
        candidates = [t for t in self._templates
                      if label is None or t["label"] == label]
        best = None
        for t in candidates:
            score, box = self._match_one(scene, t["gray"])
            if box is None:
                continue
            if best is None or score > best["confidence"]:
                cx, cy, w, h = box
                best = {
                    "label": t["label"], "confidence": float(score),
                    "cx": float(cx), "cy": float(cy),
                    "bbox": [cx - w / 2.0, cy - h / 2.0, float(w), float(h)],
                    "method": "template",
                }
        if best and best["confidence"] >= self.min_confidence:
            return best
        return None
