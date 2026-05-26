"""Multi-device MJPEG viewer — one HTTP server, N device streams.

Single page at `/` shows a grid of all configured devices, each tile pulling
its own MJPEG stream from `/stream/<name>`. Per-device snapshots at
`/still/<name>`. Combined health at `/healthz`.

Usage:
    pairs = [("ios", "iphone-A"), ("ios", "iphone-B"), ("android", "pixel-1")]
    viewer = MultiViewerServer(pairs)
    viewer.start()
    print(viewer.url)
    ...
    viewer.stop()
"""
import http.server
import json
import re
import socket
import socketserver
import threading
import time

from .named_client import NamedStreamClient


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _grid_html(devices):
    tiles = []
    for d in devices:
        label = f"{d['platform']} | {d['name']}"
        tiles.append(
            f'<figure data-name="{d["name"]}">'
            f'  <figcaption>{label} <span class="state">…</span></figcaption>'
            f'  <img src="/stream/{d["name"]}" alt="{label}">'
            f"</figure>"
        )
    grid = "\n".join(tiles)
    return (
        "<!doctype html>\n"
        "<html lang=en><head><meta charset=utf-8>"
        "<title>mobile-use multi-viewer</title>"
        "<style>"
        "html,body{margin:0;padding:0;background:#0e0e10;color:#ddd;"
        "font:13px/1.4 system-ui,sans-serif;height:100%}"
        "header{padding:8px 12px;background:#1c1c20;display:flex;"
        "justify-content:space-between;align-items:center;border-bottom:1px solid #2a2a30}"
        "header b{color:#fff}"
        "main{display:grid;gap:12px;padding:12px;"
        "grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
        "align-items:start}"
        "figure{margin:0;background:#1c1c20;border-radius:8px;overflow:hidden;"
        "box-shadow:0 0 12px rgba(0,0,0,.4);display:flex;flex-direction:column}"
        "figcaption{padding:6px 10px;font-family:ui-monospace,monospace;"
        "font-size:12px;display:flex;justify-content:space-between;align-items:center}"
        "figcaption .state{opacity:.7;font-size:11px}"
        "img{width:100%;display:block;background:#000;image-rendering:pixelated}"
        "</style></head><body>"
        "<header><span><b>mobile-use</b> &mdash; multi-device live view</span>"
        '<span id=overall>polling…</span></header>'
        f"<main>{grid}</main>"
        "<script>\n"
        "async function tick(){\n"
        "  try{\n"
        "    const r=await fetch('/healthz',{cache:'no-store'});\n"
        "    const j=await r.json();\n"
        "    let total=0,ready=0;\n"
        "    for(const d of j.devices){\n"
        "      total++; if(d.running) ready++;\n"
        "      const el=document.querySelector(`figure[data-name=\"${d.name}\"] .state`);\n"
        "      if(el) el.textContent = d.running ?\n"
        "        `${d.fps.toFixed(1)}fps · #${d.frame_no}` : 'stream stopped';\n"
        "    }\n"
        "    document.getElementById('overall').textContent = `${ready}/${total} streams live`;\n"
        "  }catch(e){document.getElementById('overall').textContent='viewer offline'}\n"
        "}\n"
        "tick();setInterval(tick,1000);\n"
        "</script></body></html>"
    )


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """One worker thread per request — MJPEG streams hold connections open."""
    daemon_threads = True
    allow_reuse_address = True


class MultiViewerServer:
    """HTTP server hosting N MJPEG streams under /stream/<name>.

    Args:
        device_pairs: list of (platform, name) tuples
        port: explicit port; None → auto-allocate
        fps / quality / max_dim: forwarded to each NamedStreamClient
        host: bind host (default loopback)
        client_factory: override for testing — `fn(platform, name, fps, quality, max_dim) → client`
    """

    def __init__(self, device_pairs, port=None, fps=4, quality=60, max_dim=800,
                 host="127.0.0.1", client_factory=None):
        if not device_pairs:
            raise ValueError("MultiViewerServer: at least one device required")
        seen = set()
        self.devices = []
        for pair in device_pairs:
            if len(pair) != 2:
                raise ValueError(f"device entry must be (platform, name): {pair!r}")
            platform, name = pair
            if platform not in ("ios", "android"):
                raise ValueError(f"bad platform {platform!r} (ios|android)")
            if not _NAME_RE.match(name):
                raise ValueError(f"bad name {name!r}: must match [A-Za-z0-9_-]{{1,64}}")
            if name in seen:
                raise ValueError(f"duplicate device name {name!r}")
            seen.add(name)
            self.devices.append({"platform": platform, "name": name})

        self.host = host
        self.port = port if port is not None else _free_port()
        self.fps = fps
        self.quality = quality
        self.max_dim = max_dim

        factory = client_factory or (lambda p, n, f, q, m: NamedStreamClient(
            platform=p, name=n, fps=f, quality=q, max_dim=m,
        ))
        self.clients = {
            d["name"]: factory(d["platform"], d["name"], fps, quality, max_dim)
            for d in self.devices
        }

        self._server = None
        self._thread = None
        self._stopped = False

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    def device_list(self):
        return [dict(d) for d in self.devices]

    def start(self):
        """Start every named-stream client + the HTTP server thread."""
        for client in self.clients.values():
            try:
                client.start()
            except Exception:
                pass
        handler_cls = _make_handler(self)
        self._server = _ThreadedHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="multi-viewer-http",
        )
        self._thread.start()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        for client in self.clients.values():
            try:
                client.stop()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def _make_handler(viewer):
    """Build an HTTP handler closed over the viewer instance."""

    devices_by_name = {d["name"]: d for d in viewer.devices}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._serve_index()
            elif path == "/devices":
                self._serve_devices()
            elif path == "/healthz":
                self._serve_health()
            elif path.startswith("/stream/"):
                self._serve_stream(path[len("/stream/"):])
            elif path.startswith("/still/"):
                self._serve_still(path[len("/still/"):])
            else:
                self.send_error(404, "not found")

        def _serve_index(self):
            body = _grid_html(viewer.devices).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_devices(self):
            body = json.dumps(viewer.device_list()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_health(self):
            entries = []
            for d in viewer.devices:
                client = viewer.clients[d["name"]]
                try:
                    r = client.frame()
                except Exception as e:
                    entries.append({
                        "platform": d["platform"], "name": d["name"],
                        "running": False, "frame_no": 0, "fps": 0.0,
                        "error": str(e),
                    })
                    continue
                entries.append({
                    "platform": d["platform"], "name": d["name"],
                    "running": bool(r.get("ready")),
                    "frame_no": int(r.get("frame_no", 0)),
                    "fps": float(r.get("fps", 0.0)),
                })
            body = json.dumps({"devices": entries}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_still(self, name):
            if name not in devices_by_name:
                self.send_error(404, f"unknown device {name!r}")
                return
            jpeg = viewer.clients[name].frame_jpeg()
            if jpeg is None:
                self.send_error(503, "stream not ready")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)

        def _serve_stream(self, name):
            if name not in devices_by_name:
                self.send_error(404, f"unknown device {name!r}")
                return
            client = viewer.clients[name]
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
                    r = client.frame()
                    if r.get("ready") and r.get("frame_no", 0) != last_no:
                        last_no = r["frame_no"]
                        import base64 as _b64
                        jpeg = _b64.b64decode(r["jpeg_b64"])
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
                    fps = max(1.0, float(r.get("fps", viewer.fps)))
                    time.sleep(1.0 / (fps * 2.0))
            except (BrokenPipeError, ConnectionResetError):
                return

    return _Handler
