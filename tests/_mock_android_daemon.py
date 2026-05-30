"""Test-only mock android-harness daemon. Same contract as the real one minus Appium."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from android_harness import _ipc as ipc

NAME = os.environ.get("ANH_NAME", "default")
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))


class MockDaemon:
    def __init__(self):
        self.stop = None
        self._stream_running = False
        self._stream_frame_no = 0
        self._stream_fps = 6.0
        self._stream_quality = 60

    async def handle(self, req):
        meta = req.get("meta")
        if meta == "ping":
            return {"pong": True, "pid": os.getpid()}
        if meta == "shutdown":
            self.stop.set()
            return {"ok": True}

        method = req.get("method")
        if not method:
            return {"error": "missing method"}
        if method == "appium":
            return {"result": {"packageName": "com.android.launcher"}}
        if method == "screenshot":
            return {"result": {"path": "/tmp/anh-mock-shot.png", "bytes": 0}}
        if method == "window_size":
            return {"result": {"width": 1080, "height": 1920}}
        if method == "page_source":
            return {"result": "<hierarchy><node class='android.widget.FrameLayout'/></hierarchy>"}
        if method == "click_element":
            return {"result": {"matched": 1}}
        if method == "send_keys":
            return {"result": {"sent": req.get("params", {}).get("keys", ""), "matched": 1}}
        if method == "set_value":
            return {"result": {"set": req.get("params", {}).get("value", ""), "matched": 1}}
        if method == "active_app":
            return {"result": {"packageName": "com.android.launcher"}}
        if method == "get_orientation":
            return {"result": "PORTRAIT"}
        if method == "set_orientation":
            return {"result": (req.get("params") or {}).get("orientation", "PORTRAIT").upper()}
        # Screen stream — synthetic JPEG stub.
        if method == "screen_stream_start":
            params = req.get("params") or {}
            self._stream_fps = float(params.get("fps", 6))
            self._stream_quality = int(params.get("quality", 60))
            already = self._stream_running
            self._stream_running = True
            return {"result": {
                "running": True,
                "started": not already,
                "updated": already,
                "fps": self._stream_fps,
                "quality": self._stream_quality,
                "max_dim": int(params.get("max_dim", 800)),
            }}
        if method == "screen_stream_frame":
            if not self._stream_running:
                return {"result": {"ready": False, "frame_no": 0}}
            self._stream_frame_no += 1
            import base64
            jpeg_stub = bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605"
                "08070707090908"
            )
            return {"result": {
                "ready": True,
                "frame_no": self._stream_frame_no,
                "jpeg_b64": base64.b64encode(jpeg_stub).decode("ascii"),
                "fps": self._stream_fps,
                "quality": self._stream_quality,
            }}
        if method == "screen_stream_stop":
            was = self._stream_running
            self._stream_running = False
            self._stream_frame_no = 0
            return {"result": {"running": False, "stopped": was}}
        return {"error": f"mock: unknown method {method!r}"}


async def serve(d):
    async def handler(reader, writer):
        # Keep-alive loop — mirrors the real daemon so tests exercise socket reuse.
        try:
            while True:
                try:
                    line = await reader.readline()
                except Exception:
                    break
                if not line:
                    break
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as e:
                    resp = {"error": f"bad json: {e}"}
                else:
                    try:
                        resp = await d.handle(req)
                    except Exception as e:
                        resp = {"error": str(e)}
                try:
                    writer.write((json.dumps(resp, default=str) + "\n").encode())
                    await writer.drain()
                except Exception:
                    break
        finally:
            try:
                writer.close()
            except Exception:
                pass

    serve_task = asyncio.create_task(ipc.serve(NAME, handler))
    stop_task = asyncio.create_task(d.stop.wait())
    await asyncio.sleep(0.05)
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (serve_task, stop_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        ipc.cleanup_endpoint(NAME)


async def _main():
    d = MockDaemon()
    d.stop = asyncio.Event()
    await serve(d)


def already_running():
    return ipc.ping(NAME, timeout=0.5)


if __name__ == "__main__":
    if already_running():
        print(f"mock daemon already running for {NAME}", file=sys.stderr)
        sys.exit(0)
    open(LOG, "w").close()
    open(PID, "w").write(str(os.getpid()))
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(PID)
        except FileNotFoundError:
            pass
