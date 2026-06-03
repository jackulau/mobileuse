"""Appium WebDriver session holder + IPC relay — part of mobile-use.

One daemon per IPH_NAME:
  - long-lived process owning one Appium Remote() session via WDA
  - exposes JSON-line RPC over AF_UNIX
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
    workspace = Path(os.environ.get("IPH_AGENT_WORKSPACE", repo_root / "agent-workspace")).expanduser()
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

NAME = os.environ.get("IPH_NAME", "default")
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))

APPIUM_URL = os.environ.get("IPH_APPIUM_URL", "http://127.0.0.1:4723")
UDID = os.environ.get("IPH_UDID")
PLATFORM_VERSION = os.environ.get("IPH_PLATFORM_VERSION")
DEVICE_NAME = os.environ.get("IPH_DEVICE_NAME", "iPhone")
XCODE_ORG_ID = os.environ.get("IPH_XCODE_ORG_ID")
XCODE_SIGNING_ID = os.environ.get("IPH_XCODE_SIGNING_ID", "Apple Development")
WDA_BUNDLE_ID = os.environ.get("IPH_WDA_BUNDLE_ID")
# Idle session timeout (seconds). Appium's default is 60s; bump it so quiet
# stretches between agent calls don't kill the session under us.
NEW_COMMAND_TIMEOUT = int(os.environ.get("IPH_NEW_COMMAND_TIMEOUT", "600"))
# Min seconds between deep liveness probes. Within this window of a successful
# command we skip the extra activeAppInfo round-trip on every request; the
# dispatch path reconnects reactively if the session actually died.
PROBE_INTERVAL = float(os.environ.get("IPH_PROBE_INTERVAL", "30"))


def _is_session_error(e):
    """True if an exception looks like a dead/invalid Appium session (so we
    should reconnect + retry rather than surface it to the caller)."""
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
    """Apply arbitrary Appium caps from a JSON env var (e.g. IPH_CAPS / ANH_CAPS).

    Lets you attach to a pre-running WDA (appium:webDriverAgentUrl), override
    automationName for a new-OS quirk, set snapshotMaxDepth / skipServerInstallation,
    etc., without editing source. Keys should carry their normal prefix
    (e.g. "appium:webDriverAgentUrl"). Malformed JSON is logged and ignored.
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


def _valid_http_url(url):
    """True if ``url`` is a well-formed http(s) URL with a host.

    Guards IPH_WDA_URL so a typo (e.g. a bare IP, an ssh:// URI, or "8100")
    can't silently produce a broken appium:webDriverAgentUrl that fails the
    session create with an opaque error.
    """
    try:
        from urllib.parse import urlparse
        p = urlparse(url or "")
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _ios_tunnel_note(platform_version):
    """Reminder string about the iOS 17+ RemoteXPC tunnel, or None when N/A.

    On iOS >= 17 Apple's RemoteXPC replaced lockdownd, so Appium can only reach
    WebDriverAgent through a tunnel — its bundled appium-ios-remotexpc, or an
    external ``sudo pymobiledevice3 remote tunneld`` — over USB *or* Wi-Fi.
    Without it, session create fails with RSDRequired / InvalidServiceError.
    When the target version is unknown we emit a softer "if 17+..." hint.
    """
    from mobile_use.versions import ios_needs_tunnel
    if platform_version:
        if ios_needs_tunnel(platform_version):
            return ("iOS >= 17 needs the RemoteXPC tunnel running (Appium's bundled "
                    "appium-ios-remotexpc, or `sudo pymobiledevice3 remote tunneld`) — "
                    "over USB or Wi-Fi; USB once for the first WDA launch.")
        return None
    return ("note: if this iPhone runs iOS 17+, start the RemoteXPC tunnel before "
            "connecting (set IPH_PLATFORM_VERSION to tune this hint).")


def _default_wda_derived_data():
    """Path to a prebuilt WebDriverAgent DerivedData dir, or None.

    `mobile-use ios build-wda` lands a build under
    ~/Library/Developer/Xcode/DerivedData/WebDriverAgent-*/Build/Products/. Pointing
    the session at it (with usePrebuiltWDA) skips the 20-60s WDA reinstall/relaunch
    that otherwise runs on every session create — the slowest op in the stack.
    """
    if sys.platform != "darwin":
        return None
    derived = Path("~/Library/Developer/Xcode/DerivedData").expanduser()
    if not derived.exists():
        return None
    apps = list(derived.glob("WebDriverAgent-*/Build/Products/*/WebDriverAgentRunner-Runner.app"))
    if not apps:
        return None
    latest = max(apps, key=lambda p: p.stat().st_mtime)
    return str(latest.parents[3])


