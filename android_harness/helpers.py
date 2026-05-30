"""Android device control via Appium/UIAutomator2.

Core helpers live here. Agent-editable helpers live in
ANH_AGENT_WORKSPACE/agent_helpers.py.

Same design as iphone_harness/helpers.py:
  - thin marshalling functions; all real work happens in the daemon
  - coordinate-first interaction (`tap_at_xy`); UI-tree-aware helpers (`find`) for stable labels
  - one public escape hatch: `appium('mobile: ...', **params)` — anything UIAutomator2 supports
"""
import importlib.util
import os
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import _ipc as ipc

CORE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CORE_DIR.parent
AGENT_WORKSPACE = Path(os.environ.get("ANH_AGENT_WORKSPACE", REPO_ROOT / "agent-workspace")).expanduser()


def _load_env():
    for p in (REPO_ROOT / ".env", AGENT_WORKSPACE / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NAME = os.environ.get("ANH_NAME", "default")


MAX_RETRIES = int(os.environ.get("ANH_MAX_RETRIES", "2"))
RETRY_DELAY = float(os.environ.get("ANH_RETRY_DELAY", "0.3"))

_cached_sock = None
_cached_token = None
# Serialize the shared module-global socket across threads — the `mobile-use
# --headed` ViewerServer (ThreadingMixIn) calls screen_stream_frame() (→ _send)
# concurrently. Reentrant so the retry path re-entering _send doesn't deadlock.
_conn_lock = threading.RLock()


def _get_conn(timeout=5.0):
    """Reuse existing socket if still alive; otherwise open a new one."""
    global _cached_sock, _cached_token
    if _cached_sock is not None:
        try:
            _cached_sock.settimeout(timeout)
            return _cached_sock, _cached_token
        except OSError:
            _cached_sock = None
    s, t = ipc.connect(NAME, timeout=timeout)
    _cached_sock, _cached_token = s, t
    return s, t


def _drop_conn():
    global _cached_sock, _cached_token
    if _cached_sock is not None:
        try:
            _cached_sock.close()
        except OSError:
            pass
        _cached_sock = _cached_token = None


def _send(req, timeout=120.0, _retries=None):
    """Send a JSON-line RPC to the daemon. On unreachable/stale errors, wraps
    with a 3-line remediation pointing at `--doctor` and `--reload`."""
    if _retries is None:
        _retries = MAX_RETRIES
    with _conn_lock:
        try:
            c, token = _get_conn(timeout=min(timeout, 5.0))
            c.settimeout(timeout)
            r = ipc.request(c, token, req)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            _drop_conn()
            if _retries > 0:
                time.sleep(RETRY_DELAY * (2 ** (MAX_RETRIES - _retries)))
                from .admin import ensure_daemon
                ensure_daemon()
                return _send(req, timeout=timeout, _retries=_retries - 1)
            raise RuntimeError(
                f"android-harness daemon unreachable after {MAX_RETRIES} retries.\n"
                f"  Underlying error: {e}\n"
                f"  Likely causes: Appium not running, ANH_UDID unset/wrong, USB debugging off.\n"
                f"  Run `android-harness --doctor` to diagnose, then `android-harness --reload`."
            )
        return _check_send_result(r, req, timeout, _retries)


def _check_send_result(r, req, timeout, _retries):
    if isinstance(r, dict) and "error" in r:
        err = r["error"]
        if "stale" in err.lower() or "session" in err.lower():
            _drop_conn()
            if _retries > 0:
                time.sleep(RETRY_DELAY * (2 ** (MAX_RETRIES - _retries)))
                return _send(req, timeout=timeout, _retries=_retries - 1)
            raise RuntimeError(
                f"android-harness session went stale (Appium/UIAutomator2 dropped).\n"
                f"  Server said: {err}\n"
                f"  Fix: `android-harness --reload` (restarts daemon + Appium session). "
                f"If repeating, run `android-harness --doctor`."
            )
        raise RuntimeError(err)
    return r


# ---- escape hatch ----------------------------------------------------------

def appium(script, **args):
    """Raw Appium execute-script. Anything UIAutomator2 supports.

    Examples:
        appium('mobile: tap', x=200, y=400)
        appium('mobile: startActivity', package='com.example.app', activity='.MainActivity')
        appium('mobile: scroll', direction='down')
        appium('mobile: getNotifications')
    """
    return _send({"method": "appium", "params": {"script": script, "args": args}})["result"]


# ---- device state + recovery -----------------------------------------------

class DeviceDisconnectError(RuntimeError):
    """Device became unreachable mid-call (USB disconnect, Appium timeout, ADB crash)."""


def is_locked():
    """True if screen is locked (lockscreen visible or display off)."""
    try:
        return bool(appium("mobile: isLocked"))
    except Exception:
        try:
            info = appium("mobile: deviceInfo")
            return bool(info.get("locked", False)) if isinstance(info, dict) else False
        except Exception:
            return False


def wake_device():
    """Wake screen + dismiss lock screen. Returns True iff device ends up unlocked.

    On Android, uses `mobile: unlock` if available, falls back to pressing
    power + dismissing the lock screen via swipe up.
    """
    if not is_locked():
        return True  # already awake
    unlocked = False
    try:
        appium("mobile: unlock")
        unlocked = True
    except Exception:
        try:
            appium("mobile: pressKey", keycode=26)  # POWER
            time.sleep(0.5)
            appium("mobile: pressKey", keycode=82)  # MENU (some devices unlock)
            unlocked = True
        except Exception:
            unlocked = False
    if not unlocked:
        return False
    try:
        return not is_locked()
    except Exception:
        return False


def retry_on_disconnect(max_attempts=3, backoff=0.5):
    """Decorator: retry on device-disconnect errors with backoff + wake.

    Catches RuntimeError messages matching common disconnect signals
    (USB pull, ADB session drop, UIAutomator2 timeout) and restarts the
    daemon + wakes the device before each retry.

    Usage:
        @retry_on_disconnect(max_attempts=3)
        def open_app(package):
            appium('mobile: activateApp', appId=package)
    """
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts!r}")
    if not isinstance(backoff, (int, float)) or backoff < 0:
        raise ValueError(f"backoff must be >= 0, got {backoff!r}")
    DISCONNECT_PATTERNS = (
        "unreachable", "disconnect", "session", "stale",
        "connection", "timed out", "adb", "uiautomator",
    )

    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except RuntimeError as e:
                    msg = str(e).lower()
                    if not any(p.lower() in msg for p in DISCONNECT_PATTERNS):
                        raise
                    last_err = e
                    if attempt < max_attempts - 1:
                        time.sleep(backoff * (2 ** attempt))
                        try:
                            from .admin import ensure_daemon, restart_daemon
                            restart_daemon()
                            ensure_daemon()
                            wake_device()
                        except Exception:
                            pass
            raise DeviceDisconnectError(
                f"{fn.__name__} failed after {max_attempts} attempts. Last error: {last_err}\n"
                f"Run `android-harness --doctor` to diagnose."
            ) from last_err
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator


