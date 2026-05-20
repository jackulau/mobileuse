# Task-specific helpers, auto-loaded into iphone-harness / android-harness / mobile-use
# globals at import time. Defined here, they appear in `-c` scripts without an import.
#
# Cross-platform cleanup + organization helpers. Each public function detects which
# platform's helpers were loaded (iOS or Android) and dispatches accordingly. They are
# importable as a plain module too (no device required) — the platform dispatch raises
# only when an action that actually needs a device is invoked.
#
# Public surface (used by docs/demos/clean-and-organize-*.py and by domain skills):
#   list_installed_apps()    -> list[dict]
#   uninstall_app(id_or_label) -> dict
#   storage_summary()        -> dict
#   bulk_select(items, *, deletion_button="Delete", finder=None) -> int
#   confirm_destructive(label="Delete", timeout=4.0) -> bool
#
# Underscore-prefixed names are private — the harness loader skips them.

import os
import time


# ---- platform detection ----------------------------------------------------

def _platform():
    """Detect which harness loaded us by sniffing the globals namespace.

    Returns 'ios', 'android', or None if loaded outside either harness
    (e.g. during a unit-test import where we just want function references).
    """
    g = globals()
    # iOS-only helpers from iphone_harness/helpers.py
    if any(name in g for name in (
        "set_assistive_touch", "open_control_center", "alert_accept", "pick_wheel"
    )):
        return "ios"
    # Android-only helpers from android_harness/helpers.py
    if any(name in g for name in (
        "press_back", "press_home", "grant_permission", "open_notifications"
    )):
        return "android"
    return None


def _call(name, *args, **kwargs):
    """Invoke a helper from the host harness namespace, or raise a clear error
    if loaded outside any harness (so tests can still import this module)."""
    fn = globals().get(name)
    if fn is None:
        raise RuntimeError(
            f"agent_helpers.{name}() requires a running mobile-use harness "
            f"(iphone-harness or android-harness). Standalone import is for "
            f"introspection only."
        )
    return fn(*args, **kwargs)


# ---- list installed apps ---------------------------------------------------

def list_installed_apps():
    """Return a list of installed user apps on the connected device.

    iOS: scrapes Settings -> General -> iPhone Storage. Each row carries
         the app name and (when populated) a size string.
         Returns [{label: str, size: str | None}].

    Android: uses `mobile: shell` to run `pm list packages -3` for third-party
         packages. If shell access is denied (Appium not started with
         --relaxed-security), falls back to scraping Settings -> Apps ->
         See all apps.
         Returns [{label: str, package: str | None, size: str | None}].

    A device + daemon connection is required.
    """
    p = _platform()
    if p == "ios":
        return _ios_list_installed_apps()
    if p == "android":
        return _android_list_installed_apps()
    raise RuntimeError("list_installed_apps() requires iOS or Android harness loaded.")


