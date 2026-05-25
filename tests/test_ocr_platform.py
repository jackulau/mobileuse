"""OCR must fail cleanly on Linux (no ImportError crash; clear remediation).

Both `iphone_harness.helpers.ocr` and `android_harness.helpers.ocr` use the
macOS Vision framework. On Linux they must raise `OCRNotAvailableError`
with a useful message pointing at SETUP.md, not a bare ImportError.

`screenshot()` is mocked so we don't need a live daemon or device — we
test the platform gate in isolation.
"""
import sys

import pytest

from mobile_use._platform import OCRNotAvailableError


def _mock_screenshot(monkeypatch, helpers_mod):
    """Stub the helpers' screenshot() so ocr() can run without a device."""
    monkeypatch.setattr(helpers_mod, "screenshot", lambda *a, **kw: "/tmp/fake.png")


def test_ocr_not_available_error_is_runtime_error_subclass():
    assert issubclass(OCRNotAvailableError, RuntimeError)


def test_iphone_ocr_raises_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import helpers
    _mock_screenshot(monkeypatch, helpers)
    with pytest.raises(OCRNotAvailableError) as exc_info:
        helpers.ocr()
    msg = str(exc_info.value)
    assert "macOS" in msg or "Vision" in msg
    assert "tesseract" in msg.lower() or "SETUP.md" in msg


def test_android_ocr_raises_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from android_harness import helpers
    _mock_screenshot(monkeypatch, helpers)
    with pytest.raises(OCRNotAvailableError) as exc_info:
        helpers.ocr()
    msg = str(exc_info.value)
    assert "macOS" in msg or "Vision" in msg
    assert "tesseract" in msg.lower() or "SETUP.md" in msg


def test_iphone_ocr_does_not_crash_at_import_on_linux(monkeypatch):
    """Importing the helpers module on Linux must not blow up.

    The Vision/Foundation imports are lazy (inside the ocr() function body),
    so a Linux host with no pyobjc can still import the module — and access
    every non-OCR helper without trouble.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    # Force a fresh import path
    for mod in ("iphone_harness.helpers", "android_harness.helpers"):
        sys.modules.pop(mod, None)
    import iphone_harness.helpers  # noqa: F401
    import android_harness.helpers  # noqa: F401


def test_iphone_ocr_error_is_caught_as_runtime_error(monkeypatch):
    """Back-compat: legacy callers that catch RuntimeError still work."""
    monkeypatch.setattr(sys, "platform", "linux")
    from iphone_harness import helpers
    _mock_screenshot(monkeypatch, helpers)
    with pytest.raises(RuntimeError):
        helpers.ocr()


def test_android_ocr_message_mentions_setup_doc(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from android_harness import helpers
    _mock_screenshot(monkeypatch, helpers)
    with pytest.raises(OCRNotAvailableError) as exc_info:
        helpers.ocr()
    assert "SETUP.md" in str(exc_info.value)