# ---- perception ------------------------------------------------------------

def screenshot(path=None):
    """Save a PNG screenshot. Returns the path on the host machine."""
    r = _send({"method": "screenshot", "params": {"path": path} if path else {}})["result"]
    return r["path"]


# ---- live screen stream (consumed by viewer/server.py) --------------------

def screen_stream_start(fps=6, quality=60, max_dim=800):
    """Start the daemon's screen-capture loop. Returns daemon's reply dict."""
    _drop_conn()
    r = _send({
        "method": "screen_stream_start",
        "params": {"fps": fps, "quality": quality, "max_dim": max_dim},
    })
    return r.get("result", {"running": False})


def screen_stream_frame():
    """Pull the latest JPEG frame from the daemon. Drops cached socket first
    since the daemon closes the conn per reply (silent empty otherwise)."""
    _drop_conn()
    r = _send({"method": "screen_stream_frame", "params": {}}, timeout=10.0)
    return r.get("result", {"ready": False, "frame_no": 0})


def screen_stream_stop():
    """Cancel the daemon's capture loop. Idempotent."""
    _drop_conn()
    r = _send({"method": "screen_stream_stop", "params": {}})
    return r.get("result", {"running": False})


def window_size():
    """Logical screen size: {'width': W, 'height': H}.
    These are the units tap_at_xy(x, y) expects.
    """
    return _send({"method": "window_size", "params": {}})["result"]


