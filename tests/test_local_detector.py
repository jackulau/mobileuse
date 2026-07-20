"""B4 — local visual element matcher (OpenCV template match; optional extra).

cv2 is present in this dev/CI image, so the matching path is exercised directly.
The unavailable path is simulated by stubbing the cv2 import — proving the clean
no-op contract for installs without the [detection] extra.
"""
import pytest

np = pytest.importorskip("numpy")

import mobile_use.local_detector as ld
from mobile_use.local_detector import LocalElementMatcher, available

cv2 = pytest.importorskip("cv2")


def _scene_with_patch():
    """A 240x160 gray scene with a distinctive 32x24 patch pasted at (100, 60)."""
    rng = np.random.default_rng(7)
    scene = (rng.integers(0, 60, size=(160, 240), dtype=np.uint8))  # dark noise
    patch = np.zeros((24, 32), dtype=np.uint8)
    patch[:, :16] = 255            # high-contrast half-white block — easy to find
    patch[6:18, 20:30] = 180
    scene[60:84, 100:132] = patch
    return scene, patch


def test_available_true_with_cv2():
    assert available() is True


def test_from_samples_warns_when_zero_templates(capsys):
    # Non-empty samples but every crop missing -> 0 templates -> a diagnostic surfaces
    # (otherwise the caller silently gets an inert matcher with no hint why).
    m = LocalElementMatcher.from_samples(
        [{"label": "A", "crop": "/no/such/crop.png"},
         {"label": "B", "crop": "/also/missing.png"}])
    assert m.template_count == 0
    assert "0 templates loaded" in capsys.readouterr().err


def test_from_samples_silent_on_empty_input(capsys):
    # Empty input is legitimate (nothing captured yet) -> no warning noise.
    m = LocalElementMatcher.from_samples([])
    assert m.template_count == 0
    assert capsys.readouterr().err == ""


def test_locate_finds_patch_at_right_center():
    scene, patch = _scene_with_patch()
    m = LocalElementMatcher(min_confidence=0.7)
    m.add_template("ok-button", patch)
    res = m.locate(scene)
    assert res is not None
    assert res["label"] == "ok-button"
    assert res["confidence"] >= 0.9
    # patch center is (100+16, 60+12) = (116, 72); allow a few px slop
    assert abs(res["cx"] - 116) <= 4
    assert abs(res["cy"] - 72) <= 4
    assert res["method"] == "template"


def test_locate_returns_none_below_threshold():
    scene, patch = _scene_with_patch()
    m = LocalElementMatcher(min_confidence=0.999)  # demand near-perfect
    rng = np.random.default_rng(99)
    noisy = np.clip(patch.astype(int) + rng.integers(-90, 90, patch.shape), 0, 255).astype(np.uint8)
    m.add_template("noisy", noisy)
    # a match exists but is imperfect -> below the 0.999 gate -> None (caller falls back)
    assert m.locate(scene) is None


def test_uniform_template_is_rejected():
    m = LocalElementMatcher(min_confidence=0.7)
    m.add_template("blank", np.full((24, 32), 128, dtype=np.uint8))
    assert m.template_count == 0  # flat crop carries no signal


def test_label_filter_restricts_candidates():
    scene, patch = _scene_with_patch()
    m = LocalElementMatcher(min_confidence=0.7)
    m.add_template("ok-button", patch)
    assert m.locate(scene, label="nonexistent") is None
    assert m.locate(scene, label="ok-button") is not None


def test_graceful_noop_when_cv2_absent(monkeypatch):
    monkeypatch.setattr(ld, "_import_cv2", lambda: None)
    assert ld.available() is False
    m = LocalElementMatcher()
    m.add_template("x", "/nonexistent.png")   # add is a no-op without cv2
    assert m.template_count == 0
    assert m.locate("/whatever.png") is None   # clean no-op, no raise


def test_from_samples_loads_existing_crops(tmp_path):
    scene, patch = _scene_with_patch()
    crop_path = tmp_path / "crop.png"
    cv2.imwrite(str(crop_path), patch)
    samples = [
        {"label": "ok-button", "crop": str(crop_path)},
        {"label": "missing", "crop": str(tmp_path / "nope.png")},  # skipped
    ]
    m = LocalElementMatcher.from_samples(samples, min_confidence=0.7)
    assert m.template_count == 1
    assert m.locate(scene)["label"] == "ok-button"
