"""Appium WebDriver session holder + IPC relay for Android (UIAutomator2).

Same architecture as iphone_harness/daemon.py:
  - long-lived process
  - owns one Appium Remote() session to the device via UIAutomator2
  - exposes a JSON-line RPC over AF_UNIX
  - auto-recovers from stale sessions
"""
import asyncio
import concurrent.futures
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import _ipc as ipc


def _load_env():
    repo_root = Path(__file__).resolve().parents[1]
    workspace = Path(os.environ.get("ANH_AGENT_WORKSPACE", repo_root / "agent-workspace")).expanduser()
    for p in (repo_root / ".env", workspace / ".env"):
        if not p.exists():
            continue
        _load_env_file(p)


def _load_env_file(p):
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NAME = os.environ.get("ANH_NAME", "default")
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))

APPIUM_URL = os.environ.get("ANH_APPIUM_URL", "http://127.0.0.1:4723")
UDID = os.environ.get("ANH_UDID")
PLATFORM_VERSION = os.environ.get("ANH_PLATFORM_VERSION")
DEVICE_NAME = os.environ.get("ANH_DEVICE_NAME", "Android")
APP_PACKAGE = os.environ.get("ANH_APP_PACKAGE")
APP_ACTIVITY = os.environ.get("ANH_APP_ACTIVITY")
NEW_COMMAND_TIMEOUT = int(os.environ.get("ANH_NEW_COMMAND_TIMEOUT", "600"))
# Min seconds between deep liveness probes (see iphone_harness/daemon.py).
PROBE_INTERVAL = float(os.environ.get("ANH_PROBE_INTERVAL", "30"))


def _is_session_error(e):
    """True if an exception looks like a dead/invalid Appium session."""
    s = str(e).lower()
    return any(k in s for k in (
        "invalid session", "session id", "no such session",
        "session is either terminated", "session does not exist",
        "a session is either terminated or not started",
    ))


def log(msg):
    open(LOG, "a").write(f"{time.strftime('%H:%M:%S')} {msg}\n")


_AppiumBy = None


def _get_appium_by():
    global _AppiumBy
    if _AppiumBy is None:
        from appium.webdriver.common.appiumby import AppiumBy
        _AppiumBy = AppiumBy
    return _AppiumBy


def _apply_extra_caps(o, env_var):
    """Apply arbitrary Appium caps from a JSON env var (e.g. ANH_CAPS).

    Lets you set automationName overrides, systemPort, skipServerInstallation,
    chromedriver paths, etc. without editing source. Keys should carry their
    normal prefix (e.g. "appium:systemPort"). Malformed JSON is logged + ignored.
    """
    raw = os.environ.get(env_var)
    if not raw:
        return
    try:
        extra = json.loads(raw)
    except (ValueError, TypeError) as e:
        log(f"{env_var}: ignoring invalid JSON ({e})")
        return
    if not isinstance(extra, dict):
        log(f"{env_var}: expected a JSON object, got {type(extra).__name__}")
        return
    for k, v in extra.items():
        o.set_capability(k, v)


def _build_options():
    """UiAutomator2Options for the current Android device."""
    from appium.options.android import UiAutomator2Options
    if not UDID:
        raise RuntimeError(
            "ANH_UDID not set. Connect the Android device and either:\n"
            "  - export ANH_UDID=<serial>  (find via `adb devices`)\n"
            "  - put ANH_UDID=<serial> in <mobile_use>/.env or <agent-workspace>/.env"
        )
    o = UiAutomator2Options()
    o.platform_name = "Android"
    o.device_name = DEVICE_NAME
    o.udid = UDID
    if PLATFORM_VERSION:
        o.platform_version = PLATFORM_VERSION
    o.set_capability("appium:automationName", "UiAutomator2")
    o.set_capability("appium:newCommandTimeout", NEW_COMMAND_TIMEOUT)
    o.set_capability("appium:autoGrantPermissions", True)
    o.set_capability("appium:noReset", True)
    # Install Appium's Unicode IME so type_text/set_value can inject emoji,
    # accented, and CJK characters — bare UiAutomator2 send_keys is ASCII-only
    # and silently drops/mangles the rest. resetKeyboard restores the user's IME
    # on session teardown.
    o.set_capability("appium:unicodeKeyboard", True)
    o.set_capability("appium:resetKeyboard", True)
    # Arbitrary per-deployment cap overrides (systemPort, automationName, etc.).
    _apply_extra_caps(o, "ANH_CAPS")
    return o