def page_source():
    """Raw XML UI hierarchy from UIAutomator2."""
    return _send({"method": "page_source", "params": {}})["result"]


_tree_cache = None
_tree_cache_time = 0.0
_TREE_TTL = float(os.environ.get("ANH_TREE_TTL", "1.0"))


def ui_tree(visible_only=False, compact=False):
    """Flat list of UI elements from the current screen.

    Each element is a dict:
        {type, resource_id, text, content_desc, x, y, w, h, cx, cy, clickable, enabled, focused, visible}

    `cx, cy` are the geometric center — pass directly to tap_at_xy().
    Set compact=True for minimal fields (type, text, cx, cy) — saves tokens.

    Results are cached for ~1s to avoid duplicate fetches when calling find()
    multiple times in sequence.
    """
    global _tree_cache, _tree_cache_time
    now = time.time()
    if _tree_cache is not None and (now - _tree_cache_time) < _TREE_TTL:
        tree = _tree_cache
    else:
        xml = page_source()
        root = ET.fromstring(xml)
        tree = []
        for el in root.iter():
            a = el.attrib
            bounds = a.get("bounds", "")
            if not bounds:
                continue
            try:
                parts = bounds.replace("][", ",").strip("[]").split(",")
                x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            except (ValueError, IndexError):
                continue
            w = x2 - x1
            h = y2 - y1
            if w <= 0 or h <= 0:
                continue
            tree.append({
                "type": a.get("class", el.tag),
                "resource_id": a.get("resource-id", ""),
                "text": a.get("text", ""),
                "content_desc": a.get("content-desc", ""),
                "package": a.get("package", ""),
                "x": x1, "y": y1, "w": w, "h": h,
                "cx": x1 + w // 2, "cy": y1 + h // 2,
                "clickable": a.get("clickable") == "true",
                "enabled": a.get("enabled") == "true",
                "focused": a.get("focused") == "true",
                "visible": a.get("displayed", "true") == "true",
            })
        _tree_cache = tree
        _tree_cache_time = now

    out = tree
    if visible_only:
        out = [el for el in out if el["visible"]]
    if compact:
        out = [{"type": el["type"], "text": el["text"] or el["content_desc"],
                "cx": el["cx"], "cy": el["cy"]} for el in out]
    return out


def invalidate_tree_cache():
    """Force next ui_tree() call to fetch fresh data."""
    global _tree_cache, _tree_cache_time
    _tree_cache = None
    _tree_cache_time = 0.0


def find(text=None, resource_id=None, type=None, content_desc=None, visible_only=True, _tree=None):
    """Return the first UI element matching the given criteria, or None.
    Pass _tree to reuse a pre-fetched tree (avoids duplicate IPC calls).

        find(text='Cancel')
        find(type='android.widget.Button', resource_id='com.example:id/send')
        find(content_desc='More options')
    """
    for el in (_tree if _tree is not None else ui_tree(visible_only=visible_only)):
        if visible_only and _tree is not None and not el.get("visible", True):
            continue
        if text         is not None and el["text"]         != text:         continue
        if resource_id  is not None and el["resource_id"]  != resource_id:  continue
        if type         is not None and el["type"]         != type:         continue
        if content_desc is not None and el["content_desc"] != content_desc: continue
        return el
    return None


def find_all(text=None, resource_id=None, type=None, content_desc=None, visible_only=True, _tree=None):
    """Return all matching UI elements. Pass _tree to reuse a pre-fetched tree."""
    out = []
    for el in (_tree if _tree is not None else ui_tree(visible_only=visible_only)):
        if visible_only and _tree is not None and not el.get("visible", True):
            continue
        if text         is not None and el["text"]         != text:         continue
        if resource_id  is not None and el["resource_id"]  != resource_id:  continue
        if type         is not None and el["type"]         != type:         continue
        if content_desc is not None and el["content_desc"] != content_desc: continue
        out.append(el)
    return out


