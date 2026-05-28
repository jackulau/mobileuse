"""Live device-screen viewer — powers `mobile-use --headed`.

Serves an MJPEG stream of the current device screen by pulling frames from
the iphone-harness or android-harness daemon and re-emitting them as
multipart/x-mixed-replace over HTTP. Browser tabs render it as a live image.

Single-consumer for v1 (one viewer at a time). Viewer is read-only — it never
sends input to the device. Use the agent loop / `-c` for that.
"""
from .multi_server import MultiViewerServer  # noqa: F401
from .named_client import NamedStreamClient  # noqa: F401
from .server import ViewerServer  # noqa: F401