class Daemon:
    def __init__(self):
        self.driver = None
        self.stop = None
        self._loop = None
        # SINGLE driver worker — the selenium/Appium session is not thread-safe
        # and the screen-stream task + IPC handlers both call _drive. A
        # multi-worker default pool would race them; serialize onto one thread.
        self._exec = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anh-driver"
        )
        # Screen-stream state — populated by screen_stream_start RPC.
        self._stream_task = None
        self._stream_frame = None
        self._stream_frame_no = 0
        self._stream_fps = 6.0
        self._stream_quality = 60
        self._stream_max_dim = 800
        self._last_ok = 0.0             # monotonic time of last successful command

    async def _drive(self, fn):
        """Run a blocking driver callable on the single driver worker (serialized)."""
        return await self._loop.run_in_executor(self._exec, fn)

    async def _connect(self):
        from appium import webdriver
        opts = _build_options()
        log(f"connecting to Appium at {APPIUM_URL} (udid={UDID})")
        def _make():
            return webdriver.Remote(APPIUM_URL, options=opts)
        try:
            self.driver = await self._drive(_make)
        except Exception as e:
            raise RuntimeError(
                f"Appium session create failed: {e}\n"
                f"Checks:\n"
                f"  - Is Appium running on {APPIUM_URL}?  (start with: appium --base-path /)\n"
                f"  - Is the Android device connected?  (check: adb devices)\n"
                f"  - Is USB debugging enabled?  (Settings → Developer Options → USB Debugging)\n"
            )
        log(f"session ok ({self.driver.session_id})")

    async def _ensure_session(self):
        """If the driver is alive, no-op. Else (re)create.

        Throttled: the deep current_activity probe (a real round-trip) only runs
        when more than PROBE_INTERVAL has elapsed since the last successful
        command, so back-to-back agent steps don't each pay an extra round-trip.
        The dispatch path reconnects reactively if the session died between probes.
        """
        if self.driver is None:
            await self._connect()
            return
        try:
            await self._drive(lambda: self.driver.session_id)
        except Exception as e:
            await self._reconnect(e)
            return
        if time.monotonic() - self._last_ok < PROBE_INTERVAL:
            return
        try:
            await self._drive(lambda: self.driver.current_activity)
            self._last_ok = time.monotonic()
        except Exception as e:
            await self._reconnect(e)

    async def _reconnect(self, reason):
        log(f"stale session, reconnecting: {reason}")
        try:
            await self._drive(self.driver.quit)
        except Exception:
            pass
        self.driver = None
        await self._connect()

    async def start(self):
        self.stop = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        # Drive SIGTERM/SIGINT through the same graceful stop path as
        # meta:shutdown so an external kill / `--reload` quits the UIAutomator2
        # session instead of orphaning it. Guarded for Windows.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        await self._connect()

    async def handle(self, req):
        meta = req.get("meta")
        if meta == "ping":          return {"pong": True, "pid": os.getpid()}
        if meta == "shutdown":      self.stop.set(); return {"ok": True}
        if meta == "session":       return {"session_id": self.driver.session_id if self.driver else None}

        method = req.get("method")
        if not method:
            return {"error": "missing method"}

        try:
            await self._ensure_session()
        except Exception as e:
            return {"error": str(e)}

        params = req.get("params") or {}
        handler = _DISPATCH.get(method)
        if handler is None:
            return {"error": f"unknown method: {method}"}
        try:
            result = await handler(self, params)
            self._last_ok = time.monotonic()
            return {"result": result}
        except Exception as e:
            # Reactive recovery: the throttled probe may have skipped a dead
            # session. Reconnect once and retry before surfacing a session error.
            if _is_session_error(e):
                log(f"session error on {method}, reconnecting + retrying: {e}")
                try:
                    await self._reconnect(e)
                    result = await handler(self, params)
                    self._last_ok = time.monotonic()
                    return {"result": result}
                except Exception as e2:
                    return {"error": f"{method}: {e2}"}
            return {"error": f"{method}: {e}"}


