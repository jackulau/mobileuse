"""HTTP MJPEG viewer — stdlib-only (no extra deps).

Routes:
  GET /         → static index.html (embedded; one file ships)
  GET /stream   → multipart/x-mixed-replace JPEG stream pulled from daemon
  GET /still    → single JPEG snapshot (cheap; for embedding in test asserts)
  GET /healthz  → 200 OK if frame loop is alive on the daemon side

Run flow (synchronous owner — caller drives lifecycle):
    viewer = ViewerServer(platform="ios")        # or "android"
    viewer.start()                                # binds, threads up
    print(viewer.url)                             # http://127.0.0.1:<port>/
    ...do other work...
    viewer.stop()                                 # joins thread, stops stream
"""
import base64
import http.server
import socket
import socketserver
import threading
import time
from pathlib import Path


_INDEX_HTML = b"""<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<title>mobile-use viewer</title>
<style>
  html, body { margin:0; padding:0; background:#111; color:#ddd;
    font:13px/1.4 system-ui, sans-serif; height:100%; }
  header { padding:8px 12px; background:#222; display:flex;
    justify-content:space-between; align-items:center; }
  header b { color:#fff; }
  main { display:flex; align-items:center; justify-content:center;
    height:calc(100% - 36px); padding:12px; box-sizing:border-box; }
  img { max-width:100%; max-height:100%; image-rendering:pixelated;
    background:#000; border-radius:6px; box-shadow:0 0 16px rgba(0,0,0,.5); }
  #status { font-family:ui-monospace, monospace; opacity:.7; }
</style>
</head>
<body>
<header>
  <span><b>mobile-use</b> &mdash; live device screen</span>
  <span id=status>loading...</span>
</header>
<main>
  <img id=screen src="/stream" alt="device screen">
</main>
<script>
  // Poll /healthz for liveness; image element handles the MJPEG stream itself.
  async function tick() {
    try {
      const r = await fetch('/healthz', {cache:'no-store'});
      const j = await r.json();
      document.getElementById('status').textContent =
        j.running ? `${j.platform} | ${j.fps.toFixed(1)} fps | frame #${j.frame_no}`
                  : 'daemon: stream stopped';
    } catch (e) {
      document.getElementById('status').textContent = 'viewer offline';
    }
  }
  tick(); setInterval(tick, 1000);
</script>
</body>
</html>
"""


def _load_helpers(platform):
    """Lazy-import the right harness so the viewer module itself stays light."""
    if platform == "ios":
        from iphone_harness import helpers
        return helpers
    if platform == "android":
        from android_harness import helpers
        return helpers
    raise ValueError(f"viewer: unknown platform {platform!r} (expected 'ios' or 'android')")


def _free_port():
    """Ask the kernel for an unused TCP port on loopback."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class ViewerServer:
    """Pull frames from the device daemon and serve them over HTTP MJPEG.

    Lifecycle is explicit: start() spawns a daemon thread, stop() joins. The
    server stops the daemon-side stream loop on close so we don't leak the
    Appium-screenshot RPC after the viewer goes away.
    """

    def __init__(self, platform, port=None, fps=6, quality=60, max_dim=800,
                 host="127.0.0.1"):
        self.platform = platform
        self.port = port if port is not None else _free_port()
        self.host = host
        self.fps = fps
        self.quality = quality
        self.max_dim = max_dim
        self._helpers = _load_helpers(platform)
        self._server = None
        self._thread = None
        self._stopped = False

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    def start(self):
        """Tell the daemon to start streaming + spawn the HTTP server thread."""
        self._helpers.screen_stream_start(
            fps=self.fps, quality=self.quality, max_dim=self.max_dim,
        )
        handler_cls = _make_handler(self._helpers, self.platform)
        self._server = _ThreadedHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="viewer-http",
        )
        self._thread.start()

    def stop(self):
        """Stop the HTTP server and ask the daemon to halt the capture loop."""
        if self._stopped:
            return
        self._stopped = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        try:
            self._helpers.screen_stream_stop()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self):
        self.start(); return self

    def __exit__(self, *exc):
        self.stop()


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """One worker thread per request — MJPEG streams hold the connection open,
    so single-threaded HTTPServer would block /healthz polls behind /stream."""
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(helpers, platform):
    """Build a request handler class closed over the harness helpers module."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence default access log
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._serve_index()
            elif self.path == "/stream":
                self._serve_stream()
            elif self.path == "/still":
                self._serve_still()
            elif self.path == "/healthz":
                self._serve_health()
            else:
                self.send_error(404, "not found")

        def _serve_index(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_INDEX_HTML)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(_INDEX_HTML)

        def _serve_still(self):
            r = helpers.screen_stream_frame()
            if not r.get("ready"):
                self.send_error(503, "stream not ready")
                return
            jpeg = base64.b64decode(r["jpeg_b64"])
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)

        def _serve_health(self):
            r = helpers.screen_stream_frame()
            import json as _json
            body = _json.dumps({
                "platform": platform,
                "running": bool(r.get("ready")),
                "frame_no": int(r.get("frame_no", 0)),
                "fps": float(r.get("fps", 0)),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_stream(self):
            boundary = "mobile-use-frame"
            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={boundary}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last_no = -1
            try:
                while True:
                    r = helpers.screen_stream_frame()
                    if r.get("ready") and r.get("frame_no", 0) != last_no:
                        last_no = r["frame_no"]
                        jpeg = base64.b64decode(r["jpeg_b64"])
                        self.wfile.write(b"--" + boundary.encode() + b"\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                        )
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        try:
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                    # Poll twice the configured fps so we never miss a frame
                    # without spinning. fps is best-effort; 6fps → 12 polls/s.
                    fps = max(1.0, float(r.get("fps", 6.0)))
                    time.sleep(1.0 / (fps * 2.0))
            except (BrokenPipeError, ConnectionResetError):
                return  # client closed tab — clean shutdown

    return _Handler
