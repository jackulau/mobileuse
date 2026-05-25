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
        return {"error": f"mock: unknown method {method!r}"}


async def serve(d):
    async def handler(reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                writer.write((json.dumps({"error": f"bad json: {e}"}) + "\n").encode())
                await writer.drain()
                return
            resp = await d.handle(req)
            writer.write((json.dumps(resp, default=str) + "\n").encode())
            await writer.drain()
        except Exception as e:
            try:
                writer.write((json.dumps({"error": str(e)}) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()

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