# ---- method dispatch -------------------------------------------------------

async def _m_appium(d, params):
    script = params["script"]
    args = params.get("args", {})
    return await d._drive(lambda: d.driver.execute_script(script, args))


async def _m_screenshot(d, params):
    path = params.get("path") or str(ipc._TMP / "anh-shot.png")
    png = await d._drive(d.driver.get_screenshot_as_png)
    with open(path, "wb") as f:
        f.write(png)
    return {"path": path, "bytes": len(png)}


# ---- live screen stream (powers --headed viewer) --------------------------

async def _stream_loop(d):
    """Capture frames at d._stream_fps; JPEG-encode; store latest."""
    import io
    try:
        from PIL import Image
    except ImportError:
        log("stream: Pillow not installed — install via `pip install pillow`")
        return
    while True:
        period = 1.0 / max(0.1, d._stream_fps)
        try:
            png = await d._drive(d.driver.get_screenshot_as_png)
            img = Image.open(io.BytesIO(png))
            if d._stream_max_dim and max(img.size) > d._stream_max_dim:
                img.thumbnail((d._stream_max_dim, d._stream_max_dim))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=d._stream_quality)
            d._stream_frame = buf.getvalue()
            d._stream_frame_no += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"stream: capture failed: {e}")
        await asyncio.sleep(period)


async def _m_screen_stream_start(d, params):
    """Start (or reconfigure) capture. params: {fps, quality, max_dim}."""
    fps = float(params.get("fps", 6))
    quality = max(1, min(95, int(params.get("quality", 60))))
    max_dim = int(params.get("max_dim", 800))
    d._stream_fps = fps
    d._stream_quality = quality
    d._stream_max_dim = max_dim
    if d._stream_task is not None and not d._stream_task.done():
        return {"running": True, "updated": True, "fps": fps,
                "quality": quality, "max_dim": max_dim}
    d._stream_frame_no = 0
    d._stream_task = asyncio.create_task(_stream_loop(d))
    return {"running": True, "started": True, "fps": fps,
            "quality": quality, "max_dim": max_dim}


async def _m_screen_stream_frame(d, params):
    """Return latest captured frame as base64 JPEG. Single-consumer pull model."""
    import base64
    if d._stream_frame is None:
        return {"ready": False, "frame_no": 0}
    return {
        "ready": True,
        "frame_no": d._stream_frame_no,
        "jpeg_b64": base64.b64encode(d._stream_frame).decode("ascii"),
        "fps": d._stream_fps,
        "quality": d._stream_quality,
    }


async def _m_screen_stream_stop(d, params):
    """Cancel capture loop and clear buffered frame. Idempotent."""
    if d._stream_task is None:
        return {"running": False}
    d._stream_task.cancel()
    try:
        await d._stream_task
    except (asyncio.CancelledError, Exception):
        pass
    d._stream_task = None
    d._stream_frame = None
    return {"running": False, "stopped": True}


async def _m_page_source(d, params):
    return await d._drive(lambda: d.driver.page_source)


async def _m_window_size(d, params):
    sz = await d._drive(d.driver.get_window_size)
    return {"width": sz["width"], "height": sz["height"]}