def find_fuzzy(query, type=None, visible_only=True, _tree=None):
    """Fuzzy-match UI elements by substring across text, content_desc, and resource_id.

    Case-insensitive. Returns all matches sorted by best match quality:
      1. Exact match on any field
      2. Field starts with query
      3. Query is a substring of field

        find_fuzzy("send")       # matches "Send", "Send Message", "send_button"
        find_fuzzy("settings", type="android.widget.TextView")
    """
    q = query.lower()
    tree = _tree if _tree is not None else ui_tree(visible_only=visible_only)
    exact, prefix, substring = [], [], []
    for el in tree:
        if visible_only and _tree is not None and not el.get("visible", True):
            continue
        if type is not None and el["type"] != type:
            continue
        fields = [
            (el.get("text") or "").lower(),
            (el.get("content_desc") or "").lower(),
            (el.get("resource_id") or "").lower(),
        ]
        if q in fields:
            exact.append(el)
        elif any(f.startswith(q) for f in fields if f):
            prefix.append(el)
        elif any(q in f for f in fields if f):
            substring.append(el)
    return exact + prefix + substring


def active_app():
    """Current foreground app info: {package, activity}."""
    return _send({"method": "active_app", "params": {}})["result"]


# ---- app lifecycle ---------------------------------------------------------

def launch_app(package):
    """Launch (or foreground) an app by package id.

        launch_app("com.android.chrome")
    """
    return appium("mobile: activateApp", appId=package)


def activate_app(package):
    """Bring an already-running app to the foreground without restarting it."""
    return appium("mobile: activateApp", appId=package)


def terminate_app(package):
    """Force-stop an app by package id. Returns True if it was running."""
    return appium("mobile: terminateApp", appId=package)


def app_state(package):
    """App run state as an int: 0 not installed, 1 not running, 2 suspended,
    3 background, 4 foreground."""
    return appium("mobile: queryAppState", appId=package)


def is_app_installed(package):
    """True if the app is installed on the device."""
    return bool(appium("mobile: isAppInstalled", appId=package))


def domain_skills(package):
    """List skill .md filenames for this package, when ANH_DOMAIN_SKILLS=1."""
    if os.environ.get("ANH_DOMAIN_SKILLS") != "1":
        return []
    d = AGENT_WORKSPACE / "domain-skills" / package
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.rglob("*.md"))[:10]


# ---- input -----------------------------------------------------------------

def tap_at_xy(x, y):
    """Single tap at (x, y) coordinates."""
    invalidate_tree_cache()
    appium("mobile: clickGesture", x=x, y=y)


def tap(el):
    """Tap an element returned by find()/ui_tree(). Uses its center."""
    if el is None:
        raise RuntimeError("tap(): element is None")
    tap_at_xy(el["cx"], el["cy"])


NAV_BAR_PX = 48


def tap_safe(el, refind=None, max_scrolls=4):
    """Tap an element, scrolling it up first if it's in the navigation bar zone.

    Android reserves the bottom ~48dp for the navigation bar (Back/Home/Recents).
    Taps there may trigger system navigation instead of reaching the app.
    """
    if el is None:
        raise RuntimeError("tap_safe(): element is None")
    sz = window_size()
    danger_y = sz["height"] - NAV_BAR_PX
    cur = el
    for _ in range(max_scrolls):
        if cur["y"] + cur["h"] <= danger_y:
            tap_at_xy(cur["cx"], cur["cy"])
            return cur
        if refind is None:
            break
        midx = sz["width"] // 2
        swipe(midx, sz["height"] - 100, midx, sz["height"] - 250, duration=0.3)
        wait(0.6)
        cur = refind()
        if cur is None:
            raise RuntimeError("tap_safe: refind() returned None after scrolling")
    safe_y = min(cur["cy"], cur["y"] + 20)
    tap_at_xy(cur["cx"], safe_y)
    return cur


def double_tap(x, y):
    appium("mobile: doubleClickGesture", x=x, y=y)


def long_press(x, y, duration=1.0):
    """Touch-and-hold at (x, y) for `duration` seconds."""
    appium("mobile: longClickGesture", x=x, y=y, duration=int(duration * 1000))


