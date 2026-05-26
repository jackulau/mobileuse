#!/usr/bin/env python3
"""Multi-device viewer demo — live MJPEG grid across all connected devices.

Two modes:

  python3 docs/demos/multi-device-viewer.py --mock
      Spawn MultiViewerServer with 3 fake clients (1x1 JPEG each), hit
      /, /healthz, /still/<name>, assert shapes, exit 0. CI-safe.

  python3 docs/demos/multi-device-viewer.py
      Discover connected devices (idevice_id + adb), ensure each named
      daemon is alive, start MultiViewerServer, open browser, block
      until Ctrl+C.

Equivalent CLI: `mobile-use devices view [--mock | --no-browser | --port N]`.
"""
import argparse
import base64
import json
import sys
import time
import urllib.request


TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAcI/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/a"
    "AAgBAQABPxA//9k="
)


def _mock_client_factory():
    class _Mock:
        def __init__(self, platform, name, fps, quality, max_dim):
            self.platform, self.name, self.fps = platform, name, fps
            self._n = 0

        def start(self):
            return {"running": True}

        def frame(self):
            self._n += 1
            return {"ready": True, "frame_no": self._n,
                    "jpeg_b64": TINY_JPEG_B64, "fps": float(self.fps)}

        def frame_jpeg(self):
            return base64.b64decode(TINY_JPEG_B64)

        def stop(self):
            return {"running": False}

    return lambda p, n, f, q, m: _Mock(p, n, f, q, m)


def _mock_run():
    from mobile_use.viewer.multi_server import MultiViewerServer

    pairs = [("ios", "iphone-mock-A"), ("ios", "iphone-mock-B"), ("android", "pixel-mock-1")]
    viewer = MultiViewerServer(pairs, fps=2, client_factory=_mock_client_factory())
    viewer.start()
    print(f"viewer URL: {viewer.url}")
    print(f"streaming {len(pairs)} device(s) — grid_ok")

    try:
        time.sleep(0.3)
        with urllib.request.urlopen(viewer.url, timeout=2) as r:
            body = r.read().decode()
        assert "iphone-mock-A" in body and "pixel-mock-1" in body, "grid index missing devices"

        with urllib.request.urlopen(viewer.url + "healthz", timeout=2) as r:
            data = json.loads(r.read())
        assert len(data["devices"]) == 3, f"expected 3 devices in healthz, got {len(data['devices'])}"
        assert all(d["running"] for d in data["devices"]), "not all streams ready"

        for _, name in pairs:
            with urllib.request.urlopen(viewer.url + f"still/{name}", timeout=2) as r:
                blob = r.read()
            assert blob[:3] == b"\xff\xd8\xff", f"non-JPEG still for {name}"

        print(f"all streams ready, /still round-tripped for {len(pairs)} devices")
        return 0
    finally:
        viewer.stop()


def _real_run():
    from mobile_use import devices as _dev
    from mobile_use.viewer.multi_server import MultiViewerServer

    connected = _dev.discover_connected()
    if not connected:
        print("no devices connected", file=sys.stderr)
        for h in _dev.discovery_hints():
            print(f"  hint: {h}", file=sys.stderr)
        return 1

    pairs = [(d["platform"], d["name"]) for d in connected]
    print(f"discovered {len(pairs)} device(s):")
    for p, n in pairs:
        print(f"  {p}/{n}")

    for platform, name in pairs:
        try:
            if platform == "ios":
                from iphone_harness import admin as adm
            else:
                from android_harness import admin as adm
            adm.ensure_daemon(name=name)
        except Exception as e:
            print(f"warning: daemon {platform}/{name} not ready: {e}", file=sys.stderr)

    viewer = MultiViewerServer(pairs, fps=4)
    viewer.start()
    print(f"\nviewer URL: {viewer.url}")
    try:
        import webbrowser
        webbrowser.open(viewer.url)
    except Exception:
        pass
    print("Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true",
                    help="Run against stubbed clients (no devices needed).")
    args = ap.parse_args()
    if args.mock:
        return _mock_run()
    return _real_run()


if __name__ == "__main__":
    sys.exit(main())
