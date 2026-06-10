"""goal/022 D10 — annotated_screenshot decodes the PNG once.

The tree-mode path used to call PIL Image.open up to 3x per invocation on iOS
(twice for size, once for drawing) and 2x on Android. One decode now feeds both
the scale math and the draw surface. Asserted by counting Image.open calls.
"""
import pytest

PIL_Image = pytest.importorskip("PIL.Image")

import android_harness.helpers as ah
import iphone_harness.helpers as ih


@pytest.mark.parametrize("mod,label_key", [(ih, "label"), (ah, "text")])
def test_tree_mode_single_decode(monkeypatch, tmp_path, mod, label_key):
    shot = tmp_path / "screen.png"
    PIL_Image.new("RGB", (390, 844), (250, 250, 250)).save(shot)

    monkeypatch.setattr(mod, "screenshot", lambda path=None: str(shot))
    monkeypatch.setattr(mod, "window_size",
                        lambda: {"width": 390, "height": 844})
    monkeypatch.setattr(mod, "ui_tree", lambda visible_only=True: [
        {"type": "Button", label_key: "Send", "x": 10, "y": 20, "w": 80, "h": 30}])

    opens = []
    orig_open = PIL_Image.open

    def counting_open(fp, *a, **kw):
        opens.append(str(fp))
        return orig_open(fp, *a, **kw)

    monkeypatch.setattr(PIL_Image, "open", counting_open)

    annotated, items = mod.annotated_screenshot(run_ocr=False)
    assert len(opens) == 1, f"expected ONE decode, saw {len(opens)}: {opens}"
    assert annotated.endswith(".annotated.png")
    assert len(items) == 1


@pytest.mark.parametrize("mod,label_key", [(ih, "label"), (ah, "text")])
def test_annotated_output_written_and_correct(monkeypatch, tmp_path, mod, label_key):
    """The single-decode rewrite must not change the output contract."""
    shot = tmp_path / "screen.png"
    PIL_Image.new("RGB", (100, 200), (0, 0, 0)).save(shot)

    monkeypatch.setattr(mod, "screenshot", lambda path=None: str(shot))
    monkeypatch.setattr(mod, "window_size",
                        lambda: {"width": 100, "height": 200})
    monkeypatch.setattr(mod, "ui_tree", lambda visible_only=True: [
        {"type": "Button", label_key: "A", "x": 5, "y": 5, "w": 20, "h": 10},
        {"type": "Cell", label_key: "B", "x": 30, "y": 50, "w": 40, "h": 20}])

    annotated, items = mod.annotated_screenshot(run_ocr=False)
    import os
    assert os.path.exists(annotated)
    assert len(items) == 2
    with PIL_Image.open(annotated) as im:
        assert im.size == (100, 200)
        # Red annotation boxes actually drawn (pure-black base gains red pixels).
        raw = im.convert("RGB").tobytes()
        reds = sum(1 for i in range(0, len(raw), 3)
                   if raw[i] > 200 and raw[i + 1] < 100)
        assert reds > 0