def _ios_list_installed_apps():
    appium = globals()["appium"]
    find = globals()["find"]
    find_all = globals()["find_all"]
    tap = globals()["tap"]
    wait = globals()["wait"]
    scroll_by = globals()["scroll_by"]
    ui_tree = globals()["ui_tree"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    wait(0.6)
    appium("mobile: launchApp", bundleId="com.apple.Preferences")
    wait(2.0)

    def _tap_label(label):
        el = find(label=label, type="XCUIElementTypeCell") or find(
            label=label, type="XCUIElementTypeButton"
        )
        if el is None:
            scroll_by(dy=-300, velocity=400)
            wait(0.4)
            el = find(label=label, type="XCUIElementTypeCell") or find(
                label=label, type="XCUIElementTypeButton"
            )
        if el is None:
            raise RuntimeError(f"Couldn't find '{label}' row in Settings.")
        tap(el)

    _tap_label("General")
    wait(1.4)
    _tap_label("iPhone Storage")
    wait(3.0)  # iPhone Storage takes a beat to populate sizes.

    seen = {}
    for _ in range(40):  # up to 40 scroll steps
        invalidate_tree_cache()
        for el in ui_tree(visible_only=True):
            if el.get("type") != "XCUIElementTypeCell":
                continue
            label = el.get("label", "")
            if not label:
                continue
            # Skip non-app rows (recommendations, storage bar header).
            if any(skip in label for skip in (
                "Recommendations", "Used", "iPhone Storage", "iCloud"
            )):
                continue
            # "App Name, 1.2 GB" or "App Name, 4 KB" — the size is the last segment.
            parts = [p.strip() for p in label.split(",")]
            if len(parts) >= 2 and any(unit in parts[-1] for unit in ("KB", "MB", "GB", "B")):
                seen.setdefault(parts[0], parts[-1])
            else:
                seen.setdefault(parts[0], None)
        before = len(seen)
        scroll_by(dy=-500, velocity=500)
        wait(0.4)
        if len(seen) == before:
            break

    return [{"label": k, "size": v} for k, v in seen.items()]


def _android_list_installed_apps():
    appium = globals()["appium"]
    # Try `mobile: shell` first — fast and exhaustive.
    try:
        out = appium("mobile: shell", command="pm", args=["list", "packages", "-3"])
        text = out.get("stdout", "") if isinstance(out, dict) else str(out)
        pkgs = [line.replace("package:", "").strip() for line in text.splitlines() if "package:" in line]
        return [{"label": pkg.split(".")[-1], "package": pkg, "size": None} for pkg in pkgs]
    except Exception:
        pass

    # Fallback: scrape Settings -> Apps -> See all apps.
    find = globals()["find"]
    find_all = globals()["find_all"]
    tap = globals()["tap"]
    wait = globals()["wait"]
    scroll = globals()["scroll"]
    ui_tree = globals()["ui_tree"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: startActivity", package="com.android.settings",
           activity="com.android.settings.applications.ManageApplications")
    wait(2.0)

    seen = {}
    for _ in range(50):
        invalidate_tree_cache()
        for el in ui_tree(visible_only=True):
            text = el.get("text", "")
            rid = el.get("resource_id", "")
            if rid.endswith(":id/app_name") and text:
                seen.setdefault(text, {"label": text, "package": None, "size": None})
        before = len(seen)
        scroll(direction="down")
        wait(0.4)
        if len(seen) == before:
            break

    return list(seen.values())


# ---- uninstall app ---------------------------------------------------------

def uninstall_app(id_or_label):
    """Uninstall an app by bundle id (iOS) or package / display label.

    iOS: opens Settings -> General -> iPhone Storage -> <app> -> Delete App.
         The SpringBoard long-press path is documented in the springboard
         domain skill; this helper uses the Settings path because it works
         even when the app lives only in the App Library.

    Android: first tries the fast Appium path (`mobile: removeApp` with the
         package). Falls back to Settings -> Apps -> <app> -> Uninstall.

    Returns {ok: bool, action: 'uninstalled' | 'offloaded' | 'disabled' | 'blocked',
             reason: str | None}.
    """
    p = _platform()
    if p == "ios":
        return _ios_uninstall_app(id_or_label)
    if p == "android":
        return _android_uninstall_app(id_or_label)
    raise RuntimeError("uninstall_app() requires iOS or Android harness loaded.")


def _ios_uninstall_app(label):
    appium = globals()["appium"]
    find = globals()["find"]
    tap = globals()["tap"]
    wait = globals()["wait"]
    scroll_by = globals()["scroll_by"]
    ui_tree = globals()["ui_tree"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    wait(0.5)
    appium("mobile: launchApp", bundleId="com.apple.Preferences")
    wait(1.8)

    # Settings -> General -> iPhone Storage
    for step in ("General", "iPhone Storage"):
        cell = find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            scroll_by(dy=-300, velocity=400); wait(0.4)
            cell = find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            return {"ok": False, "action": "blocked", "reason": f"missing '{step}' row"}
        tap(cell); wait(2.0)

    # Find row whose label starts with our app name.
    target = None
    for _ in range(40):
        invalidate_tree_cache()
        for el in ui_tree(visible_only=True):
            if el.get("type") != "XCUIElementTypeCell":
                continue
            if el.get("label", "").startswith(label):
                target = el
                break
        if target is not None:
            break
        scroll_by(dy=-500, velocity=500); wait(0.3)
    if target is None:
        return {"ok": False, "action": "blocked", "reason": f"{label!r} not found in iPhone Storage"}

    tap(target); wait(1.5)

    delete = find(label="Delete App", type="XCUIElementTypeButton") or find(
        label="Delete App", type="XCUIElementTypeStaticText"
    )
    if delete is None:
        offload = find(label="Offload App", type="XCUIElementTypeButton")
        if offload is None:
            return {"ok": False, "action": "blocked", "reason": "no Delete/Offload affordance — likely a system app"}
        # Caller asked to uninstall; we won't silently downgrade.
        return {"ok": False, "action": "blocked", "reason": "system app — only Offload available"}

    tap(delete); wait(0.8)
    confirmed = confirm_destructive("Delete App")
    return {
        "ok": confirmed,
        "action": "uninstalled" if confirmed else "blocked",
        "reason": None if confirmed else "confirmation dialog not found",
    }


def _android_uninstall_app(id_or_label):
    appium = globals()["appium"]
    # Fast path: Appium can remove by package id directly.
    if "." in id_or_label:
        try:
            ok = appium("mobile: removeApp", appId=id_or_label)
            if ok:
                return {"ok": True, "action": "uninstalled", "reason": None}
        except Exception as e:
            # Fall through to the UI path.
            _ = e

    find = globals()["find"]
    tap = globals()["tap"]
    wait = globals()["wait"]
    scroll = globals()["scroll"]
    ui_tree = globals()["ui_tree"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: startActivity", package="com.android.settings",
           activity="com.android.settings.applications.ManageApplications")
    wait(2.0)

    target = None
    for _ in range(50):
        invalidate_tree_cache()
        for el in ui_tree(visible_only=True):
            if el.get("text") == id_or_label or id_or_label in el.get("text", ""):
                target = el
                break
        if target is not None:
            break
        scroll(direction="down"); wait(0.3)

    if target is None:
        return {"ok": False, "action": "blocked", "reason": f"{id_or_label!r} not found in Settings -> Apps"}

    tap(target); wait(1.5)
    uninstall = find(text="Uninstall") or find(content_desc="Uninstall")
    if uninstall is None:
        disable = find(text="Disable") or find(content_desc="Disable")
        if disable is None:
            return {"ok": False, "action": "blocked", "reason": "neither Uninstall nor Disable button present"}
        return {"ok": False, "action": "blocked", "reason": "only Disable available (system app)"}
    tap(uninstall); wait(0.8)
    confirmed = confirm_destructive("OK") or confirm_destructive("Uninstall")
    return {
        "ok": confirmed,
        "action": "uninstalled" if confirmed else "blocked",
        "reason": None if confirmed else "confirmation dialog not found",
    }


# ---- storage summary -------------------------------------------------------

def storage_summary():
    """Return {used: str, free: str, total: str, raw: str} from the device
    settings storage screen. Sizes are display strings ('45.3 GB'), not bytes —
    parsing is the caller's job.
    """
    p = _platform()
    if p == "ios":
        return _ios_storage_summary()
    if p == "android":
        return _android_storage_summary()
    raise RuntimeError("storage_summary() requires iOS or Android harness loaded.")


def _ios_storage_summary():
    appium = globals()["appium"]
    find = globals()["find"]
    tap = globals()["tap"]
    wait = globals()["wait"]
    ui_tree = globals()["ui_tree"]
    scroll_by = globals()["scroll_by"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    wait(0.5)
    appium("mobile: launchApp", bundleId="com.apple.Preferences")
    wait(1.8)

    for step in ("General", "iPhone Storage"):
        cell = find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            scroll_by(dy=-300, velocity=400); wait(0.4)
            cell = find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            raise RuntimeError(f"missing '{step}' row")
        tap(cell); wait(2.0)

    invalidate_tree_cache()
    used = free = total = None
    raw_bits = []
    for el in ui_tree(visible_only=True):
        text = (el.get("value") or el.get("label") or "").strip()
        if not text:
            continue
        raw_bits.append(text)
        # Top of iPhone Storage shows "Used X.X GB of Y.Y GB" sometimes split.
        if "Used" in text and "of" in text and "GB" in text:
            parts = text.split()
            try:
                used_idx = parts.index("Used")
                of_idx = parts.index("of", used_idx)
                used = parts[used_idx + 1] + " " + parts[used_idx + 2]
                total = parts[of_idx + 1] + " " + parts[of_idx + 2]
            except (ValueError, IndexError):
                pass
    return {"used": used, "free": free, "total": total, "raw": " | ".join(raw_bits[:20])}


def _android_storage_summary():
    appium = globals()["appium"]
    find = globals()["find"]
    wait = globals()["wait"]
    ui_tree = globals()["ui_tree"]
    invalidate_tree_cache = globals().get("invalidate_tree_cache", lambda: None)

    appium("mobile: startActivity", package="com.android.settings",
           activity="com.android.settings.Settings$StorageDashboardActivity")
    wait(2.0)
    invalidate_tree_cache()

    used = free = total = None
    raw_bits = []
    for el in ui_tree(visible_only=True):
        text = (el.get("text") or el.get("content_desc") or "").strip()
        if not text:
            continue
        raw_bits.append(text)
        low = text.lower()
        if "used" in low and "of" in low and "GB" in text:
            parts = text.split()
            for i, p_ in enumerate(parts):
                if p_.lower() == "used" and i >= 2:
                    used = parts[i - 2] + " " + parts[i - 1]
                if p_.lower() == "of" and i + 2 < len(parts):
                    total = parts[i + 1] + " " + parts[i + 2]
        if low.startswith("free"):
            parts = text.split()
            if len(parts) >= 3:
                free = parts[1] + " " + parts[2]
    return {"used": used, "free": free, "total": total, "raw": " | ".join(raw_bits[:20])}


# ---- bulk select -----------------------------------------------------------

def bulk_select(items, *, deletion_button="Delete", finder=None):
    """Generic 'enter Select mode, tap each item, hit Delete' flow.

    `items` is an iterable of finder-arguments. If `finder` is given it's called
    as `finder(item)` and must return an element dict (`None` to skip);
    otherwise each item is passed directly to `find(label=item)` on iOS or
    `find(text=item)` on Android.

    Returns the count of items that were successfully tapped (and so are
    selected when the Delete button is pressed).
    """
    p = _platform()
    find = globals()["find"]
    tap = globals()["tap"]
    wait = globals()["wait"]

    # Enter select mode — try several common affordances.
    candidates = []
    if p == "ios":
        candidates = [
            lambda: find(label="Select", type="XCUIElementTypeButton"),
            lambda: find(label="Edit", type="XCUIElementTypeButton"),
        ]
    elif p == "android":
        candidates = [
            lambda: find(text="Select"),
            lambda: find(content_desc="Select"),
            lambda: find(content_desc="More options"),  # then "Select all"
        ]
    select_btn = None
    for c in candidates:
        select_btn = c()
        if select_btn is not None:
            break
    if select_btn is not None:
        tap(select_btn); wait(0.4)

    n = 0
    for item in items:
        el = finder(item) if finder else (
            find(label=item) if p == "ios" else find(text=item)
        )
        if el is None:
            continue
        tap(el); wait(0.2); n += 1

    if n == 0:
        return 0

    if p == "ios":
        btn = find(label=deletion_button, type="XCUIElementTypeButton") or find(
            label=deletion_button, type="XCUIElementTypeStaticText"
        )
    else:
        btn = find(text=deletion_button) or find(content_desc=deletion_button)
    if btn is not None:
        tap(btn); wait(0.6)
        confirm_destructive(deletion_button)
    return n


# ---- destructive confirmation ---------------------------------------------

def confirm_destructive(label="Delete", timeout=4.0):
    """Wait briefly for a destructive-action confirmation dialog and tap the
    button whose label matches `label`. Returns True if tapped, False if the
    dialog never appeared.
    """
    find = globals().get("find")
    tap = globals().get("tap")
    if find is None or tap is None:
        return False

    p = _platform()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p == "ios":
            btn = find(label=label, type="XCUIElementTypeButton")
        else:
            btn = find(text=label) or find(content_desc=label)
        if btn is not None:
            tap(btn)
            return True
        time.sleep(0.2)
    return False