def _build_options():
    """XCUITestOptions for the current device. Lazy import — appium is only needed in the daemon process."""
    from appium.options.ios import XCUITestOptions
    if not UDID:
        raise RuntimeError(
            "IPH_UDID not set. Plug in the iPhone and either:\n"
            "  - export IPH_UDID=<udid>  (find via `idevice_id -l` or `xcrun xctrace list devices`)\n"
            "  - put IPH_UDID=<udid> in <iphone-harness>/.env or <agent-workspace>/.env"
        )
    o = XCUITestOptions()
    o.platform_name = "iOS"
    o.device_name = DEVICE_NAME
    o.udid = UDID
    if PLATFORM_VERSION:
        o.platform_version = PLATFORM_VERSION
    if XCODE_ORG_ID:
        o.xcode_org_id = XCODE_ORG_ID
        o.xcode_signing_id = XCODE_SIGNING_ID
    if WDA_BUNDLE_ID:
        o.updated_wda_bundle_id = WDA_BUNDLE_ID
    o.set_capability("appium:allowProvisioningDeviceRegistration", True)
    o.set_capability("appium:newCommandTimeout", NEW_COMMAND_TIMEOUT)
    # Don't auto-launch any app. The agent decides what to foreground; otherwise
    # connecting attaches to whatever's already on screen (SpringBoard, etc.).
    o.set_capability("appium:autoLaunch", False)
    # Reuse a prebuilt WebDriverAgent when one exists — skips the slow per-session
    # WDA reinstall. Only engages if a DerivedData build (or IPH_WDA_DERIVED_DATA_PATH)
    # is present, so setups that haven't prebuilt WDA fall back to default behavior.
    dd = os.environ.get("IPH_WDA_DERIVED_DATA_PATH") or _default_wda_derived_data()
    if dd:
        o.set_capability("appium:derivedDataPath", dd)
        o.set_capability("appium:usePrebuiltWDA", True)
    # Wireless / remote WebDriverAgent. IPH_WDA_URL points Appium at a WDA that is
    # ALREADY listening — e.g. the iPhone's Wi-Fi IP on :8100 — instead of building
    # and launching WDA over USB. Appium treats appium:webDriverAgentUrl as "WDA is
    # up here, skip the build/launch phase". The device still needs WDA installed
    # + running (USB once), and on iOS 17+ a RemoteXPC tunnel must be up (see note).
    # Read at call time (like IPH_CAPS) so it's runtime-overridable + testable.
    wda_url = os.environ.get("IPH_WDA_URL")
    if wda_url:
        if _valid_http_url(wda_url):
            o.set_capability("appium:webDriverAgentUrl", wda_url)
            log(f"wireless: attaching to WebDriverAgent at {wda_url} (appium:webDriverAgentUrl)")
            note = _ios_tunnel_note(PLATFORM_VERSION)
            if note:
                log(note)
        else:
            log(f"IPH_WDA_URL ignored — not a valid http(s) URL: {wda_url!r}")
    # Arbitrary per-deployment cap overrides (device farms, new-OS quirks, etc.).
    # Merged LAST so IPH_CAPS can still override webDriverAgentUrl above if needed.
    _apply_extra_caps(o, "IPH_CAPS")
    return o


