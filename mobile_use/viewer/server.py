"""HTTP MJPEG viewer — stdlib-only (no extra deps).

Routes:
  GET  /         → static index.html (embedded; one file ships)
  GET  /stream   → multipart/x-mixed-replace JPEG stream pulled from daemon
  GET  /still    → single JPEG snapshot (cheap; for embedding in test asserts)
  GET  /healthz  → 200 OK if frame loop is alive on the daemon side
  POST /control  → interactive control: tap / type / key (403 when read_only)

Run flow (synchronous owner — caller drives lifecycle):
    viewer = ViewerServer(platform="ios")        # or "android"
    viewer.start()                                # binds, threads up
    print(viewer.url)                             # http://127.0.0.1:<port>/
    ...do other work...
    viewer.stop()                                 # joins thread, stops stream
"""
import base64
import http.server
import json
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path


def _warn(msg):
    """One concise operator line to stderr (no traceback). Viewer is stdlib-only."""
    sys.stderr.write(f"[viewer] {msg}\n")

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
  <span style="display:flex;gap:10px;align-items:center">
    <label style="cursor:pointer;user-select:none">
      <input type=checkbox id=ctl checked> control
    </label>
    <button id=homebtn title="press home">home</button>
    <span id=status>loading...</span>
  </span>
</header>
<main>
  <img id=screen src="/stream" alt="device screen" style="cursor:crosshair">
</main>
<footer style="padding:8px 12px;background:#222;display:flex;gap:8px">
  <input id=typer placeholder="type text on device, Enter sends"
         style="flex:1;background:#111;color:#ddd;border:1px solid #444;
                border-radius:4px;padding:6px 8px">
  <button id=sendbtn>send</button>
</footer>
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

  // ---- interactive control: click-to-tap + type + keys -----------------
  const img = document.getElementById('screen');
  const ctl = document.getElementById('ctl');
  const typer = document.getElementById('typer');

  async function control(body) {
    if (!ctl.checked) return;
    try {
      const r = await fetch('/control', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (r.status === 403) { ctl.checked = false; ctl.disabled = true; }
    } catch (e) { /* surface via /healthz status; never break the mirror */ }
  }

  img.addEventListener('click', (e) => {
    // Click position over the RENDERED image -> fraction of the frame.
    // The server multiplies by the device's logical point size, so the
    // displayed-vs-natural scale never matters here.
    const rect = img.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    control({action: 'tap', fx: fx, fy: fy});
  });

  document.getElementById('homebtn').addEventListener('click',
    () => control({action: 'key', key: 'home'}));

  function sendText() {
    if (typer.value) { control({action: 'type', text: typer.value}); typer.value = ''; }
  }
  document.getElementById('sendbtn').addEventListener('click', sendText);
  typer.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); sendText(); }
  });

  // Keyboard passthrough when focus is on the page (not the text box).
  document.addEventListener('keydown', (e) => {
    if (document.activeElement === typer || !ctl.checked) return;
    if (e.key === 'Enter') { e.preventDefault(); control({action: 'key', key: 'enter'}); }
    else if (e.key === 'Backspace') { e.preventDefault(); }
    else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault(); control({action: 'type', text: e.key});
    }
  });
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


def _control_point(body, size):
    """Map a control payload onto device points.

    Preferred payload: {"fx": 0..1, "fy": 0..1} — fractions of the frame, which
    the page computes from the click position over the img's natural size (so
    server-side never needs to decode frame dimensions). Also accepts
    {"x", "y", "frame_w", "frame_h"} raw frame pixels. Returns (x, y) ints in
    device points, or raises ValueError on a malformed payload.
    """
    w, h = float(size["width"]), float(size["height"])
    if "fx" in body and "fy" in body:
        fx, fy = float(body["fx"]), float(body["fy"])
    elif all(k in body for k in ("x", "y", "frame_w", "frame_h")):
        fw, fh = float(body["frame_w"]), float(body["frame_h"])
        if fw <= 0 or fh <= 0:
            raise ValueError("frame_w/frame_h must be positive")
        fx, fy = float(body["x"]) / fw, float(body["y"]) / fh
    else:
        raise ValueError("tap needs {fx,fy} fractions or {x,y,frame_w,frame_h}")
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        raise ValueError(f"tap fraction out of range: fx={fx} fy={fy}")
    return int(round(fx * w)), int(round(fy * h))


def _dispatch_control(helpers, body, bind_name=None):
    """Route one control action through the platform helpers.

    Routing through helpers (optionally bound to a named daemon via the same
    contextvar NamedStreamClient-style per-name addressing uses) keeps the
    platform differences — tap scripts, keyboard semantics — where they are
    already solved. Returns a JSON-able result dict; raises ValueError on a
    bad payload.
    """
    token = helpers._use_name(bind_name) if bind_name else None
    try:
        action = body.get("action")
        if action == "tap":
            x, y = _control_point(body, helpers.window_size())
            helpers.tap_at_xy(x, y)
            return {"ok": True, "action": "tap", "x": x, "y": y}
        if action == "type":
            text = body.get("text", "")
            if not isinstance(text, str) or not text:
                raise ValueError("type needs a non-empty 'text' string")
            helpers.type_text(text)
            return {"ok": True, "action": "type", "chars": len(text)}
        if action == "key":
            key = body.get("key")
            keymap = {
                "enter": "press_enter",
                "home": "press_home",
                "back": "press_back",
            }
            fn_name = keymap.get(key)
            if fn_name is None:
                raise ValueError(f"unknown key {key!r} (enter|home|back)")
            fn = getattr(helpers, fn_name, None)
            if fn is None:
                raise ValueError(f"key {key!r} not supported on this platform")
            fn()
            return {"ok": True, "action": "key", "key": key}
        raise ValueError(f"unknown control action {action!r} (tap|type|key)")
    finally:
        if token is not None:
            helpers._reset_name(token)


class ViewerServer:
    """Pull frames from the device daemon and serve them over HTTP MJPEG.

    Lifecycle is explicit: start() spawns a daemon thread, stop() joins. The
    server stops the daemon-side stream loop on close so we don't leak the
    Appium-screenshot RPC after the viewer goes away.
    """

    def __init__(self, platform, port=None, fps=6, quality=60, max_dim=800,
                 host="127.0.0.1", read_only=False):
        self.platform = platform
        self.port = port if port is not None else _free_port()
        self.host = host
        self.fps = fps
        self.quality = quality
        self.max_dim = max_dim
        self.read_only = read_only
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
        handler_cls = _make_handler(self._helpers, self.platform,
                                    read_only=self.read_only)
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


def _make_handler(helpers, platform, read_only=False):
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

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/control":
                self.send_error(404, "not found")
                return
            if read_only:
                self.send_error(403, "viewer is read-only (--read-only)")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                result = _dispatch_control(helpers, body)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
                return
            except Exception as e:
                self._send_json({"ok": False, "error": f"control failed: {e}"},
                                status=500)
                return
            self._send_json(result)

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

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
                    try:
                        r = helpers.screen_stream_frame()
                    except (BrokenPipeError, ConnectionResetError):
                        return  # client went away mid-fetch
                    except Exception as e:
                        # Daemon/Appium down or mid-restart. Don't let it escape
                        # into the HTTP server thread (that dumps a full traceback);
                        # stop this stream cleanly so the page can retry the <img>.
                        _warn(f"{platform} stream stopped: {e}")
                        return
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