async def _m_click_element(d, params):
    """Find element by UIAutomator selector or XPath and click."""
    By = _get_appium_by()
    selector = params.get("selector", "")
    by = params.get("by", "uiautomator")
    index = params.get("index", 0)
    def _do():
        if by == "uiautomator":
            elements = d.driver.find_elements(By.ANDROID_UIAUTOMATOR, selector)
        elif by == "xpath":
            elements = d.driver.find_elements(By.XPATH, selector)
        elif by == "accessibility_id":
            elements = d.driver.find_elements(By.ACCESSIBILITY_ID, selector)
        elif by == "id":
            elements = d.driver.find_elements(By.ID, selector)
        else:
            raise RuntimeError(f"unknown locator strategy: {by}")
        if not elements:
            raise RuntimeError(f"no element matched {by}={selector!r}")
        elements[index].click()
        return {"matched": len(elements)}
    return await d._drive(_do)


async def _m_send_keys(d, params):
    By = _get_appium_by()
    selector = params.get("selector", "")
    by = params.get("by", "uiautomator")
    keys = params["keys"]
    index = params.get("index", 0)
    def _do():
        if by == "uiautomator":
            elements = d.driver.find_elements(By.ANDROID_UIAUTOMATOR, selector)
        elif by == "xpath":
            elements = d.driver.find_elements(By.XPATH, selector)
        elif by == "accessibility_id":
            elements = d.driver.find_elements(By.ACCESSIBILITY_ID, selector)
        elif by == "id":
            elements = d.driver.find_elements(By.ID, selector)
        else:
            raise RuntimeError(f"unknown locator strategy: {by}")
        if not elements:
            raise RuntimeError(f"no element matched {by}={selector!r}")
        el = elements[index]
        el.send_keys(keys)
        return {"sent": keys, "matched": len(elements)}
    return await d._drive(_do)


async def _m_set_value(d, params):
    By = _get_appium_by()
    selector = params.get("selector", "")
    by = params.get("by", "uiautomator")
    value = params["value"]
    index = params.get("index", 0)
    def _do():
        if by == "uiautomator":
            elements = d.driver.find_elements(By.ANDROID_UIAUTOMATOR, selector)
        elif by == "xpath":
            elements = d.driver.find_elements(By.XPATH, selector)
        elif by == "accessibility_id":
            elements = d.driver.find_elements(By.ACCESSIBILITY_ID, selector)
        elif by == "id":
            elements = d.driver.find_elements(By.ID, selector)
        else:
            raise RuntimeError(f"unknown locator strategy: {by}")
        if not elements:
            raise RuntimeError(f"no element matched {by}={selector!r}")
        el = elements[index]
        el.clear()
        el.send_keys(value)
        return {"set": value, "matched": len(elements)}
    return await d._drive(_do)


async def _m_active_app(d, params):
    """Current foreground app — uses driver properties, not execute_script."""
    def _do():
        return {
            "package": d.driver.current_package,
            "activity": d.driver.current_activity,
        }
    return await d._drive(_do)


async def _m_snapshot(d, params):
    """Gather perceive() state — screenshot + page_source + foreground app +
    window size — in ONE round-trip + ONE driver hop instead of 4. Android's
    alert() reuses the parsed tree client-side, so no separate alert RPC. Each
    field is isolated so one failure doesn't abort the rest."""
    path = params.get("path") or str(ipc._TMP / "anh-shot.png")

    def _gather():
        res = {}
        try:
            png = d.driver.get_screenshot_as_png()
            with open(path, "wb") as f:
                f.write(png)
            res["screenshot"] = {"path": path, "bytes": len(png)}
        except Exception as e:
            res["screenshot_error"] = str(e)
        try:
            res["page_source"] = d.driver.page_source
        except Exception as e:
            res["page_source_error"] = str(e)
        try:
            res["active_app"] = {
                "package": d.driver.current_package,
                "activity": d.driver.current_activity,
            }
        except Exception as e:
            res["active_app_error"] = str(e)
        try:
            sz = d.driver.get_window_size()
            res["window_size"] = {"width": sz["width"], "height": sz["height"]}
        except Exception as e:
            res["window_size_error"] = str(e)
        return res

    return await d._drive(_gather)