class Daemon:
    def __init__(self):
        self.driver = None
        self.stop = None  # asyncio.Event, set inside start()
        # Pool a SINGLE thread for blocking driver calls. The selenium/Appium
        # session is not thread-safe; the screen-stream task and IPC method
        # handlers both call _drive concurrently, so they must serialize onto
        # one worker. run_in_executor(None,...) uses a multi-worker default pool
        # and would let two driver calls land on different threads — a data race.
        self._loop = None
        self._exec = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="iph-driver"
        )
        # Screen-stream state — populated by screen_stream_start RPC.
        self._stream_task = None
        self._stream_frame = None       # latest JPEG bytes
        self._stream_frame_no = 0       # increments per capture; viewer detects drops
        self._stream_fps = 6.0
        self._stream_quality = 60
        self._stream_max_dim = 800      # largest side in px; thumbnailed
        self._last_ok = 0.0             # monotonic time of last successful command

    async def _drive(self, fn):
        """Run a blocking driver callable on the single driver worker (serialized)."""
        return await self._loop.run_in_executor(self._exec, fn)

    async def _connect(self):
        """(Re)create the WebDriver session. Retries once on transient Appium errors."""
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
                f"  - Is the iPhone plugged in and unlocked?  (check: idevice_id -l)\n"
                f"  - Is WebDriverAgent trusted on the device?  (Settings → General → VPN & Device Management)\n"
            )
        log(f"session ok ({self.driver.session_id})")

    async def _ensure_session(self):
        """If the driver is alive, no-op. Else (re)create.

        Throttled: the deep activeAppInfo probe (a real device round-trip) only
        runs when more than PROBE_INTERVAL has elapsed since the last successful
        command — so back-to-back agent steps don't each pay an extra round-trip.
        The cheap zero-round-trip session_id read still runs every time, and the
        dispatch path reconnects reactively if the session died between probes.
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
            await self._drive(lambda: self.driver.execute_script("mobile: activeAppInfo", {}))
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
        # meta:shutdown so an external kill / container-stop / `--reload` quits
        # the WDA session instead of orphaning it. add_signal_handler isn't
        # implemented on Windows — guard it.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        await self._connect()

    # ---- request handlers ----

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

        # Each method is a small dispatch. Helpers send {method: "...", params: {...}}.
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
            # session. If this looks like a session error, reconnect once and
            # retry before surfacing it.
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
    """Raw Appium escape hatch. params: {script, args}.
    `script` is e.g. 'mobile: tap', 'mobile: launchApp' — anything XCUITest supports."""
    script = params["script"]
    args = params.get("args", {})
    return await d._drive(lambda: d.driver.execute_script(script, args))


async def _m_screenshot(d, params):
    """Save a PNG screenshot to `path` (or a default tmp path). Returns path."""
    path = params.get("path") or str(ipc._TMP / "iph-shot.png")
    png = await d._drive(d.driver.get_screenshot_as_png)
    with open(path, "wb") as f:
        f.write(png)
    return {"path": path, "bytes": len(png)}


# ---- live screen stream (powers --headed viewer) --------------------------
# Producer (frame loop) lives in the daemon; consumer (HTTP MJPEG sidecar)
# pulls one frame at a time via screen_stream_frame. Single-consumer for v1.

async def _stream_loop(d):
    """Capture frames at d._stream_fps; JPEG-encode; store latest. Log + continue on errors."""
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
    """Start (or reconfigure) the capture loop. params: {fps, quality, max_dim}."""
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
    """Cancel the capture loop and clear buffered frame. Idempotent."""
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
    """Raw XML UI tree from WebDriverAgent. Helpers parse it client-side."""
    return await d._drive(lambda: d.driver.page_source)


async def _m_window_size(d, params):
    sz = await d._drive(d.driver.get_window_size)
    return {"width": sz["width"], "height": sz["height"]}


async def _m_click_element(d, params):
    """Find an element by NSPredicate and call WebElement.click() — the real
    WebDriver click, which dispatches a proper UI gesture (handles iOS apps
    that ignore synthetic `mobile: tap` events).

    params: {predicate: str, index: int = 0}
    """
    By = _get_appium_by()
    pred = params["predicate"]
    index = params.get("index", 0)
    def _do():
        elements = d.driver.find_elements(By.IOS_PREDICATE, pred)
        if not elements:
            raise RuntimeError(f"no element matched predicate: {pred!r}")
        elements[index].click()
        return {"matched": len(elements)}
    return await d._drive(_do)


async def _m_send_keys(d, params):
    """Find an element by `predicate` (iOS NSPredicate) and send `keys` to it.

    Used for picker wheels (which accept their target value as a string),
    text fields where setting `.value` is more reliable than tap+type, and any
    other element XCUITest can locate but our coordinate-based helpers can't drive.

    params: {predicate: str, keys: str, index: int = 0}
    """
    By = _get_appium_by()
    pred = params["predicate"]
    keys = params["keys"]
    index = params.get("index", 0)
    def _do():
        elements = d.driver.find_elements(By.IOS_PREDICATE, pred)
        if not elements:
            raise RuntimeError(f"no element matched predicate: {pred!r}")
        el = elements[index]
        el.send_keys(keys)
        return {"sent": keys, "matched": len(elements)}
    return await d._drive(_do)


async def _m_pick_wheel(d, params):
    """Drive a picker wheel iteratively until its value matches `target` substring.

    XCUITest's `mobile: selectPickerWheelValue` nudges the wheel one row at a
    time in the requested direction until the value contains `target` (or the
    safety limit is hit). Reliable for date/time pickers.

    params: {predicate: str, target: str, index: int = 0,
             direction: 'next'|'previous' = 'next',
             offset: float = 0.15, max_attempts: int = 30}
    """
    By = _get_appium_by()
    pred = params["predicate"]
    target = str(params["target"])
    index = params.get("index", 0)
    direction = params.get("direction", "next")
    offset = float(params.get("offset", 0.15))
    max_attempts = int(params.get("max_attempts", 30))
    def _do():
        elements = d.driver.find_elements(By.IOS_PREDICATE, pred)
        if not elements:
            raise RuntimeError(f"no picker matched: {pred!r}")
        el = elements[index]
        for i in range(max_attempts):
            cur = el.get_attribute("value") or ""
            if target in cur:
                return {"value": cur, "attempts": i}
            d.driver.execute_script("mobile: selectPickerWheelValue", {
                "elementId": el.id, "order": direction, "offset": offset,
            })
        cur = el.get_attribute("value") or ""
        return {"value": cur, "attempts": max_attempts, "matched": target in cur}
    return await d._drive(_do)


async def _m_set_value(d, params):
    """Atomic value replace via XCUITest. Falls back across Selenium versions.

    params: {predicate: str, value: str, index: int = 0}
    """
    By = _get_appium_by()
    pred = params["predicate"]
    value = params["value"]
    index = params.get("index", 0)
    def _do():
        elements = d.driver.find_elements(By.IOS_PREDICATE, pred)
        if not elements:
            raise RuntimeError(f"no element matched predicate: {pred!r}")
        el = elements[index]
        # Selenium 4.x removed Element.set_value(); use the `mobile: setValue`
        # execute-script with the element's UUID instead.
        try:
            d.driver.execute_script("mobile: setValue", {"elementId": el.id, "text": value})
        except Exception:
            # Last fallback: clear + send_keys.
            try:
                el.clear()
            except Exception:
                pass
            el.send_keys(value)
        return {"set": value, "matched": len(elements)}
    return await d._drive(_do)


async def _m_snapshot(d, params):
    """Gather the whole perceive() state — screenshot + page_source + foreground
    app + window size + alert — in ONE round-trip + ONE driver hop, instead of 5
    separate RPCs. Each field is isolated so a single failure records an error
    key but does not abort the rest (mirrors perceive()'s per-field semantics)."""
    path = params.get("path") or str(ipc._TMP / "iph-shot.png")

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
            res["active_app"] = d.driver.execute_script("mobile: activeAppInfo", {})
        except Exception as e:
            res["active_app_error"] = str(e)
        try:
            sz = d.driver.get_window_size()
            res["window_size"] = {"width": sz["width"], "height": sz["height"]}
        except Exception as e:
            res["window_size_error"] = str(e)
        try:
            res["alert"] = d.driver.execute_script("mobile: alert", {"action": "getButtons"})
        except Exception:
            res["alert"] = None
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
    # Generic XCUITest escape hatch — covers everything `mobile: ...` exposes.
    "appium":         _m_appium,
    "snapshot":       _m_snapshot,
    "get_orientation": _m_get_orientation,
    "set_orientation": _m_set_orientation,
    # Perception (need raw bytes / parsed XML, not just JSON).
    "screenshot":     _m_screenshot,
    "page_source":    _m_page_source,
    "window_size":    _m_window_size,
    # Element-level operations that need a real WebElement (can't be done via execute-script).
    "click_element":  _m_click_element,
    "send_keys":      _m_send_keys,
    "set_value":      _m_set_value,
    "pick_wheel":     _m_pick_wheel,
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
        # Quit the WebDriver session so we don't orphan the XCUITest/WDA session
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