def swipe(x1, y1, x2, y2, duration=0.4):
    """Swipe from (x1, y1) to (x2, y2). Uses UIAutomator2's drag gesture."""
    appium("mobile: dragGesture",
           startX=x1, startY=y1, endX=x2, endY=y2,
           speed=int(abs(((x2-x1)**2 + (y2-y1)**2)**0.5) / max(duration, 0.01)))


def scroll(direction="down"):
    """Scroll the current view. direction in {up, down, left, right}."""
    sz = window_size()
    appium("mobile: scrollGesture",
           left=0, top=0, width=sz["width"], height=sz["height"],
           direction=direction, percent=0.75)


def scroll_by(dy=-400, x=None, y=None):
    """Scroll by approximate pixel offset using a fling gesture.

    dy < 0 → scroll DOWN (reveal content below)
    dy > 0 → scroll UP (reveal content above)
    """
    sz = window_size()
    if x is None: x = sz["width"] // 2
    if y is None: y = sz["height"] // 2
    target_y = max(50, min(sz["height"] - 50, y + dy))
    appium("mobile: dragGesture",
           startX=x, startY=y, endX=x, endY=target_y,
           speed=2500)
    wait(0.8)
    return True


def type_text(text):
    """Type into the currently-focused text input.

    Uses the focused element's send_keys — works with UIAutomator2's built-in
    keyboard injection. For long text or Unicode, prefer set_value().
    """
    focused = 'new UiSelector().focused(true)'
    send_keys(focused, text)


def click(selector, by="uiautomator", index=0):
    """Find and click an element using a locator strategy.

        click('new UiSelector().text("Send")')
        click('//android.widget.Button[@text="Send"]', by='xpath')
        click('Send', by='accessibility_id')
        click('com.example:id/send_button', by='id')
    """
    return _send({"method": "click_element", "params": {
        "selector": selector, "by": by, "index": index,
    }})["result"]


def send_keys(selector, keys, by="uiautomator", index=0):
    """Find an element and send keys to it.

        send_keys('new UiSelector().resourceId("com.example:id/input")', 'hello')
    """
    return _send({"method": "send_keys", "params": {
        "selector": selector, "keys": keys, "by": by, "index": index,
    }})["result"]


def set_value(selector, value, by="uiautomator", index=0):
    """Clear field and set new value atomically."""
    return _send({"method": "set_value", "params": {
        "selector": selector, "value": value, "by": by, "index": index,
    }})["result"]


def paste_text(text, selector=None, by="uiautomator", index=0):
    """Inject text into a focused (or specified) text field."""
    if selector is None:
        selector = 'new UiSelector().focused(true)'
    set_value(selector, text, by=by, index=index)


# ---- device-level ----------------------------------------------------------