async def _m_get_orientation(d, params):
    """Device orientation as 'PORTRAIT' or 'LANDSCAPE' (W3C orientation endpoint)."""
    return await d._drive(lambda: d.driver.orientation)


async def _m_set_orientation(d, params):
    """Rotate the device. params: {orientation: 'PORTRAIT'|'LANDSCAPE'}."""
    o = (params.get("orientation") or "PORTRAIT").upper()
    def _set():
        d.driver.orientation = o
        return d.driver.orientation
    return await d._drive(_set)


_DISPATCH = {
    "appium":         _m_appium,
    "snapshot":       _m_snapshot,
    "get_orientation": _m_get_orientation,
    "set_orientation": _m_set_orientation,
    "screenshot":     _m_screenshot,
    "page_source":    _m_page_source,
    "window_size":    _m_window_size,
    "click_element":  _m_click_element,
    "send_keys":      _m_send_keys,
    "set_value":      _m_set_value,
    "active_app":     _m_active_app,
    # Live screen mirror — powers `mobile-use --headed`.
    "screen_stream_start":  _m_screen_stream_start,
    "screen_stream_frame":  _m_screen_stream_frame,
    "screen_stream_stop":   _m_screen_stream_stop,
}


# ---- server loop -----------------------------------------------------------

async def serve(d):
    async def handler(reader, writer):
        # Keep-alive: serve every request on this connection until the peer
        # closes (EOF) or the link breaks. The client (helpers._cached_sock)
        # reuses one socket across calls, so a one-shot handler turned every
        # steady-state request into a reconnect + RETRY_DELAY sleep.
        try:
            while True:
                try:
                    line = await reader.readline()
                except Exception as e:
                    log(f"conn read: {e}")
                    break
                if not line:
                    break  # EOF — peer closed the connection
                try:
                    resp = await d.handle(json.loads(line))
                except Exception as e:
                    log(f"conn: {e}")
                    resp = {"error": str(e)}
                try:
                    writer.write((json.dumps(resp, default=str) + "\n").encode())
                    await writer.drain()
                except Exception:
                    break  # peer hung up mid-write — stop serving this conn
        finally:
            try:
                writer.close()
            except Exception:
                pass

    serve_task = asyncio.create_task(ipc.serve(NAME, handler))
    stop_task = asyncio.create_task(d.stop.wait())
    await asyncio.sleep(0.05)
    log(f"listening on {ipc.sock_addr(NAME)} (name={NAME})")
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if serve_task.done():
            await serve_task
    finally:
        for t in (serve_task, stop_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        ipc.cleanup_endpoint(NAME)
        # Quit the WebDriver session so we don't orphan the UIAutomator2 session
        # on the device — Appium otherwise only reaps it after newCommandTimeout.
        if d.driver is not None:
            try:
                await d._drive(d.driver.quit)
            except Exception as e:
                log(f"driver.quit on shutdown: {e}")
            d.driver = None
        d._exec.shutdown(wait=False)


async def main():
    d = Daemon()
    await d.start()
    await serve(d)


def already_running():
    return ipc.ping(NAME, timeout=1.0)


if __name__ == "__main__":
    if already_running():
        print(f"daemon already running on {SOCK}", file=sys.stderr)
        sys.exit(0)
    open(LOG, "w", encoding="utf-8").close()
    open(PID, "w", encoding="utf-8").write(str(os.getpid()))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"fatal: {e}")
        sys.exit(1)
    finally:
        try: os.unlink(PID)
        except FileNotFoundError: pass
