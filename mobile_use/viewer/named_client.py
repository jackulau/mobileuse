"""Per-name MJPEG IPC client.

The existing single-device `ViewerServer` imports `iphone_harness.helpers` at
module level, which captures `IPH_NAME` (or `ANH_NAME`) once. That's fine for
the default daemon, but it can't address multiple named daemons in the same
process.

This client talks directly to the daemon socket via `<harness>._ipc` —
`connect(name)` resolves to the right per-name socket / TCP endpoint, and
`request()` sends a single JSON-RPC call. No env mutation, no module reload,
safe to instantiate one per device in parallel threads.
"""
import base64
import threading


class NamedStreamClient:
    """Pull MJPEG frames from a specific named daemon.

    Args:
        platform: 'ios' or 'android'
        name: daemon name (matches IPH_NAME / ANH_NAME). None → default daemon.
        fps / quality / max_dim: passed to screen_stream_start.
    """

    def __init__(self, platform, name=None, fps=6, quality=60, max_dim=800):
        if platform not in ("ios", "android"):
            raise ValueError(f"NamedStreamClient: unknown platform {platform!r}")
        self.platform = platform
        self.name = name
        self.fps = fps
        self.quality = quality
        self.max_dim = max_dim
        self._lock = threading.Lock()
        self._started = False
        self._ipc = self._load_ipc()

    def _load_ipc(self):
        if self.platform == "ios":
            from iphone_harness import _ipc
            return _ipc
        from android_harness import _ipc
        return _ipc

    def _call(self, method, params=None, timeout=10.0):
        """One-shot connect → request → close. Mirrors the helpers `_send` pattern."""
        c, token = self._ipc.connect(self.name, timeout=timeout)
        try:
            resp = self._ipc.request(c, token, {
                "method": method,
                "params": params or {},
            })
        finally:
            try:
                c.close()
            except Exception:
                pass
        return resp

    def start(self):
        """Tell the daemon to start its screen-capture loop. Idempotent."""
        with self._lock:
            resp = self._call("screen_stream_start", {
                "fps": self.fps,
                "quality": self.quality,
                "max_dim": self.max_dim,
            })
            self._started = True
            return resp.get("result", {"running": False})

    def frame(self):
        """Return the latest frame dict from the daemon.

        Shape: {ready: bool, frame_no: int, jpeg_b64?: str, fps?, quality?}.
        Decode jpeg_b64 with `base64.b64decode` for the JPEG bytes.
        """
        resp = self._call("screen_stream_frame", timeout=10.0)
        return resp.get("result", {"ready": False, "frame_no": 0})

    def frame_jpeg(self):
        """Convenience: return JPEG bytes or None if no frame ready yet."""
        r = self.frame()
        if not r.get("ready"):
            return None
        b64 = r.get("jpeg_b64")
        if not b64:
            return None
        return base64.b64decode(b64)

    def stop(self):
        """Cancel the daemon's capture loop. Idempotent."""
        with self._lock:
            if not self._started:
                return {"running": False}
            try:
                resp = self._call("screen_stream_stop")
                return resp.get("result", {"running": False})
            finally:
                self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        try:
            self.stop()
        except Exception:
            pass

    def __repr__(self):
        return f"NamedStreamClient(platform={self.platform!r}, name={self.name!r})"