def unlock():
    """Wake the screen and dismiss the lock screen.

    Uses UIAutomator2's built-in unlock + a swipe-up fallback.
    On PIN/pattern devices, surfaces to the user.
    """
    appium("mobile: unlock")
    wait(0.5)
    if appium("mobile: isLocked"):
        sz = window_size()
        swipe(sz["width"] // 2, sz["height"] - 10, sz["width"] // 2, sz["height"] // 3, duration=0.4)
        wait(1.0)


def press_back():
    """Press the Android Back button."""
    appium("mobile: pressKey", keycode=4)


def press_home():
    """Press the Android Home button."""
    appium("mobile: pressKey", keycode=3)


def press_recents():
    """Press the Android Recents/Overview button."""
    appium("mobile: pressKey", keycode=187)


def key_event(keycode):
    """Press an arbitrary Android keycode (android.view.KeyEvent.KEYCODE_*).

    Examples: 66 = Enter, 84 = Search, 61 = Tab, 67 = Delete, 111 = Escape.
    """
    appium("mobile: pressKey", keycode=int(keycode))


def press_enter():
    """Press Enter (keycode 66) — submits search bars / login forms with no
    visible submit button. The single most common 'type then submit' flow."""
    appium("mobile: pressKey", keycode=66)


def press_search():
    """Press the Search action key (keycode 84)."""
    appium("mobile: pressKey", keycode=84)


def hide_keyboard():
    """Dismiss the on-screen keyboard so it stops occluding Send/Next controls."""
    try:
        appium("mobile: hideKeyboard")
    except Exception:
        # Back closes the IME on Android when the keyboard is up.
        press_back()


def open_notifications():
    """Pull down the notification shade."""
    appium("mobile: openNotifications")


def close_notifications():
    """Dismiss the notification shade by pressing Back."""
    press_back()


def grant_permission(package, permission):
    """Grant a runtime permission to a package via ADB.

        grant_permission('com.example.app', 'android.permission.CAMERA')
    """
    appium("mobile: shell", command="pm", args=["grant", package, permission])


# ---- waits -----------------------------------------------------------------

def wait(seconds=1.0):
    time.sleep(seconds)


def wait_for(predicate, timeout=10.0, poll=0.3):
    """Poll predicate() until truthy or timeout. Returns the value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = predicate()
        if v:
            return v
        time.sleep(poll)
    return None


def wait_for_element(text=None, resource_id=None, type=None, content_desc=None, timeout=10.0):
    """Wait until find(...) returns non-None."""
    return wait_for(
        lambda: find(text=text, resource_id=resource_id, type=type, content_desc=content_desc),
        timeout=timeout,
    )


def wait_for_app(package, timeout=10.0):
    """Wait until the foreground app's package matches."""
    return wait_for(lambda: active_app().get("package") == package, timeout=timeout)


# ---- alerts ----------------------------------------------------------------

def alert():
    """Read the currently-shown dialog/alert, or None.
    For Android, alerts are regular UI tree nodes — this checks for common patterns.
    """
    for el in ui_tree(visible_only=True):
        if el["type"] in ("android.app.AlertDialog", "androidx.appcompat.app.AlertDialog"):
            return el
        if "android.widget.Button" in el["type"] and el["text"] in ("OK", "Cancel", "Allow", "Deny"):
            return el
    return None


def _match_dialog_button(labels):
    """Find a dialog button by visible label across modern widget classes.

    Matches any clickable element whose text or content-desc is in `labels` —
    NOT just the legacy android.widget.Button. Modern apps render dialog buttons
    as MaterialButton / AppCompatButton / Jetpack Compose nodes (no Button
    class), and the Android 13+ permission sheet is a Material dialog; requiring
    an exact android.widget.Button class silently missed all of those. Prefers
    clickable+enabled nodes, then falls back to any matching label so a Compose
    node whose clickable flag sits on an ancestor is still reachable.
    """
    candidates = ui_tree(visible_only=True)
    for require_clickable in (True, False):
        for el in candidates:
            if el.get("enabled") is False:
                continue
            if require_clickable and not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("content_desc") or "").strip()
            if text in labels or desc in labels:
                return el
    return None


_ACCEPT_LABELS = {"OK", "Ok", "ALLOW", "Allow", "YES", "Yes", "ACCEPT", "Accept",
                  "GOT IT", "Got it", "CONTINUE", "Continue",
                  "While using the app", "Only this time",
                  "Allow only while using the app", "ALLOW ONLY WHILE USING THE APP"}
_DISMISS_LABELS = {"CANCEL", "Cancel", "DENY", "Deny", "NO", "No", "NOT NOW", "Not Now",
                   "LATER", "Later", "NO THANKS", "No Thanks", "No thanks",
                   "DISMISS", "Dismiss", "SKIP", "Skip", "CLOSE", "Close",
                   "Don't allow", "DON'T ALLOW"}


def alert_accept():
    """Tap the positive button (OK, Allow, Yes) on a visible dialog."""
    btn = _match_dialog_button(_ACCEPT_LABELS)
    if btn:
        tap(btn)
        return
    raise RuntimeError("No accept button found in current dialog")


def alert_dismiss():
    """Tap the negative button (Cancel, Deny, No) on a visible dialog."""
    btn = _match_dialog_button(_DISMISS_LABELS)
    if btn:
        tap(btn)
        return
    press_back()


def auto_dismiss_dialog():
    """Dismiss any unexpected dialog (permissions, updates, system alerts).

    Call this before critical actions to clear the path. Returns True if
    a dialog was dismissed, False if screen was clean. Prefers a dismiss/deny
    button over accept so an unexpected permission prompt is not granted.
    """
    btn = _match_dialog_button(_DISMISS_LABELS)
    if btn:
        tap(btn)
        wait(0.5)
        return True
    btn = _match_dialog_button(_ACCEPT_LABELS)
    if btn:
        tap(btn)
        wait(0.5)
        return True
    return False


# ---- OCR (uses macOS Vision, same as iOS — runs on host) ------------------

def ocr(image_path=None, languages=("en-US",)):
    """Apple Vision OCR on a PNG (macOS host only).

    If image_path is None, takes a fresh screenshot first.
    Returns (lines, (width, height)).
    """
    if image_path is None:
        image_path = screenshot()
    from mobile_use._platform import OCRNotAvailableError, is_macos
    if not is_macos():
        raise OCRNotAvailableError(
            "ocr() uses the macOS Vision framework — not bundled on this host. "
            "Linux: install Tesseract (`sudo apt install tesseract-ocr` or "
            "equivalent) and wrap it yourself, or run mobile_use from a macOS "
            "host. See SETUP.md#ocr-on-linux for details."
        )
    try:
        import Vision
        from Foundation import NSURL
    except ImportError as e:
        raise OCRNotAvailableError(
            "ocr() needs PyObjC. Install: pip install pyobjc-framework-Vision"
        ) from e

    url = NSURL.fileURLWithPath_(image_path)
    src = Vision.CIImage.imageWithContentsOfURL_(url)
    if src is None:
        raise RuntimeError(f"could not load image: {image_path}")

    extent = src.extent()
    w_px, h_px = float(extent.size.width), float(extent.size.height)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    if languages:
        request.setRecognitionLanguages_(list(languages))

    handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(src, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision request failed: {err}")

    out = []
    for obs in request.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        bb = obs.boundingBox()
        x = bb.origin.x * w_px
        wi = bb.size.width * w_px
        hi = bb.size.height * h_px
        y = (1.0 - bb.origin.y - bb.size.height) * h_px
        out.append({
            "text": str(cand.string()),
            "confidence": round(float(cand.confidence()), 3),
            "box": [round(x, 1), round(y, 1), round(wi, 1), round(hi, 1)],
        })
    return out, (int(w_px), int(h_px))


def find_text(query, languages=("en-US",), case_sensitive=False):
    """Run OCR and return the first line whose text matches query."""
    lines, (w_px, h_px) = ocr(languages=languages)
    sz = window_size()
    sx = sz["width"] / w_px
    sy = sz["height"] / h_px
    if callable(query):
        match = query
    elif case_sensitive:
        match = lambda t: query in t
    else:
        q = query.lower()
        match = lambda t: q in t.lower()
    for line in lines:
        if match(line["text"]):
            x, y, w, h = line["box"]
            line = dict(line)
            line["cx_pt"] = round((x + w / 2) * sx, 1)
            line["cy_pt"] = round((y + h / 2) * sy, 1)
            return line
    return None


def annotated_screenshot(path=None, run_ocr=True):
    """Screenshot with red boxes + numeric labels around elements or OCR lines."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RuntimeError("annotated_screenshot() needs Pillow.") from e

    base = screenshot(path)
    if run_ocr:
        items, (w_px, h_px) = ocr(base)
        boxes = [(it["box"], it.get("text", "")) for it in items]
        result = items
    else:
        sz = window_size()
        img_sz = Image.open(base).size
        sx = img_sz[0] / sz["width"]
        sy = img_sz[1] / sz["height"]
        items = ui_tree(visible_only=True)
        boxes = [
            ([el["x"] * sx, el["y"] * sy, el["w"] * sx, el["h"] * sy],
             el.get("text") or el.get("content_desc") or el["type"])
            for el in items
        ]
        result = items

    img = Image.open(base).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for i, (box, _label) in enumerate(boxes):
        x, y, w, h = box
        x0, y0, x1, y1 = x, y, x + w, y + h
        draw.rectangle([x0, y0, x1, y1], outline=(255, 60, 60, 255), width=2)
        s = str(i)
        tb = draw.textbbox((0, 0), s, font=font)
        lw, lh = tb[2] - tb[0], tb[3] - tb[1]
        pad = 3
        bg = [x0, max(0, y0 - lh - pad * 2), x0 + lw + pad * 2, y0]
        draw.rectangle(bg, fill=(255, 60, 60, 220))
        draw.text((x0 + pad, bg[1] + pad), s, fill=(255, 255, 255, 255), font=font)

    annotated = (path or base).replace(".png", ".annotated.png") if (path or base).endswith(".png") else (path or base) + ".annotated.png"
    img.save(annotated)
    return annotated, result


# ---- screen recording ------------------------------------------------------

import base64 as _base64
import subprocess as _subprocess


def record_screen(duration=10, path=None, bit_rate="4M", size=None):
    """Record the device screen for `duration` seconds. Returns the host path.

    Uses Appium's `mobile: startRecordingScreen` (UIAutomator2 backend) which
    wraps `adb shell screenrecord`. Returns base64-encoded MP4; we decode and
    write locally. screenrecord caps each segment at 180s — for longer recordings
    chain multiple calls.

    Args:
        duration: seconds to record (max 180)
        path: host filesystem path; defaults to /tmp/anh-record-<ts>.mp4
        bit_rate: e.g. '4M' (default), '8M' for higher quality
        size: optional '<width>x<height>' for downscaling

    Returns:
        Path string of the saved .mp4 file.
    """
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError(f"record_screen duration must be > 0, got {duration!r}")
    if duration > 180:
        raise RuntimeError(
            f"adb screenrecord caps at 180s per segment; got {duration}.\n"
            f"  For longer: call record_screen() multiple times and concat with ffmpeg."
        )
    if path is None:
        path = str(ipc._TMP / f"anh-record-{int(time.time())}.mp4")
    import os as _os
    if _os.path.isdir(path):
        raise IsADirectoryError(f"record_screen path is a directory: {path}")
    parent = _os.path.dirname(_os.path.abspath(path))
    if parent and not _os.path.exists(parent):
        _os.makedirs(parent, exist_ok=True)
    args = {"timeLimit": int(duration) + 5, "bitRate": bit_rate}
    if size:
        args["videoSize"] = size
    try:
        appium("mobile: startRecordingScreen", **args)
    except Exception as e:
        raise RuntimeError(
            f"start screen recording failed: {e}\n"
            f"  Likely cause: UIAutomator2 version doesn't support `mobile: startRecordingScreen`.\n"
            f"  Try updating: `appium driver install --source=npm appium-uiautomator2-driver@latest`."
        )
    time.sleep(duration)
    try:
        b64 = appium("mobile: stopRecordingScreen")
    except Exception as e:
        raise RuntimeError(f"stop screen recording failed: {e}")
    with open(path, "wb") as f:
        f.write(_base64.b64decode(b64))
    return path


def start_screen_recording(bit_rate="4M", size=None):
    """Start a non-blocking screen recording. Call stop_screen_recording() to finish.

    For one-shot recording with a known duration, prefer `record_screen()`.
    """
    args = {"timeLimit": 600, "bitRate": bit_rate}
    if size:
        args["videoSize"] = size
    appium("mobile: startRecordingScreen", **args)


def stop_screen_recording(path=None):
    """Stop a recording started with start_screen_recording. Returns host path.

    The video is base64-encoded by Appium; we decode and save locally.
    """
    if path is None:
        path = str(ipc._TMP / f"anh-record-{int(time.time())}.mp4")
    b64 = appium("mobile: stopRecordingScreen")
    with open(path, "wb") as f:
        f.write(_base64.b64decode(b64))
    return path


# ---- agent-helpers hot-load ------------------------------------------------

_agent_helpers_loaded = False


def _load_agent_helpers():
    """Load ANH_AGENT_WORKSPACE/agent_helpers.py into globals. Called lazily."""
    global _agent_helpers_loaded
    if _agent_helpers_loaded:
        return
    _agent_helpers_loaded = True
    p = AGENT_WORKSPACE / "agent_helpers.py"
    if not p.exists():
        return
    spec = importlib.util.spec_from_file_location("android_harness_agent_helpers", p)
    if not spec or not spec.loader:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for k, v in vars(module).items():
        if k.startswith("_"):
            continue
        globals()[k] = v


_load_agent_helpers()
